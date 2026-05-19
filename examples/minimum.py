import logging

import config

import urst

logging.basicConfig(
    level=logging.DEBUG, format="[%(levelname)s] %(message)s  [ %(name)s ]"
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
                f"URST initialized successfully on '{config.XBEE_BASE_PORT}' @ {config.SERIAL_BAUDRATE} baud"
            )

            ser.send(b"Hello, URST!")

    except Exception as e:
        logger.error(f"Error initializing URST: {e}")


if __name__ == "__main__":
    main()
