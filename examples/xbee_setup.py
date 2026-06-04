import sys
import time

import config
import serial

GUARD_TIME = 1.25

BASE_COMMANDS = [
    f"ATID {config.XBEE_PAN_ID}",
    f"ATNI {config.XBEE_BASE_NODEID}",
    f"ATBD {config.XBEE_DATA_RATE}",
    f"ATMM {config.XBEE_MACMODE}",
    f"ATRO {config.XBEE_PACKETIZATION_TIMEOUT}",
    f"ATPL {config.XBEE_POWER_LEVEL}",
    f"ATEE {config.XBEE_AES}",
    f"ATKY {config.XBEE_AES_KEY.hex()}",
]

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
    port: str,
    configured_baud: int,
    commands: list[str],
    guard: float = 1.0,
    timeout: float = 2.0,
) -> bool:
    baudrates_to_try = [configured_baud] + list(config.baudrates.keys())
    # Remove duplicates
    seen: set[int] = set()
    baudrates_to_try = [
        x for x in baudrates_to_try if not (x in seen or seen.add(x))
    ]

    for baud in baudrates_to_try:
        print(f"Trying to connect to {port} at baudrate {baud}")

        try:
            with serial.Serial(port, baud, timeout=timeout) as ser:
                # ensure silence before +++
                print("Waiting for silence before entering command mode...")
                time.sleep(guard / 2)
                ser.write(b"+++")
                time.sleep(guard)

                resp = ser.read_until(b"\r", 64)  # read any immediate response
                if b"OK" not in resp:
                    # try to read more (some XBees respond with \r\nOK\r\n)
                    resp += ser.read(64)

                if b"OK" in resp:
                    print("Successfully entered command mode")
                    print(
                        "Command mode response:",
                        resp.decode(errors="ignore").strip(),
                    )

                    print("Sending AT commands:")
                    for cmd in commands:
                        clean_cmd = cmd.rstrip("\r\n")
                        line = (clean_cmd + "\r").encode("ascii")
                        ser.write(line)
                        resp = ser.read_until(b"\r", 64)
                        if b"OK" not in resp:
                            resp += ser.read(64)
                            time.sleep(0.1)
                        print(
                            f"> {clean_cmd}  ->  {resp.decode(errors='ignore').strip()}"
                        )
                        time.sleep(0.5)  # small delay between commands

                    ser.write(b"ATWR\r")  # ensure we save configuration
                    time.sleep(0.2)
                    ser.write(b"ATCN\r")  # ensure we exit command mode
                    return True
                else:
                    print(f"Failed to enter command mode at baudrate {baud}")

        except (serial.SerialException, OSError) as e:
            if (
                isinstance(e, FileNotFoundError)
                or getattr(e, "errno", None) == 2
            ):
                print(
                    f"Port not available: {port!r}. Verify device connection and path."
                )
                return False
            print(f"Error at baudrate {baud}: {e}")

    print(f"Could not connect to {port} at any baudrate.")
    return False


if __name__ == "__main__":
    success = True

    print("Configuring XBee Base Module...")
    if not send_configuration(
        config.BASE_PORT,
        config.SERIAL_BAUDRATE,
        BASE_COMMANDS,
        GUARD_TIME,
        config.SERIAL_TIMEOUT,
    ):
        success = False

    print("\nConfiguring XBee Device Module...")
    if not send_configuration(
        config.DEVICE_PORT,
        config.SERIAL_BAUDRATE,
        DEVICE_COMMANDS,
        GUARD_TIME,
        config.SERIAL_TIMEOUT,
    ):
        success = False

    if not success:
        sys.exit(1)
