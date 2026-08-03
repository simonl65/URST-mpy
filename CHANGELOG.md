# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
correspond to PyPI releases of `urst-mpy` (see `release.sh`).

## Unreleased

### Removed

- Stale package-level variable `__version__` from `urst/__init__.py` and its corresponding sanity test from `tests/sanity_tests.py` since the package version is managed via `pyproject.toml` and `package.json`.

### Fixed

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
