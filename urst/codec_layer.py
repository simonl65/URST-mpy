try:
    import logging
except ImportError:
    from . import logging

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


def calculate_crc16(data: bytes | bytearray) -> int:
    """
    Calculate the CRC16/CCITT-FALSE for the given data.

    Poly: 0x1021, Init: 0xFFFF, No final XOR, MSB-first.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1 ^ 4129) & 65535 if crc & 32768 else crc << 1 & 65535
    return crc


def serialize_crc(crc: int) -> bytes:
    """
    Serialize the CRC into 2 bytes, little-endian.
    """
    import struct

    return struct.pack("<H", crc)


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


def cobs_decode(data: bytes | bytearray) -> bytes | None:
    """
    Decode COBS-encoded data.
    """
    if len(data) == 0:
        return None
    if 0x00 in data:
        return None

    output = bytearray()
    index = 0
    size = len(data)

    while index < size:
        code = data[index]
        if code == 0:
            return None

        index += 1
        end = index + code - 1
        if end > size:
            return None

        output.extend(data[index:end])
        index = end

        if code < 0xFF and index < size:
            output.append(0x00)

    return bytes(output)


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
                    # Remove from buffer
                    del self._rx_buffer[: second_zero + 1]

                    # If it's just two 0x00s with nothing between, it's not a valid frame
                    if len(frame) <= 2:
                        continue
                    return frame

                # If no second zero, but we have a lot of junk before first zero, clear it
                if first_zero > 0:
                    del self._rx_buffer[:first_zero]

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
