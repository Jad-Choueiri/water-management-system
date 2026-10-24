"""Tests for the FSM. Run with:
    python -m unittest discover -s tests -v
"""

import random
import unittest

from water_system import config
from water_system.controller import Controller, State
from water_system.hardware.mock import SimulatedWorld, create_mock_hardware


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_controller():
    clock = FakeClock()
    world = SimulatedWorld(clock=clock, rng=random.Random(0))
    hw = create_mock_hardware(world)
    return clock, world, hw, Controller(hw, clock=clock)


class ControllerTests(unittest.TestCase):
    def test_starts_idle_with_everything_off(self):
        _, _, hw, ctl = make_controller()
        self.assertEqual(ctl.state, State.IDLE)
        self.assertFalse(hw.pump.is_on)
        self.assertFalse(hw.valve.is_on)

    def test_dry_soil_starts_watering(self):
        _, _, hw, ctl = make_controller()
        ctl.step(moisture_pct=20.0, tank_pct=80.0)
        self.assertEqual(ctl.state, State.WATERING)
        self.assertTrue(hw.pump.is_on)
        self.assertTrue(hw.valve.is_on)
        self.assertTrue(hw.status_led.is_on)

    def test_hysteresis_idle_does_not_start_in_the_band(self):
        _, _, hw, ctl = make_controller()
        ctl.step(45.0, 80.0)                       # between 30 and 60
        self.assertEqual(ctl.state, State.IDLE)
        self.assertFalse(hw.pump.is_on)

    def test_hysteresis_watering_keeps_going_in_the_band(self):
        _, _, hw, ctl = make_controller()
        ctl.step(20.0, 80.0)
        ctl.step(45.0, 80.0)                       # above ON level, below OFF level
        self.assertEqual(ctl.state, State.WATERING)
        self.assertTrue(hw.pump.is_on)

    def test_stops_when_target_reached(self):
        _, _, hw, ctl = make_controller()
        ctl.step(20.0, 80.0)
        ctl.step(61.0, 80.0)
        self.assertEqual(ctl.state, State.COOLDOWN)
        self.assertFalse(hw.pump.is_on)
        self.assertFalse(hw.valve.is_on)

    def test_max_run_time_stops_pump(self):
        clock, _, hw, ctl = make_controller()
        ctl.step(20.0, 80.0)                       # soil never improves
        clock.advance(config.PUMP_MAX_RUN_SECONDS - 1)
        ctl.step(20.0, 80.0)
        self.assertEqual(ctl.state, State.WATERING)
        clock.advance(1)
        ctl.step(20.0, 80.0)
        self.assertEqual(ctl.state, State.COOLDOWN)
        self.assertFalse(hw.pump.is_on)

    def test_cooldown_blocks_restart_until_it_finishes(self):
        clock, _, hw, ctl = make_controller()
        ctl.step(20.0, 80.0)
        ctl.step(61.0, 80.0)                       # -> COOLDOWN
        clock.advance(config.PUMP_COOLDOWN_SECONDS - 1)
        ctl.step(10.0, 80.0)                       # very dry, but still resting
        self.assertEqual(ctl.state, State.COOLDOWN)
        self.assertFalse(hw.pump.is_on)
        clock.advance(1)
        ctl.step(10.0, 80.0)                       # cooldown over -> IDLE
        self.assertEqual(ctl.state, State.IDLE)
        ctl.step(10.0, 80.0)                       # now it may water again
        self.assertEqual(ctl.state, State.WATERING)

    def test_low_tank_blocks_start_and_resumes_with_hysteresis(self):
        _, _, hw, ctl = make_controller()
        ctl.step(10.0, 10.0)                       # dry soil but empty tank
        self.assertEqual(ctl.state, State.LOCKOUT)
        self.assertFalse(hw.pump.is_on)
        ctl.step(10.0, 20.0)                       # refilling, still under 25
        self.assertEqual(ctl.state, State.LOCKOUT)
        ctl.step(10.0, 26.0)
        self.assertEqual(ctl.state, State.IDLE)

    def test_tank_running_dry_during_watering_stops_pump(self):
        _, _, hw, ctl = make_controller()
        ctl.step(20.0, 80.0)
        ctl.step(25.0, 14.0)
        self.assertEqual(ctl.state, State.LOCKOUT)
        self.assertFalse(hw.pump.is_on)
        self.assertFalse(hw.valve.is_on)


class ClosedLoopTest(unittest.TestCase):
    def test_controller_waters_the_simulated_garden(self):
        clock, world, hw, ctl = make_controller()
        world.set_soil_pct(20.0)
        seen = set()
        for _ in range(100):                       # 100 simulated seconds
            clock.advance(1.0)
            ctl.step(world.soil_pct(), world.tank_pct())
            seen.add(ctl.state)
        self.assertIn(State.WATERING, seen)
        self.assertIn(State.COOLDOWN, seen)
        self.assertGreater(world.total_liters(), 0.0)
        self.assertFalse(hw.pump.is_on)


if __name__ == "__main__":
    unittest.main()