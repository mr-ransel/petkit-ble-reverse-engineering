"""Low-level BLE packet building and parsing for W5 protocol."""

from __future__ import annotations

import struct
import time
from datetime import datetime, timezone

from .const import CMD_HEADER, TRAILER, TYPE_REQUEST
from .models import (
    BatteryInfo,
    DeviceInfo,
    DeviceSettings,
    DeviceState,
    VersionInfo,
)


def build_packet(cmd: int, data: bytes = b"", msg_type: int = TYPE_REQUEST,
                 seq: int = 0) -> bytes:
    """Build a framed BLE packet."""
    data_len = len(data)
    packet = bytearray()
    packet.extend(CMD_HEADER)
    packet.append(cmd & 0xFF)
    packet.append(msg_type & 0xFF)
    packet.append(seq & 0xFF)
    packet.append(data_len & 0xFF)
    packet.append((data_len >> 8) & 0xFF)
    packet.extend(data)
    packet.append(TRAILER)
    return bytes(packet)


def parse_response(raw: bytes | bytearray) -> dict | None:
    """Parse a raw notification into {cmd, type, seq, data}. Returns None if invalid."""
    if len(raw) < 8 or raw[0] != 0xFA or raw[1] != 0xFC or raw[2] != 0xFD:
        return None
    cmd = raw[3]
    msg_type = raw[4]
    seq = raw[5]
    data_len = raw[6] | (raw[7] << 8)
    payload = bytes(raw[8:8 + data_len]) if data_len > 0 else b""
    return {"cmd": cmd, "type": msg_type, "seq": seq, "data": payload}


def build_time_sync_payload() -> bytes:
    """Build payload for CMD 84 (time sync)."""
    epoch_2000 = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
    now = datetime.now(timezone.utc).timestamp()
    seconds = int(now - epoch_2000)
    tz_offset_hours = time.timezone / -3600
    tz_byte = int(tz_offset_hours) + 12
    payload = bytearray()
    payload.append(0x00)
    payload.extend(struct.pack(">i", seconds))
    payload.append(tz_byte & 0xFF)
    return bytes(payload)


def build_init_payload(device_id: int, secret: bytes) -> bytes:
    """Build payload for CMD 73 (init device)."""
    id_bytes = struct.pack(">q", device_id)
    secret_padded = (secret + b"\x00" * 8)[:8]
    return id_bytes + secret_padded


def decode_device_id(data: bytes) -> DeviceInfo:
    """Decode CMD 213 response."""
    info = DeviceInfo()
    if len(data) >= 8:
        # Matches the official app's ByteUtil.bytes2Long implementation.
        info.device_id = int.from_bytes(data[:8], "big")
        info.initialized = info.device_id != 0
    if len(data) > 8:
        sn_bytes = data[8:min(22, len(data))]
        info.serial_number = sn_bytes.decode("ascii", errors="replace").rstrip("\x00")
    return info


def decode_version(data: bytes) -> VersionInfo:
    """Decode CMD 200 response."""
    if len(data) < 2:
        return VersionInfo()
    return VersionInfo(hardware=data[0], firmware=data[1])


def decode_state(data: bytes) -> DeviceState:
    """Decode CMD 210 response."""
    from .const import MODE_NAMES

    if len(data) < 12:
        return DeviceState()

    pump_runtime = struct.unpack(">I", data[6:10])[0]
    state = DeviceState(
        power_on=bool(data[0]),
        mode=data[1],
        mode_name=MODE_NAMES.get(data[1], f"unknown ({data[1]})"),
        do_not_disturb=bool(data[2]),
        breakdown_warning=bool(data[3]),
        low_water_warning=bool(data[4]),
        filter_warning=bool(data[5]),
        pump_runtime_seconds=pump_runtime,
        filter_remaining_pct=data[10],
        pump_running=bool(data[11]),
    )
    if len(data) >= 16:
        state.today_pump_seconds = struct.unpack(">I", data[12:16])[0]

    return state


def decode_settings(data: bytes) -> DeviceSettings:
    """Decode CMD 211 response."""
    if len(data) < 13:
        return DeviceSettings()

    settings = DeviceSettings(
        smart_work_minutes=data[0],
        smart_sleep_minutes=data[1],
        lamp_enabled=bool(data[2]),
        lamp_brightness=data[3],
        lamp_on_minutes=struct.unpack(">H", data[4:6])[0],
        lamp_off_minutes=struct.unpack(">H", data[6:8])[0],
        dnd_enabled=bool(data[8]),
        dnd_start_minutes=struct.unpack(">H", data[9:11])[0],
        dnd_end_minutes=struct.unpack(">H", data[11:13])[0],
    )
    if len(data) >= 14:
        settings.child_lock = bool(data[13])

    return settings


def decode_battery(data: bytes) -> BatteryInfo:
    """Decode CMD 66 response."""
    if len(data) < 2:
        return BatteryInfo()
    return BatteryInfo(voltage_raw=int.from_bytes(data[:2], "big"))


def build_change_mode_payload(mode: int, submode: int = 0) -> bytes:
    """Build payload for CMD 220 (change mode)."""
    return bytes([mode & 0xFF, submode & 0xFF])


def build_settings_payload(settings: DeviceSettings,
                           child_lock: bool | None = None) -> bytes:
    """Build payload for CMD 221 (write all settings)."""
    payload = bytearray()
    payload.append(settings.smart_work_minutes & 0xFF)
    payload.append(settings.smart_sleep_minutes & 0xFF)
    payload.append(1 if settings.lamp_enabled else 0)
    payload.append(settings.lamp_brightness & 0xFF)
    payload.extend(struct.pack(">H", settings.lamp_on_minutes))
    payload.extend(struct.pack(">H", settings.lamp_off_minutes))
    payload.append(1 if settings.dnd_enabled else 0)
    payload.extend(struct.pack(">H", settings.dnd_start_minutes))
    payload.extend(struct.pack(">H", settings.dnd_end_minutes))
    lock = child_lock if child_lock is not None else settings.child_lock
    if lock is not None:
        payload.append(1 if lock else 0)
    return bytes(payload)
