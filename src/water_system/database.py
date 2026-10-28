"""
database.py - SQLite persistence for readings and events.

One shared connection protected by a lock, so it is safe to call from the
sensor thread, the control thread and Flask at the same time.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from water_system import config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,       -- Unix time (seconds)
    moisture_pct REAL,                -- NULL if the sensor failed
    tank_pct     REAL,
    flow_lpm     REAL,
    state        TEXT,
    pump_on      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,            -- STARTUP, STATE_CHANGE, FAULT, ...
    state   TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Database:
    def __init__(self, path: Union[str, Path] = config.DATABASE_PATH,
                 clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: we guard the connection with our own lock.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ writes
    def log_reading(self, moisture_pct: Optional[float],
                    tank_pct: Optional[float], flow_lpm: Optional[float],
                    state: str, pump_on: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO readings "
                "(ts, moisture_pct, tank_pct, flow_lpm, state, pump_on) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self._clock(), moisture_pct, tank_pct, flow_lpm,
                 state, 1 if pump_on else 0))
            self._conn.commit()

    def log_event(self, kind: str, state: Optional[str] = None,
                  message: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, state, message) "
                "VALUES (?, ?, ?, ?)",
                (self._clock(), kind, state, message))
            self._conn.commit()

    # ------------------------------------------------------------------- reads
    def latest_readings(self, limit: int = 100) -> List[Dict]:
        """Newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM readings ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self, limit: int = 50) -> List[Dict]:
        """Newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def count(self, table: str) -> int:
        if table not in ("readings", "events"):     # never trust table names
            raise ValueError(f"unknown table: {table}")
        with self._lock:
            return self._conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        # --------------------------------------------------------------- retention
    def prune(self) -> Dict[str, int]:
        """Delete rows older than the retention limits. Returns rows deleted."""
        now = self._clock()
        readings_cutoff = now - config.DB_READINGS_RETENTION_DAYS * 86400
        events_cutoff = now - config.DB_EVENTS_RETENTION_DAYS * 86400
        with self._lock:
            deleted_readings = self._conn.execute(
                "DELETE FROM readings WHERE ts < ?", (readings_cutoff,)).rowcount
            deleted_events = self._conn.execute(
                "DELETE FROM events WHERE ts < ?", (events_cutoff,)).rowcount
            self._conn.commit()
        if deleted_readings or deleted_events:
            log.info("Pruned %d readings, %d events",
                     deleted_readings, deleted_events)
        return {"readings": deleted_readings, "events": deleted_events}

    # ----------------------------------------------------- dashboard queries
    def history(self, since_seconds: float, limit: int = 1000) -> List[Dict]:
        """Readings from the last `since_seconds`, OLDEST first (for charts)."""
        cutoff = self._clock() - since_seconds
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM readings WHERE ts >= ? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (cutoff, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def summary(self, since_seconds: float) -> Dict[str, object]:
        """Aggregate statistics for the last `since_seconds`."""
        cutoff = self._clock() - since_seconds
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, AVG(moisture_pct) AS avg_m, "
                "MIN(moisture_pct) AS min_m, MAX(moisture_pct) AS max_m, "
                "AVG(tank_pct) AS avg_t, SUM(pump_on) AS pump_samples "
                "FROM readings WHERE ts >= ?", (cutoff,)).fetchone()
            cycles = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ? "
                "AND kind = 'STATE_CHANGE' AND state = 'WATERING'",
                (cutoff,)).fetchone()[0]
            faults = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ? AND kind = 'FAULT'",
                (cutoff,)).fetchone()[0]
        return {
            "readings": row["n"],
            "avg_moisture": row["avg_m"],
            "min_moisture": row["min_m"],
            "max_moisture": row["max_m"],
            "avg_tank": row["avg_t"],
            # Samples with the pump on. Multiply by the logging interval
            # (DB_LOG_INTERVAL_SECONDS) for an approximate pump-on time.
            "pump_on_samples": row["pump_samples"] or 0,
            "watering_cycles": cycles,
            "faults": faults,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()