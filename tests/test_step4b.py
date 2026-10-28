"""Tests for retention, summary queries and controller->database wiring."""

import random
import unittest

from water_system import config
from water_system.controller import Controller, State
from water_system.database import Database
from water_system.hardware.mock import SimulatedWorld, create_mock_hardware

DAY = 86400.0


class ManualClock:
    def __init__(self, start=100 * DAY):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class PruneTests(unittest.TestCase):
    def test_prune_deletes_only_old_readings(self):
        clock = ManualClock()
        db = Database(":memory:", clock=clock)
        db.log_reading(10.0, 50.0, 0.0, "IDLE", False)        # will be old
        clock.advance((config.DB_READINGS_RETENTION_DAYS + 1) * DAY)
        db.log_reading(20.0, 50.0, 0.0, "IDLE", False)        # fresh
        deleted = db.prune()
        self.assertEqual(deleted["readings"], 1)
        self.assertEqual(db.count("readings"), 1)
        self.assertEqual(db.latest_readings(1)[0]["moisture_pct"], 20.0)
        db.close()

    def test_events_are_kept_longer_than_readings(self):
        clock = ManualClock()
        db = Database(":memory:", clock=clock)
        db.log_event("FAULT", "FAULT", "old but important")
        clock.advance((config.DB_READINGS_RETENTION_DAYS + 1) * DAY)
        deleted = db.prune()
        self.assertEqual(deleted["events"], 0)
        self.assertEqual(db.count("events"), 1)
        clock.advance(config.DB_EVENTS_RETENTION_DAYS * DAY)
        self.assertEqual(db.prune()["events"], 1)
        db.close()


class SummaryTests(unittest.TestCase):
    def test_summary_statistics(self):
        clock = ManualClock()
        db = Database(":memory:", clock=clock)
        db.log_reading(20.0, 80.0, 4.0, "WATERING", True)
        db.log_reading(40.0, 70.0, 0.0, "IDLE", False)
        db.log_reading(60.0, 60.0, 0.0, "IDLE", False)
        db.log_event("STATE_CHANGE", "WATERING", "soil dry")
        db.log_event("FAULT", "FAULT", "dry run")
        s = db.summary(3600)
        self.assertEqual(s["readings"], 3)
        self.assertAlmostEqual(s["avg_moisture"], 40.0)
        self.assertEqual(s["min_moisture"], 20.0)
        self.assertEqual(s["max_moisture"], 60.0)
        self.assertEqual(s["pump_on_samples"], 1)
        self.assertEqual(s["watering_cycles"], 1)
        self.assertEqual(s["faults"], 1)
        db.close()

    def test_summary_of_empty_database(self):
        db = Database(":memory:", clock=ManualClock())
        s = db.summary(3600)
        self.assertEqual(s["readings"], 0)
        self.assertIsNone(s["avg_moisture"])
        self.assertEqual(s["pump_on_samples"], 0)
        db.close()

    def test_summary_ignores_data_outside_the_window(self):
        clock = ManualClock()
        db = Database(":memory:", clock=clock)
        db.log_reading(50.0, 50.0, 0.0, "IDLE", False)
        clock.advance(2 * 3600)
        self.assertEqual(db.summary(3600)["readings"], 0)
        self.assertEqual(db.summary(3 * 3600)["readings"], 1)
        db.close()

    def test_history_is_oldest_first_and_keeps_the_newest(self):
        clock = ManualClock()
        db = Database(":memory:", clock=clock)
        for i in range(5):
            db.log_reading(float(i), 50.0, 0.0, "IDLE", False)
            clock.advance(1)
        rows = db.history(3600, limit=3)
        self.assertEqual([r["moisture_pct"] for r in rows], [2.0, 3.0, 4.0])
        db.close()


class EventWiringTests(unittest.TestCase):
    def make(self, on_event):
        clock = ManualClock(start=0.0)
        world = SimulatedWorld(clock=clock, rng=random.Random(0))
        hw = create_mock_hardware(world)
        return hw, Controller(hw, clock=clock, on_event=on_event)

    def test_state_change_is_logged(self):
        db = Database(":memory:", clock=ManualClock())
        _, ctl = self.make(db.log_event)
        ctl.step(20.0, 80.0)
        ev = db.recent_events(1)[0]
        self.assertEqual(ev["kind"], "STATE_CHANGE")
        self.assertEqual(ev["state"], "WATERING")
        self.assertIn("soil dry", ev["message"])
        db.close()

    def test_fault_is_logged_with_fault_kind(self):
        db = Database(":memory:", clock=ManualClock())
        _, ctl = self.make(db.log_event)
        ctl.report_sensor_error("moisture sensor disconnected")
        ev = db.recent_events(1)[0]
        self.assertEqual(ev["kind"], "FAULT")
        self.assertIn("disconnected", ev["message"])
        db.close()

    def test_broken_logger_cannot_stop_the_controller(self):
        def exploding_callback(kind, state, message):
            raise RuntimeError("disk full")

        hw, ctl = self.make(exploding_callback)
        ctl.step(20.0, 80.0)
        self.assertEqual(ctl.state, State.WATERING)
        self.assertTrue(hw.pump.is_on)


if __name__ == "__main__":
    unittest.main()