"""
config.py - Central configuration for the Raspberry Pi Water Management System.

Design rules:
  1. NO magic numbers anywhere else in the codebase. Import them from here.
  2. This module has NO hardware imports, so it is safe to load on any machine.
  3. All pin numbers use BCM (Broadcom GPIO) numbering, NOT physical pin numbers.

Run this file directly to print and validate the active configuration:
    python src/water_system/config.py
"""

import os
import platform
from pathlib import Path

# =============================================================================
# 1. PROJECT PATHS
# =============================================================================
# This file lives at <project>/src/water_system/config.py
# parents[0] = water_system, parents[1] = src, parents[2] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "water_system.db"

# =============================================================================
# 2. RUN MODE (HARDWARE vs. SIMULATION)
# =============================================================================
# WATER_MOCK_MODE environment variable:
#   "auto" (default) -> mock unless we detect a real Raspberry Pi
#   "1" / "true"     -> force mock (simulation)
#   "0" / "false"    -> force real hardware


def _running_on_raspberry_pi() -> bool:
    """Best-effort detection of a Raspberry Pi (Linux only)."""
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False


def _resolve_mock_mode() -> bool:
    setting = os.getenv("WATER_MOCK_MODE", "auto").strip().lower()
    if setting in ("1", "true", "yes", "on"):
        return True
    if setting in ("0", "false", "no", "off"):
        return False
    # "auto": simulate everywhere except on a real Pi
    return not _running_on_raspberry_pi()


MOCK_MODE: bool = _resolve_mock_mode()

# =============================================================================
# 3. GPIO PIN MAP (BCM numbering)
# =============================================================================
# --- Actuators (outputs) ---
PIN_PUMP_RELAY = 17      # Relay controlling the water pump
PIN_VALVE_RELAY = 27     # Relay controlling the solenoid valve
PIN_STATUS_LED = 22      # Status LED (on = pump running)
PIN_BUZZER = 23         # Alarm buzzer (fault indication)

# --- Sensors (inputs) ---
PIN_ULTRASONIC_TRIG = 5  # HC-SR04 trigger (output pulse)
PIN_ULTRASONIC_ECHO = 6  # HC-SR04 echo (input) - NEEDS 5V->3.3V divider!
PIN_FLOW_SENSOR = 13     # YF-S201 flow sensor pulse output (interrupt-driven)

# --- Soil moisture sensor ---
# The Pi has no analog inputs. Moisture sensors are analog, so real hardware
# would use an ADC (e.g. MCP3008 over SPI) on this channel.
MOISTURE_ADC_CHANNEL = 0

# Most cheap relay modules turn ON when the input pin is driven LOW.
RELAY_ACTIVE_LOW = True

# =============================================================================
# 4. CONTROL THRESHOLDS (with hysteresis)
# =============================================================================
# Soil moisture, in percent (0 = bone dry, 100 = saturated).
# Pump turns ON when moisture < ON threshold, and OFF when moisture > OFF
# threshold. The gap between them is the HYSTERESIS band.
MOISTURE_PUMP_ON_BELOW_PCT = 30.0
MOISTURE_PUMP_OFF_ABOVE_PCT = 60.0

# Water tank level, in percent full.
# If the tank drops below LOW, the pump is locked out (it would run dry).
# It stays locked out until the tank refills past RESUME.
TANK_LOW_CUTOFF_PCT = 15.0
TANK_RESUME_PCT = 25.0

# =============================================================================
# 5. SAFETY LIMITS (fail-safe design)
# =============================================================================
PUMP_MAX_RUN_SECONDS = 60          # Hard cap on a single watering cycle
PUMP_COOLDOWN_SECONDS = 300        # Rest period after each cycle (5 min)
DRY_RUN_CHECK_DELAY_SECONDS = 5    # After pump starts, wait this long...
MIN_FLOW_RATE_LPM = 0.2            # ...then require at least this flow (L/min)
SENSOR_STALE_TIMEOUT_SECONDS = 10  # No fresh reading in this time = FAULT

# =============================================================================
# 6. TIMING
# =============================================================================
SENSOR_POLL_INTERVAL_SECONDS = 1.0   # How often we sample sensors
CONTROL_LOOP_INTERVAL_SECONDS = 0.5  # How often the state machine runs
DB_LOG_INTERVAL_SECONDS = 5.0        # How often we write a reading to SQLite
DB_READINGS_RETENTION_DAYS = 7       # keep sensor samples this long
DB_EVENTS_RETENTION_DAYS = 90        # keep state changes / faults this long
DB_PRUNE_INTERVAL_SECONDS = 3600     # how often the main loop prunes (Step 6)
FLOW_CALC_WINDOW_SECONDS = 2.0       # Window for converting pulses -> L/min

# =============================================================================
# 7. SENSOR CALIBRATION
# =============================================================================
# --- Tank geometry (ultrasonic sensor mounted at the top, pointing down) ---
TANK_FULL_DISTANCE_CM = 5.0     # Sensor-to-water distance when tank is full
TANK_EMPTY_DISTANCE_CM = 45.0   # Sensor-to-water distance when tank is empty

# --- Ultrasonic physics ---
SPEED_OF_SOUND_CM_PER_S = 34300  # at ~20 C
ULTRASONIC_TIMEOUT_SECONDS = 0.05

