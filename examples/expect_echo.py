import logging

import config

import urst

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s] %(message)s  [ %(name)s ]",
)
logger = logging


def main():
    try:
        logger.debug("Initialising...")
        ser = urst.Urst(
            config.XBEE_BASE_PORT,
            config.SERIAL_BAUDRATE,
            timeout=config.SERIAL_TIMEOUT,
        )

        if ser:
            logger.debug(
                f"URST initialized successfully on '{config.XBEE_BASE_PORT}' @ {config.SERIAL_BAUDRATE} baud, timeout {config.SERIAL_TIMEOUT} seconds."
            )

    except Exception as e:
        logger.error(f"Error initializing URST: {e}")
        return

    loop_counter = 0

    while loop_counter < 50:
        # Send a message
        message = (
            b"\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09" * 10
        )  # binary data
        chars_sent = ser.send(message)
        logger.debug(f"Sent {chars_sent} characters ({message}).")

        # Expect an echo of the exact same message back (assumes device is configured to echo)
        reply = ser.read()
        if reply is None:
            logger.error("No reply received.")
            print("No reply received.")

        # Check that sent and received messages match
        if reply == message:
            print(
                f"Echo received successfully, messages match: sent {chars_sent}, received {len(reply)}."
            )
        else:
            logger.error(
                "Echo mismatch: sent and received messages do not match."
            )
            print(
                f"Echo mismatch: sent and received messages do not match: sent {chars_sent}, received {len(reply)}."
            )

        loop_counter += 1


if __name__ == "__main__":
    main()
