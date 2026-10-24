"""
controller.py - Finite State Machine that decides when to water.

States (Step 3A): IDLE, WATERING, COOLDOWN, LOCKOUT
(FAULT is added in Step 3B.)

The controller never touches RPi.GPIO. It only uses the Hardware interfaces,
so it behaves identically on a laptop (mock) and on a Raspberry Pi (real).
"""

import logging
import time
from enum import Enum
from typing import Callable, Dict, Optional

from water_system import config
from water_system.hardware.base import Hardware

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "IDLE"
    WATERING = "WATERING"
    COOLDOWN = "COOLDOWN"
    LOCKOUT = "LOCKOUT"


class Controller:
    def __init__(self, hw: Hardware,
                 clock: Callable[[], float] = time.monotonic):
        self.hw = hw
        self._clock = clock
        self.state = State.IDLE
        self.state_since = clock()
        self.last_reason = "startup"
        self.last_moisture: Optional[float] = None
        self.last_tank: Optional[float] = None
        hw.all_outputs_off()                 # always begin in the safe state

    # ------------------------------------------------------------------ helpers
    def seconds_in_state(self) -> float:
        return self._clock() - self.state_since

    def _start_pump(self) -> None:
        self.hw.valve.on()                   # open the path FIRST
        self.hw.pump.on()
        self.hw.status_led.on()

    def _stop_pump(self) -> None:
        self.hw.pump.off()                   # stop the pump FIRST
        self.hw.valve.off()
        self.hw.status_led.off()

    def _transition(self, new_state: State, reason: str) -> None:
        if self.state == State.WATERING:
            self._stop_pump()                # exit action
        log.info("STATE %s -> %s (%s)", self.state.value, new_state.value, reason)
        self.state = new_state
        self.state_since = self._clock()
        self.last_reason = reason
        if new_state == State.WATERING:
            self._start_pump()               # entry action

    # --------------------------------------------------------------- main logic
    def step(self, moisture_pct: float, tank_pct: float) -> State:
        """Run ONE decision cycle with the given sensor readings."""
        self.last_moisture = moisture_pct
        self.last_tank = tank_pct

        if self.state == State.IDLE:
            if tank_pct < config.TANK_LOW_CUTOFF_PCT:
                self._transition(State.LOCKOUT, f"tank low ({tank_pct:.0f}%)")
            elif moisture_pct < config.MOISTURE_PUMP_ON_BELOW_PCT:
                self._transition(State.WATERING, f"soil dry ({moisture_pct:.0f}%)")

        elif self.state == State.WATERING:
            if tank_pct < config.TANK_LOW_CUTOFF_PCT:
                self._transition(State.LOCKOUT, f"tank low ({tank_pct:.0f}%)")
            elif moisture_pct > config.MOISTURE_PUMP_OFF_ABOVE_PCT:
                self._transition(State.COOLDOWN,
                                 f"target reached ({moisture_pct:.0f}%)")
            elif self.seconds_in_state() >= config.PUMP_MAX_RUN_SECONDS:
                self._transition(State.COOLDOWN, "max run time reached")

        elif self.state == State.COOLDOWN:
            if self.seconds_in_state() >= config.PUMP_COOLDOWN_SECONDS:
                self._transition(State.IDLE, "cooldown finished")

        elif self.state == State.LOCKOUT:
            if tank_pct >= config.TANK_RESUME_PCT:
                self._transition(State.IDLE, f"tank refilled ({tank_pct:.0f}%)")

        return self.state

    def status(self) -> Dict[str, object]:
        """Snapshot for the dashboard (Step 5)."""
        return {
            "state": self.state.value,
            "reason": self.last_reason,
            "seconds_in_state": round(self.seconds_in_state(), 1),
            "moisture_pct": self.last_moisture,
            "tank_pct": self.last_tank,
            "pump_on": self.hw.pump.is_on,
            "valve_on": self.hw.valve.is_on,
        }