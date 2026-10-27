"""
controller.py - Finite State Machine that decides when to water.

States: IDLE, WATERING, COOLDOWN, LOCKOUT, FAULT

FAULT is a LATCHED safe state: pump OFF, valve OFF, buzzer ON. It is entered
on dry-run (pump on, no flow), sensor errors, or stale data, and it only
clears when a human calls reset_fault().

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
    FAULT = "FAULT"


class Controller:
    def __init__(self, hw: Hardware,
                 clock: Callable[[], float] = time.monotonic):
        self.hw = hw
        self._clock = clock
        self.state = State.IDLE
        self.state_since = clock()
        self.last_reason = "startup"
        self.fault_reason: Optional[str] = None
        self.last_moisture: Optional[float] = None
        self.last_tank: Optional[float] = None
        self.last_flow: Optional[float] = None
        self._last_good_reading = clock()
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
        # ---- exit actions ----
        if self.state == State.WATERING:
            self._stop_pump()
        if self.state == State.FAULT:
            self.hw.buzzer.off()
            self.fault_reason = None
        log.info("STATE %s -> %s (%s)", self.state.value, new_state.value, reason)
        self.state = new_state
        self.state_since = self._clock()
        self.last_reason = reason
        # ---- entry actions ----
        if new_state == State.WATERING:
            self._start_pump()
        elif new_state == State.FAULT:
            self._stop_pump()                # belt and braces: outputs OFF
            self.hw.buzzer.on()
            self.fault_reason = reason

    def _enter_fault(self, reason: str) -> None:
        if self.state != State.FAULT:        # latch: don't re-enter
            self._transition(State.FAULT, reason)

    # ------------------------------------------------------------ fault inputs
    def report_sensor_error(self, reason: str) -> None:
        """Called by the main loop when a sensor read raises SensorReadError."""
        self._enter_fault(f"sensor error: {reason}")

    def check_stale(self) -> None:
        """Called every cycle: no good reading for too long -> FAULT."""
        age = self._clock() - self._last_good_reading
        if age >= config.SENSOR_STALE_TIMEOUT_SECONDS:
            self._enter_fault(f"stale sensor data ({age:.0f}s)")

    def reset_fault(self) -> bool:
        """Human acknowledgement. Returns True if a fault was cleared."""
        if self.state != State.FAULT:
            return False
        self._last_good_reading = self._clock()   # fresh start for stale check
        self._transition(State.IDLE, "fault reset by operator")
        return True

    # --------------------------------------------------------------- main logic
    def step(self, moisture_pct: float, tank_pct: float,
             flow_lpm: float = 0.0) -> State:
        """Run ONE decision cycle with the given (valid) sensor readings."""
        self.last_moisture = moisture_pct
        self.last_tank = tank_pct
        self.last_flow = flow_lpm
        self._last_good_reading = self._clock()

        if self.state == State.FAULT:
            return self.state                # latched: only reset_fault() exits

        # Dry-run detection: pump has had time to build flow, but none is seen.
        if (self.state == State.WATERING
                and self.seconds_in_state() >= config.DRY_RUN_CHECK_DELAY_SECONDS
                and flow_lpm < config.MIN_FLOW_RATE_LPM):
            self._enter_fault(f"dry run: flow {flow_lpm:.2f} L/min")
            return self.state

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
            "fault_reason": self.fault_reason,
            "seconds_in_state": round(self.seconds_in_state(), 1),
            "moisture_pct": self.last_moisture,
            "tank_pct": self.last_tank,
            "flow_lpm": self.last_flow,
            "pump_on": self.hw.pump.is_on,
            "valve_on": self.hw.valve.is_on,
            "buzzer_on": self.hw.buzzer.is_on,
        }