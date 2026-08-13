try:
    import logging
except ImportError:
    from . import logging

import struct

# MicroPython compatibility for typing
try:  # noqa: SIM105
    from typing import Any
except ImportError:
    # Minimal fallback for MicroPython
    pass

# MicroPython compatibility for time
import time  # noqa: E402

try:
    _ = time.ticks_ms  # type: ignore
except AttributeError:
    # Desktop Python shim
    def ticks_ms():
        return int(time.time() * 1000)

    def ticks_diff(later, earlier):
        return later - earlier

    time.ticks_ms = ticks_ms  # type: ignore
    time.ticks_diff = ticks_diff  # type: ignore

from collections import deque  # noqa: E402

from . import constants  # noqa: E402
from .codec_layer import (  # noqa: E402
    calculate_crc16,
    cobs_decode,
    cobs_encode,
    serialize_crc,
)

logger = logging.getLogger(__name__)

# Pre-computed CONNECT capability payload (fixed protocol constants).
# Hoisted to avoid re-running struct.pack on every handshake call.
_CONNECT_PAYLOAD = struct.pack(
    "<BHBBHBB", constants.PROTOCOL_VERSION, 8192, 32, 1, 1000, 3, 0
)

# Module-level tuples avoid re-allocating set objects on each membership test.
# Using tuple for MicroPython portability (frozenset availability varies by port).
_VALID_FRAME_TYPES = (
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
)
_EMPTY_PAYLOAD_TYPES = (
    constants.FRAME_ACK,
    constants.FRAME_NAK,
    constants.FRAME_BUSY,
    constants.FRAME_READY,
)
_DATA_FRAG_TYPES = (constants.FRAME_DATA, constants.FRAME_FRAG)
_PAYLOAD_FRAME_TYPES = (
    constants.FRAME_DATA,
    constants.FRAME_FRAG,
    constants.FRAME_CONNECT,
)


def _is_empty_payload_only_type(frame_type):
    return frame_type in _EMPTY_PAYLOAD_TYPES


def build_frame(
    frame_type: int, seq: int, payload: bytes = b"", *, request_id: int = 0
) -> bytes:
    """
    Build a complete physical frame (delimiter + COBS + delimiter).

    Order: Header (type, seq, request_id) -> Payload -> CRC -> COBS -> Delimiters.

    `request_id` (§3.2.3, §5.8) correlates a request/response exchange;
    it is orthogonal to `seq` (frame-level ACK/NAK) and to a FRAG
    payload's own Message ID (fragmentation-only, §6.2). Callers not
    doing request/response correlation may leave it at the default 0.
    """
    if frame_type not in _VALID_FRAME_TYPES:
        raise ValueError(f"Unknown frame type: {frame_type}")
    if not 0 <= seq <= 0xFF:
        raise ValueError("Sequence number must be in range 0..255")
    if not 0 <= request_id <= 0xFF:
        raise ValueError("Request ID must be in range 0..255")

    logical = bytes([frame_type, seq, request_id]) + payload
    crc = calculate_crc16(logical)
    with_crc = logical + serialize_crc(crc)
    encoded = cobs_encode(with_crc)
    delimiter = bytes([constants.FRAME_DELIMITER])
    return delimiter + encoded + delimiter


