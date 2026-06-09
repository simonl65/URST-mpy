"""
Configure an XBee device module via UART on a device.
"""

import sys

import config
import utime  # pyright: ignore[reportMissingModuleSource]
from machine import UART, Pin

GUARD_TIME = 1.25

DEVICE_COMMANDS = [
    f"ATID {config.XBEE_PAN_ID}",
    f"ATNI {config.XBEE_DEVICE_NODEID}",
    f"ATBD {config.XBEE_DATA_RATE}",
    f"ATMM {config.XBEE_MACMODE}",
    f"ATRO {config.XBEE_PACKETIZATION_TIMEOUT}",
    f"ATPL {config.XBEE_POWER_LEVEL}",
    f"ATEE {config.XBEE_AES}",
    f"ATKY {config.XBEE_AES_KEY.hex()}",
]


def send_configuration(
    port_num, configured_baud, commands, guard=1.0, timeout=2.0
):
    """Configure XBee module via UART on Pico-W

    Args:
        port_num: UART port number (0 or 1)
        configured_baud: Current/configured baudrate
        commands: List of AT commands to send
        guard: Guard time in seconds
        timeout: Read timeout in milliseconds

    Returns:
        bool: True if configuration successful, False otherwise
    """
    baudrates_to_try = [configured_baud] + list(config.baudrates.keys())
    # Remove duplicates
    seen = set()
    baudrates_to_try = [
        x for x in baudrates_to_try if not (x in seen or seen.add(x))
    ]

    for baud in baudrates_to_try:
        print(f"Trying to connect to UART{port_num} at baudrate {baud}")

        try:
            # Initialize UART with specified baudrate
            uart = UART(port_num, baudrate=baud, tx=Pin(0), rx=Pin(1))

            # Ensure silence before +++
            print("Waiting for silence before entering command mode...")
            utime.sleep(guard / 2)
            uart.write(b"+++")
            utime.sleep(guard)

            # Read response
            resp = b""
            while uart.any():
                resp += uart.readline()

            if b"OK" not in resp:
                # Try to read more
                utime.sleep(0.1)
                while uart.any():
                    resp += uart.readline()

            if b"OK" in resp:
                print("Successfully entered command mode")
                print(f"Command mode response: {resp.decode().strip()}")

                print("Sending AT commands:")
                for cmd in commands:
                    clean_cmd = cmd.rstrip("\r\n")
                    line = (clean_cmd + "\r").encode("ascii")
                    uart.write(line)

                    utime.sleep(0.1)  # small delay for response
                    resp = b""
                    while uart.any():
                        resp += uart.readline()

                    if b"OK" not in resp:
                        utime.sleep(0.1)
                        while uart.any():
                            resp += uart.readline()

                    print(f"> {clean_cmd}  ->  {resp.decode().strip()}")
                    utime.sleep(0.5)  # delay between commands

                # Save configuration
                uart.write(b"ATWR\r")
                utime.sleep(0.2)
                uart.write(b"ATCN\r")  # Exit command mode
                utime.sleep(0.2)

                uart.deinit()
                return True
            else:
                print(f"Failed to enter command mode at baudrate {baud}")
                uart.deinit()

        except Exception as e:
            print(f"Error at baudrate {baud}: {e}")

    print(f"Could not connect to UART{port_num} at any baudrate.")
    return False


if __name__ == "__main__":
    success = True

    # For Pico-W: UART0 uses GPIO0(TX) and GPIO1(RX), UART1 uses GPIO8(TX) and GPIO9(RX)
    # Determine which UART port to use based on your config
    uart_port = getattr(config, "XBEE_UART_PORT", 0)

    print("Configuring XBee DEVICE Module...")
    if not send_configuration(
        uart_port,
        config.SERIAL_BAUDRATE,
        DEVICE_COMMANDS,
        GUARD_TIME,
        config.SERIAL_TIMEOUT,
    ):
        success = False

    if not success:
        sys.exit(1)
