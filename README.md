# URST (Universal Reliable Serial Transport) for Python

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-SUL--1.0-green.svg)](LICENSE.md)

**URST for Python** is a professional-grade implementation of the [Universal Reliable Serial Transport (URST) protocol](URST-Specification.md). It provides reliable, error-checked, and fragmented data transmission over unreliable serial (UART/XBee) connections.

## Key Features

- **Reliable Delivery**: Strict stop-and-wait ARQ (Automatic Repeat Request) with configurable timeouts and retries.
- **Error Detection**: Robust CRC-16/CCITT_FALSE validation for every frame.
- **Robust Framing**: Uses COBS (Consistent Overhead Byte Stuffing) for zero-byte-free encoding, ensuring unambiguous frame delimiting via `0x00`.
- **Message Fragmentation**: Automatically handles messages larger than the physical MTU (up to 8KB+ reassembly).
- **Connection Handshake**: Built-in capability negotiation and sequence synchronization.
- **Simple API**: Clean `send()` and `read()` interface that abstracts away the complexity of serial framing and retransmission.

## Installation

URST requires Python 3.12 or later and `pyserial`.

### Using `uv` (Recommended)

```bash
uv add urst
```

### Using `pip`

```bash
pip install urst
```

## Quick Start

```python
from urst import Urst
import logging

# Configure logging to see protocol activity [Optional]
logging.basicConfig(level=logging.INFO)

# Initialize URST on your serial port
transport = Urst(port="/dev/ttyUSB0", baud=57600, timeout=1.0)

# Send a message (automatically handles framing, CRC, and ACK waiting)
transport.send(b"Hello, URST Device!")

# Read a complete message (handles reassembly of fragments)
message = transport.read()
if message:
    print(f"Received: {message.decode()}")
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
│    Codec Layer (Encoding/IO)      │  COBS, CRC, UART
└───────────────────────────────────┘
```

For full technical details, please refer to the [URST Specification](URST-Specification.md).

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

### Setup

```bash
git clone https://github.com/simonl65/urst-py.git
cd urst-py
uv sync
```

### Running Tests

```bash
uv run pytest
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
