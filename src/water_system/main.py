"""
main.py - Entry point for the Water Management System.

    python -m water_system.main                 normal run, real database file
    python -m water_system.main --memory        throwaway in-memory database
    python -m water_system.main --blocked       (simulation) inject a blocked pipe
    python -m water_system.main --soil 20       (simulation) starting soil moisture
    python -m water_system.main --host 0.0.0.0  allow other devices to connect

Ctrl+C (SIGINT) and SIGTERM both trigger a clean shutdown: threads stop,
then every output is switched OFF, then the database is closed.
"""

import argparse
import logging
import signal
import sys
import threading
from typing import Optional, Sequence

from water_system import config
from water_system.database import Database
from water_system.hardware import create_hardware
from water_system.system import WaterSystem
from water_system.webapp import create_app

log = logging.getLogger("water_system.main")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Water Management System")
    p.add_argument("--host", default="127.0.0.1",
                   help="dashboard bind address (default: this machine only)")
    p.add_argument("--port", type=int, default=config.FLASK_PORT)
    p.add_argument("--memory", action="store_true",
                   help="use a throwaway in-memory database")
    p.add_argument("--blocked", action="store_true",
                   help="simulation only: inject a blocked pipe")
    p.add_argument("--soil", type=float, default=None,
                   help="simulation only: starting soil moisture percent")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)

    if not config.MOCK_MODE and (args.blocked or args.soil is not None):
        print("--blocked and --soil only work in simulation mode.")
        return 2

    db = Database(":memory:" if args.memory else config.DATABASE_PATH)
    hw = create_hardware()
    if config.MOCK_MODE:
        if args.soil is not None:
            hw.world.set_soil_pct(args.soil)
        if args.blocked:
            hw.world.set_flow_blocked(True)

    system = WaterSystem(hw, db)
    app = create_app(system.controller, db, system.lock)

    stop_requested = threading.Event()

    def on_signal(signum, frame):
        stop_requested.set()

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)

    web = threading.Thread(
        target=lambda: app.run(host=args.host, port=args.port,
                               debug=False, use_reloader=False),
        name="flask", daemon=True)

    try:
        system.start()
        web.start()
        mode = "SIMULATION" if config.MOCK_MODE else "REAL HARDWARE"
        log.info("Running in %s mode. Dashboard: http://%s:%d",
                 mode, args.host, args.port)
        log.info("Press Ctrl+C to stop.")
        # Wake up regularly: on Windows, a long wait can hide Ctrl+C.
        while not stop_requested.wait(0.5):
            pass
        log.info("Shutdown requested")
    finally:
        system.stop()      # threads stop, THEN outputs go OFF
        db.close()
        log.info("Goodbye. All outputs OFF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
    