try:
    import logging
except ImportError:
    from . import logging
import sys

# MicroPython compatibility for typing
try:
    from typing import TYPE_CHECKING, Any
except ImportError:
    TYPE_CHECKING = False
    # Minimal fallback
    pass

# MicroPython compatibility for time. Checked independently per shimmed
# attribute (not just `ticks_ms`) since protocol_layer's own shim may
# already have patched some of these onto the shared `time` module by
# the time this import chain reaches here.
import time

try:
    _ = time.ticks_ms  # type: ignore
except AttributeError:

    def ticks_ms():
        return int(time.time() * 1000)

    time.ticks_ms = ticks_ms  # type: ignore

try:
    _ = time.ticks_diff  # type: ignore
except AttributeError:

    def ticks_diff(later, earlier):
        return later - earlier

    time.ticks_diff = ticks_diff  # type: ignore

try:
    _ = time.ticks_add  # type: ignore
except AttributeError:

    def ticks_add(ticks, delta):
        return ticks + delta

    time.ticks_add = ticks_add  # type: ignore

from . import constants
from .codec_layer import CodecLayer
from .protocol_layer import ProtocolLayer

if TYPE_CHECKING:
    pass  # type: ignore

logger = logging.getLogger(__name__)


try:
    from random import getrandbits as _getrandbits
except ImportError:  # minimal MicroPython builds omit `random`
    _getrandbits = None


def _is_serial_like(port):
    """Return whether *port* provides the byte-stream API URST requires."""
    return callable(getattr(port, "read", None)) and callable(
        getattr(port, "write", None)
    )


def _initial_request_id() -> int:
    """Pick a starting Request ID for a new session (§5.8.2).

    Correlation can only reject a stale reply if a new session numbers its
    requests differently from the one before it. Starting every instance
    at 0 made that check inert wherever sessions are short-lived: a
    one-shot CLI process against a long-lived link had every exchange it
    ever made carry `request_id` 0, so `read()` compared 0 against 0 and
    delivered the previous command's leftover reply as the answer to the
    next one -- the exact failure §5.8 exists to prevent, reintroduced by
    the choice of starting value.

    `random` is optional on minimal MicroPython builds, so fall back to
    the millisecond clock this module already depends on. Either source
    suffices: this needs only to differ from the *previous* session on the
    same link, not to be unguessable.
    """
    if _getrandbits is not None:
        return _getrandbits(8)
    return time.ticks_ms() & 0xFF  # type: ignore


