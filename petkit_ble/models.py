"""Data models for PetKit W5-family devices."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    """Device identification from CMD 213."""
    device_id: int = 0
    serial_number: str = ""
    initialized: bool = False


@dataclass
class VersionInfo:
    """Hardware/firmware version from CMD 200."""
    hardware: int = 0
    firmware: int = 0


@dataclass
class DeviceState:
    """Live running state from CMD 210."""
    power_on: bool = False
    mode: int = 0
    mode_name: str = "Off"
    do_not_disturb: bool = False
    breakdown_warning: bool = False
    low_water_warning: bool = False
    filter_warning: bool = False
    pump_runtime_seconds: int = 0
    filter_remaining_pct: int = 0
    pump_running: bool = False
    today_pump_seconds: int | None = None

    @property
    def pump_runtime_hours(self) -> float:
        return round(self.pump_runtime_seconds / 3600, 1)

    @property
    def today_pump_minutes(self) -> float | None:
        if self.today_pump_seconds is None:
            return None
        return round(self.today_pump_seconds / 60, 1)


@dataclass
class DeviceSettings:
    """Device settings from CMD 211."""
    smart_work_minutes: int = 0
    smart_sleep_minutes: int = 0
    lamp_enabled: bool = False
    lamp_brightness: int = 0
    lamp_on_minutes: int = 0
    lamp_off_minutes: int = 0
    dnd_enabled: bool = False
    dnd_start_minutes: int = 0
    dnd_end_minutes: int = 0
    child_lock: bool | None = None

    @staticmethod
    def _minutes_to_time(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    @property
    def lamp_on_time(self) -> str:
        return self._minutes_to_time(self.lamp_on_minutes)

    @property
    def lamp_off_time(self) -> str:
        return self._minutes_to_time(self.lamp_off_minutes)

    @property
    def dnd_start_time(self) -> str:
        return self._minutes_to_time(self.dnd_start_minutes)

    @property
    def dnd_end_time(self) -> str:
        return self._minutes_to_time(self.dnd_end_minutes)


@dataclass
class BatteryInfo:
    """Battery/voltage info from CMD 66."""
    voltage_raw: int = 0


@dataclass
class W5Data:
    """Complete snapshot of all device data from a single poll."""
    info: DeviceInfo = field(default_factory=DeviceInfo)
    version: VersionInfo = field(default_factory=VersionInfo)
    state: DeviceState = field(default_factory=DeviceState)
    settings: DeviceSettings = field(default_factory=DeviceSettings)
    battery: BatteryInfo = field(default_factory=BatteryInfo)
