"""
webapp.py - Flask REST API for the water management system.

create_app() receives the controller, database and a shared lock. It never
builds hardware itself, so tests can inject fakes (dependency injection).

Endpoints:
  GET  /api/status        live controller status
  GET  /api/history       readings for the last N minutes (chart data)
  GET  /api/events        recent state changes and faults
  GET  /api/summary       aggregate statistics
  POST /api/fault/reset   clear a latched fault
  POST /api/pump/stop     interrupt a watering cycle

There is deliberately NO endpoint that starts the pump.
"""

import threading
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

from water_system import config
from water_system.controller import Controller
from water_system.database import Database

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

def _int_arg(name: str, default: int, low: int, high: int) -> int:
    """Read an integer query parameter, clamped to [low, high]."""
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def create_app(controller: Controller, db: Database,
               lock: Optional[threading.RLock] = None) -> Flask:
    app = Flask(__name__)
    lock = lock or threading.RLock()
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")
    @app.get("/api/status")
    def status():
        with lock:
            data = controller.status()
        data["mock_mode"] = config.MOCK_MODE
        return jsonify(data)

    @app.get("/api/history")
    def history():
        minutes = _int_arg("minutes", 60, 1, 7 * 24 * 60)
        limit = _int_arg("limit", 500, 1, 5000)
        return jsonify(db.history(minutes * 60, limit))

    @app.get("/api/events")
    def events():
        limit = _int_arg("limit", 50, 1, 500)
        return jsonify(db.recent_events(limit))

    @app.get("/api/summary")
    def summary():
        hours = _int_arg("hours", 24, 1, 24 * 90)
        return jsonify(db.summary(hours * 3600))

    @app.post("/api/fault/reset")
    def fault_reset():
        with lock:
            cleared = controller.reset_fault()
        if cleared:
            return jsonify({"ok": True, "message": "fault cleared"})
        return jsonify({"ok": False, "message": "no fault to clear"}), 409

    @app.post("/api/pump/stop")
    def pump_stop():
        with lock:
            stopped = controller.manual_stop()
        if stopped:
            return jsonify({"ok": True, "message": "watering stopped"})
        return jsonify({"ok": False, "message": "pump is not running"}), 409

    return app