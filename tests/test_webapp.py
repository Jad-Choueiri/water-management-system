"""Tests for the REST API using Flask's built-in test client (no server)."""

import random
import unittest

from water_system.controller import Controller, State
from water_system.database import Database
from water_system.hardware.mock import SimulatedWorld, create_mock_hardware
from water_system.webapp import create_app


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.db = Database(":memory:", clock=self.clock)
        world = SimulatedWorld(clock=self.clock, rng=random.Random(0))
        self.hw = create_mock_hardware(world)
        self.ctl = Controller(self.hw, clock=self.clock,
                              on_event=self.db.log_event)
        self.client = create_app(self.ctl, self.db).test_client()

    def tearDown(self):
        self.db.close()

    def test_status_reports_idle_at_start(self):
        data = self.client.get("/api/status").get_json()
        self.assertEqual(data["state"], "IDLE")
        self.assertFalse(data["pump_on"])
        self.assertTrue(data["mock_mode"])

    def test_status_reflects_watering(self):
        self.ctl.step(20.0, 80.0, 4.0)
        data = self.client.get("/api/status").get_json()
        self.assertEqual(data["state"], "WATERING")
        self.assertTrue(data["pump_on"])

    def test_history_returns_logged_readings(self):
        self.db.log_reading(42.0, 80.0, 0.0, "IDLE", False)
        rows = self.client.get("/api/history?minutes=10").get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["moisture_pct"], 42.0)

    def test_events_endpoint_lists_state_changes(self):
        self.ctl.step(20.0, 80.0, 4.0)
        rows = self.client.get("/api/events").get_json()
        self.assertEqual(rows[0]["kind"], "STATE_CHANGE")
        self.assertEqual(rows[0]["state"], "WATERING")

    def test_summary_endpoint(self):
        self.db.log_reading(40.0, 70.0, 0.0, "IDLE", False)
        data = self.client.get("/api/summary?hours=1").get_json()
        self.assertEqual(data["readings"], 1)

    def test_bad_query_values_fall_back_to_defaults(self):
        resp = self.client.get("/api/history?minutes=abc&limit=-5")
        self.assertEqual(resp.status_code, 200)

    def test_reset_with_no_fault_returns_409(self):
        resp = self.client.post("/api/fault/reset")
        self.assertEqual(resp.status_code, 409)

    def test_reset_clears_a_fault(self):
        self.ctl.report_sensor_error("test")
        self.assertEqual(self.ctl.state, State.FAULT)
        resp = self.client.post("/api/fault/reset")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.ctl.state, State.IDLE)
        self.assertFalse(self.hw.buzzer.is_on)

    def test_stop_interrupts_watering(self):
        self.ctl.step(20.0, 80.0, 4.0)
        resp = self.client.post("/api/pump/stop")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.ctl.state, State.COOLDOWN)
        self.assertFalse(self.hw.pump.is_on)

    def test_stop_when_idle_returns_409(self):
        resp = self.client.post("/api/pump/stop")
        self.assertEqual(resp.status_code, 409)

    def test_get_is_not_allowed_on_action_endpoints(self):
        self.assertEqual(self.client.get("/api/fault/reset").status_code, 405)
        self.assertEqual(self.client.get("/api/pump/stop").status_code, 405)

    def test_there_is_no_endpoint_to_start_the_pump(self):
        self.assertEqual(self.client.post("/api/pump/start").status_code, 404)
        self.assertEqual(self.client.post("/api/pump/on").status_code, 404)


if __name__ == "__main__":
    unittest.main()