"""
mock.py - Simulated hardware for development on a laptop.

SimulatedWorld is a tiny "physics model" of the garden:
  - Pump ON + valve OPEN + water in tank  -> water flows
  - Flowing: soil gets wetter, tank drains
  - Not flowing: soil slowly dries

Time is integrated LAZILY: every access first advances the simulation by
the elapsed time since the last access. No background thread is needed.
"""

import logging
import random
import threading
import time
from typing import Callable, Dict, Optional

from water_system import config
from water_system.hardware.base import (
    DigitalOutput, FlowSensor, Hardware, MoistureSensor, SensorReadError,
    TankLevelSensor, adc_to_moisture_pct, clamp, distance_to_tank_pct,
    moisture_pct_to_adc, tank_pct_to_distance_cm,
)

log = logging.getLogger(__name__)


class SimulatedWorld:
    """Thread-safe model of soil, tank, pump and valve."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 rng: Optional[random.Random] = None):
        self._clock = clock                       # injectable for tests
        self.rng = rng or random.Random()
        self._lock = threading.Lock()
        self._soil_pct = config.MOCK_SOIL_START_PCT
        self._tank_pct = config.MOCK_TANK_START_PCT
        self._pump_on = False
        self._valve_open = False
        self._flow_blocked = False                # fault injection
        self._sensors_failed = False              # fault injection
        self._total_liters = 0.0
        self._last_update = clock()

    # ---- internal physics (call with the lock held) -------------------------
    def _is_flowing_locked(self) -> bool:
        return (self._pump_on and self._valve_open
                and not self._flow_blocked and self._tank_pct > 0.0)

    def _advance_locked(self) -> None:
        now = self._clock()
        dt = max(0.0, now - self._last_update)
        self._last_update = now
        if dt == 0.0:
            return
        if self._is_flowing_locked():
            self._soil_pct += config.MOCK_SOIL_WET_RATE_PCT_PER_S * dt
            self._tank_pct -= config.MOCK_TANK_DRAIN_PCT_PER_S * dt
            self._total_liters += config.MOCK_FLOW_RATE_LPM / 60.0 * dt
        else:
            self._soil_pct -= config.MOCK_SOIL_DRY_RATE_PCT_PER_S * dt
        self._soil_pct = clamp(self._soil_pct, 0.0, 100.0)
        self._tank_pct = clamp(self._tank_pct, 0.0, 100.0)

    # ---- actuator side (called by MockDigitalOutput) ------------------------
    def set_pump(self, on: bool) -> None:
        with self._lock:
            self._advance_locked()    # settle the past BEFORE changing state
            self._pump_on = on

    def set_valve(self, open_: bool) -> None:
        with self._lock:
            self._advance_locked()
            self._valve_open = open_

    # ---- sensor side ---------------------------------------------------------
    def soil_pct(self) -> float:
        with self._lock:
            self._advance_locked()
            return self._soil_pct

    def tank_pct(self) -> float:
        with self._lock:
            self._advance_locked()
            return self._tank_pct

    def flow_lpm(self) -> float:
        with self._lock:
            self._advance_locked()
            return config.MOCK_FLOW_RATE_LPM if self._is_flowing_locked() else 0.0

    def total_liters(self) -> float:
        with self._lock:
            self._advance_locked()
            return self._total_liters

    def sensors_failed(self) -> bool:
        with self._lock:
            return self._sensors_failed

    # ---- fault injection / test controls ------------------------------------
    def set_soil_pct(self, pct: float) -> None:
        with self._lock:
            self._advance_locked()
            self._soil_pct = clamp(pct, 0.0, 100.0)

    def set_tank_pct(self, pct: float) -> None:
        with self._lock:
            self._advance_locked()
            self._tank_pct = clamp(pct, 0.0, 100.0)

    def set_flow_blocked(self, blocked: bool) -> None:
        """Simulate a clogged pipe / dry pump: pump runs but no water moves."""
        with self._lock:
            self._advance_locked()
            self._flow_blocked = blocked

    def set_sensors_failed(self, failed: bool) -> None:
        """Simulate a disconnected sensor: reads raise SensorReadError."""
        with self._lock:
            self._sensors_failed = failed

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            self._advance_locked()
            return {
                "soil_pct": self._soil_pct,
                "tank_pct": self._tank_pct,
                "pump_on": self._pump_on,
                "valve_open": self._valve_open,
                "flowing": self._is_flowing_locked(),
                "total_liters": self._total_liters,
            }


# =============================================================================
# MOCK DEVICES
# =============================================================================
def _noise(world: SimulatedWorld, sigma: float) -> float:
    # normalvariate is thread-safe; random.gauss is not.
    return world.rng.normalvariate(0.0, sigma)


class MockDigitalOutput(DigitalOutput):
    """Logs what the real GPIO pin WOULD do, and optionally tells the world."""

    def __init__(self, name: str, pin: int, active_low: bool = False,
                 on_change: Optional[Callable[[bool], None]] = None):
        super().__init__(name, pin, active_low)
        self._on_change = on_change

    def _drive(self, level_high: bool, logical_on: bool) -> None:
        log.info("[MOCK GPIO%02d] %-10s -> %-4s (logical %s)",
                 self.pin, self.name, "HIGH" if level_high else "LOW",
                 "ON" if logical_on else "OFF")
        if self._on_change:
            self._on_change(logical_on)


class MockMoistureSensor(MoistureSensor):
    def __init__(self, world: SimulatedWorld):
        self._world = world

    def read_percent(self) -> float:
        if self._world.sensors_failed():
            raise SensorReadError("Mock moisture sensor disconnected")
        noisy = self._world.soil_pct() + _noise(self._world,
                                                config.MOCK_SENSOR_NOISE_PCT)
        # Pretend to be a 10-bit ADC: integer counts, 0..1023.
        raw = int(round(clamp(moisture_pct_to_adc(noisy), 0, 1023)))
        return adc_to_moisture_pct(raw)   # SAME code the real sensor uses


class MockTankLevelSensor(TankLevelSensor):
    def __init__(self, world: SimulatedWorld):
        self._world = world

    def read_percent(self) -> float:
        if self._world.sensors_failed():
            raise SensorReadError("Mock ultrasonic sensor: no echo")
        noisy = self._world.tank_pct() + _noise(self._world,
                                                config.MOCK_SENSOR_NOISE_PCT)
        distance_cm = tank_pct_to_distance_cm(clamp(noisy, 0.0, 100.0))
        return distance_to_tank_pct(distance_cm)


class MockFlowSensor(FlowSensor):
    def __init__(self, world: SimulatedWorld):
        self._world = world

    def read_lpm(self) -> float:
        lpm = self._world.flow_lpm()
        if lpm == 0.0:
            return 0.0
        return max(0.0, lpm + _noise(self._world, 0.1))

    def total_liters(self) -> float:
        return self._world.total_liters()


# =============================================================================
# FACTORY
# =============================================================================
def create_mock_hardware(world: Optional[SimulatedWorld] = None) -> Hardware:
    world = world or SimulatedWorld()
    return Hardware(
        pump=MockDigitalOutput("pump", config.PIN_PUMP_RELAY,
                               config.RELAY_ACTIVE_LOW, world.set_pump),
        valve=MockDigitalOutput("valve", config.PIN_VALVE_RELAY,
                                config.RELAY_ACTIVE_LOW, world.set_valve),
        status_led=MockDigitalOutput("status_led", config.PIN_STATUS_LED),
        buzzer=MockDigitalOutput("buzzer", config.PIN_BUZZER),
        moisture=MockMoistureSensor(world),
        tank=MockTankLevelSensor(world),
        flow=MockFlowSensor(world),
        world=world,
    )