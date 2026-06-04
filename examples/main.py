import time

import config
import urst
from machine import UART, Pin

# MicroPython example for Raspberry Pi Pico
# This script demonstrates how to initialize URST using UART

print("URST MicroPython Example")
led = Pin("LED", Pin.OUT)
led.on()
time.sleep(2)

print(
    f"Initializing URST with baudrate {config.SERIAL_BAUDRATE} on UART{config.XBEE_DEVICE_PORT}..."
)

try:
    # Initialize UART 0 on pins GP0 (TX) and GP1 (RX)
    # On a Pico, UART(0) uses GP0/GP1 by default.
    uart = UART(
        config.XBEE_DEVICE_PORT,
        baudrate=config.SERIAL_BAUDRATE,
        tx=Pin(0),
        rx=Pin(1),
    )

    # Initialize URST with the UART object
    transport = urst.Urst(uart)

    # Alternatively, you can let Urst initialize it for you by passing the ID:
    # transport = urst.Urst(0, baud=57600)

except (ImportError, AttributeError):
    print("Not running on MicroPython, or machine module not available.")
    print("Falling back to a mock/simulated serial for demonstration.")

    # This part is just so the script can be 'run' on desktop without errors
    class MockUART:
        def write(self, data):
            return len(data)

        def read(self, n):
            return b""

        def any(self):
            return 0

    transport = urst.Urst(MockUART())

print("Sending message...")
# send() handles fragmentation and reliable delivery
transport.send(
    b"Hello from URST on MicroPython! This message may be fragmented if it's too long. URST will handle it for you. This is a test message to demonstrate the capabilities of URST over UART on a Raspberry Pi Pico. Enjoy using URST for your projects! URST ensures that even if the message is too long for a single UART frame, it will be split into fragments and reassembled correctly on the receiving end. Hello from URST on MicroPython! This message may be fragmented if it's too long. URST will handle it for you. This is a test message to demonstrate the capabilities of URST over UART on a Raspberry Pi Pico. Enjoy using URST for your projects! URST ensures that even if the message is too long for a single UART frame, it will be split into fragments and reassembled correctly on the receiving end. Hello from URST on MicroPython! This message may be fragmented if it's too long. URST will handle it for you. This is a test message to demonstrate the capabilities of URST over UART on a Raspberry Pi Pico. Enjoy using URST for your projects! URST ensures that even if the message is too long for a single UART frame, it will be split into fragments and reassembled correctly on the receiving end. "
)

print("Waiting for response...")
try:
    while True:
        # read() reassembles fragments and returns a complete message
        msg = transport.read()
        if msg:
            print(f"Received: {msg.decode()}")
            print("\n=====\nDONE!\n=====\n")
            break

        # Small sleep to be friendly to the MicroPython event loop
        led.toggle()
        time.sleep(0.1)

except KeyboardInterrupt:
    print("Stopped.")

finally:
    for _ in range(30):
        led.toggle()
        time.sleep(0.05)
    led.off()
