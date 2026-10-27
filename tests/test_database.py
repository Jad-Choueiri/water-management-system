"""Tests for the SQLite layer. Run with:
    python -m unittest discover -s tests -v
"""

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from water_system.database import Database


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1.0
        return self.t


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:", clock=Clock())

    def tearDown(self):
        self.db.close()

    def test_reading_round_trip(self):
        self.db.log_reading(42.5, 80.0, 4.0, "WATERING", True)
        row = self.db.latest_readings(1)[0]
        self.assertEqual(row["moisture_pct"], 42.5)
        self.assertEqual(row["tank_pct"], 80.0)
        self.assertEqual(row["state"], "WATERING")
        self.assertEqual(row["pump_on"], 1)

    def test_latest_readings_newest_first_with_limit(self):
        for i in range(5):
            self.db.log_reading(float(i), 50.0, 0.0, "IDLE", False)
        rows = self.db.latest_readings(3)
        self.assertEqual([r["moisture_pct"] for r in rows], [4.0, 3.0, 2.0])

    def test_event_round_trip(self):
        self.db.log_event("FAULT", "FAULT", "dry run: flow 0.00 L/min")
        ev = self.db.recent_events(1)[0]
        self.assertEqual(ev["kind"], "FAULT")
        self.assertIn("dry run", ev["message"])

    def test_null_values_allowed_when_sensor_failed(self):
        self.db.log_reading(None, None, None, "FAULT", False)
        row = self.db.latest_readings(1)[0]
        self.assertIsNone(row["moisture_pct"])
        self.assertEqual(row["state"], "FAULT")

    def test_count_rejects_unknown_table(self):
        with self.assertRaises(ValueError):
            self.db.count("readings; DROP TABLE events")

    def test_concurrent_writers_lose_nothing(self):
        def worker():
            for _ in range(50):
                self.db.log_reading(1.0, 2.0, 3.0, "IDLE", False)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.db.count("readings"), 200)


class PersistenceTest(unittest.TestCase):
    def test_data_survives_reopening_the_file(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            db = Database(path)
            db.log_event("STARTUP", "IDLE", "hello")
            db.close()
            db2 = Database(path)
            self.assertEqual(db2.recent_events(1)[0]["message"], "hello")
            db2.close()


if __name__ == "__main__":
    unittest.main()