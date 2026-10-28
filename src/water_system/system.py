"""
system.py - Owns the controller, database and the background threads.

Threads:
  control   read sensors -> controller.step()
  logger    write a reading to SQLite
  pruner    delete old rows
  watchdog  detect stale sensor data, even if the control loop is stuck

WaterSystem.stop() also calls hw.cleanup(), so the system owns the
hardware lifecycle. The caller owns (and closes) the database.
"""

import logging
import threading
from typing import Callable, List, Optional

from water_system import config
from water_system.controller import Controller
from water_system.database import Database
from water_system.hardware.base import Hardware, SensorReadError

log = logging.getLogger(__name__)


class WaterSystem:
    def __init__(self, hw: Hardware, db: Database, *,
                 control_interval: float = config.CONTROL_LOOP_INTERVAL_SECONDS,
                 log_interval: float = config.DB_LOG_INTERVAL_SECONDS,
                 prune_interval: float = config.DB_PRUNE_INTERVAL_SECONDS,
                 watchdog_interval: float = 1.0):
        self.hw = hw
        self.db = db
        self.lock = threading.RLock()        # shared with the Flask app
        self.controller = Controller(hw, on_event=db.log_event)
        self._intervals = {
            "control": control_interval,
            "logger": log_interval,
            "pruner": prune_interval,
            "watchdog": watchdog_interval,
        }
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        self._started = False
        self._stopped = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.db.log_event("STARTUP", self.controller.state.value,
                          "system started")
        self._spawn("control", self._control_cycle)
        self._spawn("watchdog", self._watchdog_cycle)
        self._spawn("logger", self._log_cycle)
        self._spawn("pruner", self._prune_cycle)
        log.info("System started (%d threads)", len(self._threads))

    def stop(self) -> None:
        """Safe to call more than once."""
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()                # 1. tell every thread to finish
        for t in self._threads:               # 2. wait for them
            t.join(timeout=5.0)
            if t.is_alive():
                log.error("Thread %s did not stop in time", t.name)
        with self.lock:                       # 3. only now force outputs OFF
            self.hw.cleanup()
        try:
            self.db.log_event("SHUTDOWN", self.controller.state.value,
                              "system stopped")
        except Exception:
            log.exception("could not log shutdown")
        log.info("System stopped")

    def wait_for_stop(self, timeout: Optional[float] = None) -> bool:
        return self._stop_event.wait(timeout)

    # -------------------------------------------------------------- threads
    def _spawn(self, name: str, cycle: Callable[[], None]) -> None:
        interval = self._intervals[name]

        def runner() -> None:
            while not self._stop_event.is_set():
                try:
                    cycle()
                except Exception:
                    log.exception("%s cycle failed", name)
                self._stop_event.wait(interval)   # wakes instantly on stop

        t = threading.Thread(target=runner, name=name, daemon=True)
        self._threads.append(t)
        t.start()

    def _control_cycle(self) -> None:
        # Sensor reads happen OUTSIDE the lock: a hung sensor must not
        # freeze the watchdog or the dashboard.
        try:
            moisture = self.hw.moisture.read_percent()
            tank = self.hw.tank.read_percent()
            flow = self.hw.flow.read_lpm()
        except SensorReadError as exc:
            with self.lock:
                self.controller.report_sensor_error(str(exc))
            return
        except Exception as exc:              # a bug must also fail SAFE
            log.exception("unexpected error while reading sensors")
            with self.lock:
                self.controller.report_sensor_error(f"internal error: {exc}")
            return
        with self.lock:
            self.controller.step(moisture, tank, flow)

    def _watchdog_cycle(self) -> None:
        with self.lock:
            self.controller.check_stale()

    def _log_cycle(self) -> None:
        with self.lock:
            s = self.controller.status()
        self.db.log_reading(s["moisture_pct"], s["tank_pct"], s["flow_lpm"],
                            s["state"], s["pump_on"])

    def _prune_cycle(self) -> None:
        self.db.prune()