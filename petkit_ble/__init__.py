"""PetKit BLE library for W5-family water fountains."""

from .const import W5_BLE_NAMES
from .device import PetkitW5Device
from .models import DeviceInfo, DeviceSettings, DeviceState, VersionInfo

__all__ = [
    "PetkitW5Device",
    "DeviceInfo",
    "DeviceState",
    "DeviceSettings",
    "VersionInfo",
    "W5_BLE_NAMES",
]
