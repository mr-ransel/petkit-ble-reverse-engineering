# PetKit Eversweet Solo 2 (W5/CTW2) BLE Protocol

Reverse-engineered BLE protocol for the PetKit Eversweet Solo 2 wireless water fountain.

**Target device:** PetKit Eversweet Solo 2 (wireless water fountain)
**BLE advertisement name:** `Petkit_CTW2`

## Device Family

This protocol is shared across the W5 device family:

| BLE Name | Product | typeCode |
|---|---|---|
| `Petkit_W5` | Eversweet (original) | 1 |
| `Petkit_W5C` | Eversweet variant | 2 |
| `Petkit_W5N` | Eversweet variant | 3 |
| `Petkit_W4X` | Eversweet W4X | — |
| `Petkit_W4XUVC` | Eversweet W4X UVC | — |
| `Petkit_CTW2` | Eversweet Solo 2 (wireless) | — |

All use the same GATT service, packet framing, and command set. Minor differences exist in extended features based on `typeCode` and firmware version.

## GATT Service

| UUID | Role |
|---|---|
| `0000aaa0-0000-1000-8000-00805f9b34fb` | Service |
| `0000aaa2-0000-1000-8000-00805f9b34fb` | Write characteristic (commands to device) |
| `0000aaa1-0000-1000-8000-00805f9b34fb` | Notify characteristic (responses from device) |

## Packet Framing

All BLE packets (both directions) use the same framing:

```
FA FC FD <cmd:1> <type:1> <seq:1> <len_lo:1> <len_hi:1> [data:N] FB
```

| Field | Size | Description |
|---|---|---|
| Header | 3 bytes | Always `FA FC FD` |
| cmd | 1 byte | Command ID |
| type | 1 byte | 1=Request, 2=Response, 3=Non-response request |
| seq | 1 byte | Sequence number (0-255, wrapping) |
| len | 2 bytes | Little-endian payload length |
| data | N bytes | Payload (may be 0 bytes) |
| Trailer | 1 byte | Always `FB` |

## Connection & Authentication Flow

### Normal (read-only, uninitialized device)

1. **Connect** to GATT service
2. **Subscribe** to notify characteristic (`0xAAA1`)
3. **CMD 213** — Get device ID + serial number
4. **CMD 86** — Verify with zero secret (8 null bytes). Works when deviceId == 0 (uninitialized)
5. **CMD 84** — Time sync (required before reads)
6. Read commands: CMD 200, CMD 210, CMD 211, CMD 66, CMD 215/216

### Full initialization flow (as done by the official app)

1. CMD 213 — Get device ID
2. If deviceId == 0 (new device): app calls a cloud API to get a server-assigned deviceId + secret
3. CMD 73 — Init device (writes deviceId + secret permanently to device)
4. CMD 86 — Verify with the assigned secret
5. CMD 84 — Time sync
6. Read/write commands

### Security model

- **Uninitialized devices (deviceId == 0)** accept a zero secret (8 null bytes) for CMD 86. This is the key discovery that enables cloud-free BLE access.
- **Initialized devices** require the exact 8-byte secret that was written during CMD 73. The secret is generated server-side.
- CMD 86 response: `data[0] == 1` means success, anything else means failure. On failure, the device may disconnect.
- Sending commands before authentication causes the device to disconnect.

## Command Reference

### Authentication & Setup Commands

#### CMD 213 — Get Device ID
- **Direction:** Request → Response
- **Request payload:** (empty)
- **Response payload:** 8 bytes deviceId (little-endian) + up to 14 bytes ASCII serial number

#### CMD 86 — Verify Secret
- **Direction:** Request → Response
- **Request payload:** 8 bytes (secret, zero-padded)
- **Response payload:** 1 byte — `0x01` = success, `0x00` = failure

#### CMD 73 — Initialize Device (WRITES PERMANENTLY)
- **Direction:** Request → Response
- **Request payload:** 8 bytes deviceId (big-endian) + 8 bytes secret
- **Response payload:** 1 byte — `0x01` = success
- **WARNING:** This permanently writes the device ID and secret. Can only be undone with a physical factory reset.

