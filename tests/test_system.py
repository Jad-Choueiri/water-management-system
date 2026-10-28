"""Tests for WaterSystem. These use REAL threads with tiny intervals,
so each test finishes in well under a second."""

import time
import unittest
from unittest import mock

from water_system import config
from water_system.controller import State
from water_system.database import Database
from water_system.hardware.mock import create_mock_hardware
from water_system.system import WaterSystem

FAST = dict(control_interval=0.01, log_interval=0.01,
            prune_interval=0.05, watchdog_interval=0.01)


def wait_until(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class WaterSystemTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.hw = create_mock_hardware()
        self.system = WaterSystem(self.hw, self.db, **FAST)

    def tearDown(self):
        self.system.stop()
        self.db.close()

    def test_control_loop_starts_watering_dry_soil(self):
        self.hw.world.set_soil_pct(20.0)
        self.system.start()
        self.assertTrue(wait_until(
            lambda: self.system.controller.state == State.WATERING))
        self.assertTrue(self.hw.pump.is_on)

    def test_readings_are_logged_to_the_database(self):
        self.system.start()
        self.assertTrue(wait_until(lambda: self.db.count("readings") >= 3))

    def test_stop_joins_threads_and_turns_everything_off(self):
        self.hw.world.set_soil_pct(20.0)
        self.system.start()
        self.assertTrue(wait_until(lambda: self.hw.pump.is_on))
        self.system.stop()
        self.assertFalse(self.hw.pump.is_on)
        self.assertFalse(self.hw.valve.is_on)
        self.assertFalse(any(t.is_alive() for t in self.system._threads))

    def test_stop_is_idempotent(self):
        self.system.start()
        self.system.stop()
        self.system.stop()                    # must not raise

    def test_startup_and_shutdown_events_are_logged(self):
        self.system.start()
        self.system.stop()
        kinds = [e["kind"] for e in self.db.recent_events(10)]
        self.assertIn("STARTUP", kinds)
        self.assertIn("SHUTDOWN", kinds)

    def test_sensor_failure_causes_fault_and_pump_off(self):
        self.hw.world.set_sensors_failed(True)
        self.system.start()
        self.assertTrue(wait_until(
            lambda: self.system.controller.state == State.FAULT))
        self.assertFalse(self.hw.pump.is_on)
        self.assertTrue(self.hw.buzzer.is_on)

    def test_unexpected_exception_fails_safe(self):
        def boom():
            raise RuntimeError("boom")
        self.hw.moisture.read_percent = boom  # simulate a driver bug
        self.system.start()
        self.assertTrue(wait_until(
            lambda: self.system.controller.state == State.FAULT))
        self.assertIn("internal error", self.system.controller.fault_reason)


class WatchdogTest(unittest.TestCase):
    def test_watchdog_detects_a_stalled_control_loop(self):
        db = Database(":memory:")
        hw = create_mock_hardware()
        # Control loop runs ONCE, then sleeps for 1000 s = "stuck".
        system = WaterSystem(hw, db, control_interval=1000, log_interval=1000,
                             prune_interval=1000, watchdog_interval=0.02)
        try:
            with mock.patch.object(config, "SENSOR_STALE_TIMEOUT_SECONDS", 0.3):
                system.start()
                self.assertTrue(wait_until(
                    lambda: system.controller.state == State.FAULT))
                self.assertIn("stale", system.controller.fault_reason)
        finally:
            system.stop()
            db.close()


if __name__ == "__main__":
    unittest.main()