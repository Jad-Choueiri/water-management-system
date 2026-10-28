"""
demo_db.py - Run the FSM for ~35 s and show what got logged.
    python -m water_system.demo_db
Uses an in-memory database, so it never touches your real data file.
"""

import time

from water_system import config
from water_system.controller import Controller
from water_system.database import Database
from water_system.hardware import SensorReadError, create_hardware


def main() -> int:
    if not config.MOCK_MODE:
        print("Demo only runs in MOCK mode.")
        return 1

    db = Database(":memory:")
    hw = create_hardware()
    ctl = Controller(hw, on_event=db.log_event)    # the observer wiring
    db.log_event("STARTUP", ctl.state.value, "demo started")
    hw.world.set_soil_pct(25.0)

    try:
        for i in range(35):
            try:
                ctl.step(hw.moisture.read_percent(),
                         hw.tank.read_percent(),
                         hw.flow.read_lpm())
                s = ctl.status()
                db.log_reading(s["moisture_pct"], s["tank_pct"],
                               s["flow_lpm"], s["state"], s["pump_on"])
            except SensorReadError as exc:
                ctl.report_sensor_error(str(exc))
            time.sleep(1)
    finally:
        hw.cleanup()

    print("\n--- Events (oldest first) ---")
    for e in reversed(db.recent_events(10)):
        when = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        print(f"{when}  {e['kind']:<12} {e['state']:<9} {e['message']}")

    print("\n--- Summary (last hour) ---")
    for key, value in db.summary(3600).items():
        shown = round(value, 1) if isinstance(value, float) else value
        print(f"{key:<16} {shown}")

    print(f"\nReadings stored: {db.count('readings')}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())