# fmt: off

"""RENAME this file to config.py"""

baudrates = {9600: 3, 19200: 4, 38400: 5, 57600: 6, 115200: 7}

SERIAL_TIMEOUT  = 0.1   # Seconds
SERIAL_BAUDRATE = 57600 # Must be one of the keys in baudrates

MAX_FRAME_BYTES = 255  # Maximum allowed bytes per frame. Make this small enough for any attached device buffer, but large enough to be efficient.

XBEE_PAN_ID         = "3210"            # ID
XBEE_BASE_PORT      = "/dev/ttyUSB0"
XBEE_DEVICE_PORT    = "/dev/ttyUSB1"
XBEE_UART_PORT      = 0                 # 0 or 1
XBEE_BASE_NODEID    = "BASE01"          # NI (0-20 ASCII characters)
XBEE_DEVICE_NODEID  = "NODE01"          # NI

XBEE_POWER_LEVEL    = 4         # PL (0: Lowest, 1: Low, 2: Medium, 3: High, 4: Highest)
XBEE_PACKETIZATION_TIMEOUT = 3  # RO (x character times)
XBEE_MACMODE        = 1         # MM (0: + MaxStream header w/ACKS, 1: 802.15.4  no ACKS, 2: 802.15.4  w/ACKS, 4: 802.15.4 + MaxStream header no ACKS)

# Experimental
XBEE_AES        = 0     # EE (0: Disabled, 1: Enabled !!! Unreliable, use with care !!!)
XBEE_AES_KEY    = b""   # KY (0-32 hex characters)

# !!! DO NOT CHANGE ANYTHING BELOW HERE !!!
XBEE_API        = 0  # AP (0: Disabled, 1: Enabled)
XBEE_DATA_RATE  = baudrates[SERIAL_BAUDRATE]  # BD
