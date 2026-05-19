import logging
import struct
import time
from typing import Any

from . import constants
from .codec_layer import (
    calculate_crc16,
    cobs_decode,
    cobs_encode,
    serialize_crc,
)

logger = logging.getLogger(__name__)

_VALID_FRAME_TYPES = {
    constants.FRAME_DATA,
    constants.FRAME_ACK,
    constants.FRAME_NAK,
    constants.FRAME_FRAG,
    constants.FRAME_CONNECT,
    constants.FRAME_CONNECT_ACK,
    constants.FRAME_ERROR,
    constants.FRAME_ABORT,
    constants.FRAME_BUSY,
    constants.FRAME_READY,
}


def _is_empty_payload_only_type(frame_type: int) -> bool:
    return frame_type in {
        constants.FRAME_ACK,
        constants.FRAME_NAK,
        constants.FRAME_BUSY,
        constants.FRAME_READY,
    }


def build_frame(frame_type: int, seq: int, payload: bytes = b"") -> bytes:
    """
    Build a complete physical frame (delimiter + COBS + delimiter).

    Order: Header (type, seq) -> Payload -> CRC -> COBS -> Delimiters.
    """
    if frame_type not in _VALID_FRAME_TYPES:
        raise ValueError(f"Unknown frame type: {frame_type}")
    if not 0 <= seq <= 0xFF:
        raise ValueError("Sequence number must be in range 0..255")

    logical = bytes([frame_type, seq]) + payload
    crc = calculate_crc16(logical)
    with_crc = logical + serialize_crc(crc)
    encoded = cobs_encode(with_crc)
    delimiter = bytes([constants.FRAME_DELIMITER])
    return delimiter + encoded + delimiter


def parse_frame(raw: bytes) -> dict | None:
    """
    Strips delimiters, COBS-decodes, validates CRC, and parses the header.

    Returns:
        {'type': int, 'seq': int, 'payload': bytes} or None on failure.
    """
    if len(raw) < 3:
        return None

    delimiter = constants.FRAME_DELIMITER
    if raw[0] != delimiter or raw[-1] != delimiter:
        return None

    encoded = raw[1:-1]
    decoded = cobs_decode(encoded)
    if decoded is None or len(decoded) < 4:
        return None

    payload_with_header = decoded[:-2]
    received_crc = int.from_bytes(decoded[-2:], byteorder="little")
    expected_crc = calculate_crc16(payload_with_header)
    if received_crc != expected_crc:
        return None

    frame_type = payload_with_header[0]
    seq = payload_with_header[1]
    payload = payload_with_header[2:]

    if frame_type not in _VALID_FRAME_TYPES:
        return None
    if len(payload) > constants.MAX_PAYLOAD_SIZE and len(payload) != 252 and frame_type == constants.FRAME_DATA:
        return None
    if _is_empty_payload_only_type(frame_type) and payload:
        return None

    return {"type": frame_type, "seq": seq, "payload": payload}


