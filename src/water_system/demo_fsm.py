"""
demo_fsm.py - Watch the FSM run against the simulated garden in real time.
    python -m water_system.demo_fsm            normal watering cycle
    python -m water_system.demo_fsm blocked    blocked pipe -> FAULT
"""

import logging
import sys
import time

from water_system import config
from water_system.controller import Controller, State
from water_system.hardware import SensorReadError, create_hardware


def main() -> int:
    logging.basicConfig(level="INFO", format="    log: %(message)s")
    if not config.MOCK_MODE:
        print("Demo only runs in MOCK mode.")
        return 1

    hw = create_hardware()
    ctl = Controller(hw)
    hw.world.set_soil_pct(25.0)
    if len(sys.argv) > 1 and sys.argv[1] == "blocked":
        hw.world.set_flow_blocked(True)

    try:
        for _ in range(40):
            try:
                ctl.step(hw.moisture.read_percent(),
                         hw.tank.read_percent(),
                         hw.flow.read_lpm())
            except SensorReadError as exc:
                ctl.report_sensor_error(str(exc))
            s = ctl.status()
            print(f"{s['state']:<9} soil={s['moisture_pct']:5.1f}%  "
                  f"tank={s['tank_pct']:5.1f}%  flow={s['flow_lpm']:3.1f}  "
                  f"pump={'ON ' if s['pump_on'] else 'off'}  "
                  f"buzzer={'ON' if s['buzzer_on'] else 'off'}")
            if ctl.state == State.FAULT:
                print(f"\nFAULT: {s['fault_reason']}")
                break
            time.sleep(1)
    finally:
        hw.cleanup()
        print("\nAll outputs OFF (safe state).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())