from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from urst import constants
from urst.codec_layer import CodecLayer
from urst.core_handler import Urst
from urst.protocol_layer import ProtocolLayer, build_frame


class FakeSerial:
    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.write_calls: list[bytes] = []
        self.read_index = 0

    def write(self, data: bytes) -> int:
        self.write_calls.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    @property
    def in_waiting(self) -> int:
        # Reflects genuinely unread bytes, like a real serial port's
        # buffer -- needed so drain/discard logic has something real to
        # observe instead of a fixed stub value.
        return max(0, len(self.data) - self.read_index)

    def read(self, size: int = 1) -> bytes:
        if self.read_index >= len(self.data):
            return b""
        res = self.data[self.read_index : self.read_index + size]
        self.read_index += size
        return res

    def append(self, data: bytes) -> None:
        """Simulate more bytes arriving on the wire after construction."""
        self.data += data


@pytest.fixture
def mock_serial_module(monkeypatch):
    def factory(**kwargs):
        return FakeSerial()

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=factory))


class FakeUart(FakeSerial):
    init_calls: list[tuple[object, int]] = []

    def __init__(self, port=None, baudrate: int = 57600) -> None:
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.init_calls.append((port, baudrate))


@pytest.fixture
def micropython_runtime(monkeypatch: pytest.MonkeyPatch):
    FakeUart.init_calls.clear()
    monkeypatch.setattr(
        sys, "implementation", SimpleNamespace(name="micropython")
    )
    monkeypatch.setitem(sys.modules, "machine", SimpleNamespace(UART=FakeUart))
    return FakeUart


def test_micropython_accepts_machine_uart(micropython_runtime) -> None:
    uart = micropython_runtime()

    transport = Urst(uart)

    assert transport.ser is uart
    assert micropython_runtime.init_calls == [(None, 57600)]


def test_micropython_accepts_uart_identifier(micropython_runtime) -> None:
    transport = Urst(1, baud=115200)

    assert isinstance(transport.ser, micropython_runtime)
    assert transport.ser.port == 1
    assert transport.ser.baudrate == 115200
    assert micropython_runtime.init_calls == [(1, 115200)]


def test_micropython_accepts_serial_like_object(micropython_runtime) -> None:
    port = FakeSerial()

    transport = Urst(port)

    assert transport.ser is port
    assert micropython_runtime.init_calls == []


def test_desktop_accepts_serial_like_object() -> None:
    port = FakeSerial()

    transport = Urst(port)

    assert transport.ser is port


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


# ---------------------------------------------------------------------------
# Stale-frame drain before CONNECT (relay/PTY reconnect scenario)
# ---------------------------------------------------------------------------
#
# A long-lived relay (e.g. a gateway exposing the physical link as a PTY)
# can hand a brand-new client session bytes left over from a previous one
# -- a response the earlier client never read, or a device retransmit that
# outlived it. Before this fix, ProtocolLayer.connect() read whatever was
# first on the wire with no regard for whether it predated this session,
# so a stale response frame could survive the handshake and later be
# mistaken for the answer to an unrelated request.


def test_discard_buffered_drops_pending_bytes_and_reassembly_state() -> None:
    fake_serial = FakeSerial(data=b"stale garbage bytes")
    codec = CodecLayer(fake_serial)
    codec._rx_buffer = bytearray(b"leftover-partial-frame")

    discarded = codec.discard_buffered(quiet_ms=1, max_wait_ms=20)

    assert discarded == len(b"leftover-partial-frame") + len(
        b"stale garbage bytes"
    )
    assert codec._rx_buffer == bytearray()
    assert fake_serial.read_index == len(fake_serial.data)


class RespondingFakeSerial(FakeSerial):
    """FakeSerial whose CONNECT_ACK response only appears on the wire once
    a CONNECT frame is actually written -- so pre-loaded stale bytes and
    the genuine handshake response aren't indistinguishable to a drain
    that just reads whatever is already waiting."""

    def __init__(self, stale: bytes, response: bytes) -> None:
        super().__init__(data=stale)
        self._response = response
        self._responded = False

    def write(self, data: bytes) -> int:
        super().write(data)
        if not self._responded and constants.FRAME_CONNECT in data:
            self.append(self._response)
            self._responded = True
        return len(data)


def test_connect_discards_a_stale_response_frame_from_a_previous_session() -> (
    None
):
    stale_pong = build_frame(constants.FRAME_DATA, 1, b"PONG")
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, b"")
    fake_serial = RespondingFakeSerial(stale=stale_pong, response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is True
    # The stale frame must not have been queued as if it were a payload
    # belonging to this session.
    assert not protocol._recv_queue


def test_connect_drain_does_not_break_a_clean_handshake() -> None:
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, b"")
    fake_serial = RespondingFakeSerial(stale=b"", response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is True
