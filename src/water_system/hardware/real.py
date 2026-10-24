"""
real.py - Real Raspberry Pi hardware drivers.

!! UNTESTED until you have a Pi + wiring. !!
RPi.GPIO and spidev are imported INSIDE create_real_hardware(), so merely
importing this file on a laptop never fails.

Assumed wiring:
  - Moisture sensor -> MCP3008 ADC (SPI0, CE0), channel config.MOISTURE_ADC_CHANNEL
  - HC-SR04 ECHO goes through a 5V->3.3V voltage divider
"""

import logging
import threading
import time

from water_system import config
from water_system.hardware.base import (
    DigitalOutput, FlowSensor, Hardware, MoistureSensor, SensorReadError,
    TankLevelSensor, adc_to_moisture_pct, distance_to_tank_pct,
    echo_time_to_distance_cm, pulses_to_lpm,
)

log = logging.getLogger(__name__)


class RealDigitalOutput(DigitalOutput):
    def __init__(self, gpio, name: str, pin: int, active_low: bool = False):
        super().__init__(name, pin, active_low)
        self._gpio = gpio
        # Start in the OFF electrical level so nothing glitches at boot.
        gpio.setup(pin, gpio.OUT,
                   initial=gpio.HIGH if self.pin_level_high else gpio.LOW)

    def _drive(self, level_high: bool, logical_on: bool) -> None:
        self._gpio.output(self.pin,
                          self._gpio.HIGH if level_high else self._gpio.LOW)


class RealMoistureSensor(MoistureSensor):
    """Reads an MCP3008 10-bit ADC over SPI."""

    def __init__(self, spi, channel: int):
        self._spi = spi
        self._channel = channel

    def read_percent(self) -> float:
        try:
            reply = self._spi.xfer2([1, (8 + self._channel) << 4, 0])
        except Exception as exc:
            raise SensorReadError(f"SPI read failed: {exc}") from exc
        raw = ((reply[1] & 3) << 8) | reply[2]
        return adc_to_moisture_pct(raw)


class RealTankLevelSensor(TankLevelSensor):
    """HC-SR04 ultrasonic sensor, POLLED: trigger, then time the echo."""

    def __init__(self, gpio):
        self._gpio = gpio
        gpio.setup(config.PIN_ULTRASONIC_TRIG, gpio.OUT, initial=gpio.LOW)
        gpio.setup(config.PIN_ULTRASONIC_ECHO, gpio.IN)

    def read_percent(self) -> float:
        g = self._gpio
        g.output(config.PIN_ULTRASONIC_TRIG, True)      # 10 microsecond pulse
        time.sleep(0.00001)
        g.output(config.PIN_ULTRASONIC_TRIG, False)

        deadline = time.perf_counter() + config.ULTRASONIC_TIMEOUT_SECONDS
        while g.input(config.PIN_ULTRASONIC_ECHO) == 0:
            if time.perf_counter() > deadline:
                raise SensorReadError("Ultrasonic: no echo start")
        start = time.perf_counter()
        deadline = start + config.ULTRASONIC_TIMEOUT_SECONDS
        while g.input(config.PIN_ULTRASONIC_ECHO) == 1:
            if time.perf_counter() > deadline:
                raise SensorReadError("Ultrasonic: echo never ended")
        echo_seconds = time.perf_counter() - start
        return distance_to_tank_pct(echo_time_to_distance_cm(echo_seconds))


class RealFlowSensor(FlowSensor):
    """YF-S201, INTERRUPT-driven: a callback counts every pulse edge."""

    def __init__(self, gpio):
        self._lock = threading.Lock()
        self._pulses = 0
        self._window_start_pulses = 0
        self._window_start_time = time.monotonic()
        self._lpm = 0.0
        gpio.setup(config.PIN_FLOW_SENSOR, gpio.IN, pull_up_down=gpio.PUD_UP)
        gpio.add_event_detect(config.PIN_FLOW_SENSOR, gpio.RISING,
                              callback=self._on_pulse)

    def _on_pulse(self, channel) -> None:
        with self._lock:
            self._pulses += 1

    def read_lpm(self) -> float:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._window_start_time
            if elapsed >= config.FLOW_CALC_WINDOW_SECONDS:
                self._lpm = pulses_to_lpm(
                    self._pulses - self._window_start_pulses, elapsed)
                self._window_start_pulses = self._pulses
                self._window_start_time = now
            return self._lpm

    def total_liters(self) -> float:
        with self._lock:
            return self._pulses / config.FLOW_PULSES_PER_LITER


def create_real_hardware() -> Hardware:
    import RPi.GPIO as GPIO      # only exists on a Raspberry Pi
    import spidev

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1_350_000

    def cleanup() -> None:
        spi.close()
        GPIO.cleanup()

    return Hardware(
        pump=RealDigitalOutput(GPIO, "pump", config.PIN_PUMP_RELAY,
                               config.RELAY_ACTIVE_LOW),
        valve=RealDigitalOutput(GPIO, "valve", config.PIN_VALVE_RELAY,
                                config.RELAY_ACTIVE_LOW),
        status_led=RealDigitalOutput(GPIO, "status_led", config.PIN_STATUS_LED),
        buzzer=RealDigitalOutput(GPIO, "buzzer", config.PIN_BUZZER),
        moisture=RealMoistureSensor(spi, config.MOISTURE_ADC_CHANNEL),
        tank=RealTankLevelSensor(GPIO),
        flow=RealFlowSensor(GPIO),
        _cleanup_fn=cleanup,
    )