"""
base.py - Hardware Abstraction Layer (HAL) interfaces and conversion math.

Everything above this layer (state machine, web UI) talks ONLY to these
interfaces. It never imports RPi.GPIO and never checks MOCK_MODE.

Contents:
  1. Pure conversion functions (ADC -> %, echo time -> distance, ...)
  2. DigitalOutput  - base class that handles active-low logic once
  3. Sensor interfaces
  4. Hardware       - a bundle of every device the system uses
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from water_system import config


class SensorReadError(Exception):
    """Raised when a sensor cannot deliver a valid reading."""


# =============================================================================
# 1. PURE CONVERSION FUNCTIONS (no hardware, easy to unit test)
# =============================================================================
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def adc_to_moisture_pct(raw: float) -> float:
    """Convert a raw 10-bit ADC value to soil moisture percent.
    Capacitive sensors read HIGH when dry, LOW when wet."""
    span = config.MOISTURE_ADC_DRY_VALUE - config.MOISTURE_ADC_WET_VALUE
    pct = (config.MOISTURE_ADC_DRY_VALUE - raw) / span * 100.0
    return clamp(pct, 0.0, 100.0)


def moisture_pct_to_adc(pct: float) -> float:
    """Inverse of adc_to_moisture_pct (used by the mock to fake ADC values)."""
    span = config.MOISTURE_ADC_DRY_VALUE - config.MOISTURE_ADC_WET_VALUE
    return config.MOISTURE_ADC_DRY_VALUE - (pct / 100.0) * span


def echo_time_to_distance_cm(echo_seconds: float) -> float:
    """HC-SR04: sound travels to the water AND back, so divide by 2."""
    return echo_seconds * config.SPEED_OF_SOUND_CM_PER_S / 2.0


def distance_to_tank_pct(distance_cm: float) -> float:
    """Ultrasonic sensor sits at the top: small distance = full tank."""
    span = config.TANK_EMPTY_DISTANCE_CM - config.TANK_FULL_DISTANCE_CM
    pct = (config.TANK_EMPTY_DISTANCE_CM - distance_cm) / span * 100.0
    return clamp(pct, 0.0, 100.0)


def tank_pct_to_distance_cm(pct: float) -> float:
    """Inverse of distance_to_tank_pct (used by the mock)."""
    span = config.TANK_EMPTY_DISTANCE_CM - config.TANK_FULL_DISTANCE_CM
    return config.TANK_EMPTY_DISTANCE_CM - (pct / 100.0) * span


def pulses_to_lpm(pulses: int, window_seconds: float) -> float:
    """Flow sensor: pulses counted over a time window -> liters per minute."""
    liters = pulses / config.FLOW_PULSES_PER_LITER
    return liters / window_seconds * 60.0


# =============================================================================
# 2. DIGITAL OUTPUT (relay, LED, buzzer)
# =============================================================================
class DigitalOutput(ABC):
    """
    A switchable output. Callers think in LOGICAL terms (on/off).
    This class converts to the ELECTRICAL level (HIGH/LOW) using active_low,
    then asks the subclass to drive the pin. (Template Method pattern.)
    """

    def __init__(self, name: str, pin: int, active_low: bool = False):
        self.name = name
        self.pin = pin
        self.active_low = active_low
        self._is_on = False          # every output starts OFF (safe state)
        self._lock = threading.Lock()

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def pin_level_high(self) -> bool:
        """Electrical level for the CURRENT logical state."""
        # ON + active-low  -> LOW      ON + active-high -> HIGH
        # OFF + active-low -> HIGH     OFF + active-high -> LOW
        return self._is_on != self.active_low

    def on(self) -> None:
        self._set(True)

    def off(self) -> None:
        self._set(False)

    def _set(self, state: bool) -> None:
        with self._lock:
            if state == self._is_on:
                return               # no change, no GPIO write
            self._is_on = state
            self._drive(self.pin_level_high, state)

    @abstractmethod
    def _drive(self, level_high: bool, logical_on: bool) -> None:
        """Physically (or virtually) set the pin."""


# =============================================================================
# 3. SENSOR INTERFACES
# =============================================================================
class MoistureSensor(ABC):
    @abstractmethod
    def read_percent(self) -> float:
        """Soil moisture 0-100 %. Raises SensorReadError on failure."""


class TankLevelSensor(ABC):
    @abstractmethod
    def read_percent(self) -> float:
        """Tank level 0-100 %. Raises SensorReadError on failure."""


class FlowSensor(ABC):
    @abstractmethod
    def read_lpm(self) -> float:
        """Current flow in liters/minute."""

    @abstractmethod
    def total_liters(self) -> float:
        """Total liters since start-up."""


# =============================================================================
# 4. HARDWARE BUNDLE
# =============================================================================
@dataclass
class Hardware:
    """Every device in the system, in one object passed to the controller."""
    pump: DigitalOutput
    valve: DigitalOutput
    status_led: DigitalOutput
    buzzer: DigitalOutput
    moisture: MoistureSensor
    tank: TankLevelSensor
    flow: FlowSensor
    world: Optional[object] = None                   # SimulatedWorld in mock mode
    _cleanup_fn: Optional[Callable[[], None]] = None

    def all_outputs_off(self) -> None:
        """Drive every output to its safe state. Pump first!"""
        for device in (self.pump, self.valve, self.status_led, self.buzzer):
            device.off()

    def cleanup(self) -> None:
        self.all_outputs_off()
        if self._cleanup_fn:
            self._cleanup_fn()