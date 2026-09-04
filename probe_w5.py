#!/usr/bin/env python3
"""
PetKit Eversweet Solo 2 (W5/CTW2) BLE Probe Script

Connects to a PetKit water fountain over BLE, authenticates with a
zero secret (works on uninitialized devices), and reads device state.

Modes:
    (default)       Connect, auth, read all state — clean human-readable output
    --raw           Show raw BLE packet hex in addition to decoded values
    --json          Output as JSON (for scripting)
    --self-init --confirm-permanent-init
                    Initialize the device with our own secret (DANGEROUS)

Usage:
    python probe_w5.py              # read device state
    python probe_w5.py --raw        # same but with raw packet details
    python probe_w5.py --json       # output as JSON
"""

import argparse
import asyncio
import json as json_lib
import os
import struct
import sys
import time
from datetime import datetime, timezone

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Missing dependency. Install with:")
    print("  pip install bleak")
    sys.exit(1)


# -- GATT UUIDs --
SERVICE_UUID = "0000aaa0-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000aaa2-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000aaa1-0000-1000-8000-00805f9b34fb"

# -- BLE device names to scan for --
W5_NAMES = {
    "Petkit_W5", "Petkit_W5C", "Petkit_W5N",
    "Petkit_W4X", "Petkit_W4XUVC", "Petkit_CTW2",
}

# -- Packet framing --
CMD_HEADER = bytes([0xFA, 0xFC, 0xFD])
TRAILER = 0xFB

# -- Command IDs --
CMD_BATTERY = 66
CMD_DEVICE_INIT = 73
CMD_TIME_SYNC = 84
CMD_VERIFY = 86
CMD_GET_VERSION_HW = 200
CMD_GET_VERSION_FW = 201
CMD_GET_STATE = 210
CMD_GET_SETTINGS = 211
CMD_GET_DEVICE_ID = 213
CMD_GET_EXT_SETTINGS = 215

# -- Message types --
TYPE_REQUEST = 1
TYPE_RESPONSE = 2

# -- Mode names --
MODE_NAMES = {0: "Off", 1: "Normal", 2: "Smart"}

# Global state
_sequence = 0
_verbose = False


def next_sequence():
    global _sequence
    seq = _sequence % 256
    _sequence += 1
    return seq


def log(msg):
    if _verbose:
        print(msg)


def build_packet(cmd, data=b"", msg_type=TYPE_REQUEST):
    seq = next_sequence()
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
    return bytes(packet), seq


def build_time_sync_payload():
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


def build_init_payload(device_id, secret):
    id_bytes = struct.pack(">q", device_id)
    secret_padded = (secret + b"\x00" * 8)[:8]
    return id_bytes + secret_padded


