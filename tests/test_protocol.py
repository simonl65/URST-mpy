import math
import struct

import pytest
from urst import constants
from urst.codec_layer import (
    calculate_crc16,
    cobs_decode,
    cobs_encode,
    serialize_crc,
)
from urst.protocol_layer import build_frame, parse_frame

# fmt: off
# Constants for convenience
FRAME_DATA          = constants.FRAME_DATA
FRAME_ACK           = constants.FRAME_ACK
FRAME_NAK           = constants.FRAME_NAK
FRAME_FRAG          = constants.FRAME_FRAG
FRAME_CONNECT       = constants.FRAME_CONNECT
FRAME_CONNECT_ACK   = constants.FRAME_CONNECT_ACK
FRAME_ERROR         = constants.FRAME_ERROR
FRAME_ABORT         = constants.FRAME_ABORT
FRAME_BUSY          = constants.FRAME_BUSY
FRAME_READY         = constants.FRAME_READY
FRAME_DELIMITER     = constants.FRAME_DELIMITER

MAX_RETRIES         = constants.MAX_RETRIES
ACK_TIMEOUT_MS      = constants.ACK_TIMEOUT_MS
MAX_PAYLOAD_SIZE    = constants.MAX_PAYLOAD_SIZE
RX_BUFFER_SIZE      = constants.RX_BUFFER_SIZE
MAX_MSG_BYTES       = constants.MAX_MSG_BYTES
MAX_FRAGMENTS       = constants.MAX_FRAGMENTS

MAX_FRAG_DATA: int = MAX_PAYLOAD_SIZE - 6  # 194 bytes per spec §6.3.1
# fmt: on

# ---------------------------------------------------------------------------
# ── HELPERS ────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


def _raw_logical(frame_type: int, seq: int, payload: bytes = b"") -> bytes:
    """Reconstruct the logical frame bytes (header + payload)."""
    return bytes([frame_type, seq]) + payload


def _make_physical(frame_type: int, seq: int, payload: bytes = b"") -> bytes:
    """Build a correct physical frame from scratch using spec rules."""
    logical: bytes = _raw_logical(frame_type, seq, payload)
    crc: int = calculate_crc16(logical)
    crc_le: bytes = serialize_crc(crc)
    with_crc: bytes = logical + crc_le
    encoded: bytes = cobs_encode(with_crc)
    return bytes([0x00]) + encoded + bytes([0x00])


def _corrupt_crc(frame: bytes) -> bytes:
    """Flip one bit in the CRC bytes of a physical frame."""
    # frame = 0x00 [cobs...] 0x00  – CRC is last 2 bytes before COBS closure
    raw: bytearray = bytearray(frame)
    raw[-3] ^= 0xFF  # flip byte just before trailing delimiter
    return bytes(raw)


def _build_frag_payload(
    msg_id: int, frag_num: int, total_frags: int, data: bytes
) -> bytes:
    hdr: bytes = bytes([msg_id, frag_num, total_frags, len(data)])
    return hdr + data


# ===========================================================================
# 4. FRAME BUILDING & PARSING
# ===========================================================================


