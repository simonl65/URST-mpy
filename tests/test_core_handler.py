from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from urst import constants
from urst.core_handler import Urst
from urst.protocol_layer import build_frame


class FakeSerial:
    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.write_calls: list[bytes] = []
        self.read_index = 0
        self.in_waiting = 0

    def write(self, data: bytes) -> int:
        self.write_calls.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, size: int = 1) -> bytes:
        if self.read_index >= len(self.data):
            return b""
        res = self.data[self.read_index : self.read_index + size]
        self.read_index += size
        return res


@pytest.fixture
def mock_serial_module(monkeypatch):
    def factory(**kwargs):
        return FakeSerial()

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=factory))


def test_read_valid_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = build_frame(constants.FRAME_DATA, 0, b"Hello")
    fake_serial = FakeSerial(data=frame)
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )

    urst = Urst("/dev/null", 57600)
    # Mock handshake to be connected
    urst.protocol.is_connected = True
    result = urst.read()

    assert result == b"Hello"


def test_read_timeout_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_serial = FakeSerial(data=b"")
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )

    urst = Urst("/dev/null", 57600)
    urst.protocol.is_connected = True
    result = urst.read()

    assert result == b""


def test_send_under_max_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_serial = FakeSerial()
    # Mock ACK response
    ack = build_frame(constants.FRAME_ACK, 0)
    fake_serial.data = ack

    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )

    urst = Urst("/dev/null", 57600)
    urst.protocol.is_connected = True

    data = b"Short message"
    sent = urst.send(data)

    assert sent == len(data)
    assert len(fake_serial.write_calls) > 0