#### CMD 84 — Time Sync
- **Direction:** Request → Response
- **Request payload:** 1 byte (0x00) + 4 bytes seconds since 2000-01-01 (big-endian signed int) + 1 byte timezone (UTC offset + 12)
- **Response payload:** 1 byte — `0x01` = success

### Read Commands

#### CMD 210 — Get Running State
- **Direction:** Request → Response
- **Request payload:** (empty)
- **Response payload (12-16 bytes):**

| Byte | Field | Values |
|---|---|---|
| 0 | powerStatus | 0=off, 1=on |
| 1 | mode | 0=off, 1=normal, 2=smart |
| 2 | nightNoDisturb | 0/1 |
| 3 | breakdownWarning | 0/1 |
| 4 | **lackWarning** | **0=OK, 1=LOW WATER** |
| 5 | filterWarning | 0/1 |
| 6-9 | pumpRunTime | 4 bytes big-endian, total seconds |
| 10 | filterPercent | 0-100, filter life remaining % |
| 11 | runStatus | 0=idle, 1=running |
| 12-15 | todayPumpRunTime | (optional) 4 bytes big-endian, seconds today |

The `todayPumpRunTime` field (bytes 12-15) is only present on newer firmware versions:
- typeCode 2: firmware >= 24
- Other typeCodes: firmware >= 35

#### CMD 211 — Get Settings
- **Direction:** Request → Response
- **Request payload:** (empty)
- **Response payload (13-14 bytes):**

| Byte | Field | Type |
|---|---|---|
| 0 | smartWorkingTime | Minutes (smart mode run duration) |
| 1 | smartSleepTime | Minutes (smart mode sleep duration) |
| 2 | lampRingSwitch | 0=off, 1=on |
| 3 | lampRingBrightness | 0-255 |
| 4-5 | lampRingLightUpTime | Big-endian, minutes from midnight |
| 6-7 | lampRingGoOutTime | Big-endian, minutes from midnight |
| 8 | noDisturbingSwitch | 0=off, 1=on |
| 9-10 | noDisturbingStartTime | Big-endian, minutes from midnight |
| 11-12 | noDisturbingEndTime | Big-endian, minutes from midnight |
| 13 | isLock | (optional) 0/1, child lock |

The `isLock` byte is only present on firmware versions that support it.

#### CMD 200 — Get Hardware/Firmware Version
- **Direction:** Request → Response
- **Request payload:** (empty)
- **Response payload:** 2 bytes — `[hardware_version, firmware_version]`

#### CMD 66 — Get Battery/Voltage
- **Direction:** Request → Response
- **Request payload:** (empty)
- **Response payload:** 2 bytes — little-endian raw voltage value
- **Note:** This is a raw ADC value, not a calibrated percentage. Not a reliable water level indicator.

#### CMD 215 — Get Extended Light Settings
- **Direction:** Request → Response
- **Request payload:** (empty)
- **Response payload:**

| Byte | Field |
|---|---|
| 0 | lightConfig (1=config mode 1, else mode 2) |
| 1 | number of time slots |
| 2-5 | reserved (0) |
| 6+ | Time slots: 5 bytes each (2B start_minutes_BE, 2B end_minutes_BE, 1B reserved) |

#### CMD 216 — Get Extended DND Settings
- Same structure as CMD 215 but for Do Not Disturb schedules
- `disturbConfig` instead of `lightConfig`

### Write Commands (NOT YET TESTED)

#### CMD 220 — Change Device Mode
- **Request payload:** 2 bytes — `[mode, submode]`
- **Modes:** 0=off, 1=normal, 2=smart
- **Response:** `data[0] == 0` indicates failure

#### CMD 221 — Write All Settings
- **Request payload (13-14 bytes):**

| Byte | Field |
|---|---|
| 0 | smartWorkingTime |
| 1 | smartSleepTime |
| 2 | lampRingSwitch |
| 3 | lampRingBrightness |
| 4-5 | lampRingLightUpTime (big-endian) |
| 6-7 | lampRingGoOutTime (big-endian) |
| 8 | noDisturbingSwitch |
| 9-10 | noDisturbingStartTime (big-endian) |
| 11-12 | noDisturbingEndTime (big-endian) |
| 13 | isLock (if supported) |

