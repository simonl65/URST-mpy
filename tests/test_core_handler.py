from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from urst import constants
from urst.codec_layer import CodecLayer
from urst.core_handler import Urst
from urst.protocol_layer import ProtocolLayer, build_frame

# A conformant CONNECT/CONNECT_ACK capability payload (§5.6.1): the first
# byte is protocol_version, which peers now validate (§5.6.1.1), so
# handshake fixtures must carry a real one rather than b"".
CAPS = bytes([constants.PROTOCOL_VERSION, 0, 32, 32, 1, 232, 3, 3, 0])


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
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, CAPS)
    fake_serial = RespondingFakeSerial(stale=stale_pong, response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is True
    # The stale frame must not have been queued as if it were a payload
    # belonging to this session.
    assert not protocol._recv_queue


def test_connect_drain_does_not_break_a_clean_handshake() -> None:
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, CAPS)
    fake_serial = RespondingFakeSerial(stale=b"", response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is True


# ---------------------------------------------------------------------------
# §5.6.1.1 protocol_version validation
# ---------------------------------------------------------------------------
#
# Reproduces the real deployment failure this check exists to catch: a
# device left on protocol_version 4 (2-byte header) while the host ran
# version 5 (3-byte header). CRC cannot detect that -- both sides
# checksum the same byte range and disagree only about how to read it --
# so the handshake "succeeded" and the fault surfaced as DATA frames that
# were never ACKed, i.e. indistinguishable from a failing radio link.


def test_connect_refuses_a_peer_advertising_a_different_protocol_version() -> (
    None
):
    older_caps = bytes(
        [constants.PROTOCOL_VERSION - 1, 0, 32, 32, 1, 232, 3, 3, 0]
    )
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, older_caps)
    fake_serial = RespondingFakeSerial(stale=b"", response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is False
    assert protocol.is_connected is False


def test_connect_refuses_a_peer_sending_no_capability_payload() -> None:
    # Too short to carry a version: treated as a mismatch, never as an
    # unversioned legacy peer to be accommodated (§5.6.1.1).
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, b"")
    fake_serial = RespondingFakeSerial(stale=b"", response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is False
    assert protocol.is_connected is False


def test_version_mismatch_fails_fast_without_burning_every_retry() -> None:
    """A mismatch is permanent; retrying the handshake cannot fix it."""
    older_caps = bytes(
        [constants.PROTOCOL_VERSION - 1, 0, 32, 32, 1, 232, 3, 3, 0]
    )
    connect_ack = build_frame(constants.FRAME_CONNECT_ACK, 0, older_caps)
    fake_serial = RespondingFakeSerial(stale=b"", response=connect_ack)

    protocol = ProtocolLayer(CodecLayer(fake_serial))

    assert protocol.connect() is False
    # Exactly one CONNECT written, not MAX_RETRIES + 1 of them.
    connects = [
        c for c in fake_serial.write_calls if constants.FRAME_CONNECT in c
    ]
    assert len(connects) == 1


def test_incoming_connect_from_mismatched_peer_is_rejected_with_error() -> None:
    older_caps = bytes(
        [constants.PROTOCOL_VERSION - 1, 0, 32, 32, 1, 232, 3, 3, 0]
    )
    incoming = build_frame(constants.FRAME_CONNECT, 0, older_caps)
    fake_serial = FakeSerial(data=incoming)

    protocol = ProtocolLayer(CodecLayer(fake_serial))
    result = protocol.receive_frame(timeout_ms=50)

    assert result is None
    assert protocol.is_connected is False

    from urst.protocol_layer import parse_frame

    sent = [parse_frame(c) for c in fake_serial.write_calls]
    # No CONNECT_ACK -- the peer must not be led to believe it connected.
    assert not any(p and p["type"] == constants.FRAME_CONNECT_ACK for p in sent)
    errors = [p for p in sent if p and p["type"] == constants.FRAME_ERROR]
    assert errors, "should report INCOMPATIBLE_VERSION back to the peer"
    assert errors[0]["payload"][0] == constants.ERROR_INCOMPATIBLE_VERSION


def test_matching_version_still_connects_normally() -> None:
    incoming = build_frame(constants.FRAME_CONNECT, 0, CAPS)
    fake_serial = FakeSerial(data=incoming)

    protocol = ProtocolLayer(CodecLayer(fake_serial))
    result = protocol.receive_frame(timeout_ms=50)

    assert result is not None
    assert protocol.is_connected is True


# ---------------------------------------------------------------------------
# §5.8 Request ID / response correlation
# ---------------------------------------------------------------------------
#
# ACK/NAK exchange for the outbound command itself isn't exercised here --
# these tests preload the reply frame(s) directly and drive Urst.read(),
# which is where request/response correlation actually lives (§5.8 is a
# Handler-layer concern; the Protocol Layer's own ACK/NAK machinery is
# unaffected, per §5.1.2's amendment).


def _make_urst(monkeypatch: pytest.MonkeyPatch, data: bytes = b"") -> Urst:
    fake_serial = FakeSerial(data=data)
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )
    urst = Urst("/dev/null", 57600)
    urst.protocol.is_connected = True
    return urst