class Urst:
    """
    Main interface for the Universal Reliable Serial Transport (URST) protocol.
    """

    def __init__(self, port: Any, baud: int = 57600, *, timeout: float = 1.0):
        logger.debug("Initializing Urst")
        self.port = port
        self.baud = baud
        self.timeout = timeout

        if sys.implementation.name == "micropython":
            import machine

            if isinstance(port, machine.UART) or _is_serial_like(port):
                self.ser = port
            else:
                # port could be id (int)
                self.ser = machine.UART(port, baudrate=baud)  # type: ignore
        else:
            # Desktop implementation
            if _is_serial_like(port):
                # Already a serial-like object (e.g. mock or already opened serial)
                self.ser = port
            else:
                try:
                    from serial import Serial as SerialImpl  # type: ignore

                    self.ser = SerialImpl(
                        port=port, baudrate=baud, timeout=timeout
                    )
                except ImportError as exc:
                    raise RuntimeError(
                        "pyserial is required to use Urst on desktop Python"
                    ) from exc

        self.codec = CodecLayer(self.ser)
        self.protocol = ProtocolLayer(self.codec)
        self._msg_id = 0
        # Reassembly state keyed by (request_id, msg_id) -- see §5.8.4. Two
        # unrelated exchanges whose independently-wrapping Message IDs
        # happen to collide MUST NOT be reassembled into one message.
        self._reassembly: dict[tuple[int, int], Any] = {}
        self._reassembly_deadline: dict[tuple[int, int], int] = {}
        # Request ID bookkeeping (§5.8). The starting value is randomised
        # so a fresh session cannot mistake the previous one's leftover
        # reply for its own -- see `_initial_request_id()`.
        self._next_request_id = _initial_request_id()
        self._awaiting_request_id: int | None = None
        self.last_request_id: int | None = None

    def send(self, data: bytes, request_id: int | None = None) -> int:
        """
        Send data over the URST transport with automatic fragmentation and reliability.

        `request_id`: pass the Request ID being replied to when sending a
        response (§5.8.3). Omit it to start a new request/response
        exchange -- a fresh Request ID is assigned automatically and
        `read()` will then only accept a reply carrying that same ID
        (§5.8.2).
        """
        new_request = request_id is None
        if new_request:
            request_id = self._next_request_id
            self._next_request_id = (self._next_request_id + 1) & 0xFF

        max_frag_data = constants.MAX_PAYLOAD_SIZE - 6  # 194 bytes

        if len(data) <= max_frag_data:
            if self.protocol.send_reliable(
                constants.FRAME_DATA, data, request_id
            ):
                if new_request:
                    self._awaiting_request_id = request_id
                return len(data)
            return 0

        total_frags = (len(data) + max_frag_data - 1) // max_frag_data
        msg_id = self._msg_id
        self._msg_id = (self._msg_id + 1) & 0xFF

        for i in range(total_frags):
            chunk = data[i * max_frag_data : (i + 1) * max_frag_data]
            # Fragment payload structure (§6.2)
            header = bytes([msg_id, i, total_frags, len(chunk)])
            if not self.protocol.send_reliable(
                constants.FRAME_FRAG, header + chunk, request_id
            ):
                # §5.7.2: tell the peer to drop its partial reassembly
                # rather than leaving it to time out (§6.3.4).
                self.protocol.send_abort(msg_id, request_id)
                return i * max_frag_data

        if new_request:
            self._awaiting_request_id = request_id
        return len(data)

    def reply(self, data: bytes) -> int:
        """Send `data` as the response to whatever was last delivered by
        `read()` (§5.8.3): sugar for `send(data, request_id=self.last_request_id)`.

        Raises `RuntimeError` if nothing has been read yet -- there is
        nothing to reply to, and silently defaulting `request_id` to 0
        would produce a reply that fails correlation on the requester's
        side rather than failing loudly here.
        """
        if self.last_request_id is None:
            raise RuntimeError(
                "reply() called with nothing received yet to reply to"
            )
        return self.send(data, request_id=self.last_request_id)

    @property
    def reassembly_in_progress(self) -> bool:
        """Whether a fragmented message is part-way reassembled.

        `read()` is single-shot: it returns b"" as soon as a frame read
        times out (ACK_TIMEOUT_MS) while keeping the partial reassembly
        for a later call, so §6.3.4's much longer reassembly deadline is
        only reachable by calling `read()` again. A peer streaming a
        large response can easily pause longer than ACK_TIMEOUT_MS
        between fragments, so callers must use this to tell "nothing came
        back" from "still arriving" instead of treating the first b"" as
        a failure.
        """
        return bool(self._reassembly)

    def _fragment_timeout_ms(self, total_frags: int) -> int:
        """§6.3.4 required default: total_frags * (MAX_RETRIES+1) * ACK_TIMEOUT_MS."""
        return (
            total_frags * (constants.MAX_RETRIES + 1) * constants.ACK_TIMEOUT_MS
        )

    def _discard_expired_reassembly(self) -> None:
        now = time.ticks_ms()
        expired = [
            key
            for key, deadline in self._reassembly_deadline.items()
            if time.ticks_diff(now, deadline) >= 0
        ]
        for key in expired:
            logger.warning(f"Fragment reassembly timed out for {key} (§6.3.4)")
            del self._reassembly[key]
            del self._reassembly_deadline[key]

    def read(self, bytes_to_read: int = -1) -> bytes:
        """
        Read a complete URST message (reassembled if necessary).

        If a request is currently outstanding (§5.8.2), any complete
        message whose Request ID doesn't match is discarded rather than
        delivered -- it cannot be the expected reply.
        """
        expected = self._awaiting_request_id
        stale_budget = constants.MAX_FRAGMENTS  # bound worst-case looping

        while True:
            self._discard_expired_reassembly()

            frame = self.protocol.receive_frame()
            if not frame:
                return b""  # Timeout or duplicate frame (already ACKed)

            frame_type = frame["type"]
            payload = frame["payload"]
            request_id = frame["request_id"]

            if frame_type == constants.FRAME_DATA:
                if expected is not None and request_id != expected:
                    logger.warning(
                        f"Discarding stale DATA (request_id={request_id}, "
                        f"expected {expected}) -- §5.8.2"
                    )
                    stale_budget -= 1
                    if stale_budget <= 0:
                        return b""
                    continue
                self.last_request_id = request_id
                if expected is not None:
                    self._awaiting_request_id = None
                return payload

            if frame_type == constants.FRAME_FRAG:
                if len(payload) < 4:
                    continue

                msg_id = payload[0]
                frag_num = payload[1]
                total = payload[2]
                data_len = payload[3]
                data = payload[4 : 4 + data_len]
                key = (request_id, msg_id)

                if key not in self._reassembly:
                    if self._reassembly:
                        # §6.3.2/§5.8.4: only one concurrent reassembly --
                        # reject the incoming fragment, addressed to its
                        # own (rejected) exchange's Request ID.
                        self.protocol.send_error(
                            request_id,
                            constants.ERROR_CAPABILITY_EXCEEDED,
                        )
                        continue
                    self._reassembly[key] = {"total": total, "fragments": {}}
                    self._reassembly_deadline[key] = time.ticks_add(
                        time.ticks_ms(), self._fragment_timeout_ms(total)
                    )

                self._reassembly[key]["fragments"][frag_num] = data

                if len(self._reassembly[key]["fragments"]) == total:
                    # Reassemble message
                    msg = b"".join(
                        self._reassembly[key]["fragments"][j]
                        for j in range(total)
                    )
                    del self._reassembly[key]
                    del self._reassembly_deadline[key]
                    if expected is not None and request_id != expected:
                        logger.warning(
                            f"Discarding stale reassembled message "
                            f"(request_id={request_id}, expected {expected}) "
                            "-- §5.8.2"
                        )
                        stale_budget -= 1
                        if stale_budget <= 0:
                            return b""
                        continue
                    self.last_request_id = request_id
                    if expected is not None:
                        self._awaiting_request_id = None
                    return msg
                continue

            if frame_type == constants.FRAME_ABORT:
                if len(payload) == 2:
                    _reason_code, message_id = payload[0], payload[1]
                    key = (request_id, message_id)
                    if key in self._reassembly:
                        logger.warning(f"Peer aborted message {key} (§5.7.2)")
                        del self._reassembly[key]
                        del self._reassembly_deadline[key]
                continue

            # Handle other frame types or continue waiting
            if (
                frame_type == constants.FRAME_CONNECT
                or frame_type == constants.FRAME_CONNECT_ACK
            ):
                continue
