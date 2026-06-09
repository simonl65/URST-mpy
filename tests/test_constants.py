import pytest
from urst import constants


@pytest.mark.skipif(False, reason="urst module found")  # Placeholder logic
class TestProtocolConstants:
    def test_max_payload_size(self) -> None:
        assert constants.MAX_PAYLOAD_SIZE == 200, (
            "MAX_PAYLOAD_SIZE MUST be 200 (§5.5, §7.3)"
        )

    def test_max_retries(self) -> None:
        assert constants.MAX_RETRIES >= 3, (
            "MAX_RETRIES MUST be at least 3 (§5.5, §7.1)"
        )

    def test_ack_timeout_default(self) -> None:
        assert constants.ACK_TIMEOUT_MS == 1000, (
            "Default ACK_TIMEOUT_MS MUST be 1000 ms (§5.5)"
        )

    def test_rx_buffer_size(self) -> None:
        assert constants.RX_BUFFER_SIZE >= 512, (
            "RX_BUFFER_SIZE MUST be at least 512 (§5.5)"
        )

    def test_max_msg_bytes(self) -> None:
        assert constants.MAX_MSG_BYTES == 8192, (
            "MAX_MSG_BYTES default MUST be 8192 (§5.5)"
        )

    def test_max_fragments(self) -> None:
        assert constants.MAX_FRAGMENTS == 32, (
            "MAX_FRAGMENTS default MUST be 32 (§5.5)"
        )

    def test_frame_delimiter_is_zero(self) -> None:
        assert constants.FRAME_DELIMITER == 0x00, (
            "FRAME_DELIMITER MUST be 0x00 (§7.3)"
        )

    def test_frame_type_values(self) -> None:
        assert constants.FRAME_DATA == 0x01
        assert constants.FRAME_ACK == 0x02
        assert constants.FRAME_NAK == 0x03
        assert constants.FRAME_FRAG == 0x04
        assert constants.FRAME_CONNECT == 0x05
        assert constants.FRAME_CONNECT_ACK == 0x06
        assert constants.FRAME_ERROR == 0x07
        assert constants.FRAME_ABORT == 0x08
        assert constants.FRAME_BUSY == 0x09
        assert constants.FRAME_READY == 0x0A

    def test_max_frag_data_size(self) -> None:
        """Derived constant: 194 bytes of application data per fragment."""
        assert constants.MAX_PAYLOAD_SIZE - 6 == 194, (
            "Fragment data size MUST be MAX_PAYLOAD_SIZE - 6 = 194 (§6.3.1)"
        )