def test_send_assigns_a_fresh_request_id_used_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_serial = FakeSerial(data=build_frame(constants.FRAME_ACK, 0))
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )
    urst = Urst("/dev/null", 57600)
    urst.protocol.is_connected = True

    urst.send(b"CMD")

    sent_frame = fake_serial.write_calls[0]
    from urst.protocol_layer import parse_frame

    parsed = parse_frame(sent_frame)
    assert parsed is not None
    assert parsed["request_id"] == 0  # first assigned id
    assert urst._awaiting_request_id == 0


def test_read_discards_a_stale_reply_and_accepts_the_matching_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # seq must increment across frames from the same peer stream regardless
    # of logical message -- otherwise the Protocol Layer's own duplicate
    # detection (independent of Request ID) drops the second frame first.
    stale_reply = build_frame(constants.FRAME_DATA, 0, b"OLD", request_id=5)
    real_reply = build_frame(constants.FRAME_DATA, 1, b"NEW", request_id=7)
    urst = _make_urst(monkeypatch, data=stale_reply + real_reply)
    urst._awaiting_request_id = 7

    result = urst.read()

    assert result == b"NEW"
    assert urst.last_request_id == 7
    assert urst._awaiting_request_id is None


def test_read_accepts_anything_when_not_awaiting_a_specific_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = build_frame(constants.FRAME_DATA, 0, b"CMD", request_id=42)
    urst = _make_urst(monkeypatch, data=incoming)

    result = urst.read()

    assert result == b"CMD"
    assert urst.last_request_id == 42


def test_send_echoes_an_explicit_request_id_without_touching_awaiting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_serial = FakeSerial(data=build_frame(constants.FRAME_ACK, 0))
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )
    urst = Urst("/dev/null", 57600)
    urst.protocol.is_connected = True

    urst.send(b"REPLY", request_id=42)

    from urst.protocol_layer import parse_frame

    sent = parse_frame(fake_serial.write_calls[0])
    assert sent is not None
    assert sent["request_id"] == 42
    assert urst._awaiting_request_id is None


def test_fragmented_reassembly_rejects_a_concurrent_different_key_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frag_a = build_frame(
        constants.FRAME_FRAG, 0, bytes([1, 0, 2, 1]) + b"A", request_id=1
    )
    frag_b = build_frame(
        constants.FRAME_FRAG, 1, bytes([2, 0, 1, 1]) + b"B", request_id=1
    )
    frag_a_final = build_frame(
        constants.FRAME_FRAG, 2, bytes([1, 1, 2, 1]) + b"C", request_id=1
    )
    urst = _make_urst(monkeypatch, data=frag_a + frag_b + frag_a_final)

    result = urst.read()

    # frag_b (a different Message ID) must be rejected via ERROR while the
    # in-progress reassembly for msg_id=1 completes normally.
    assert result == b"AC"
    error_frames = [
        c
        for c in urst.protocol.codec.ser.write_calls
        if len(c) > 0 and constants.FRAME_ERROR.to_bytes(1, "little")[0] in c
    ]
    assert error_frames  # an ERROR frame was sent for the rejected fragment


def test_abort_received_clears_matching_reassembly_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frag = build_frame(
        constants.FRAME_FRAG, 0, bytes([9, 0, 2, 1]) + b"A", request_id=3
    )
    abort = build_frame(constants.FRAME_ABORT, 0, bytes([0, 9]), request_id=3)
    trailing_timeout = b""
    urst = _make_urst(monkeypatch, data=frag + abort + trailing_timeout)

    result = urst.read()

    assert result == b""  # nothing left to deliver: reassembly was aborted
    assert urst._reassembly == {}