# --- Flow sensor (YF-S201 ~ 450 pulses per liter) ---
FLOW_PULSES_PER_LITER = 450.0

# --- Moisture ADC calibration (10-bit ADC: 0..1023) ---
# Capacitive sensors read HIGH when dry and LOW when wet.
MOISTURE_ADC_DRY_VALUE = 800
MOISTURE_ADC_WET_VALUE = 350

# =============================================================================
# 8. MOCK / SIMULATION SETTINGS (only used when MOCK_MODE is True)
# =============================================================================
MOCK_SOIL_START_PCT = 45.0        # Simulated starting soil moisture
MOCK_SOIL_DRY_RATE_PCT_PER_S = 0.4   # Soil dries this fast with pump off
MOCK_SOIL_WET_RATE_PCT_PER_S = 1.5   # Soil wets this fast with pump on
MOCK_TANK_START_PCT = 80.0        # Simulated starting tank level
MOCK_TANK_DRAIN_PCT_PER_S = 0.3   # Tank drains this fast while pump runs
MOCK_FLOW_RATE_LPM = 4.0          # Simulated flow when pump is on
MOCK_SENSOR_NOISE_PCT = 0.5       # Random jitter so data looks realistic

# =============================================================================
# 9. WEB DASHBOARD (Flask)
# =============================================================================
FLASK_HOST = "0.0.0.0"   # Listen on all interfaces so a phone can connect
FLASK_PORT = 5000
FLASK_DEBUG = False       # Debug reloader spawns a second process -> breaks threads

# =============================================================================
# 10. LOGGING
# =============================================================================
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


# =============================================================================
# VALIDATION - catch bad settings at startup instead of mid-operation
# =============================================================================
def validate_config() -> None:
    """Raise ValueError if the configuration is internally inconsistent."""
    errors = []

    if not MOISTURE_PUMP_ON_BELOW_PCT < MOISTURE_PUMP_OFF_ABOVE_PCT:
        errors.append("Moisture ON threshold must be below OFF threshold "
                      "(hysteresis band would be empty).")

    if not TANK_LOW_CUTOFF_PCT < TANK_RESUME_PCT:
        errors.append("Tank LOW cutoff must be below RESUME level.")

    if not TANK_FULL_DISTANCE_CM < TANK_EMPTY_DISTANCE_CM:
        errors.append("Tank FULL distance must be smaller than EMPTY distance.")

    if MOISTURE_ADC_DRY_VALUE == MOISTURE_ADC_WET_VALUE:
        errors.append("Moisture ADC dry/wet calibration values are identical.")

    if DRY_RUN_CHECK_DELAY_SECONDS >= PUMP_MAX_RUN_SECONDS:
        errors.append("Dry-run check delay must be shorter than max pump run time.")

    # Every GPIO pin must be unique - two devices on one pin is a wiring bug.
    pins = {
        "PIN_PUMP_RELAY": PIN_PUMP_RELAY,
        "PIN_VALVE_RELAY": PIN_VALVE_RELAY,
        "PIN_STATUS_LED": PIN_STATUS_LED,
        "PIN_BUZZER": PIN_BUZZER,
        "PIN_ULTRASONIC_TRIG": PIN_ULTRASONIC_TRIG,
        "PIN_ULTRASONIC_ECHO": PIN_ULTRASONIC_ECHO,
        "PIN_FLOW_SENSOR": PIN_FLOW_SENSOR,
    }
    seen = {}
    for name, pin in pins.items():
        if pin in seen:
            errors.append(f"GPIO {pin} is assigned to both {seen[pin]} and {name}.")
        seen[pin] = name

    if errors:
        raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(errors))


def print_summary() -> None:
    """Human-readable dump of the active configuration."""
    mode = "MOCK (simulation)" if MOCK_MODE else "REAL HARDWARE"
    print("=" * 56)
    print(" Water Management System - Active Configuration")
    print("=" * 56)
    print(f" Run mode        : {mode}")
    print(f" Platform        : {platform.system()} ({platform.machine()})")
    print(f" Project root    : {PROJECT_ROOT}")
    print(f" Database path   : {DATABASE_PATH}")
    print(f" Pump relay pin  : GPIO {PIN_PUMP_RELAY} "
          f"({'active-low' if RELAY_ACTIVE_LOW else 'active-high'})")
    print(f" Pump ON/OFF     : moisture < {MOISTURE_PUMP_ON_BELOW_PCT}% "
          f"-> ON, > {MOISTURE_PUMP_OFF_ABOVE_PCT}% -> OFF")
    print(f" Tank lockout    : < {TANK_LOW_CUTOFF_PCT}% "
          f"(resume at {TANK_RESUME_PCT}%)")
    print(f" Max pump run    : {PUMP_MAX_RUN_SECONDS}s, "
          f"cooldown {PUMP_COOLDOWN_SECONDS}s")
    print(f" Dashboard       : http://{FLASK_HOST}:{FLASK_PORT}")
    print("=" * 56)


# Validate on import so a bad config fails immediately and loudly.
validate_config()

if __name__ == "__main__":
    print_summary()
    print("Configuration is valid.")