def parse_frame(raw: bytes) -> dict | None:
    """
    Strips delimiters, COBS-decodes, validates CRC, and parses the header.

    Returns:
        {'type': int, 'seq': int, 'request_id': int, 'payload': bytes}
        or None on failure.
    """
    if len(raw) < 3:
        return None

    delimiter = constants.FRAME_DELIMITER
    if raw[0] != delimiter or raw[-1] != delimiter:
        return None

    encoded = raw[1:-1]
    decoded = cobs_decode(encoded)
    if decoded is None or len(decoded) < 5:
        return None

    payload_with_header = decoded[:-2]
    received_crc = int.from_bytes(decoded[-2:], "little")
    expected_crc = calculate_crc16(payload_with_header)
    if received_crc != expected_crc:
        return None

    frame_type = payload_with_header[0]
    seq = payload_with_header[1]
    request_id = payload_with_header[2]
    payload = payload_with_header[3:]

    if frame_type not in _VALID_FRAME_TYPES:
        return None
    if (
        len(payload) > constants.MAX_PAYLOAD_SIZE
        and len(payload) != 252
        and frame_type == constants.FRAME_DATA
    ):
        return None
    if _is_empty_payload_only_type(frame_type) and payload:
        return None

    return {
        "type": frame_type,
        "seq": seq,
        "request_id": request_id,
        "payload": payload,
    }


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
        self._recv_queue = deque(
            (), constants.MAX_FRAGMENTS
        )  # O(1) popleft on MicroPython
        # Set by send_reliable() when a CONNECT from the peer abandons the
        # in-flight send (§5.6.2 extension, pending spec text). Distinct
        # from a plain False return so core_handler.send() knows not to
        # send ABORT for a message the peer never asked about (§5.7.2 --
        # ABORT belongs to a session that no longer exists).
        self.session_reset_during_send = False
        # Set every time _reset_session_state() runs (any CONNECT that
        # resets seq numbering, as either recipient or initiator).
        # ProtocolLayer only owns seq state; Urst owns reassembly buffers
        # and Request ID bookkeeping (§5.8.4) one layer up, so it can't
        # clear those itself (US-104). Urst checks and clears this flag
        # after receive_frame() to keep its own state in sync.
        self.session_reset_pending = False
        logger.debug("Initializing Protocol Layer")

    def _reset_session_state(self) -> None:
        """Reset seq numbering after a CONNECT (§5.6.2), as recipient or
        initiator. Flags `session_reset_pending` for the layer above."""
        self.next_send_seq = 0
        self.expected_recv_seq = 0
        self.last_received_seq = -1
        self.is_connected = True
        self.session_reset_pending = True

    def _peer_version_ok(self, payload: bytes, context: str) -> bool:
        """Check a CONNECT/CONNECT_ACK capability payload's version (§5.6.1.1).

        Nothing else in the protocol catches a header-layout mismatch: the
        CRC covers the same byte range whichever layout each side assumes,
        so every frame passes CRC and only the *interpretation* of byte 2
        differs. Without this check a mismatch looks like a successful
        handshake followed by DATA that is never ACKed -- i.e. exactly
        like a bad link, which is how it was originally misdiagnosed.

        A payload too short to carry a version is a mismatch, not a
        legacy peer to accommodate: pre-5 peers are wire-incompatible.
        """
        if not payload:
            logger.error(
                f"{context}: peer sent no capability payload; cannot verify "
                f"protocol version (local v{constants.PROTOCOL_VERSION}) -- "
                "refusing connection (§5.6.1.1)"
            )
            return False
        peer_version = payload[0]
        if peer_version != constants.PROTOCOL_VERSION:
            logger.error(
                f"{context}: incompatible URST protocol version -- peer "
                f"advertises v{peer_version}, local is "
                f"v{constants.PROTOCOL_VERSION}. The frame header layout "
                "differs, so CRC cannot detect this; refusing connection "
                "rather than silently misparsing traffic (§5.6.1.1). "
                "Upgrade both ends to the same URST version."
            )
            return False
        return True

    def connect(self) -> bool:
        """Perform the CONNECT handshake with retries (§5.6).

        Drains any bytes already buffered on the transport first: a new
        session (e.g. a fresh CLI process against a long-lived relay/PTY)
        must not inherit frames left over from a previous session -- see
        URST-mpy#4 and CHANGELOG.md. Only done once, before the first
        attempt; retries within this same call keep listening normally
        for the handshake response.
        """
        self.codec.discard_buffered()
        payload = _CONNECT_PAYLOAD
        for attempt in range(constants.MAX_RETRIES + 1):
            logger.debug(f"Handshake attempt {attempt + 1}")
            self.codec.write_frame(
                build_frame(constants.FRAME_CONNECT, 0, payload)
            )
            # Handshake must not use the queue as it needs fresh response
            resp = self.codec.read_frame(constants.ACK_TIMEOUT_MS)
            if resp:
                p = parse_frame(resp)
                if p and p["type"] == constants.FRAME_CONNECT_ACK:
                    if not self._peer_version_ok(
                        p["payload"], "CONNECT_ACK received"
                    ):
                        # Retrying cannot fix a version mismatch, and the
                        # peer is not going to change its mind: fail now.
                        return False
                    self._reset_session_state()
                    logger.debug("URST Connected (received CONNECT_ACK)")
                    return True
                if p and p["type"] == constants.FRAME_CONNECT:
                    if not self._peer_version_ok(
                        p["payload"], "simultaneous CONNECT"
                    ):
                        self.send_error(
                            p["request_id"],
                            constants.ERROR_INCOMPATIBLE_VERSION,
                            f"protocol v{constants.PROTOCOL_VERSION} != "
                            f"peer v{p['payload'][0] if p['payload'] else '?'}",
                        )
                        return False
                    self.codec.write_frame(
                        build_frame(
                            constants.FRAME_CONNECT_ACK, p["seq"], payload
                        )
                    )
                    self._reset_session_state()
                    logger.debug(
                        "URST Connected (simultaneous CONNECT resolved)"
                    )
                    return True
                if p:
                    logger.warning(
                        f"Unexpected frame during handshake: {p['type']}"
                    )
            else:
                logger.warning("Handshake timeout")
        return False

    def send_reliable(
        self, frame_type: int, payload: bytes, request_id: int = 0
    ) -> bool:
        """Send a frame reliably using stop-and-wait (§5.1.1).

        `request_id` (§3.2.3) is carried unchanged across all retries of
        this frame, matching a fragmented send's requirement to use the
        same Request ID for every fragment (§5.8.2).
        """
        if (
            not self.is_connected
            and frame_type != constants.FRAME_CONNECT
            and not self.connect()
        ):
            logger.error("Failed to establish connection before sending")
            return False

        self.session_reset_during_send = False
        seq = self.next_send_seq
        frame = build_frame(frame_type, seq, payload, request_id=request_id)

        for attempt in range(constants.MAX_RETRIES + 1):
            logger.debug(
                f"Sending frame type {frame_type:#x}, seq {seq}, attempt {attempt + 1}"
            )
            self.codec.write_frame(frame)

            start_wait = time.ticks_ms()  # type: ignore
            while (
                time.ticks_diff(time.ticks_ms(), start_wait)
                < constants.ACK_TIMEOUT_MS
            ):  # type: ignore
                # Read fresh frames only, bypassing the queue
                p = self.receive_frame(timeout_ms=100, use_queue=False)
                if p:
                    if p["type"] == constants.FRAME_ACK and p["seq"] == seq:
                        self.next_send_seq = (self.next_send_seq + 1) & 0xFF
                        logger.debug(f"Received ACK for seq {seq}")
                        return True
                    if p["type"] == constants.FRAME_NAK and p["seq"] == seq:
                        # A NAK for the frame we just sent means the peer's
                        # sequence state disagrees with ours despite strict
                        # stop-and-wait -- i.e. desynchronization (§5.3.3).
                        # Retransmitting the identical frame can never
                        # resolve that; §5.6.2 requires re-establishing the
                        # connection (which resets both sides' sequence
                        # numbers) before retrying.
                        logger.warning(
                            f"Received NAK for seq {seq}: peer is "
                            "desynchronized, re-establishing connection "
                            "before retrying (§5.3.3)"
                        )
                        if not self.connect():
                            logger.error(
                                "Failed to re-establish connection after NAK"
                            )
                            return False
                        seq = self.next_send_seq
                        frame = build_frame(
                            frame_type, seq, payload, request_id=request_id
                        )
                        break

                    if p["type"] == constants.FRAME_CONNECT:
                        # receive_frame() already answered this CONNECT
                        # (CONNECT_ACK sent, seq state reset, is_connected
                        # left True) -- the peer has moved on to a new
                        # session that shares none of our seq numbering.
                        # Retrying is pointless (frames land as unsolicited
                        # fragments in the new session) and ABORT is worse
                        # (it would reference a message the new session
                        # never asked about, poisoning it in turn). Give up
                        # immediately rather than exhausting retries.
                        logger.warning(
                            f"CONNECT received while awaiting ACK for seq "
                            f"{seq} -- peer already reset the session; "
                            "abandoning this send without retrying or "
                            "sending ABORT (§5.6.2 extension)"
                        )
                        self.session_reset_during_send = True
                        return False

                    # If it's a payload frame, it's already been ACKed by receive_frame.
                    # We must queue it so Urst.read() can find it later.
                    if p["type"] in _DATA_FRAG_TYPES:
                        logger.debug(
                            f"Queuing payload frame type {p['type']} received during wait"
                        )
                        self._recv_queue.append(p)
            else:
                logger.warning(f"Timeout waiting for ACK for seq {seq}")

        return False

    def receive_frame(
        self, timeout_ms: int | None = None, use_queue: bool = True
    ) -> dict | None:
        """Receive a frame and handle ACKs/seq checks (§5.1.2, §5.6.2)."""
        if use_queue and self._recv_queue:
            return self._recv_queue.popleft()

        if timeout_ms is None:
            timeout_ms = constants.ACK_TIMEOUT_MS

        raw = self.codec.read_frame(timeout_ms)
        if not raw:
            return None
        p = parse_frame(raw)
        if not p:
            return None
        ft, seq = p["type"], p["seq"]

        if ft == constants.FRAME_ACK or ft == constants.FRAME_NAK:
            return p

        if ft in _PAYLOAD_FRAME_TYPES:
            if ft == constants.FRAME_CONNECT or seq == self.expected_recv_seq:
                if ft == constants.FRAME_CONNECT:
                    if not self._peer_version_ok(
                        p["payload"], "CONNECT received"
                    ):
                        # Best-effort diagnostic only: this ERROR is framed
                        # with OUR header layout, which is precisely what
                        # the peer disagrees about, so it may well be
                        # unparseable at the far end (§5.6.1.1).
                        self.send_error(
                            p["request_id"],
                            constants.ERROR_INCOMPATIBLE_VERSION,
                            f"protocol v{constants.PROTOCOL_VERSION} != "
                            f"peer v{p['payload'][0] if p['payload'] else '?'}",
                        )
                        return None
                    payload = _CONNECT_PAYLOAD
                    self.codec.write_frame(
                        build_frame(constants.FRAME_CONNECT_ACK, seq, payload)
                    )
                    self._reset_session_state()
                    return p
                self.codec.write_frame(
                    build_frame(
                        constants.FRAME_ACK, seq, request_id=p["request_id"]
                    )
                )
                self.last_received_seq, self.expected_recv_seq = (
                    seq,
                    (self.expected_recv_seq + 1) & 0xFF,
                )
                return p
            if seq == self.last_received_seq:
                self.codec.write_frame(
                    build_frame(
                        constants.FRAME_ACK, seq, request_id=p["request_id"]
                    )
                )
                return None
            self.codec.write_frame(
                build_frame(
                    constants.FRAME_NAK, seq, request_id=p["request_id"]
                )
            )
        return p

    def send_abort(
        self, message_id: int, request_id: int = 0, reason_code: int = 0
    ) -> None:
        """Send ABORT for `message_id` (§5.7.2). Not acknowledged; best-effort."""
        payload = bytes([reason_code, message_id])
        self.codec.write_frame(
            build_frame(
                constants.FRAME_ABORT,
                self.next_send_seq,
                payload,
                request_id=request_id,
            )
        )

    def send_error(
        self, request_id: int, error_code: int, text: str = ""
    ) -> None:
        """Send ERROR (§5.7.1), e.g. CAPABILITY_EXCEEDED. Not acknowledged."""
        text_bytes = text.encode("utf-8")[:249]
        payload = bytes([error_code, 0, 0, 0, len(text_bytes)]) + text_bytes
        self.codec.write_frame(
            build_frame(
                constants.FRAME_ERROR,
                self.next_send_seq,
                payload,
                request_id=request_id,
            )
        )