def minutes_to_time(minutes):
    """Convert minutes-from-midnight to HH:MM string."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def decode_state(data):
    """Decode CMD 210 response into a dict."""
    if len(data) < 12:
        return {"error": f"state data too short ({len(data)}b)", "raw": data.hex()}

    pump_runtime = struct.unpack(">I", data[6:10])[0]
    result = {
        "power": "on" if data[0] else "off",
        "mode": MODE_NAMES.get(data[1], f"unknown ({data[1]})"),
        "mode_raw": data[1],
        "do_not_disturb": bool(data[2]),
        "breakdown_warning": bool(data[3]),
        "low_water_warning": bool(data[4]),
        "filter_warning": bool(data[5]),
        "pump_runtime_seconds": pump_runtime,
        "pump_runtime_hours": round(pump_runtime / 3600, 1),
        "filter_remaining_pct": data[10],
        "run_status": data[11],
    }
    if len(data) >= 16:
        today_pump = struct.unpack(">I", data[12:16])[0]
        result["today_pump_seconds"] = today_pump
        result["today_pump_minutes"] = round(today_pump / 60, 1)

    return result


def decode_settings(data):
    """Decode CMD 211 response into a dict."""
    if len(data) < 13:
        return {"error": f"settings data too short ({len(data)}b)", "raw": data.hex()}

    lamp_on = struct.unpack(">H", data[4:6])[0]
    lamp_off = struct.unpack(">H", data[6:8])[0]
    dnd_start = struct.unpack(">H", data[9:11])[0]
    dnd_end = struct.unpack(">H", data[11:13])[0]

    result = {
        "smart_work_minutes": data[0],
        "smart_sleep_minutes": data[1],
        "lamp_enabled": bool(data[2]),
        "lamp_brightness": data[3],
        "lamp_on_time": minutes_to_time(lamp_on),
        "lamp_off_time": minutes_to_time(lamp_off),
        "dnd_enabled": bool(data[8]),
        "dnd_start_time": minutes_to_time(dnd_start),
        "dnd_end_time": minutes_to_time(dnd_end),
    }
    if len(data) >= 14:
        result["child_lock"] = bool(data[13])

    return result


def decode_version(data):
    """Decode CMD 200 response."""
    if len(data) < 2:
        return {"error": f"version data too short ({len(data)}b)", "raw": data.hex()}
    return {
        "hardware": data[0],
        "firmware": data[1],
    }


def decode_battery(data):
    """Decode CMD 66 response."""
    if len(data) < 2:
        return {"raw": data.hex()}
    return {
        "voltage_raw": int.from_bytes(data[:2], "big"),
        "raw": data.hex(),
    }


def decode_device_id(data):
    """Decode CMD 213 response."""
    result = {}
    if len(data) >= 8:
        # Matches the official app's ByteUtil.bytes2Long implementation.
        result["device_id"] = int.from_bytes(data[:8], "big")
        result["initialized"] = result["device_id"] != 0
    if len(data) > 8:
        sn_bytes = data[8:min(22, len(data))]
        result["serial_number"] = sn_bytes.decode("ascii", errors="replace").rstrip("\x00")
    return result


def format_state(state):
    """Format state dict for human-readable display."""
    lines = []
    lines.append(f"  Power:              {state['power'].upper()}")
    lines.append(f"  Mode:               {state['mode']}")
    lines.append(f"  Low water:          {'YES - NEEDS REFILL' if state['low_water_warning'] else 'OK'}")
    lines.append(f"  Filter:             {state['filter_remaining_pct']}% remaining" +
                 (" (REPLACE SOON)" if state['filter_warning'] else ""))
    lines.append(f"  Pump running:       {'Yes' if state['run_status'] else 'No'}")
    lines.append(f"  Do not disturb:     {'On' if state['do_not_disturb'] else 'Off'}")
    lines.append(f"  Breakdown alert:    {'YES' if state['breakdown_warning'] else 'No'}")
    lines.append(f"  Total pump time:    {state['pump_runtime_hours']}h")
    if "today_pump_minutes" in state:
        lines.append(f"  Today pump time:    {state['today_pump_minutes']}min")
    return "\n".join(lines)


def format_settings(settings):
    """Format settings dict for human-readable display."""
    lines = []
    lines.append(f"  Smart mode:         {settings['smart_work_minutes']}min on / {settings['smart_sleep_minutes']}min off")
    lamp = "On" if settings["lamp_enabled"] else "Off"
    lines.append(f"  Lamp:               {lamp} (brightness {settings['lamp_brightness']})")
    lines.append(f"  Lamp schedule:      {settings['lamp_on_time']} - {settings['lamp_off_time']}")
    dnd = "On" if settings["dnd_enabled"] else "Off"
    lines.append(f"  Do not disturb:     {dnd}")
    if settings["dnd_enabled"]:
        lines.append(f"  DND schedule:       {settings['dnd_start_time']} - {settings['dnd_end_time']}")
    if "child_lock" in settings:
        lines.append(f"  Child lock:         {'On' if settings['child_lock'] else 'Off'}")
    return "\n".join(lines)


def format_device_info(info, version, battery):
    """Format device identification info."""
    lines = []
    if "serial_number" in info:
        lines.append(f"  Serial number:      {info['serial_number']}")
    lines.append(f"  Device ID:          {info.get('device_id', '?')}" +
                 (" (uninitialized)" if not info.get('initialized') else ""))
    if version and "hardware" not in version.get("error", ""):
        lines.append(f"  Hardware:           v{version.get('hardware', '?')}")
        lines.append(f"  Firmware:           v{version.get('firmware', '?')}")
    if battery and "voltage_raw" in battery:
        lines.append(f"  Voltage (raw):      {battery['voltage_raw']}")
    return "\n".join(lines)


class W5Probe:
    def __init__(self):
        self.responses = {}
        self.raw_notifications = []

    def notification_handler(self, sender, data: bytearray):
        self.raw_notifications.append(bytes(data))

        if len(data) >= 8 and data[0] == 0xFA and data[1] == 0xFC and data[2] == 0xFD:
            cmd = data[3]
            msg_type = data[4]
            seq = data[5]
            data_len = data[6] | (data[7] << 8)
            payload = data[8:8 + data_len] if data_len > 0 else b""
            log(f"  << CMD={cmd} type={msg_type} seq={seq} len={data_len} data={payload.hex()}")
            self.responses[cmd] = {
                "type": msg_type,
                "seq": seq,
                "data": payload,
                "raw": bytes(data),
            }

    async def send_and_wait(self, client, cmd, data=b"", msg_type=TYPE_REQUEST, timeout=3.0):
        packet, seq = build_packet(cmd, data, msg_type)
        log(f"  >> CMD={cmd} seq={seq} data={data.hex() if data else '(empty)'} raw={packet.hex()}")

        if not client.is_connected:
            log(f"     [!] Connection lost before CMD {cmd}")
            return None

        self.responses.pop(cmd, None)
        try:
            await client.write_gatt_char(WRITE_CHAR_UUID, packet)
        except Exception as e:
            log(f"     [!] Write failed: {e}")
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            if cmd in self.responses:
                return self.responses[cmd]
            await asyncio.sleep(0.1)

        log(f"     [!] No response for CMD {cmd} within {timeout}s")
        return None


async def scan_for_device(timeout=15):
    found = []

    def detection_callback(device, advertisement_data):
        name = advertisement_data.local_name or device.name or ""
        if name in W5_NAMES:
            found.append((device, advertisement_data))

    scanner = BleakScanner(detection_callback=detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return found


async def read_device(device, output_json=False):
    """Connect, authenticate, and read all device data."""
    probe = W5Probe()
    results = {}

    if not output_json:
        print(f"Connecting to {device.name or 'unknown'} [{device.address}]...")

    async with BleakClient(device) as client:
        if not output_json:
            print("Connected. Reading device data...\n")

        await client.start_notify(NOTIFY_CHAR_UUID, probe.notification_handler)
        await asyncio.sleep(0.3)

        # CMD 213 — Device ID + Serial Number
        resp = await probe.send_and_wait(client, CMD_GET_DEVICE_ID)
        device_info = {}
        if resp and resp["data"]:
            device_info = decode_device_id(resp["data"])
        results["device"] = device_info
        results["device"]["name"] = device.name
        results["device"]["address"] = device.address

        # CMD 86 — Auth with zero secret
        resp = await probe.send_and_wait(client, CMD_VERIFY, bytes(8))
        auth_ok = resp and resp["data"] and resp["data"][0] == 1
        if not auth_ok:
            results["error"] = "Authentication failed (zero secret rejected)"
            if output_json:
                print(json_lib.dumps(results, indent=2))
            else:
                print("Authentication failed. Device may already be initialized with a secret.")
            return results

        # CMD 84 — Time sync
        ts_payload = build_time_sync_payload()
        await probe.send_and_wait(client, CMD_TIME_SYNC, ts_payload)

        # CMD 200 — Version
        resp = await probe.send_and_wait(client, CMD_GET_VERSION_HW, b"")
        version = {}
        if resp and resp["data"]:
            version = decode_version(resp["data"])
        results["version"] = version

        # CMD 210 — Running state
        resp = await probe.send_and_wait(client, CMD_GET_STATE)
        state = {}
        if resp and resp["type"] == TYPE_RESPONSE and resp["data"]:
            state = decode_state(resp["data"])
        results["state"] = state

        # CMD 211 — Settings
        resp = await probe.send_and_wait(client, CMD_GET_SETTINGS, b"")
        settings = {}
        if resp and resp["data"]:
            settings = decode_settings(resp["data"])
        results["settings"] = settings

        # CMD 66 — Battery/voltage
        resp = await probe.send_and_wait(client, CMD_BATTERY, b"")
        battery = {}
        if resp and resp["data"]:
            battery = decode_battery(resp["data"])
        results["battery"] = battery

        # CMD 215 — Extended settings
        resp = await probe.send_and_wait(client, 215, b"")
        if resp and resp["data"]:
            results["extended_settings_raw"] = resp["data"].hex()

        # Cleanup
        if client.is_connected:
            try:
                await client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass

    # Output
    if output_json:
        print(json_lib.dumps(results, indent=2))
    else:
        print("--- Device Info ---")
        print(format_device_info(device_info, version, battery))
        print()
        if state:
            print("--- Status ---")
            print(format_state(state))
            print()
        if settings:
            print("--- Settings ---")
            print(format_settings(settings))
            print()

    return results


async def self_init_device(device):
    """Initialize an uninitialized device with our own secret."""
    probe = W5Probe()

    print(f"\nConnecting to {device.name or 'unknown'} [{device.address}]...")

    async with BleakClient(device) as client:
        print("Connected.\n")

        await client.start_notify(NOTIFY_CHAR_UUID, probe.notification_handler)
        await asyncio.sleep(0.3)

        # CMD 213 — Check if uninitialized
        resp = await probe.send_and_wait(client, CMD_GET_DEVICE_ID)
        if resp and resp["data"]:
            info = decode_device_id(resp["data"])
            print(f"  Serial Number: {info.get('serial_number', '?')}")
            print(f"  Device ID:     {info.get('device_id', '?')}")
            if info.get("initialized"):
                print("\n  Device is already initialized. Aborting.")
                return
        print()

        our_secret = os.urandom(8)
        our_device_id = 1

        # CMD 73 — Write device ID + secret
        print("Writing device ID and secret...")
        init_payload = build_init_payload(our_device_id, our_secret)
        resp = await probe.send_and_wait(client, CMD_DEVICE_INIT, init_payload)
        if resp and resp["data"] and resp["data"][0] == 1:
            print(f"  Init ACCEPTED")
            print(f"  Device ID: {our_device_id}")
            print(f"  Secret:    {our_secret.hex()}")
            print()
            print("  *** SAVE THIS SECRET — you need it for all future connections ***")
        else:
            print("  Init REJECTED or no response.")
            return

        # CMD 86 — Verify with our secret
        print("\nVerifying secret...")
        resp = await probe.send_and_wait(client, CMD_VERIFY, our_secret)
        if resp and resp["data"] and resp["data"][0] == 1:
            print("  Auth passed with our secret!")
        else:
            print("  Auth FAILED.")
            return

        # CMD 84 — Time sync
        ts_payload = build_time_sync_payload()
        resp = await probe.send_and_wait(client, CMD_TIME_SYNC, ts_payload)
        if resp and resp["data"] and resp["data"][0] == 1:
            print("  Time synced.")

        # Read state to confirm everything works
        print("\nReading device state to confirm...\n")
        resp = await probe.send_and_wait(client, CMD_GET_STATE)
        if resp and resp["type"] == TYPE_RESPONSE and resp["data"]:
            state = decode_state(resp["data"])
            print("--- Status ---")
            print(format_state(state))
        else:
            print("  Could not read state.")

        if client.is_connected:
            try:
                await client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass

    print("\nDone.")


async def main():
    global _verbose

    parser = argparse.ArgumentParser(description="Read PetKit water fountain status over BLE")
    parser.add_argument("--raw", action="store_true", help="Show raw BLE packet details")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--self-init", action="store_true",
        help="Initialize device with our own secret (WRITES to device, dangerous)",
    )
    parser.add_argument(
        "--confirm-permanent-init", action="store_true",
        help="Required with --self-init to acknowledge the permanent device write",
    )
    args = parser.parse_args()

    _verbose = args.raw

    if args.self_init and not args.confirm_permanent_init:
        parser.error("--self-init requires --confirm-permanent-init")
    if args.confirm_permanent_init and not args.self_init:
        parser.error("--confirm-permanent-init is only valid with --self-init")

    if args.self_init:
        print("*" * 60)
        print("*** SELF-INIT MODE ***")
        print("*")
        print("* This will WRITE a device ID and secret to your fountain.")
        print("* This may prevent the official PetKit app from ever")
        print("* connecting to this device.")
        print("*")
        print("* Only proceed if you have confirmed a factory reset")
        print("* procedure exists for your device.")
        print("*" * 60)
        confirm = input("\nType 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return
        print()

    if not args.json:
        print(f"Scanning for PetKit water fountain...")

    devices = await scan_for_device(timeout=10)

    if not devices:
        msg = "No PetKit water fountain found. Check power, Bluetooth, and range."
        if args.json:
            print(json_lib.dumps({"error": msg}))
        else:
            print(msg)
        return

    device = devices[0][0]
    if not args.json and len(devices) > 1:
        seen = set()
        unique = []
        for d, _ in devices:
            if d.address not in seen:
                seen.add(d.address)
                unique.append(d)
        if len(unique) > 1:
            print(f"Found {len(unique)} devices, using first: {device.name} [{device.address}]")

    if args.self_init:
        await self_init_device(device)
    else:
        await read_device(device, output_json=args.json)


if __name__ == "__main__":
    asyncio.run(main())
