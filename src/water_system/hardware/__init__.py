"""
Hardware package. The ONLY place that decides mock vs. real is here.

    from water_system.hardware import create_hardware
    hw = create_hardware()
"""

from water_system import config
from water_system.hardware.base import Hardware, SensorReadError

__all__ = ["create_hardware", "Hardware", "SensorReadError"]


def create_hardware() -> Hardware:
    """Factory: build simulated or real devices based on config.MOCK_MODE."""
    if config.MOCK_MODE:
        from water_system.hardware.mock import create_mock_hardware
        return create_mock_hardware()
    from water_system.hardware.real import create_real_hardware
    return create_real_hardware()