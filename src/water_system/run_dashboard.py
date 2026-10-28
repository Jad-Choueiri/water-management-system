"""
run_dashboard.py - TEMPORARY launcher so you can see the dashboard move.
    python -m water_system.run_dashboard
    python -m water_system.run_dashboard blocked     (inject a blocked pipe)

Open http://localhost:5000 in your browser. Press Ctrl+C to stop.
Step 6 replaces this with the real main loop.
"""

import logging
import sys
import threading
import time

from water_system import config
from water_system.controller import Controller
from water_system.database import Database
from water_system.hardware import SensorReadError, create_hardware
from water_system.webapp import create_app


def control_loop(hw, ctl, db, lock, stop):
    """Read sensors, run one FSM step, log a reading. Once per second."""
    while not stop.is_set():
        with lock:
            try:
                ctl.step(hw.moisture.read_percent(),
                         hw.tank.read_percent(),
                         hw.flow.read_lpm())
            except SensorReadError as exc:
                ctl.report_sensor_error(str(exc))
            s = ctl.status()
        db.log_reading(s["moisture_pct"], s["tank_pct"], s["flow_lpm"],
                       s["state"], s["pump_on"])
        stop.wait(1.0)


def main() -> int:
    logging.basicConfig(level="INFO", format="%(asctime)s %(message)s")
    if not config.MOCK_MODE:
        print("This temporary launcher only runs in MOCK mode.")
        return 1

    db = Database(":memory:")                     # fresh each run
    hw = create_hardware()
    ctl = Controller(hw, on_event=db.log_event)
    lock = threading.RLock()
    hw.world.set_soil_pct(25.0)
    if len(sys.argv) > 1 and sys.argv[1] == "blocked":
        hw.world.set_flow_blocked(True)

    stop = threading.Event()
    worker = threading.Thread(target=control_loop,
                              args=(hw, ctl, db, lock, stop), daemon=True)
    worker.start()

    app = create_app(ctl, db, lock)
    print("\nDashboard: http://localhost:5000   (Ctrl+C to stop)\n")
    try:
        app.run(host="127.0.0.1", port=config.FLASK_PORT, debug=False,
                use_reloader=False)
    finally:
        stop.set()
        worker.join(timeout=3)
        hw.cleanup()
        db.close()
        print("\nAll outputs OFF (safe state).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())