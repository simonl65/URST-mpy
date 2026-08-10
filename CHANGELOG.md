# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
correspond to PyPI releases of `urst-mpy` (see `release.sh`).

## Unreleased

### Added

- **Request ID header field and §5.8 request/response correlation** (spec v0.4.0, `protocol_version` 4→5, **breaking**). Frame header grows from 2 to 3 bytes (`[type][seq][request_id]`). A side awaiting a specific reply now discards any complete message whose Request ID doesn't match, instead of risking delivery of a stale message left over from an earlier exchange as the answer to an unrelated later request -- see `URST-Specification.md` §5.8 for full rationale and the live reproduction that prompted it (`diff-drive-robot`'s `Get Log`, over the same long-lived gateway PTY implicated in the URST-mpy#4 fix below).
  - `build_frame()`/`parse_frame()` gain a `request_id` field; `ProtocolLayer.send_reliable()` takes and echoes it across retries; ACK/NAK now echo it too.
  - `Urst.send(data, request_id=None)`: omit `request_id` to start a new request (a fresh ID is assigned and `read()` will then filter by it); pass it explicitly to reply to a received request (`request_id=urst.last_request_id`).
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
