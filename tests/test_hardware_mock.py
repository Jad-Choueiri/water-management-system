"""Unit tests for the hardware abstraction + simulation. Run with:
    python -m unittest discover -s tests -v
"""

import random
import unittest

from water_system import config
from water_system.hardware.base import (
    SensorReadError, adc_to_moisture_pct, distance_to_tank_pct, pulses_to_lpm,
)
from water_system.hardware.mock import (
    MockDigitalOutput, SimulatedWorld, create_mock_hardware,
)


class FakeClock:
    """A controllable clock: tests 'wait' 10 s instantly."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_world():
    clock = FakeClock()
    return clock, SimulatedWorld(clock=clock, rng=random.Random(0))


class ConversionTests(unittest.TestCase):
    def test_adc_endpoints_and_clamping(self):
        self.assertAlmostEqual(adc_to_moisture_pct(config.MOISTURE_ADC_DRY_VALUE), 0.0)
        self.assertAlmostEqual(adc_to_moisture_pct(config.MOISTURE_ADC_WET_VALUE), 100.0)
        self.assertEqual(adc_to_moisture_pct(1023), 0.0)    # drier than "dry"
        self.assertEqual(adc_to_moisture_pct(0), 100.0)     # wetter than "wet"

    def test_tank_distance_conversion(self):
        self.assertAlmostEqual(distance_to_tank_pct(config.TANK_FULL_DISTANCE_CM), 100.0)
        self.assertAlmostEqual(distance_to_tank_pct(config.TANK_EMPTY_DISTANCE_CM), 0.0)
        self.assertEqual(distance_to_tank_pct(0.0), 100.0)
        self.assertEqual(distance_to_tank_pct(500.0), 0.0)

    def test_pulses_to_lpm(self):
        # One liter of pulses over 60 s is exactly 1 L/min.
        self.assertAlmostEqual(pulses_to_lpm(config.FLOW_PULSES_PER_LITER, 60.0), 1.0)


class OutputTests(unittest.TestCase):
    def test_active_low_output_levels(self):
        out = MockDigitalOutput("pump", 17, active_low=True)
        self.assertFalse(out.is_on)
        self.assertTrue(out.pin_level_high)    # OFF = HIGH for active-low
        out.on()
        self.assertTrue(out.is_on)
        self.assertFalse(out.pin_level_high)   # ON = LOW for active-low

    def test_active_high_output_levels(self):
        out = MockDigitalOutput("led", 22, active_low=False)
        self.assertFalse(out.pin_level_high)
        out.on()
        self.assertTrue(out.pin_level_high)


class WorldTests(unittest.TestCase):
    def test_soil_dries_when_pump_off(self):
        clock, world = make_world()
        world.set_soil_pct(50.0)
        clock.advance(10)
        self.assertAlmostEqual(world.soil_pct(),
                               50.0 - config.MOCK_SOIL_DRY_RATE_PCT_PER_S * 10)

    def test_watering_raises_soil_and_drains_tank(self):
        clock, world = make_world()
        world.set_soil_pct(50.0)
        world.set_tank_pct(80.0)
        world.set_valve(True)
        world.set_pump(True)
        clock.advance(10)
        snap = world.snapshot()
        self.assertAlmostEqual(snap["soil_pct"],
                               50.0 + config.MOCK_SOIL_WET_RATE_PCT_PER_S * 10)
        self.assertAlmostEqual(snap["tank_pct"],
                               80.0 - config.MOCK_TANK_DRAIN_PCT_PER_S * 10)
        self.assertAlmostEqual(snap["total_liters"],
                               config.MOCK_FLOW_RATE_LPM / 60.0 * 10)

    def test_pump_without_valve_does_not_water(self):
        clock, world = make_world()
        world.set_soil_pct(50.0)
        world.set_pump(True)               # valve still closed
        clock.advance(10)
        self.assertLess(world.soil_pct(), 50.0)
        self.assertEqual(world.flow_lpm(), 0.0)

    def test_blocked_pipe_gives_zero_flow(self):
        clock, world = make_world()
        world.set_valve(True)
        world.set_pump(True)
        world.set_flow_blocked(True)
        clock.advance(5)
        self.assertEqual(world.flow_lpm(), 0.0)

    def test_empty_tank_stops_flow(self):
        clock, world = make_world()
        world.set_tank_pct(0.0)
        world.set_valve(True)
        world.set_pump(True)
        clock.advance(5)
        self.assertEqual(world.flow_lpm(), 0.0)


class MockHardwareTests(unittest.TestCase):
    def test_sensor_fault_raises(self):
        _, world = make_world()
        hw = create_mock_hardware(world)
        world.set_sensors_failed(True)
        with self.assertRaises(SensorReadError):
            hw.moisture.read_percent()
        with self.assertRaises(SensorReadError):
            hw.tank.read_percent()

    def test_mock_sensors_track_world(self):
        _, world = make_world()
        world.set_soil_pct(40.0)
        world.set_tank_pct(60.0)
        hw = create_mock_hardware(world)
        self.assertAlmostEqual(hw.moisture.read_percent(), 40.0, delta=3.0)
        self.assertAlmostEqual(hw.tank.read_percent(), 60.0, delta=3.0)
        self.assertEqual(hw.flow.read_lpm(), 0.0)

    def test_cleanup_turns_everything_off(self):
        _, world = make_world()
        hw = create_mock_hardware(world)
        hw.valve.on()
        hw.pump.on()
        hw.cleanup()
        self.assertFalse(hw.pump.is_on)
        self.assertFalse(hw.valve.is_on)
        self.assertEqual(world.flow_lpm(), 0.0)


if __name__ == "__main__":
    unittest.main()