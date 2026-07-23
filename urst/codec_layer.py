try:
    import logging
except ImportError:
    from . import logging

try:
    import micropython
except ImportError:
    # CPython shim — decorators are no-ops on desktop
    class _MpShim:
        @staticmethod
        def native(fn):
            return fn
        @staticmethod
        def viper(fn):
            return fn
    micropython = _MpShim()  # type: ignore

# MicroPython compatibility for typing
try:
    from typing import Any
except ImportError:
    # Minimal fallback for MicroPython
    pass

# MicroPython compatibility for time
import time

try:
    _ = time.ticks_ms
except AttributeError:
    # Desktop Python shim
    def ticks_ms():
        return int(time.time() * 1000)

    def ticks_diff(later, earlier):
        return later - earlier

    time.ticks_ms = ticks_ms
    time.ticks_diff = ticks_diff

logger = logging.getLogger(__name__)


# CRC16/CCITT-FALSE lookup table — built once at import time.
# Poly: 0x1021, Init: 0xFFFF, no final XOR, MSB-first.
# Replaces the inner range(8) bit-loop with a single table lookup per byte.
def _build_crc16_table():
    table = bytearray(512)  # 256 × 2-byte entries, stored big-endian pairs
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = (crc << 1 ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table[i * 2]     = (crc >> 8) & 0xFF
        table[i * 2 + 1] = crc & 0xFF
    return bytes(table)

_CRC16_TABLE = _build_crc16_table()


@micropython.native
def calculate_crc16(data: bytes | bytearray) -> int:
    """
    Calculate the CRC16/CCITT-FALSE for the given data.

    Poly: 0x1021, Init: 0xFFFF, No final XOR, MSB-first.
    Uses a 256-entry lookup table to eliminate the inner bit-loop.
    """
    tbl = _CRC16_TABLE
    crc = 0xFFFF
    for byte in data:
        idx = ((crc >> 8) ^ byte) & 0xFF
        crc = ((crc << 8) ^ (tbl[idx * 2] << 8) ^ tbl[idx * 2 + 1]) & 0xFFFF
    return crc


def serialize_crc(crc):
    """Serialize CRC into 2 bytes, little-endian (no struct dependency)."""
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


@micropython.native
def cobs_encode(data: bytes | bytearray) -> bytes:
    """
    Encode data using Consistent Overhead Byte Stuffing (COBS).
    """
    output = bytearray()
    code_index = 0
    code = 1
    output.append(0)

    for byte in data:
        if byte == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
            continue

        output.append(byte)
        code += 1

        if code == 0xFF:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1

    output[code_index] = code
    return bytes(output)


@micropython.native
def cobs_decode(data: bytes | bytearray) -> bytes | None:
    """
    Decode COBS-encoded data.

    Uses a pre-allocated output buffer (worst case == input length) to avoid
    per-block heap allocations from bytearray.extend() slices.
    """
    size = len(data)
    if size == 0:
        return None
    if 0x00 in data:
        return None

    # Worst-case decoded length equals input length (no overhead bytes removed
    # could exceed the input size).
    output = bytearray(size)
    write = 0
    index = 0

    while index < size:
        code = data[index]
        if code == 0:
            return None

        index += 1
        end = index + code - 1
        if end > size:
            return None

        # Copy the block bytes directly into the pre-allocated buffer.
        block_len = end - index
        output[write : write + block_len] = data[index:end]
        write += block_len
        index = end

        if code < 0xFF and index < size:
            output[write] = 0x00
            write += 1

    return bytes(output[:write])


class CodecLayer:
    """
    Handles encoding and decoding of URST packets at the byte level.
    """

    def __init__(self, ser: Any):
        self.ser = ser
        self._rx_buffer = bytearray()
        logger.debug("Initializing Codec Layer")

    def write_frame(self, frame: bytes) -> int:
        """
        Write a complete physical frame to the serial port.
        """
        sent = self.ser.write(frame)
        if hasattr(self.ser, "flush"):
            self.ser.flush()
        return sent

    def read_frame(self, timeout_ms: int = 1000) -> bytes | None:
        """
        Read from serial until a complete frame is found (between two 0x00 delimiters).
        """
        start_time = time.ticks_ms()

        while time.ticks_diff(time.ticks_ms(), start_time) < timeout_ms:
            # Check if we already have a frame in the buffer
            if b"\x00" in self._rx_buffer:
                # Find the first 0x00
                first_zero = self._rx_buffer.find(b"\x00")

                # If there's another 0x00 after it, we might have a frame
                second_zero = self._rx_buffer.find(b"\x00", first_zero + 1)
                if second_zero != -1:
                    # Extract the frame
                    frame = bytes(self._rx_buffer[first_zero : second_zero + 1])
                    # Remove from buffer by reassigning to remaining slice
                    self._rx_buffer = self._rx_buffer[second_zero + 1 :]

                    # If it's just two 0x00s with nothing between, it's not a valid frame
                    if len(frame) <= 2:
                        continue
                    return frame

                # If no second zero, but we have a lot of junk before first zero, clear it
                if first_zero > 0:
                    self._rx_buffer = self._rx_buffer[first_zero:]

            # Read more data
            if hasattr(self.ser, "in_waiting"):
                bytes_to_read = max(1, self.ser.in_waiting)
            elif hasattr(self.ser, "any"):
                bytes_to_read = max(1, self.ser.any())
            else:
                bytes_to_read = 1

            data = self.ser.read(bytes_to_read)
            if data:
                self._rx_buffer.extend(data)
            else:
                time.sleep(0.01)  # Avoid busy waiting

        return None
