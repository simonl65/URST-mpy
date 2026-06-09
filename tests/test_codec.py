import struct

from urst.codec_layer import (
    calculate_crc16,
    cobs_decode,
    cobs_encode,
    serialize_crc,
)


class TestCRC16:
    """
    Known-good vectors produced by an independent CRC-16/CCITT_FALSE
    implementation (poly=0x1021, init=0xFFFF, no final XOR, MSB-first).
    """

    def _ref_crc(self, data: bytes) -> int:
        """Pure-Python reference implementation."""
        crc: int = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                crc = (
                    ((crc << 1) ^ 0x1021) & 0xFFFF
                    if crc & 0x8000
                    else (crc << 1) & 0xFFFF
                )
        return crc

    def test_check_value(self) -> None:
        """The canonical check value for '123456789' is 0x29B1."""
        assert calculate_crc16(b"123456789") == 0x29B1

    def test_empty_input(self) -> None:
        assert calculate_crc16(b"") == self._ref_crc(b"")

    def test_single_zero_byte(self) -> None:
        assert calculate_crc16(b"\x00") == self._ref_crc(b"\x00")

    def test_single_one_byte(self) -> None:
        assert calculate_crc16(b"\x01") == self._ref_crc(b"\x01")

    def test_all_zeros(self) -> None:
        data: bytes = bytes(16)
        assert calculate_crc16(data) == self._ref_crc(data)

    def test_all_ff(self) -> None:
        data: bytes = bytes([0xFF] * 16)
        assert calculate_crc16(data) == self._ref_crc(data)

    def test_max_payload(self) -> None:
        data: bytes = bytes(range(256)) * 1
        assert calculate_crc16(data[:200]) == self._ref_crc(data[:200])

    def test_self_consistency_with_reference(self) -> None:
        import os

        data: bytes = os.urandom(150)
        assert calculate_crc16(data) == self._ref_crc(data)

    def test_different_inputs_different_crcs(self) -> None:
        a: int = calculate_crc16(b"hello")
        b: int = calculate_crc16(b"hellp")
        assert a != b, (
            "Different data should (almost certainly) produce different CRCs"
        )

    def test_serialize_little_endian(self) -> None:
        crc: int = 0x1234
        ser: bytes = serialize_crc(crc)
        assert len(ser) == 2
        assert ser[0] == 0x34, "Low byte first (little-endian) (§3.2.4)"
        assert ser[1] == 0x12, "High byte second (little-endian) (§3.2.4)"

    def test_serialize_zero(self) -> None:
        assert serialize_crc(0x0000) == b"\x00\x00"

    def test_serialize_max(self) -> None:
        assert serialize_crc(0xFFFF) == b"\xff\xff"

    def test_serialize_roundtrip(self) -> None:
        val: int
        for val in [0x0000, 0x0001, 0x1021, 0x29B1, 0xFFFF]:
            ser: bytes = serialize_crc(val)
            recovered: int = struct.unpack_from("<H", ser)[0]
            assert recovered == val


class TestCOBS:
    """§3.5"""

    def test_empty_input_encode(self) -> None:
        """COBS of empty data must return a single 0x01 byte."""
        enc: bytes = cobs_encode(b"")
        assert enc == b"\x01", "COBS encode of empty data MUST return 0x01"

    def test_empty_input_decode(self) -> None:
        dec: bytes | None = cobs_decode(b"\x01")
        assert dec == b""

    def test_no_zero_bytes_in_output(self) -> None:
        data: bytes
        for data in [b"hello", bytes(range(1, 50)), b"\x01\x02\x03"]:
            enc: bytes = cobs_encode(data)
            assert 0x00 not in enc, (
                "Encoded data MUST NOT contain 0x00 (§3.5.1)"
            )

    def test_all_zeros_input(self) -> None:
        data: bytes = b"\x00" * 8
        enc: bytes = cobs_encode(data)
        assert 0x00 not in enc

    def test_mixed_zeros(self) -> None:
        data: bytes = b"\x11\x22\x00\x33\x00\x44"
        enc: bytes = cobs_encode(data)
        assert 0x00 not in enc
        dec: bytes | None = cobs_decode(enc)
        assert dec == data

    def test_roundtrip_no_zeros(self) -> None:
        data: bytes = bytes(range(1, 200))
        assert cobs_decode(cobs_encode(data)) == data

    def test_roundtrip_with_zeros(self) -> None:
        data: bytes = bytes([0, 1, 0, 2, 0, 3, 0])
        assert cobs_decode(cobs_encode(data)) == data

    def test_roundtrip_all_zeros(self) -> None:
        data: bytes = bytes(50)
        assert cobs_decode(cobs_encode(data)) == data

    def test_roundtrip_max_frame(self) -> None:
        """Max logical frame with CRC is 204 bytes."""
        data: bytes = bytes([i % 256 for i in range(204)])
        assert cobs_decode(cobs_encode(data)) == data

    def test_254_byte_boundary(self) -> None:
        """COBS inserts a code byte every 254 data bytes."""
        data: bytes = bytes([i % 253 + 1 for i in range(254)])
        enc: bytes = cobs_encode(data)
        assert 0x00 not in enc
        dec: bytes | None = cobs_decode(enc)
        assert dec == data

    def test_embedded_zero_in_encoded_is_failure(self) -> None:
        """An embedded 0x00 in COBS-encoded data MUST cause decode failure (§3.5.4)."""
        valid_enc: bytes = cobs_encode(b"hello world")
        corrupted: bytearray = bytearray(valid_enc)
        corrupted[3] = 0x00
        result: bytes | None = cobs_decode(bytes(corrupted))
        assert result is None, (
            "cobs_decode MUST return None for data containing embedded 0x00 (§3.5.4)"
        )

    def test_single_nonzero_byte(self) -> None:
        assert cobs_decode(cobs_encode(b"\x42")) == b"\x42"

    def test_deterministic(self) -> None:
        data: bytes = b"test data 12345"
        assert cobs_encode(data) == cobs_encode(data)
