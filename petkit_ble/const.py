"""Constants for PetKit W5-family BLE protocol."""

# GATT UUIDs
SERVICE_UUID = "0000aaa0-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000aaa2-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000aaa1-0000-1000-8000-00805f9b34fb"

# BLE advertisement names for W5-family devices
W5_BLE_NAMES = frozenset({
    "Petkit_W5",
    "Petkit_W5C",
    "Petkit_W5N",
    "Petkit_W4X",
    "Petkit_W4XUVC",
    "Petkit_CTW2",
})

# Packet framing
CMD_HEADER = bytes([0xFA, 0xFC, 0xFD])
TRAILER = 0xFB

# Message types
TYPE_REQUEST = 1
TYPE_RESPONSE = 2

# Command IDs — authentication & setup
CMD_BATTERY = 66
CMD_DEVICE_INIT = 73
CMD_TIME_SYNC = 84
CMD_VERIFY = 86

# Command IDs — read
CMD_GET_VERSION = 200
CMD_GET_STATE = 210
CMD_GET_SETTINGS = 211
CMD_GET_DEVICE_ID = 213
CMD_GET_EXT_LIGHT = 215
CMD_GET_EXT_DND = 216

# Command IDs — write
CMD_CHANGE_MODE = 220
CMD_WRITE_SETTINGS = 221
CMD_RESET_FILTER = 222
CMD_WRITE_LIGHT_SCHEDULE = 225
CMD_WRITE_DND_SCHEDULE = 226
CMD_START_OTA = 83

# Mode values
MODE_OFF = 0
MODE_NORMAL = 1
MODE_SMART = 2
MODE_NAMES = {MODE_OFF: "Off", MODE_NORMAL: "Normal", MODE_SMART: "Smart"}
