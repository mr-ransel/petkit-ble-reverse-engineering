#!/usr/bin/env python3
"""
PetKit Eversweet Solo 2 (W5/CTW2) BLE Probe Script

Connects to a PetKit water fountain over BLE, authenticates with a
zero secret (works on uninitialized devices), and reads device state.

Modes:
    (default)       Connect, auth, read all state — clean human-readable output
    --raw           Show raw BLE packet hex in addition to decoded values
    --json          Output as JSON (for scripting)
    --self-init     Initialize the device with our own secret (DANGEROUS)

Usage:
    python probe.py              # read device state
    python probe.py --raw        # same but with raw packet details
    python probe.py --json       # output as JSON
"""

import argparse
import asyncio
import dataclasses
import json as json_lib
import logging
import os
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Missing dependency. Install with:")
    print("  pip install bleak")
    sys.exit(1)

from petkit_ble import PetkitW5Device, W5_BLE_NAMES
from petkit_ble.const import NOTIFY_CHAR_UUID, WRITE_CHAR_UUID
from petkit_ble.device import AuthenticationError
from petkit_ble.models import W5Data


async def scan_for_device(timeout=10):
    found = []

    def detection_callback(device, advertisement_data):
        name = advertisement_data.local_name or device.name or ""
        if name in W5_BLE_NAMES:
            found.append((device, advertisement_data))

    scanner = BleakScanner(detection_callback=detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()
    return found


def format_state(state):
    lines = []
    lines.append(f"  Power:              {'ON' if state.power_on else 'OFF'}")
    lines.append(f"  Mode:               {state.mode_name}")
    lines.append(f"  Low water:          {'YES - NEEDS REFILL' if state.low_water_warning else 'OK'}")
    lines.append(f"  Filter:             {state.filter_remaining_pct}% remaining" +
                 (" (REPLACE SOON)" if state.filter_warning else ""))
    lines.append(f"  Pump running:       {'Yes' if state.pump_running else 'No'}")
    lines.append(f"  Do not disturb:     {'On' if state.do_not_disturb else 'Off'}")
    lines.append(f"  Breakdown alert:    {'YES' if state.breakdown_warning else 'No'}")
    lines.append(f"  Total pump time:    {state.pump_runtime_hours}h")
    if state.today_pump_minutes is not None:
        lines.append(f"  Today pump time:    {state.today_pump_minutes}min")
    return "\n".join(lines)


def format_settings(settings):
    lines = []
    lines.append(f"  Smart mode:         {settings.smart_work_minutes}min on / {settings.smart_sleep_minutes}min off")
    lamp = "On" if settings.lamp_enabled else "Off"
    lines.append(f"  Lamp:               {lamp} (brightness {settings.lamp_brightness})")
    lines.append(f"  Lamp schedule:      {settings.lamp_on_time} - {settings.lamp_off_time}")
    dnd = "On" if settings.dnd_enabled else "Off"
    lines.append(f"  Do not disturb:     {dnd}")
    if settings.dnd_enabled:
        lines.append(f"  DND schedule:       {settings.dnd_start_time} - {settings.dnd_end_time}")
    if settings.child_lock is not None:
        lines.append(f"  Child lock:         {'On' if settings.child_lock else 'Off'}")
    return "\n".join(lines)


def format_device_info(data: W5Data):
    lines = []
    if data.info.serial_number:
        lines.append(f"  Serial number:      {data.info.serial_number}")
    lines.append(f"  Device ID:          {data.info.device_id}" +
                 (" (uninitialized)" if not data.info.initialized else ""))
    if data.version.hardware or data.version.firmware:
        lines.append(f"  Hardware:           v{data.version.hardware}")
        lines.append(f"  Firmware:           v{data.version.firmware}")
    if data.battery.voltage_raw:
        lines.append(f"  Voltage (raw):      {data.battery.voltage_raw}")
    return "\n".join(lines)


def data_to_dict(data: W5Data) -> dict:
    """Convert W5Data to a JSON-serializable dict."""
    result = {}
    for section_name in ("info", "version", "state", "settings", "battery"):
        obj = getattr(data, section_name)
        result[section_name] = dataclasses.asdict(obj)
    # Add computed properties
    result["state"]["pump_runtime_hours"] = data.state.pump_runtime_hours
    result["state"]["today_pump_minutes"] = data.state.today_pump_minutes
    result["settings"]["lamp_on_time"] = data.settings.lamp_on_time
    result["settings"]["lamp_off_time"] = data.settings.lamp_off_time
    result["settings"]["dnd_start_time"] = data.settings.dnd_start_time
    result["settings"]["dnd_end_time"] = data.settings.dnd_end_time
    return result


async def read_device(ble_device, output_json=False):
    device = PetkitW5Device()

    if not output_json:
        print(f"Connecting to {ble_device.name or 'unknown'} [{ble_device.address}]...")

    async with BleakClient(ble_device) as client:
        if not output_json:
            print("Connected. Reading device data...\n")

        await client.start_notify(NOTIFY_CHAR_UUID, device.handle_notification)
        device.set_write_func(lambda data: client.write_gatt_char(WRITE_CHAR_UUID, data))
        await asyncio.sleep(0.3)

        try:
            data = await device.async_poll()
        except AuthenticationError as e:
            if output_json:
                print(json_lib.dumps({"error": str(e)}))
            else:
                print(f"Authentication failed: {e}")
                print("Device may already be initialized with a secret.")
            return

        if client.is_connected:
            try:
                await client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass

    if output_json:
        result = data_to_dict(data)
        result["ble_name"] = ble_device.name
        result["ble_address"] = ble_device.address
        print(json_lib.dumps(result, indent=2))
    else:
        print("--- Device Info ---")
        print(format_device_info(data))
        print()
        print("--- Status ---")
        print(format_state(data.state))
        print()
        print("--- Settings ---")
        print(format_settings(data.settings))
        print()


async def self_init_device(ble_device):
    device = PetkitW5Device()

    print(f"\nConnecting to {ble_device.name or 'unknown'} [{ble_device.address}]...")

    async with BleakClient(ble_device) as client:
        print("Connected.\n")

        await client.start_notify(NOTIFY_CHAR_UUID, device.handle_notification)
        device.set_write_func(lambda data: client.write_gatt_char(WRITE_CHAR_UUID, data))
        await asyncio.sleep(0.3)

        # Check if uninitialized
        id_data = await device.async_get_info()
        print(f"  Serial Number: {id_data.info.serial_number or '?'}")
        print(f"  Device ID:     {id_data.info.device_id}")
        if id_data.info.initialized:
            print("\n  Device is already initialized. Aborting.")
            return
        print()

        our_secret = os.urandom(8)
        our_device_id = 1

        print("Writing device ID and secret...")
        if await device.async_init_device(our_device_id, our_secret):
            print(f"  Init ACCEPTED")
            print(f"  Device ID: {our_device_id}")
            print(f"  Secret:    {our_secret.hex()}")
            print()
            print("  *** SAVE THIS SECRET — you need it for all future connections ***")
        else:
            print("  Init REJECTED or no response.")
            return

        print("\nVerifying secret...")
        try:
            await device.authenticate(our_secret)
            print("  Auth passed with our secret!")
        except AuthenticationError:
            print("  Auth FAILED.")
            return

        print("\nReading device state to confirm...\n")
        from petkit_ble.device import async_read_all
        data = await async_read_all(device)
        print("--- Status ---")
        print(format_state(data.state))

        if client.is_connected:
            try:
                await client.stop_notify(NOTIFY_CHAR_UUID)
            except Exception:
                pass

    print("\nDone.")


async def main():
    parser = argparse.ArgumentParser(description="Read PetKit water fountain status over BLE")
    parser.add_argument("--raw", action="store_true", help="Show raw BLE packet details")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--self-init", action="store_true",
        help="Initialize device with our own secret (WRITES to device, dangerous)",
    )
    args = parser.parse_args()

    if args.raw:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("petkit_ble").setLevel(logging.DEBUG)

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
        print("Scanning for PetKit water fountain...")

    devices = await scan_for_device(timeout=10)

    if not devices:
        msg = "No PetKit water fountain found. Check power, Bluetooth, and range."
        if args.json:
            print(json_lib.dumps({"error": msg}))
        else:
            print(msg)
        return

    ble_device = devices[0][0]
    if not args.json and len(devices) > 1:
        seen = set()
        unique = []
        for d, _ in devices:
            if d.address not in seen:
                seen.add(d.address)
                unique.append(d)
        if len(unique) > 1:
            print(f"Found {len(unique)} devices, using first: {ble_device.name} [{ble_device.address}]")

    if args.self_init:
        await self_init_device(ble_device)
    else:
        await read_device(ble_device, output_json=args.json)


if __name__ == "__main__":
    asyncio.run(main())
