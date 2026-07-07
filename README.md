# URST (Universal Reliable Serial Transport) for MicroPython

[![License](https://img.shields.io/badge/license-SUL--1.0-green.svg)](LICENSE.md)

**URST for MicroPython** is a professional-grade implementation of the [Universal Reliable Serial Transport (URST) protocol](URST-Specification.md). It provides reliable, error-checked, and fragmented data transmission over unreliable serial (UART/XBee) connections, specifically optimized for MicroPython devices like the Raspberry Pi Pico and ESP32.

## Key Features

- **Reliable Delivery**: Strict stop-and-wait ARQ (Automatic Repeat Request) with configurable timeouts and retries.
- **MicroPython Optimized**: Native support for `machine.UART` and `utime.ticks_ms()` for precise timing on hardware.
- **Hardware Agnostic**: Works on Desktop Python (via `pyserial`) and MicroPython seamlessly.
- **Error Detection**: Robust CRC-16/CCITT_FALSE validation for every frame.
- **Robust Framing**: Uses COBS (Consistent Overhead Byte Stuffing) for zero-byte-free encoding, ensuring unambiguous frame delimiting via `0x00`.
- **Message Fragmentation**: Automatically handles messages larger than the physical MTU (up to 8KB+ reassembly).
- **Connection Handshake**: Built-in capability negotiation and sequence synchronization.
- **Simple API**: Clean `send()` and `read()` interface that abstracts away the complexity of serial framing and retransmission.

## Installation

### For MicroPython Devices

**Option A — Source install via mip (simplest):**

```bash
mpremote mip install github:simonl65/URST-mpy
```

**Option B — Pre-compiled .mpy (smallest flash footprint, fastest startup):**

Pre-compiling with `mpy-cross` reduces the package from ~19.7 KB to ~6.4 KB on flash
and eliminates the parse-and-compile step at boot time.

```bash
# 1. Install mpy-cross (once)
pip install mpy-cross

# 2. Clone the repo and build
git clone https://github.com/simonl65/urst-mpy.git
cd urst-mpy
make mpy          # produces dist/urst/*.mpy

# 3. Deploy the compiled files to your device
mpremote cp -r dist/urst :
```

**Option C — Copy source directly:**

Copy the `urst/` directory from this repository to the root of your MicroPython device's filesystem.

### For Desktop Development

If you want to use it on your PC (e.g., for testing or gateway applications), install `pyserial` first:

```bash
pip install pyserial
```

## Quick Start (MicroPython)

```python
import urst
import machine
import time

# 1. Initialize UART on your device (e.g., Raspberry Pi Pico)
uart = machine.UART(0, baudrate=57600, tx=machine.Pin(0), rx=machine.Pin(1))

# 2. Initialize URST with the UART object
transport = urst.Urst(uart)

# 3. Send a message (automatically handles framing, CRC, and ACK waiting)
# It will fragment large data into ~194 byte chunks automatically.
transport.send(b"Hello from Pico!")

# 4. Read a complete message (handles reassembly of fragments)
while True:
    message = transport.read()
    if message:
        print(f"Received: {message.decode()}")
    time.sleep(0.1)
```

## Quick Start (Desktop Python)

```python
from urst import Urst

# Initialize URST on your serial port (requires pyserial)
transport = Urst(port="/dev/ttyUSB0", baud=57600)

transport.send(b"Hello from Desktop!")
message = transport.read()
```

## Protocol Architecture

URST follows a strictly layered architecture to ensure separation of concerns:

```text
┌───────────────────────────────────┐
│    Handler Layer (Application)    │  User API: send(), read()
├───────────────────────────────────┤
│     Protocol Layer (Reliable)     │  CONNECT/ACK/NAK, Retransmission
├───────────────────────────────────┤
│     Transport Layer (Framing)     │  Frame Type, Sequence Numbers
├───────────────────────────────────┤
│    Codec Layer (Encoding/IO)      │  COBS, CRC, UART (machine/pyserial)
└───────────────────────────────────┘
```

For full technical details, please refer to the [URST Specification](URST-Specification.md).

## Development

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for local development and testing.

```bash
git clone https://github.com/simonl65/urst-mpy.git
cd urst-mpy
uv sync
```

### Running Tests

If using UV:

```bash
uv run pytest
```

If you aren't using UV you should set `PYTHONPATH` to ensure the tests find the package correctly:

```bash
PYTHONPATH=. pytest
```

### Linting & Formatting

```bash
uv run ruff check .
uv run ruff format .
```

## License

This project is licensed under the **Sustainable Use License (SUL-1.0)**. See the [LICENSE.md](LICENSE.md) file for details.

---

**Author:** Simon R. Lincoln ([oss@codeability.co.uk](mailto:oss@codeability.co.uk))
