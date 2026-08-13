# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
correspond to PyPI releases of `urst-mpy` (see `release.sh`).

## Unreleased

### Fixed

- **A CONNECT arriving while `send_reliable()` is still waiting for an ACK is now recognised and abandons the send, instead of being silently ignored and retried against invalidated sequence state (US-003, §5.6.2 extension).**

  `receive_frame()` already answers a mid-stream CONNECT correctly -- it sends CONNECT_ACK and resets `next_send_seq`/`expected_recv_seq`/`last_received_seq` -- but that reset happens underneath a `send_reliable()` call that has no branch for it: the CONNECT frame fell through unhandled, and the sender kept retransmitting its pre-reset `seq` into a session that had just restarted its numbering. The retries land at the peer as fragments of a message it never asked about, and any resulting `ERROR:Fragment transfer failed` reply (device-side, `otampy`'s `manager.py`) becomes the next stale frame poisoning the *following* command -- the mechanism behind `diff-drive-robot`'s intermittent channel-0 wedges (`ping` failing while channel-1 telemetry stays healthy; recoverable only by an MCU reset, not a gateway restart).

  `ProtocolLayer.send_reliable()` now recognises `FRAME_CONNECT` in its ACK-wait loop and returns `False` immediately, without retrying and without sending ABORT -- ABORT would itself reference a message the new session never asked about. A new `session_reset_during_send` flag distinguishes this from an ordinary retry-exhaustion failure so `core_handler.send()` can skip its own ABORT call for the same reason.

  This is the `urst-mpy` side of US-003's three-part scope (`docs/development/urst-stale-response-tasks.md` in `diff-drive-robot`); the `otampy` device library's own `ERROR:Fragment transfer failed` reply on this path is a separate, `otampy`-side change. US-004 (whether this alone lets a new CLI invocation recover an already-wedged device without an MCU reset) is unverified and remains open.

  - `tests/test_protocol.py::TestConnectDuringSend::test_connect_mid_send_should_abandon_the_in_flight_message` (previously `xfail(strict=True)`) now passes; the companion characterisation test in the same class is updated to assert the new one-FRAG-then-abandon behaviour instead of the old four-retries defect.
  - `tests/test_core_handler.py::test_send_does_not_abort_when_peer_reset_the_session_mid_send` covers `core_handler.send()`'s ABORT suppression.
  - No wire-format change, no `PROTOCOL_VERSION` bump: purely local behaviour on the sending side.

## [3.1.1] - 2026-08-12

### Fixed

- **A new session's Request IDs no longer start at a fixed 0, which had left §5.8.2 correlation inert wherever sessions are short-lived.** `Urst.__init__` now seeds `_next_request_id` randomly (`random.getrandbits(8)`, falling back to `time.ticks_ms() & 0xFF` on minimal MicroPython builds that omit `random`).

  3.0.0 added the Request ID field and the rule that a side awaiting a reply discards any complete message whose ID doesn't match. The rule was implemented correctly but could never fire in the deployment it was written for: a one-shot CLI process against a long-lived link starts a fresh `Urst` per invocation, the caller never passes an explicit `request_id`, and the responder only echoes back what it received -- so *every exchange in the whole system carried `request_id` 0*. `read()` compared 0 against 0 and delivered the previous command's leftover reply as the answer to the next one, which is precisely the failure §5.8 exists to prevent, reintroduced by the choice of starting value.

  Observed in `diff-drive-robot`: a `ping` returned `Error: Fragment transfer failed` -- a device-side string that a never-fragmented `PONG` reply cannot produce -- because it received the leftover reply to the preceding `cat ota.log`. The following `ping`, with the backlog drained, succeeded.

  Residual collision probability is 1/256 per session pair, against the previous 1/1. No wire-format change and no `protocol_version` bump: this alters only which values a fresh session picks, which the protocol already permits.
  - `tests/test_core_handler.py` gains four tests: distinct starting IDs across fresh instances, the single-byte range invariant, wrapping from a high start (now reachable on a second send rather than only after 256), and the no-`random` fallback branch that runs on-device.
  - `test_send_assigns_a_fresh_request_id_used_on_the_wire` no longer asserts a literal 0; it asserts the ID the instance actually allocated.

  **This does not address the related defect where a CONNECT arriving mid-send resets sequence state underneath an in-flight `send_reliable()`**, which has no CONNECT branch and silently ignores it, then keeps retransmitting the pre-reset `seq` into a session that restarted at 0. That is reproduced but deliberately left unfixed here -- see `TestConnectDuringSend` in `tests/test_protocol.py`, whose second test is `xfail(strict=True)` against the intended behaviour.

## [3.1.0] - 2026-08-11

### Added

- **`Urst.reassembly_in_progress`**, a read-only accessor letting a caller tell "nothing came back" from "still arriving" without reaching into private state.

  `read()` is single-shot: it returns `b""` as soon as a frame read times out (`ACK_TIMEOUT_MS`, 1s by default) while keeping the partial reassembly for a later call. §6.3.4's much longer reassembly deadline — `total_frags * (MAX_RETRIES + 1) * ACK_TIMEOUT_MS` — is therefore only reachable by calling `read()` again, which callers had no way to know they should do.

  A constrained peer streaming a large fragmented response routinely pauses longer than `ACK_TIMEOUT_MS` between fragments; ~1.2s gaps were measured mid-transfer on a Pico W sending a 56-fragment reply over a radio link. A caller treating the first `b""` as failure abandoned the transfer part-way and retried the whole request, colliding a fresh CONNECT with the still-streaming response and desynchronising both ends.

  No behaviour change to `read()` itself, so its single-shot contract and existing timeout test are untouched.

## [3.0.1] - 2026-08-10

### Added

- **Peers now validate each other's `protocol_version` during the CONNECT handshake and refuse to connect on a mismatch** (spec v0.4.1, new §5.6.1.1), with a new ERROR code `INCOMPATIBLE_VERSION` (0x02) reported back to the peer on a best-effort basis.

  **Nothing else in the protocol could detect a header-layout mismatch.** The CRC cannot: it covers the whole logical frame, and two implementations that disagree about the header length still checksum the *identical byte range*, differing only in how they interpret the disputed byte. Every frame therefore passes CRC on both sides.

  Found the hard way in a live deployment immediately after the 3.0.0 release: the device was still running 2.x (`protocol_version` 4, 2-byte header) while the host CLI ran 3.0.0 (5, 3-byte header). The CONNECT handshake succeeded on the first attempt every time, and the fault surfaced only as DATA frames that were never acknowledged — a signature indistinguishable from a marginal radio link, which is exactly how it was first misdiagnosed. (Root cause of the mismatch itself was a stale default branch; see `release.sh`'s new fast-forward step.)

  Spec v0.4.1 also **corrects a factual error introduced in v0.4.0**, which asserted that a version mismatch "will fail CRC validation on every frame". It does not, as the above demonstrates.
  - A CONNECT/CONNECT_ACK whose capability payload is missing or too short to carry a version is treated as a mismatch, not as an unversioned legacy peer to accommodate.
  - `connect()` fails fast on mismatch rather than burning all `MAX_RETRIES` attempts: retrying cannot change the peer's version.
  - `tests/test_core_handler.py` gains five tests covering both roles (initiator rejecting a mismatched CONNECT_ACK, responder rejecting a mismatched CONNECT with an ERROR and no CONNECT_ACK), the missing-payload case, fail-fast behaviour, and the matching-version happy path.

## [3.0.0] - 2026-08-10

### Added

- **Request ID header field and §5.8 request/response correlation** (spec v0.4.0, `protocol_version` 4→5, **breaking**). Frame header grows from 2 to 3 bytes (`[type][seq][request_id]`). A side awaiting a specific reply now discards any complete message whose Request ID doesn't match, instead of risking delivery of a stale message left over from an earlier exchange as the answer to an unrelated later request -- see `URST-Specification.md` §5.8 for full rationale and the live reproduction that prompted it (`diff-drive-robot`'s `Get Log`, over the same long-lived gateway PTY implicated in the URST-mpy#4 fix below).
  - `build_frame()`/`parse_frame()` gain a `request_id` field; `ProtocolLayer.send_reliable()` takes and echoes it across retries; ACK/NAK now echo it too.
  - `Urst.send(data, request_id=None)`: omit `request_id` to start a new request (a fresh ID is assigned and `read()` will then filter by it); pass it explicitly to reply to a received request (`request_id=urst.last_request_id`), or use the new `Urst.reply(data)` convenience (raises `RuntimeError` if nothing has been read yet).
  - Fragment reassembly is now keyed by `(request_id, msg_id)`, not `msg_id` alone (§5.8.4, §6.3.2) -- the FRAG payload's own `Message ID` remains a separate, fragmentation-only counter, unrelated to Request ID.
  - `tests/test_core_handler.py` covers stale-reply discarding, matching-reply delivery, explicit reply echoing, and the (request_id, msg_id) reassembly keying.

- **Fragment reassembly timeout (§6.3.4)**, previously a spec-MUST left unimplemented. An in-progress reassembly now expires after `total_frags * (MAX_RETRIES+1) * ACK_TIMEOUT_MS` and is discarded on the next `read()`.
  - `tests/test_core_handler.py::test_fragment_reassembly_times_out_and_is_discarded`.

- **`max_concurrent_message_ids` enforcement (§6.3.2)**, previously unimplemented -- a fragment for a second concurrent `(request_id, msg_id)` while one reassembly is already open is now rejected with an ERROR/CAPABILITY_EXCEEDED frame (`ProtocolLayer.send_error()`) instead of being silently merged into the wrong reassembly.
  - `tests/test_core_handler.py::test_fragmented_reassembly_rejects_a_concurrent_different_key_with_error`.

- **ABORT (§5.7.2) is no longer a defined-but-unused frame type.** Given a real payload (`reason_code` + `message_id`, was underspecified as "0-1 bytes"); a sender now sends it when it exhausts `MAX_RETRIES` mid-fragment-send (`ProtocolLayer.send_abort()`), and a receiver clears the matching reassembly state on receipt.
  - `tests/test_core_handler.py::test_send_aborts_a_fragmented_message_on_retry_exhaustion`, `test_abort_received_clears_matching_reassembly_state`.

## [2.0.0] - 2026-08-10

### Removed

- Stale package-level variable `__version__` from `urst/__init__.py` and its corresponding sanity test from `tests/sanity_tests.py` since the package version is managed via `pyproject.toml` and `package.json`.

### Fixed

- **`send_reliable()` retransmitted the identical frame after a NAK instead
  of re-establishing the connection, causing a livelock under real
  desynchronization.** §5.3.3 of the spec is explicit that an out-of-order
  frame (which is exactly what triggers a receiver to send NAK) "indicates
  protocol desynchronization" that "MUST be resolved via connection
  re-establishment (CONNECT)"; §5.6.2 likewise requires sending CONNECT
  "whenever either side detects persistent desynchronization that cannot be
  resolved via existing ACK/NAK/timeout logic." The implementation instead
  just logged a warning and resent the exact same bytes, which by
  definition cannot resolve a desync the peer has already rejected once.

  Reproduced against a real deployment (diff-drive-robot, over a marginal
  XBee radio link): an `upd` file transfer NAK-looped on the same chunk
  seq for 12 consecutive attempts (6 inner retries, then 6 more from the
  caller's own outer retry, since `next_send_seq` never advances without a
  successful ACK) before giving up entirely — a deterministic failure, not
  a probabilistic one, matching Issues_with_0.3.2.md #1 ("deadlock that can
  only resolve by chance") and #12 ("simultaneous transmission... potential
  livelock"). The same code path is shared by fragmented sends
  (`send_reliable(FRAME_FRAG, ...)`), so this is also a plausible
  contributor to `Error: Fragment transfer failed` on links flaky enough to
  desynchronize mid-transfer.

  `ProtocolLayer.send_reliable()` now calls `self.connect()` (which resets
  both sides' sequence numbers per §5.6.2) before retrying, on the first
  NAK it receives, rather than resending the rejected frame. If
  reconnection itself fails, `send_reliable()` now fails fast rather than
  continuing to retry a frame it has no way to deliver.
  - `tests/test_protocol.py::TestNakTriggersResync` covers both the happy
    path (NAK → CONNECT → retried frame with reset seq → ACK) and the
    give-up path (peer NAKs the CONNECT too).

- **Stale response frames from a previous session could be misdelivered as
  the answer to a later, unrelated request** ([#4](https://github.com/simonl65/URST-mpy/issues/4)).
  `ProtocolLayer.connect()` now calls `CodecLayer.discard_buffered()` once,
  before sending its own `CONNECT` frame, to drop anything already sitting
  on the transport.

  Found and reproduced against a real deployment (diff-drive-robot's
  gateway, which exposes the physical link as a long-lived PTY that many
  short-lived `otampy` CLI invocations connect to in turn): a `PONG` left
  over from an earlier `ping` was still sitting unread in the channel, and
  a later `cat` command's `send_reliable()` ACK-wait loop picked it up,
  queued it as if it were an interleaved payload for the new session, and
  handed it back as "the" response -- `Error: Unexpected response to
  command 'CAT:ota.log'. Expected prefix 'CAT_OK', got 'PONG'`. The same
  mechanism is the likely explanation for the intermittent, seemingly
  random `Error: Fragment transfer failed` reports on otherwise-healthy
  links: an old error response from a genuinely failed earlier transfer,
  resurfacing as the reply to a different, unrelated command.

  **Decision — drain-on-connect vs. per-request correlation IDs.** The
  protocol has no concept of a request/response correlation token beyond a
  sequence number that only the *local* side resets on reconnect; a
  device's long-lived `Urst` instance (constructed once at boot) has no
  notion of "sessions" at all. A wire-level fix (e.g. a session/epoch id in
  every frame) would close the gap completely but is a breaking protocol
  change. Draining already-buffered bytes at the start of `connect()`
  fixes the reproduced case outright, since the backlog only accumulates
  *between* sessions -- and is non-breaking. Accepted as sufficient for
  now; revisit if a race within an active session (not just between
  sessions) is ever demonstrated.
  - New `CodecLayer.discard_buffered()`, `constants.STALE_DRAIN_QUIET_MS`
    (50 ms) and `constants.STALE_DRAIN_MAX_MS` (250 ms).
  - `tests/test_core_handler.py` gained a direct reproduction: a stale
    `FRAME_DATA` frame pre-loaded on a fake transport is discarded by
    `connect()` and never reaches `_recv_queue`, while a genuine
    `CONNECT_ACK` produced only after the `CONNECT` is written still
    completes the handshake normally.
  - `tests/test_core_handler.py`'s `FakeSerial.in_waiting` changed from a
    fixed `0` stub to a property reflecting genuinely unread bytes -- the
    stub value meant `discard_buffered()` (which reads via `in_waiting`)
    had nothing real to observe in tests.