def test_send_aborts_a_fragmented_message_on_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No ACKs ever arrive -> send_reliable exhausts MAX_RETRIES on the
    # first fragment and gives up.
    urst = _make_urst(monkeypatch, data=b"")
    urst.protocol.codec.read_frame = lambda *_a, **_kw: None  # never ACKed

    big_payload = b"x" * (constants.MAX_PAYLOAD_SIZE - 6 + 1)  # forces FRAG
    sent = urst.send(big_payload)

    assert sent == 0
    from urst.protocol_layer import parse_frame

    abort_frames = [
        parse_frame(c)
        for c in urst.protocol.codec.ser.write_calls
        if parse_frame(c) is not None
        and parse_frame(c)["type"] == constants.FRAME_ABORT
    ]
    assert abort_frames, "sender must send ABORT on retry exhaustion (§5.7.2)"


def test_fragment_reassembly_times_out_and_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the first of 2 fragments ever arrives; nothing completes it.
    frag = build_frame(
        constants.FRAME_FRAG, 0, bytes([4, 0, 2, 1]) + b"A", request_id=1
    )
    urst = _make_urst(monkeypatch, data=frag)
    urst.read()  # buffers fragment 0/2, then times out waiting for frame 1
    assert (1, 4) in urst._reassembly

    # Force the deadline into the past and let the next read() sweep it.
    urst._reassembly_deadline[(1, 4)] = 0
    urst.read()

    assert (1, 4) not in urst._reassembly


def test_reply_echoes_the_request_id_of_the_last_received_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = build_frame(constants.FRAME_DATA, 0, b"CMD", request_id=9)
    fake_serial = FakeSerial(
        data=incoming + build_frame(constants.FRAME_ACK, 1)
    )
    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda **_: fake_serial)
    )
    urst = Urst("/dev/null", 57600)
    urst.protocol.is_connected = True
    urst.read()

    urst.reply(b"RESP")

    from urst.protocol_layer import parse_frame

    reply_frame = fake_serial.write_calls[
        -2
    ]  # last write before reading the ACK
    parsed = parse_frame(reply_frame)
    assert parsed is not None
    assert parsed["request_id"] == 9
    assert urst._awaiting_request_id is None  # reply(), not a new request


def test_reply_without_a_prior_read_raises() -> None:
    urst = object.__new__(Urst)
    urst.last_request_id = None

    with pytest.raises(RuntimeError):
        Urst.reply(urst, b"RESP")


def test_reassembly_in_progress_reports_a_partial_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers need to distinguish "nothing came back" from "a fragmented
    reply is part-way in".

    `read()` is deliberately single-shot: it returns b"" the moment
    `receive_frame()` times out (after ACK_TIMEOUT_MS) while keeping the
    partial reassembly for the next call, so §6.3.4's much longer
    reassembly deadline is only reachable by calling `read()` again. A
    constrained peer streaming a large response routinely pauses longer
    than ACK_TIMEOUT_MS between fragments, so a caller that treats the
    first b"" as failure gives up mid-transfer -- and then retries the
    whole request, colliding a fresh CONNECT with the still-streaming
    response.
    """
    frag = build_frame(
        constants.FRAME_FRAG, 0, bytes([7, 0, 2, 1]) + b"A", request_id=1
    )
    urst = _make_urst(monkeypatch, data=frag)

    assert urst.reassembly_in_progress is False

    assert urst.read() == b""  # fragment 0 of 2 buffered, then timed out
    assert urst.reassembly_in_progress is True


def test_reassembly_in_progress_clears_once_the_message_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frag_0 = build_frame(
        constants.FRAME_FRAG, 0, bytes([7, 0, 2, 1]) + b"A", request_id=1
    )
    frag_1 = build_frame(
        constants.FRAME_FRAG, 1, bytes([7, 1, 2, 1]) + b"B", request_id=1
    )
    urst = _make_urst(monkeypatch, data=frag_0 + frag_1)

    assert urst.read() == b"AB"
    assert urst.reassembly_in_progress is False
