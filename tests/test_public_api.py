from urst import __all__, cobs_decode, cobs_encode
from urst.codec_layer import (
    cobs_decode as codec_cobs_decode,
)
from urst.codec_layer import (
    cobs_encode as codec_cobs_encode,
)


def test_cobs_helpers_are_public_exports() -> None:
    assert {"Urst", "cobs_decode", "cobs_encode"} <= set(__all__)
    assert cobs_encode is codec_cobs_encode
    assert cobs_decode is codec_cobs_decode
    assert cobs_decode(cobs_encode(b"\x00URST\x00")) == b"\x00URST\x00"
