"""
demo.py - Watch the simulated hardware react. Run with:
    python -m water_system.demo

It refuses to run on real hardware, because it drives the pump on purpose.
"""

import logging
import time

from water_system import config
from water_system.hardware import SensorReadError, create_hardware


def show(hw, label: str) -> None:
    print(f"{label:<22} soil={hw.moisture.read_percent():5.1f}%  "
          f"tank={hw.tank.read_percent():5.1f}%  "
          f"flow={hw.flow.read_lpm():4.1f} L/min  "
          f"total={hw.flow.total_liters():.3f} L")


def run_for(hw, label: str, seconds: int) -> None:
    for _ in range(seconds):
        show(hw, label)
        time.sleep(1)


def main() -> int:
    logging.basicConfig(level=config.LOG_LEVEL, format="    log: %(message)s")

    if not config.MOCK_MODE:
        print("Demo only runs in MOCK mode (it would switch real relays).")
        return 1

    hw = create_hardware()
    try:
        hw.world.set_soil_pct(25.0)

        print("\n--- 1. Idle: soil should slowly dry ---")
        run_for(hw, "idle", 3)

        print("\n--- 2. Valve open + pump ON: soil rises, tank drains ---")
        hw.valve.on()
        hw.pump.on()
        hw.status_led.on()
        run_for(hw, "watering", 6)
        hw.pump.off()
        hw.valve.off()
        hw.status_led.off()

        print("\n--- 3. Fault: blocked pipe (pump on, zero flow) ---")
        hw.world.set_flow_blocked(True)
        hw.valve.on()
        hw.pump.on()
        run_for(hw, "pump on, blocked", 3)
        hw.pump.off()
        hw.valve.off()
        hw.world.set_flow_blocked(False)

        print("\n--- 4. Fault: sensor disconnected ---")
        hw.world.set_sensors_failed(True)
        try:
            hw.moisture.read_percent()
        except SensorReadError as exc:
            print(f"Caught SensorReadError: {exc}")
    finally:
        hw.cleanup()
        print("\nAll outputs switched OFF (safe state).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())