class ProtocolLayer:
    """
    Handles the URST protocol logic, including sequence management and reliable delivery.
    """

    def __init__(self, codec: Any):
        self.codec = codec
        self.next_send_seq = 0
        self.expected_recv_seq = 0
        self.last_received_seq = -1
        self.is_connected = False
        self._recv_queue: list[dict] = []
        logger.debug("Initializing Protocol Layer")

    def connect(self) -> bool:
        """Perform the CONNECT handshake with retries (§5.6)."""
        payload = struct.pack("<BHBBHBB", 4, 8192, 32, 1, 1000, 3, 0)
        for attempt in range(constants.MAX_RETRIES + 1):
            logger.debug(f"Handshake attempt {attempt + 1}")
            self.codec.write_frame(build_frame(constants.FRAME_CONNECT, 0, payload))
            # Handshake must not use the queue as it needs fresh response
            resp = self.codec.read_frame(constants.ACK_TIMEOUT_MS)
            if resp:
                p = parse_frame(resp)
                if p and p["type"] == constants.FRAME_CONNECT_ACK:
                    self.next_send_seq, self.expected_recv_seq, self.last_received_seq = 0, 0, -1
                    self.is_connected = True
                    logger.debug("URST Connected (received CONNECT_ACK)")
                    return True
                if p and p["type"] == constants.FRAME_CONNECT:
                    self.codec.write_frame(build_frame(constants.FRAME_CONNECT_ACK, p["seq"], payload))
                    self.next_send_seq, self.expected_recv_seq, self.last_received_seq = 0, 0, -1
                    self.is_connected = True
                    logger.debug("URST Connected (simultaneous CONNECT resolved)")
                    return True
                if p:
                    logger.warning(f"Unexpected frame during handshake: {p['type']}")
            else:
                logger.warning("Handshake timeout")
        return False

    def send_reliable(self, frame_type: int, payload: bytes) -> bool:
        """Send a frame reliably using stop-and-wait (§5.1.1)."""
        if not self.is_connected and frame_type != constants.FRAME_CONNECT and not self.connect():
            logger.error("Failed to establish connection before sending")
            return False

        seq = self.next_send_seq
        frame = build_frame(frame_type, seq, payload)

        for attempt in range(constants.MAX_RETRIES + 1):
            logger.debug(f"Sending frame type {frame_type:#x}, seq {seq}, attempt {attempt + 1}")
            self.codec.write_frame(frame)

            start_wait = time.time()
            while (time.time() - start_wait) < (constants.ACK_TIMEOUT_MS / 1000.0):
                # Read fresh frames only, bypassing the queue
                p = self.receive_frame(timeout_ms=100, use_queue=False)
                if p:
                    if p["type"] == constants.FRAME_ACK and p["seq"] == seq:
                        self.next_send_seq = (self.next_send_seq + 1) & 0xFF
                        logger.debug(f"Received ACK for seq {seq}")
                        return True
                    if p["type"] == constants.FRAME_NAK and p["seq"] == seq:
                        logger.warning(f"Received NAK for seq {seq}, retrying...")
                        break

                    # If it's a payload frame, it's already been ACKed by receive_frame.
                    # We must queue it so Urst.read() can find it later.
                    if p["type"] in {constants.FRAME_DATA, constants.FRAME_FRAG}:
                        logger.debug(f"Queuing payload frame type {p['type']} received during wait")
                        self._recv_queue.append(p)
            else:
                logger.warning(f"Timeout waiting for ACK for seq {seq}")

        return False

    def receive_frame(self, timeout_ms: int | None = None, use_queue: bool = True) -> dict | None:
        """Receive a frame and handle ACKs/seq checks (§5.1.2, §5.6.2)."""
        if use_queue and self._recv_queue:
            return self._recv_queue.pop(0)

        if timeout_ms is None:
            timeout_ms = constants.ACK_TIMEOUT_MS

        raw = self.codec.read_frame(timeout_ms)
        if not raw:
            return None
        p = parse_frame(raw)
        if not p:
            return None
        ft, seq = p["type"], p["seq"]

        if ft in {constants.FRAME_ACK, constants.FRAME_NAK}:
            return p

        if ft in {constants.FRAME_DATA, constants.FRAME_FRAG, constants.FRAME_CONNECT}:
            if ft == constants.FRAME_CONNECT or seq == self.expected_recv_seq:
                if ft == constants.FRAME_CONNECT:
                    payload = struct.pack("<BHBBHBB", 4, 8192, 32, 1, 1000, 3, 0)
                    self.codec.write_frame(build_frame(constants.FRAME_CONNECT_ACK, seq, payload))
                    self.next_send_seq, self.expected_recv_seq, self.last_received_seq = 0, 0, -1
                    self.is_connected = True
                    return p
                self.codec.write_frame(build_frame(constants.FRAME_ACK, seq))
                self.last_received_seq, self.expected_recv_seq = seq, (self.expected_recv_seq + 1) & 0xFF
                return p
            if seq == self.last_received_seq:
                self.codec.write_frame(build_frame(constants.FRAME_ACK, seq))
                return None
            self.codec.write_frame(build_frame(constants.FRAME_NAK, seq))
        return p
