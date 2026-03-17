"""High-level device interface for PetKit W5-family water fountains.

This module is BLE-transport-agnostic. It accepts callables for writing and
subscribing to notifications, so it works with both raw bleak and HA's
Bluetooth integration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from .const import (
    CMD_BATTERY,
    CMD_CHANGE_MODE,
    CMD_DEVICE_INIT,
    CMD_GET_DEVICE_ID,
    CMD_GET_EXT_DND,
    CMD_GET_EXT_LIGHT,
    CMD_GET_SETTINGS,
    CMD_GET_STATE,
    CMD_GET_VERSION,
    CMD_RESET_FILTER,
    CMD_TIME_SYNC,
    CMD_VERIFY,
    CMD_WRITE_SETTINGS,
    TYPE_RESPONSE,
)
from .models import W5Data
from .protocol import (
    build_change_mode_payload,
    build_init_payload,
    build_packet,
    build_settings_payload,
    build_time_sync_payload,
    decode_battery,
    decode_device_id,
    decode_settings,
    decode_state,
    decode_version,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

# Type alias: an async function that writes bytes to the device
WriteFunc = Callable[[bytes], Coroutine[Any, Any, None]]


class AuthenticationError(Exception):
    """Raised when BLE authentication fails."""


class PetkitW5Device:
    """Interface to a PetKit W5-family water fountain over BLE.

    This class does not manage BLE connections. The caller is responsible for:
    1. Connecting to the device
    2. Subscribing to notifications and calling `handle_notification()` for each
    3. Providing a `write_func` that sends bytes to the write characteristic

    This design lets it work with both standalone bleak and HA's bluetooth stack.
    """

    def __init__(self) -> None:
        self._responses: dict[int, dict] = {}
        self._sequence: int = 0
        self._write_func: WriteFunc | None = None

    def set_write_func(self, func: WriteFunc) -> None:
        """Set the function used to write packets to the device."""
        self._write_func = func

    def handle_notification(self, _sender: Any, data: bytearray) -> None:
        """Feed a raw BLE notification into the protocol handler.

        Pass this as the callback when subscribing to the notify characteristic.
        """
        parsed = parse_response(data)
        if parsed is not None:
            _LOGGER.debug(
                "RX cmd=%d type=%d seq=%d len=%d",
                parsed["cmd"], parsed["type"], parsed["seq"], len(parsed["data"]),
            )
            self._responses[parsed["cmd"]] = parsed

    async def _send(self, cmd: int, data: bytes = b"", timeout: float = 3.0) -> dict | None:
        """Send a command and wait for the response."""
        if self._write_func is None:
            raise RuntimeError("write_func not set — call set_write_func() first")

        seq = self._sequence % 256
        self._sequence += 1

        packet = build_packet(cmd, data, seq=seq)
        _LOGGER.debug("TX cmd=%d seq=%d data=%s", cmd, seq, data.hex() if data else "(empty)")

        self._responses.pop(cmd, None)
        await self._write_func(packet)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cmd in self._responses:
                return self._responses[cmd]
            await asyncio.sleep(0.05)

        _LOGGER.warning("No response for cmd=%d within %.1fs", cmd, timeout)
        return None

    async def authenticate(self, secret: bytes = bytes(8)) -> bool:
        """Run the auth handshake: get device ID, verify secret, sync time.

        Args:
            secret: 8-byte secret. Defaults to zero secret (works on uninitialized devices).

        Returns:
            True if authentication succeeded.

        Raises:
            AuthenticationError: If the device rejects the secret.
        """
        resp = await self._send(CMD_GET_DEVICE_ID)
        if not resp or not resp["data"]:
            raise AuthenticationError("No response to CMD 213 (get device ID)")

        resp = await self._send(CMD_VERIFY, secret[:8].ljust(8, b"\x00"))
        if not resp or not resp["data"] or resp["data"][0] != 1:
            raise AuthenticationError("Secret rejected by device")

        await self._send(CMD_TIME_SYNC, build_time_sync_payload())
        return True

    async def async_poll(self, secret: bytes = bytes(8)) -> W5Data:
        """Authenticate and read all device data in one shot.

        This is the primary method for polling. It authenticates, then reads
        device info, version, state, settings, and battery.

        Args:
            secret: 8-byte secret for authentication.

        Returns:
            W5Data with all fields populated.
        """
        await self.authenticate(secret)
        return await async_read_all(self)

    async def async_get_info(self) -> W5Data:
        """Read device info (CMD 213) without authentication. Useful for discovery."""
        data = W5Data()
        resp = await self._send(CMD_GET_DEVICE_ID)
        if resp and resp["data"]:
            data.info = decode_device_id(resp["data"])
        return data

    async def async_init_device(self, device_id: int, secret: bytes) -> bool:
        """Initialize an uninitialized device with a device ID and secret.

        WARNING: This permanently writes to the device. Only a physical factory
        reset can undo this.

        Returns:
            True if the device accepted the init command.
        """
        payload = build_init_payload(device_id, secret)
        resp = await self._send(CMD_DEVICE_INIT, payload)
        return bool(resp and resp["data"] and resp["data"][0] == 1)

    async def async_change_mode(self, mode: int, submode: int = 0) -> bool:
        """Change device mode (CMD 220). 0=off, 1=normal, 2=smart."""
        payload = build_change_mode_payload(mode, submode)
        resp = await self._send(CMD_CHANGE_MODE, payload)
        return bool(resp and resp["data"] and resp["data"][0] != 0)

    async def async_write_settings(self, settings) -> bool:
        """Write all settings to the device (CMD 221)."""
        payload = build_settings_payload(settings)
        resp = await self._send(CMD_WRITE_SETTINGS, payload)
        return bool(resp and resp["data"] and resp["data"][0] != 0)

    async def async_reset_filter(self) -> bool:
        """Reset filter usage counter (CMD 222)."""
        resp = await self._send(CMD_RESET_FILTER)
        return bool(resp and resp["data"] and resp["data"][0] != 0)


async def async_read_all(device: PetkitW5Device) -> W5Data:
    """Read all device data (assumes already authenticated)."""
    data = W5Data()

    # Device ID + serial
    resp = await device._send(CMD_GET_DEVICE_ID)
    if resp and resp["data"]:
        data.info = decode_device_id(resp["data"])

    # Version
    resp = await device._send(CMD_GET_VERSION)
    if resp and resp["data"]:
        data.version = decode_version(resp["data"])

    # Running state
    resp = await device._send(CMD_GET_STATE)
    if resp and resp["type"] == TYPE_RESPONSE and resp["data"]:
        data.state = decode_state(resp["data"])

    # Settings
    resp = await device._send(CMD_GET_SETTINGS)
    if resp and resp["data"]:
        data.settings = decode_settings(resp["data"])

    # Battery/voltage
    resp = await device._send(CMD_BATTERY)
    if resp and resp["data"]:
        data.battery = decode_battery(resp["data"])

    return data