This command is used for changing smart mode parameters, lamp/DND settings, and child lock — they all send the full settings blob to CMD 221.

#### CMD 222 — Reset Filter
- **Request payload:** (empty)
- Resets filter usage counter to 100%.

#### CMD 225 — Update Light Schedule (Extended)
- **Request payload:**

| Byte | Field |
|---|---|
| 0 | lightConfig (1 or 0) |
| 1 | number of time slots |
| 2-5 | reserved (0) |
| 6+ | Time slots: 5 bytes each (2B start, 2B end, 1B 0x00) |

#### CMD 226 — Update DND Schedule (Extended)
- Same structure as CMD 225 but for Do Not Disturb

#### CMD 83 — Start OTA
- **Request payload:** (empty)
- Puts device into OTA (firmware update) mode

#### CMD 230 — Device Push Notification (device-initiated)
- **Direction:** Device → Phone (unsolicited push)
- Contains combined state + settings data (25+ bytes)
- Client should respond with `CMD 230, data=[0x01], type=RESPONSE` to acknowledge
- Layout varies by firmware version — older firmware uses 25 bytes (12 state + 13 settings), newer firmware inserts 4 bytes of `todayPumpRunTime` between state and settings (29 bytes total)

## What's Been Tested

### Confirmed working on a Petkit_CTW2 (Eversweet Solo 2, Wireless)

- **Zero-secret authentication:** CMD 86 with 8 null bytes succeeds on an uninitialized device (deviceId == 0)
- **Full read flow:** CMD 213 → CMD 86 → CMD 84 → CMD 200 → CMD 210 → CMD 211 → CMD 66 → CMD 215
- **Water level detection:** `lackWarning` (byte 4 of CMD 210) is a reliable binary sensor:
  - `0` = Water OK (tested with 3/4 full and completely full)
  - `1` = Low water (tested with bowl removed, very little water, and barely enough water)
  - There is a clear threshold — it flips between "OK" and "low" reliably
- **Voltage reading (CMD 66):** Returns a raw ADC value. Correlates loosely with conditions but is **not** a reliable analog water gauge.
- **Settings readback (CMD 211):** All fields decode correctly — smart mode timing, lamp settings, DND schedule, child lock.
- **Device info (CMD 213):** Returns 8-byte device ID + 14-byte serial number.

### What Hasn't Been Tested

- **Write commands** (CMD 220, 221, 222, 225, 226) — Fully documented but not sent to a real device
- **CMD 73 (init)** — The probe script supports `--self-init` but it was intentionally avoided to keep the device uninitialized and accessible with zero-secret auth
- **CMD 230 (push notifications)** — Device-initiated state updates; would need a long-running connection to observe
- **CMD 83 (OTA)** — Firmware update trigger, intentionally not tested
- **Extended settings (CMD 215/216)** — Read but not fully decoded in the probe script
- **Multi-device scenarios** — Only tested with one CTW2
- **Initialized device with real secret** — Only zero-secret auth on an uninitialized device has been tested

### Surmised but Not Proven

- **Any W5-family device should work the same way.** All W5-family BLE names route to the same protocol handler and command set. The framing and command IDs are identical.
- **Unlink doesn't clear the device secret.** The app's "delete device" function only calls a cloud API and clears local data. No BLE command is sent to the device to wipe the stored secret. A physical factory reset is the only way to return to deviceId == 0.
- **Self-init would work** with CMD 73 using a made-up deviceId and random secret, since the device has no way to validate them without server communication. However, this would permanently lock out zero-secret access.

## Probe Script

The `probe_w5.py` script implements the read-only flow:

```
python probe_w5.py              # Human-readable output
python probe_w5.py --raw        # Include raw BLE packet hex
python probe_w5.py --json       # Structured JSON output
python probe_w5.py --self-init  # Initialize device (DANGEROUS, permanent)
```

Requires `bleak` (`pip install bleak`).