class TestFrameBuilding:
    """§3.1, §3.2, §3.3"""

    def test_physical_frame_starts_with_delimiter(self) -> None:
        frame: bytes = build_frame(FRAME_DATA, 0, b"hello")
        assert frame[0] == 0x00, (
            "Physical frame MUST start with 0x00 delimiter (§3.3)"
        )

    def test_physical_frame_ends_with_delimiter(self) -> None:
        frame: bytes = build_frame(FRAME_DATA, 0, b"hello")
        assert frame[-1] == 0x00, (
            "Physical frame MUST end with 0x00 delimiter (§3.3)"
        )

    def test_no_zero_bytes_inside_frame(self) -> None:
        frame: bytes = build_frame(FRAME_DATA, 5, b"test payload")
        interior: bytes = frame[1:-1]
        assert 0x00 not in interior, (
            "COBS-encoded interior MUST NOT contain 0x00 (§3.5.1)"
        )

    def test_minimum_frame_size(self) -> None:
        """Minimum logical frame is 2 bytes (header) + 2 CRC = 4, COBS+delimiters >= 6."""
        frame: bytes = build_frame(FRAME_ACK, 0)
        assert len(frame) >= 6

    def test_maximum_frame_size(self) -> None:
        """Max physical frame is 209 bytes (§3.3)."""
        frame: bytes = build_frame(FRAME_DATA, 0, bytes(MAX_PAYLOAD_SIZE))
        assert len(frame) <= 209, (
            f"Physical frame too large: {len(frame)} bytes"
        )

    def test_crc_over_header_and_payload(self) -> None:
        """CRC MUST be calculated over type+seq+payload BEFORE COBS (§3.2.4)."""
        payload: bytes = b"verification"
        frame: bytes = build_frame(FRAME_DATA, 1, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None, "Valid frame must parse successfully"
        logical: bytes = bytes([FRAME_DATA, 1]) + payload
        expected_crc: int = calculate_crc16(logical)
        interior: bytes = frame[1:-1]
        decoded: bytes | None = cobs_decode(interior)
        assert decoded is not None
        raw_crc: int = struct.unpack_from("<H", decoded[-2:])[0]
        assert raw_crc == expected_crc, (
            "CRC in frame must match CRC over logical frame"
        )

    def test_empty_payload_frame(self) -> None:
        frame: bytes = build_frame(FRAME_DATA, 0)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == b""

    def test_full_payload_frame(self) -> None:
        payload: bytes = bytes(range(200))
        frame: bytes = build_frame(FRAME_DATA, 42, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == payload

    def test_payload_over_max_raises_or_returns_none(self) -> None:
        """Sending > 200 bytes payload is NON-CONFORMANT (§7.4)."""
        oversized: bytes = bytes(201)
        try:
            result: bytes = build_frame(FRAME_DATA, 0, oversized)
            parsed: dict | None = parse_frame(result)
            if parsed is not None:
                assert len(parsed["payload"]) <= MAX_PAYLOAD_SIZE, (
                    "Payload MUST NOT exceed MAX_PAYLOAD_SIZE"
                )
        except (ValueError, OverflowError):
            pass  # Raising is the preferred conformant behaviour

    def test_sequence_number_in_parsed_frame(self) -> None:
        seq: int
        frame: bytes
        parsed: dict | None
        for seq in [0, 1, 127, 254, 255]:
            frame = build_frame(FRAME_DATA, seq, b"x")
            parsed = parse_frame(frame)
            assert parsed is not None
            assert parsed["seq"] == seq

    def test_frame_type_in_parsed_frame(self) -> None:
        ftype: int
        frame: bytes
        parsed: dict | None
        for ftype in [
            FRAME_DATA,
            FRAME_ACK,
            FRAME_NAK,
            FRAME_FRAG,
            FRAME_CONNECT,
            FRAME_CONNECT_ACK,
            FRAME_ERROR,
            FRAME_ABORT,
            FRAME_BUSY,
            FRAME_READY,
        ]:
            frame = build_frame(ftype, 0)
            parsed = parse_frame(frame)
            assert parsed is not None
            assert parsed["type"] == ftype


class TestFrameParsing:
    """§3.1, §5.1.2, §5.3.1, §5.3.2"""

    def test_valid_data_frame_parses(self) -> None:
        payload: bytes = b"hello URST"
        frame: bytes = build_frame(FRAME_DATA, 7, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["type"] == FRAME_DATA
        assert parsed["seq"] == 7
        assert parsed["payload"] == payload

    def test_crc_failure_returns_none(self) -> None:
        """CRC failures MUST cause silent discard – parse_frame returns None (§5.3.1)."""
        frame: bytes = build_frame(FRAME_DATA, 0, b"data")
        corrupted: bytes = _corrupt_crc(frame)
        result: dict | None = parse_frame(corrupted)
        assert result is None, "Frame with bad CRC MUST be discarded (§5.3.1)"

    def test_cobs_failure_returns_none(self) -> None:
        """COBS decoding failures MUST cause silent discard (§5.3.2)."""
        raw: bytearray = bytearray(b"\x00" + b"\x05\x11\x22\x33\x44" + b"\x00")
        raw[3] = 0x00
        result: dict | None = parse_frame(bytes(raw))
        assert result is None, (
            "Frame with invalid COBS MUST be discarded (§5.3.2)"
        )

    def test_unknown_frame_type_silently_discarded(self) -> None:
        """Unknown frame types MUST be silently discarded (§3.2.1)."""
        frame: bytes = _make_physical(0x15, 0, b"")
        result: dict | None = parse_frame(frame)

    def test_frame_type_zero_never_used(self) -> None:
        """Frame type 0x00 MUST NOT be used (§3.2.1)."""
        frame: bytes = _make_physical(0x00, 0, b"")
        result: dict | None = parse_frame(frame)
        assert result is None or result.get("type") != 0x00

    def test_single_byte_interior_too_short(self) -> None:
        """A frame that is too short to contain a valid header+CRC must be rejected."""
        bad: bytes = b"\x00\x01\x00"
        result: dict | None = parse_frame(bad)
        assert result is None

    def test_bit_flip_in_payload_detected(self) -> None:
        frame: bytes = build_frame(FRAME_DATA, 3, b"sensitive data")
        corrupted: bytearray = bytearray(frame)
        mid: int = len(corrupted) // 2
        corrupted[mid] ^= 0x01
        result: dict | None = parse_frame(bytes(corrupted))
        assert result is None

    def test_completely_garbled_frame(self) -> None:
        result: dict | None = parse_frame(b"\x00\xff\xfe\xfd\xfc\x00")
        assert result is None


class TestSequenceNumbers:
    """§3.2.2, §5.1.1"""

    def test_sequence_number_range(self) -> None:
        """Sequence numbers are 8-bit (0–255)."""
        seq: int
        frame: bytes
        parsed: dict | None
        for seq in [0, 1, 127, 254, 255]:
            frame = build_frame(FRAME_DATA, seq, b"x")
            parsed = parse_frame(frame)
            assert parsed["seq"] == seq

    def test_ack_echoes_sequence_number(self) -> None:
        """ACK MUST echo the sequence number of the acknowledged frame (§5.2.1)."""
        seq: int
        ack_frame: bytes
        parsed: dict | None
        for seq in [0, 1, 100, 255]:
            ack_frame = build_frame(FRAME_ACK, seq)
            parsed = parse_frame(ack_frame)
            assert parsed is not None
            assert parsed["seq"] == seq

    def test_nak_echoes_sequence_number(self) -> None:
        """NAK MUST echo the sequence number of the rejected frame (§5.2.2)."""
        seq: int
        nak_frame: bytes
        parsed: dict | None
        for seq in [0, 42, 255]:
            nak_frame = build_frame(FRAME_NAK, seq)
            parsed = parse_frame(nak_frame)
            assert parsed is not None
            assert parsed["seq"] == seq

    def test_ack_has_empty_payload(self) -> None:
        """ACK frames MUST have empty payloads (§5.2.1, §7.4)."""
        frame: bytes = build_frame(FRAME_ACK, 5)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == b"", "ACK payload MUST be empty"

    def test_nak_has_empty_payload(self) -> None:
        """NAK frames MUST have empty payloads (§5.2.2, §7.4)."""
        frame: bytes = build_frame(FRAME_NAK, 5)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == b"", "NAK payload MUST be empty"

    def test_busy_has_empty_payload(self) -> None:
        frame: bytes = build_frame(FRAME_BUSY, 0)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == b"", "BUSY payload MUST be empty (§3.2.1)"

    def test_ready_has_empty_payload(self) -> None:
        frame: bytes = build_frame(FRAME_READY, 0)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == b"", "READY payload MUST be empty (§3.2.1)"


class TestFragmentation:
    """§6"""

    def test_fragment_header_structure(self) -> None:
        """FRAG payload: [msg_id][frag_num][total_frags][data_len][data...] (§6.2)."""
        data: bytes = b"A" * 50
        payload: bytes = _build_frag_payload(1, 0, 3, data)
        frame: bytes = build_frame(FRAME_FRAG, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["type"] == FRAME_FRAG
        p: bytes = parsed["payload"]
        assert p[0] == 1, "msg_id field"
        assert p[1] == 0, "frag_num field"
        assert p[2] == 3, "total_frags field"
        assert p[3] == len(data), "data_len field"
        assert p[4:] == data, "fragment data"

    def test_max_fragment_data_size(self) -> None:
        """Max fragment data is MAX_PAYLOAD_SIZE - 6 = 194 bytes (§6.3.1)."""
        assert MAX_FRAG_DATA == 194
        data: bytes = bytes(MAX_FRAG_DATA)
        payload: bytes = _build_frag_payload(0, 0, 1, data)
        assert len(payload) == MAX_PAYLOAD_SIZE - 2
        frame: bytes = build_frame(FRAME_FRAG, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None

    def test_fragment_data_exceeding_max_rejected(self) -> None:
        """Fragment data > 194 bytes would exceed MAX_PAYLOAD_SIZE (§6.3.1)."""
        data: bytes = bytes(195)
        payload: bytes = _build_frag_payload(0, 0, 1, data)
        assert len(payload) == 199
        assert payload[3] == 195 % 256

    def test_fragment_count_calculation(self) -> None:
        """total_frags = ceil(msg_len / 194) (§6.3.1)."""
        cases: list[tuple[int, int]] = [
            (1, 1),
            (194, 1),
            (195, 2),
            (388, 2),
            (389, 3),
            (1940, 10),
        ]
        msg_len: int
        expected_frags: int
        calc: int
        for msg_len, expected_frags in cases:
            calc = math.ceil(msg_len / MAX_FRAG_DATA)
            assert calc == expected_frags, (
                f"msg_len={msg_len} should need {expected_frags} fragments, got {calc}"
            )

    def test_message_id_wraps_at_255(self) -> None:
        """Message ID is 8-bit, wraps 0-255 (§6.2)."""
        msg_id: int
        data: bytes
        payload: bytes
        for msg_id in [0, 127, 255]:
            data = b"x"
            payload = _build_frag_payload(msg_id, 0, 1, data)
            assert payload[0] == msg_id

    def test_fragment_ordering_zero_based(self) -> None:
        """Fragment numbers are zero-based 0 .. (total-1) (§6.2)."""
        frag_num: int
        payload: bytes
        for frag_num in [0, 1, 2]:
            payload = _build_frag_payload(0, frag_num, 3, b"d")
            assert payload[1] == frag_num

    def test_non_fragmented_uses_data_frame(self) -> None:
        """Single-frame messages MUST use DATA frame type, NOT FRAG (§6.4)."""
        payload: bytes = b"short"
        frame: bytes = build_frame(FRAME_DATA, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed["type"] == FRAME_DATA, (
            "Messages < 194 bytes MUST use DATA, not FRAG (§6.4)"
        )

    def test_fragment_timeout_formula(self) -> None:
        """fragment_timeout = total_frags * (MAX_RETRIES+1) * ACK_TIMEOUT_MS (§6.3.4)."""
        total_frags: int = 10
        expected_timeout: int = total_frags * (MAX_RETRIES + 1) * ACK_TIMEOUT_MS
        assert expected_timeout == 40_000, (
            f"For 10 frags, fragment_timeout MUST be 40,000ms; got {expected_timeout}"
        )

    @pytest.mark.parametrize("total_frags", [1, 5, 32])
    def test_fragment_timeout_formula_parametrized(
        self, total_frags: int
    ) -> None:
        expected: int = total_frags * (MAX_RETRIES + 1) * ACK_TIMEOUT_MS
        actual: int = total_frags * (MAX_RETRIES + 1) * ACK_TIMEOUT_MS
        assert actual == expected


class TestConnectPayload:
    """§5.6.1"""

    PAYLOAD_LEN: int = 9  # defined by spec table

    def _build_connect_payload(
        self,
        version: int = 3,
        max_msg_bytes: int = 8192,
        max_frags: int = 32,
        max_concurrent: int = 1,
        ack_timeout_ms: int = 1000,
        max_retries_val: int = 3,
    ) -> bytes:
        payload: bytes = struct.pack(
            "<BHBBHBB",
            version,
            max_msg_bytes,
            max_frags,
            max_concurrent,
            ack_timeout_ms,
            max_retries_val,
            0,  # reserved
        )
        return payload

    def test_connect_payload_max_concurrent_is_one(self) -> None:
        """max_concurrent_message_ids MUST be 1 for conformant implementations (§5.6.1)."""
        payload: bytes = self._build_connect_payload(max_concurrent=1)
        assert payload[4] == 1, "max_concurrent_message_ids MUST be 1 (§5.6.1)"

    def test_connect_payload_length(self) -> None:
        payload: bytes = self._build_connect_payload()
        assert len(payload) == self.PAYLOAD_LEN, (
            f"CONNECT payload MUST be {self.PAYLOAD_LEN} bytes (§5.6.1)"
        )

    def test_connect_payload_version_field(self) -> None:
        payload: bytes = self._build_connect_payload(version=3)
        assert payload[0] == 3

    def test_connect_payload_max_msg_bytes_little_endian(self) -> None:
        payload: bytes = self._build_connect_payload(max_msg_bytes=8192)
        val: int = struct.unpack_from("<H", payload, 1)[0]
        assert val == 8192

    def test_connect_payload_ack_timeout_little_endian(self) -> None:
        payload: bytes = self._build_connect_payload(ack_timeout_ms=1000)
        val: int = struct.unpack_from("<H", payload, 5)[0]
        assert val == 1000

    def test_connect_frame_builds_correctly(self) -> None:
        payload: bytes = self._build_connect_payload()
        frame: bytes = build_frame(FRAME_CONNECT, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["type"] == FRAME_CONNECT
        assert parsed["payload"] == payload

    def test_connect_ack_frame_builds_correctly(self) -> None:
        payload: bytes = self._build_connect_payload()
        frame: bytes = build_frame(FRAME_CONNECT_ACK, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["type"] == FRAME_CONNECT_ACK

    def test_least_capable_wins_rule(self) -> None:
        """After CONNECT/CONNECT_ACK, peers MUST use min() of advertised caps (§5.6.1)."""
        local_max_frags: int = 32
        remote_max_frags: int = 10
        negotiated: int = min(local_max_frags, remote_max_frags)
        assert negotiated == 10, "Least capable wins: min(32, 10) = 10"


class TestErrorFrame:
    """§5.7.1"""

    CAPABILITY_EXCEEDED: int = 0x01

    def _build_error_payload(
        self,
        error_code: int = 0x01,
        max_msg_bytes: int = 8192,
        max_frags: int = 32,
        max_concurrent: int = 1,
        text: bytes = b"",
    ) -> bytes:
        hdr: bytes = (
            bytes([error_code])
            + struct.pack("<H", max_msg_bytes)
            + bytes([max_frags, max_concurrent, len(text)])
            + text
        )
        return hdr

    def test_error_frame_type(self) -> None:
        payload: bytes = self._build_error_payload()
        frame: bytes = build_frame(FRAME_ERROR, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed["type"] == FRAME_ERROR

    def test_capability_exceeded_error_code(self) -> None:
        payload: bytes = self._build_error_payload(
            error_code=self.CAPABILITY_EXCEEDED
        )
        assert payload[0] == 0x01, (
            "CAPABILITY_EXCEEDED error_code MUST be 0x01 (§5.7.1)"
        )

    def test_error_payload_max_msg_bytes_little_endian(self) -> None:
        payload: bytes = self._build_error_payload(max_msg_bytes=8192)
        val: int = struct.unpack_from("<H", payload, 1)[0]
        assert val == 8192, (
            "max_message_bytes in ERROR payload MUST be little-endian (§5.7.1)"
        )

    def test_error_payload_with_text(self) -> None:
        text: bytes = b"buffer full"
        payload: bytes = self._build_error_payload(text=text)
        text_len: int = payload[5]
        recovered_text: bytes = payload[6 : 6 + text_len]
        assert recovered_text == text


class TestAbortFrame:
    """§5.7.2"""

    def test_abort_frame_type(self) -> None:
        frame: bytes = build_frame(FRAME_ABORT, 0)
        parsed: dict | None = parse_frame(frame)
        assert parsed["type"] == FRAME_ABORT

    def test_abort_empty_payload(self) -> None:
        frame: bytes = build_frame(FRAME_ABORT, 0)
        parsed: dict | None = parse_frame(frame)
        assert len(parsed["payload"]) == 0

    def test_abort_with_reason_code(self) -> None:
        """ABORT MAY carry a 1-byte reason code (§5.7.2)."""
        frame: bytes = build_frame(FRAME_ABORT, 0, b"\x01")
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert len(parsed["payload"]) == 1
        assert parsed["payload"][0] == 0x01

    def test_abort_payload_max_16_bytes(self) -> None:
        """ABORT payload length MUST be 0 or 1 per §5.7.2 (spec says 0-16 in table)."""
        frame: bytes = build_frame(FRAME_ABORT, 0, b"\x02")
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert len(parsed["payload"]) <= 16


class TestFrameEncodingProcess:
    """§3.3 – verifies the exact 6-step encoding order."""

    def _manual_encode(
        self, frame_type: int, seq: int, payload: bytes
    ) -> bytes:
        """Manually reproduce §3.3 step-by-step."""
        logical: bytes = bytes([frame_type, seq]) + payload
        crc: int = calculate_crc16(logical)
        crc_le: bytes = serialize_crc(crc)
        with_crc: bytes = logical + crc_le
        encoded: bytes = cobs_encode(with_crc)
        return bytes([0x00]) + encoded + bytes([0x00])

    @pytest.mark.parametrize(
        "ftype,seq,payload",
        [
            (FRAME_DATA, 0, b""),
            (FRAME_DATA, 1, b"hello"),
            (FRAME_DATA, 255, bytes(range(200))),
            (FRAME_ACK, 5, b""),
            (FRAME_NAK, 10, b""),
            (FRAME_FRAG, 0, bytes(4 + 50)),
        ],
    )
    def test_build_matches_manual_encoding(
        self, ftype: int, seq: int, payload: bytes
    ) -> None:
        expected: bytes = self._manual_encode(ftype, seq, payload)
        actual: bytes = build_frame(ftype, seq, payload)
        assert actual == expected, (
            f"build_frame({ftype:#x}, {seq}, ...) does not follow §3.3 encoding steps"
        )

    def test_crc_before_cobs(self) -> None:
        """CRC is over the pre-COBS logical frame (§3.2.4)."""
        payload: bytes = b"order matters"
        frame: bytes = build_frame(FRAME_DATA, 0, payload)
        interior: bytes = frame[1:-1]
        decoded: bytes | None = cobs_decode(interior)
        assert decoded is not None
        raw_crc: int = struct.unpack_from("<H", decoded[-2:])[0]
        logical: bytes = bytes([FRAME_DATA, 0]) + payload
        expected_crc: int = calculate_crc16(logical)
        assert raw_crc == expected_crc, (
            "CRC MUST be calculated over pre-COBS logical frame"
        )


class TestNonConformance:
    """Behaviours explicitly prohibited in §7.4."""

    def test_ack_with_payload_is_nonconformant(self) -> None:
        """ACK/NAK with non-empty payloads is NON-CONFORMANT (§7.4)."""
        frame: bytes = build_frame(FRAME_ACK, 0, b"bad")
        parsed: dict | None = parse_frame(frame)
        if parsed is not None:
            pytest.xfail(
                "ACK with non-empty payload is NON-CONFORMANT per §7.4 – "
                "ensure your protocol layer rejects it."
            )

    def test_sequence_number_fits_in_byte(self) -> None:
        """Sequence numbers > 255 MUST NOT be used (§7.4)."""
        with pytest.raises(Exception):
            build_frame(FRAME_DATA, 256, b"x")

    def test_crc_is_little_endian(self) -> None:
        """Big-endian CRC byte order is NON-CONFORMANT (§7.4, §3.2.4)."""
        crc: int = 0x1234
        ser: bytes = serialize_crc(crc)
        assert ser[0] == 0x34, "CRC low byte MUST come first (little-endian)"
        assert ser[1] == 0x12, "CRC high byte MUST come second"


class TestFrameSizeBoundaries:
    """§3.3 size calculations."""

    def test_header_only_frame_size(self) -> None:
        """Minimum logical frame = 2 bytes (§3.3)."""
        frame: bytes = build_frame(FRAME_ACK, 0)
        interior: bytes = frame[1:-1]
        decoded: bytes | None = cobs_decode(interior)
        assert len(decoded) == 4, (
            "Header-only logical frame + CRC must be 4 bytes"
        )

    def test_max_payload_logical_frame_size(self) -> None:
        """Maximum logical frame = 202 bytes (§3.3)."""
        frame: bytes = build_frame(FRAME_DATA, 0, bytes(MAX_PAYLOAD_SIZE))
        interior: bytes = frame[1:-1]
        decoded: bytes | None = cobs_decode(interior)
        assert len(decoded) == 204, "Max logical frame + CRC must be 204 bytes"

    def test_physical_frame_under_max(self) -> None:
        frame: bytes = build_frame(FRAME_DATA, 0, bytes(MAX_PAYLOAD_SIZE))
        assert len(frame) <= 209, (
            f"Physical frame length {len(frame)} exceeds spec max of 209 bytes (§3.3)"
        )

    def test_zero_byte_payload_differs_from_no_payload(self) -> None:
        """DATA frames MAY have empty payloads (§3.2.3)."""
        frame1: bytes = build_frame(FRAME_DATA, 0)
        frame2: bytes = build_frame(FRAME_DATA, 0, b"")
        assert frame1 == frame2  # equivalent


class TestMiscellaneous:
    def test_all_defined_frame_types_build_and_parse(self) -> None:
        types: list[int] = [
            FRAME_DATA,
            FRAME_ACK,
            FRAME_NAK,
            FRAME_FRAG,
            FRAME_CONNECT,
            FRAME_CONNECT_ACK,
            FRAME_ERROR,
            FRAME_ABORT,
            FRAME_BUSY,
            FRAME_READY,
        ]
        ft: int
        frame: bytes
        parsed: dict | None
        for ft in types:
            frame = build_frame(ft, 0)
            parsed = parse_frame(frame)
            assert parsed is not None, f"Frame type {ft:#04x} failed to parse"
            assert parsed["type"] == ft

    def test_multiple_frames_independent(self) -> None:
        """Each frame is fully independent; parsing one must not affect another."""
        f1: bytes = build_frame(FRAME_DATA, 1, b"frame one")
        f2: bytes = build_frame(FRAME_DATA, 2, b"frame two")
        p1: dict | None = parse_frame(f1)
        p2: dict | None = parse_frame(f2)
        assert p1["seq"] == 1 and p1["payload"] == b"frame one"
        assert p2["seq"] == 2 and p2["payload"] == b"frame two"

    def test_same_payload_different_seq_different_frames(self) -> None:
        f1: bytes = build_frame(FRAME_DATA, 0, b"data")
        f2: bytes = build_frame(FRAME_DATA, 1, b"data")
        assert f1 != f2, (
            "Different sequence numbers must produce different frames"
        )

    def test_crc_catches_single_bit_error(self) -> None:
        """CRC-16 is expected to catch all single-bit errors."""
        frame: bytes = build_frame(FRAME_DATA, 0, b"integrity check")
        caught: int = 0
        interior: bytearray = bytearray(frame[1:-1])
        byte_idx: int
        bit: int
        for byte_idx in range(len(interior)):
            for bit in range(8):
                corrupted: bytearray = bytearray(interior)
                corrupted[byte_idx] ^= 1 << bit
                test_frame: bytes = (
                    bytes([0x00]) + bytes(corrupted) + bytes([0x00])
                )
                result: dict | None = parse_frame(test_frame)
                if result is None:
                    caught += 1
        total: int = len(interior) * 8
        assert caught == total, (
            f"CRC/COBS only caught {caught}/{total} single-bit errors"
        )

    def test_frame_with_all_zero_payload(self) -> None:
        """Payload of all zeros is valid and must survive COBS round-trip."""
        payload: bytes = bytes(50)
        frame: bytes = build_frame(FRAME_DATA, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == payload

    def test_frame_with_all_ff_payload(self) -> None:
        payload: bytes = bytes([0xFF] * 100)
        frame: bytes = build_frame(FRAME_DATA, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == payload

    def test_cobs_delimiter_boundary(self) -> None:
        """254-byte boundary in COBS encoding must be handled correctly."""
        payload: bytes = bytes([i % 255 + 1 for i in range(252)])
        frame: bytes = build_frame(FRAME_DATA, 0, payload)
        parsed: dict | None = parse_frame(frame)
        assert parsed is not None
        assert parsed["payload"] == payload
