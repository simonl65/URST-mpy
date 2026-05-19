import urst
import time

# MicroPython example for Raspberry Pi Pico
# This script demonstrates how to initialize URST using machine.UART


def main():
    print("Initializing URST...")

    try:
        import machine

        # Initialize UART 0 on pins GP0 (TX) and GP1 (RX)
        # On a Pico, UART(0) uses GP0/GP1 by default.
        uart = machine.UART(
            0, baudrate=57600, tx=machine.Pin(0), rx=machine.Pin(1)
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
    transport.send(b"Hello from URST on MicroPython!")

    print("Waiting for response...")
    try:
        while True:
            # read() reassembles fragments and returns a complete message
            msg = transport.read()
            if msg:
                print(f"Received: {msg.decode()}")

            # Small sleep to be friendly to the MicroPython event loop
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
