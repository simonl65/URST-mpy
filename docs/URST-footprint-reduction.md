# URST MicroPython Footprint Reduction Analysis

This document identifies every concrete opportunity to reduce the flash and RAM
footprint of `urst` on MicroPython devices (Raspberry Pi Pico, ESP32, etc.).
Findings are grouped by category and followed by a prioritised todo list.

No code changes are included here — this is the planning artefact only.

---

## Baseline Measurements

Compiled with `mpy-cross` (the format MicroPython loads from flash):

| File                 | Source (.py)           | Compiled (.mpy)      | Saving       |
| -------------------- | ---------------------- | -------------------- | ------------ |
| `__init__.py`        | 671 B                  | 426 B                | 245 B        |
| `constants.py`       | 1,106 B                | 393 B                | 713 B        |
| `codec_layer.py`     | 4,705 B                | 1,318 B              | 3,387 B      |
| `transport_layer.py` | 223 B                  | 212 B                | 11 B         |
| `protocol_layer.py`  | 9,104 B                | 2,983 B              | 6,121 B      |
| `core_handler.py`    | 4,393 B                | 1,464 B              | 2,929 B      |
| **Total**            | **20,202 B (19.7 KB)** | **6,796 B (6.6 KB)** | **13,406 B** |

> The `.mpy` column is the real on-device cost when pre-compiled. The `.py`
> column matters if source files are deployed directly (common with `mpremote
mip install`).

---

## Finding 1 — Dead module: `transport_layer.py`

**Impact: flash (small), RAM (small), startup (small)**

`transport_layer.py` contains a `TransportLayer` class that has never been
imported or instantiated anywhere in the codebase — not in `__init__.py`,
`core_handler.py`, `protocol_layer.py`, or the tests. The class body is just
an `__init__` with a `logger.debug` call.

The file is 223 bytes of source and 212 bytes compiled. More importantly,
because MicroPython evaluates every `.py`/`.mpy` in the package directory at
import time, even dead code costs RAM for the module dict and the `logging`
import it triggers.

**Action:** Delete `transport_layer.py` and its test coverage (if any). Verify
no external consumers reference it.

---

## Finding 2 — Logging shim duplicated across four files

**Impact: flash (medium), RAM (medium), readability**

The 25-line `MockLogger` / `MockLogging` shim lives in `__init__.py` and is
re-imported as `from . import logging` in every other module. This is already
the right approach for `codec_layer.py`, `protocol_layer.py`, and
`core_handler.py` — but `__init__.py` itself defines the shim and then
_also_ imports the real `logging` for its own `logger = logging.getLogger()`
call, even though `__init__.py` never actually logs anything.

The concrete issues:

1. The `MockLogger` stub implements four methods (`debug`, `info`, `warning`,
   `error`) that each do nothing but `pass`. All four are defined redundantly
   in every device deploy.
2. `logger = logging.getLogger(__name__)` in `__init__.py` allocates a logger
   object that is never used.
3. On MicroPython with no `logging` package installed, the fallback mock is
   instantiated at module import — wasting RAM for four empty function objects.

**Option A — Minimal stub (recommended):** Replace the four-method mock with a
single `__getattr__` that swallows all attribute access:

```python
class _NoLog:
    def __getattr__(self, _): return lambda *a, **k: None
class _NoLogging:
    def getLogger(self, _): return _NoLog()
logging = _NoLogging()
```

This is ~5 lines instead of 25 and allocates fewer objects.

**Option B — Conditional logging module:** Extract the shim into a single
`urst/logging.py` that MicroPython-specific deploys can stub out at the
filesystem level (standard MicroPython pattern).

**Option C — Remove logging entirely from `__init__.py`:** The public
`__init__` only needs `from .core_handler import Urst`. The logger line and
shim can be deleted completely from that file.

---

## Finding 3 — Type annotations in source

**Impact: flash (medium) when deployed as `.py`**

When `.py` files are deployed directly (as `mpremote mip install` does),
type annotations are parsed and stored in the AST but contribute no runtime
behaviour. They add ~485 bytes across the three annotated files:

| File                | Approx annotation bytes |
| ------------------- | ----------------------- |
| `codec_layer.py`    | 234 B                   |
| `protocol_layer.py` | 175 B                   |
| `core_handler.py`   | 76 B                    |

Union types (`bytes | bytearray`, `dict | None`, `int | None`) are Python 3.10+
syntax and will raise `SyntaxError` on MicroPython 1.22 or earlier because
MicroPython's parser does not yet support PEP 604 union notation.

**Action:** Strip annotations from the deployed source, or move to a
MicroPython-safe subset (`Optional[X]` via a `TYPE_CHECKING` guard, or remove
entirely). This also removes the `from typing import Any` / `TYPE_CHECKING`
boilerplate in three files.

> Note: This is a **source deployment** concern only. `.mpy` compilation strips
> annotations automatically.

---

## Finding 4 — `micropython.const()` not used for integer constants

**Impact: RAM (medium — within the defining module only)**

`constants.py` defines 14 integer literals as plain module-level variables.

MicroPython provides `micropython.const()` to inline integer values at
bytecode compile time within the **same module**. A constant declared as:

```python
from micropython import const
FRAME_DATA = const(0x01)
```

…is substituted as a literal integer anywhere within `constants.py` itself.
Additionally, if a name starts with an underscore (e.g. `_FRAME_DATA`), the
constant is not added to the module dict at all, saving the RAM for that
entry.

**Critical limitation:** `const()` inlining only applies within the module
where the constant is declared. In `protocol_layer.py`, `constants.FRAME_DATA`
is still a two-step attribute lookup (`LOAD_ATTR constants` then
`LOAD_ATTR FRAME_DATA`) regardless of whether `const()` is used in
`constants.py`. The MicroPython compiler does not inline `const()` values
across module boundaries.

The correct way to get inlining in `protocol_layer.py` is to **import the
names directly** and declare local `const()` aliases:

```python
from .constants import FRAME_DATA as _FRAME_DATA
# or, if merged into the same module:
_FRAME_DATA = const(0x01)
```

`protocol_layer.py` makes **43 references** to `constants.X` values,
including 12 inside `receive_frame()`. The full benefit requires either
merging constants into the module that uses them, or using direct `from X
import Y` imports combined with local `const()` re-declaration.

**Caveat:** `micropython.const()` is a no-op on CPython (the function exists
but does nothing), so this change is safe for the desktop/test path. The
`from micropython import const` import must be guarded:

```python
try:
    from micropython import const
except ImportError:
    def const(x): return x
```

---

## Finding 5 — Inline set literals in hot paths allocate on every call

**Impact: RAM (small but repeated)**

MicroPython does not fold constant set literals like CPython does. Each
`in {A, B, C}` expression inside a function allocates a new set object on
every invocation. The following hot-path locations are affected:

| Location                                                                   | Call frequency                                      |
| -------------------------------------------------------------------------- | --------------------------------------------------- |
| `_is_empty_payload_only_type()`                                            | called from `parse_frame()` on every received frame |
| `receive_frame()` line 225: `{FRAME_ACK, FRAME_NAK}`                       | every received frame                                |
| `receive_frame()` lines 228–232: `{FRAME_DATA, FRAME_FRAG, FRAME_CONNECT}` | every received frame                                |
| `send_reliable()` lines 198–201: `{FRAME_DATA, FRAME_FRAG}`                | during every ACK wait loop                          |
| `read()` lines 133–135: `{FRAME_CONNECT, FRAME_CONNECT_ACK}`               | every non-data frame                                |

**Recommended approach — module-level `tuple` constants (not `frozenset`):**
`frozenset` is available as a builtin on MicroPython, but `tuple` is simpler,
universally supported across all ports, and `in` membership testing is
identical for this use case:

```python
_ACK_NAK_TYPES = (FRAME_ACK, FRAME_NAK)
_PAYLOAD_TYPES = (FRAME_DATA, FRAME_FRAG, FRAME_CONNECT)
_EMPTY_PAYLOAD_TYPES = (FRAME_ACK, FRAME_NAK, FRAME_BUSY, FRAME_READY)
```

For the two-element cases, a chained `or` avoids even the tuple lookup:

```python
if ft == FRAME_ACK or ft == FRAME_NAK:
```

This generates two `COMPARE_OP` bytecodes with zero heap allocation.

---

## Finding 6 — `serialize_crc()` imports `struct` inside the function body

**Impact: RAM (small), CPU (small)**

`codec_layer.py` defers `import struct` into the body of `serialize_crc()`.
On CPython this is cached after the first call and costs nothing. On
MicroPython, `import` inside a function body re-executes the module lookup on
every invocation because MicroPython's import cache behaviour differs.

Additionally, `serialize_crc` can be replaced by two bytes of bit-arithmetic
with no `struct` dependency at all:

```python
def serialize_crc(crc):
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])
```

This removes the `struct` import from `codec_layer.py` entirely (it is already
top-level in `protocol_layer.py` so `struct` itself is not eliminated from the
device, but the per-call overhead is removed).

---

## Finding 7 — `math.ceil` imported for a single integer division

**Impact: flash (small), RAM (small)**

`core_handler.py` imports `math` solely for `math.ceil(len(data) /
max_frag_data)`. `math` is a non-trivial module on MicroPython (it includes
floating-point trigonometry etc.).

Integer ceiling division is expressible without `math`:

```python
total_frags = (len(data) + max_frag_data - 1) // max_frag_data
```

This eliminates the `import math` line and its RAM cost.

---

## Finding 8 — `_CONNECT_PAYLOAD` recalculated on every handshake call

**Impact: CPU (small), RAM (small)**

Both `connect()` and `receive_frame()` contain:

```python
payload = struct.pack("<BHBBHBB", 4, 8192, 32, 1, 1000, 3, 0)
```

This allocates a new `bytes` object and runs `struct.pack` every time either
function is called. Because the values are fixed protocol constants they should
be a module-level constant:

```python
_CONNECT_PAYLOAD = b'\x04\x00\x20\x20\x01\xe8\x03\x03\x00'
```

or kept as `struct.pack(...)` at module level (evaluated once at import).

---

## Finding 9 — `_recv_queue` is a `list` with `pop(0)`

**Impact: RAM/CPU (small but systemic)**

`ProtocolLayer._recv_queue` is a `list` used as a FIFO queue with
`append()` (O(1)) and `pop(0)` (O(n)). On MicroPython, `list.pop(0)`
causes a full element shift in memory. For typical usage where the queue
rarely holds more than one or two items the cost is negligible, but a
`collections.deque` would give O(1) pops and is available on MicroPython.

**Important MicroPython difference:** Unlike CPython's `deque(maxlen=None)`
for an unbounded queue, MicroPython's `deque` requires `maxlen` to be a
positive integer — `maxlen=0` creates a zero-capacity deque, not an
unbounded one. Use `MAX_FRAGMENTS` as the practical upper bound (the queue
can never legitimately grow larger than the number of in-flight fragments):

```python
from collections import deque
from .constants import MAX_FRAGMENTS
self._recv_queue = deque((), MAX_FRAGMENTS)
# append: self._recv_queue.append(p)
# pop:    self._recv_queue.popleft()
```

The CPython test path is also compatible since CPython's `deque` accepts a
positional integer `maxlen`.

---

## Finding 10 — Pre-compiled `.mpy` deployment not documented/automated

**Impact: flash (large — 66% saving), startup latency (large)**

As the baseline table shows, pre-compiling with `mpy-cross` reduces the total
package from **19.7 KB to 6.6 KB** — a 66% reduction in flash usage and a
significant reduction in parse time at boot. The current `package.json` (used
by `mpremote mip install`) ships raw `.py` files.

`package.json` supports a `"urls"` field that can point to `.mpy` files
compiled for a specific MicroPython ABI version. Alternatively, a `Makefile`
or CI step can run `mpy-cross` and produce a `dist/` directory for manual
installs.

This is the **single highest-impact** change available without touching any
source code.

---

## Finding 11 — `@micropython.viper` for CRC-16 and COBS inner loops

**Impact: CPU (large), RAM (medium — avoids GC pressure)**

This is already explored in `docs/URST-performance-improvements.md`.
Key additions from this analysis:

**CRC-16:** The nested-loop implementation iterates 8 times per input byte.
For a 200-byte payload that is 1,600 loop iterations, each executing a
conditional expression and two bitwise operations as Python objects. A Viper
or `@micropython.native` implementation would cut this to near-native speed.

A pre-computed 256-entry lookup table eliminates the inner loop entirely
(one table lookup per byte instead of 8 iterations). The table costs 512 bytes
of flash as binary data, or ~1,505 bytes as a Python tuple literal — the latter
is larger than the current loop source so the table approach only makes sense
if stored as a `bytes` literal (512 B) or compiled into `.mpy`.

**COBS encode/decode:** As detailed in the existing performance doc, wrapping
with `@micropython.viper` and writing into a pre-allocated buffer eliminates
repeated `bytearray.append()` heap allocations. The pre-allocated buffer
should live on `CodecLayer` (e.g. `self._cobs_buf = bytearray(256)`), sized
to `MAX_PAYLOAD_SIZE + overhead`.

**Dependency:** Viper requires `mpy-cross` targeting the correct MicroPython
ABI; the desktop test path needs a fallback. The existing `@micropython.native`
decorator (Stage 4) is a safer intermediate step — it removes Python object
overhead without requiring the strict type contracts of Viper.

---

## Finding 12 — `__init__.py` exports an unused `logger`

**Impact: RAM (trivial)**

`__init__.py` creates `logger = logging.getLogger(__name__)` at module level
but never calls it. This allocates a logger object (or a `MockLogger`) for no
purpose.

---

## Prioritised Todo List

Items are ordered by impact-to-effort ratio. "Flash" savings are in bytes of
`.py` source (relevant for direct deploy) / `.mpy` compiled (relevant for
pre-compiled deploy). All items preserve full functionality.

---

### ✅ Priority 1 — Pre-compile to `.mpy` and update `package.json`

**Effort:** Low | **Flash saving:** ~13 KB (66%) | **Startup saving:** Large

- [ ] Add a `Makefile` or `justfile` target that runs `mpy-cross` over all
      `urst/*.py` and writes output to `dist/urst/`.
- [ ] Update `package.json` to point `"urls"` at the `.mpy` files in the
      repository (requires committing compiled artefacts or using a CI release
      workflow).
- [ ] Document the two install paths (source vs pre-compiled) in `README.md`.

---

### ✅ Priority 2 — Delete dead `transport_layer.py`

**Effort:** Trivial | **Flash saving:** 223 B (.py) / 212 B (.mpy) | **RAM
saving:** 1 module dict + logger object

- [ ] Confirm no external consumers import `TransportLayer`.
- [ ] Delete `urst/transport_layer.py`.
- [ ] Remove any test or example references.

---

### ✅ Priority 3 — Replace `import math` with integer arithmetic

**Effort:** Trivial | **Flash saving:** removes `math` module load |
**RAM saving:** `math` module dict

- [ ] In `core_handler.py`, replace:
  ```python
  import math
  …
  total_frags = math.ceil(len(data) / max_frag_data)
  ```
  with:
  ```python
  total_frags = (len(data) + max_frag_data - 1) // max_frag_data
  ```
- [ ] Remove the `import math` line.

---

### ✅ Priority 4 — Apply `micropython.const()` to `constants.py` and direct imports in `protocol_layer.py`

**Effort:** Low | **RAM saving:** underscore-prefixed constants not added to
module dict; within-module inlining reduces lookup overhead |
**Important caveat:** `const()` only inlines values within the _declaring_
module — cross-module `constants.FRAME_X` references in `protocol_layer.py`
are **not** inlined automatically.

- [ ] Add `const` import shim to `constants.py`:
  ```python
  try:
      from micropython import const
  except ImportError:
      def const(x): return x
  ```
- [ ] Wrap all 14 integer constants with `const(…)` and prefix private ones
      with `_` (e.g. `_FRAME_DATA = const(0x01)`) so they are not stored in the
      module dict at all.
- [ ] In `protocol_layer.py` and other callers, switch from
      `constants.FRAME_X` attribute lookups to direct `from .constants import
FRAME_X` imports, then re-declare as local `const()` aliases for full
      inlining benefit.
- [ ] Verify tests still pass (CPython: `const` is identity function, no
      behaviour change).

---

### ✅ Priority 5 — Hoist `_CONNECT_PAYLOAD` to a module constant

**Effort:** Trivial | **RAM saving:** avoids two `bytes` allocations and two
`struct.pack` calls per connect handshake

- [ ] In `protocol_layer.py`, add at module level:
  ```python
  _CONNECT_PAYLOAD = struct.pack("<BHBBHBB", 4, 8192, 32, 1, 1000, 3, 0)
  ```
- [ ] Replace both inline `struct.pack(...)` calls in `connect()` and
      `receive_frame()` with `_CONNECT_PAYLOAD`.

---

### ✅ Priority 6 — Replace `serialize_crc` struct dependency with bit ops

**Effort:** Trivial | **Flash saving:** removes `struct` import from
`codec_layer.py` | **CPU saving:** removes per-call import lookup

- [ ] Replace `serialize_crc` body:
  ```python
  def serialize_crc(crc):
      return bytes([crc & 0xFF, (crc >> 8) & 0xFF])
  ```
- [ ] Remove the `import struct` inside `serialize_crc`.

---

### ✅ Priority 7 — Hoist inline set literals to module-level tuple constants

**Effort:** Low | **RAM saving:** eliminates repeated set allocation in hot
receive/send loops

- [ ] In `protocol_layer.py`, add module-level constants using `tuple` (not
      `frozenset` — prefer `tuple` for universal MicroPython port compatibility):
  ```python
  _ACK_NAK_TYPES = (FRAME_ACK, FRAME_NAK)
  _PAYLOAD_TYPES = (FRAME_DATA, FRAME_FRAG, FRAME_CONNECT)
  _EMPTY_PAYLOAD_TYPES = (FRAME_ACK, FRAME_NAK, FRAME_BUSY, FRAME_READY)
  ```
- [ ] For two-element cases, prefer chained `or` instead (zero allocation):
  ```python
  if ft == FRAME_ACK or ft == FRAME_NAK:
  ```
- [ ] Replace all inline `{…}` set literals with the named tuple constants
      or `or` chains.
- [ ] Do the same for the two inline sets in `core_handler.py`.

---

### ✅ Priority 8 — Slim down the logging shim in `__init__.py`

**Effort:** Low | **Flash saving:** ~500 B | **RAM saving:** 4 function objects
per MockLogger instance

- [ ] Replace the 25-line `MockLogger`/`MockLogging` block in `__init__.py`
      with the 5-line `__getattr__` variant (see Finding 2, Option A).
- [ ] Remove the dead `logger = logging.getLogger(__name__)` line from
      `__init__.py`.
- [ ] Verify the shim in the other three modules (`from . import logging`)
      still resolves correctly.

---

### ✅ Priority 9 — Strip type annotations from source files for device deploy

**Effort:** Medium | **Flash saving:** ~485 B (.py source) | **Compatibility:**
fixes potential `SyntaxError` on MicroPython < 1.22 for PEP 604 unions

- [ ] Audit all `X | Y` union annotations — these will fail on older
      MicroPython. Replace with unannotated or `Optional` style under
      `TYPE_CHECKING` guard.
- [ ] Consider adding a `tools/strip_annotations.py` script (using `ast`) that
      produces a `dist/` version with annotations removed, keeping the source tree
      clean and fully annotated for development.
- [ ] Alternatively, rely entirely on `.mpy` compilation (Priority 1) since
      `mpy-cross` strips annotations automatically.

---

### ✅ Priority 10 — Replace `list` FIFO with `collections.deque`

**Effort:** Low | **CPU saving:** O(1) vs O(n) pop from front of queue

- [ ] In `protocol_layer.py`, change `_recv_queue` initialisation to:
  ```python
  from collections import deque
  from .constants import MAX_FRAGMENTS
  self._recv_queue = deque((), MAX_FRAGMENTS)
  ```
  **Note:** MicroPython's `deque` requires a positive integer `maxlen` —
  `maxlen=0` is a zero-capacity deque, not unbounded. `MAX_FRAGMENTS` (32) is
  the correct upper bound since the queue can never hold more frames than the
  maximum number of fragments in a message.
- [ ] Change `self._recv_queue.pop(0)` to `self._recv_queue.popleft()`.
- [ ] Guard the import: `collections.deque` is available on MicroPython 1.17+.
- [ ] Update tests that inspect `_recv_queue` directly.

---

### ✅ Priority 11 — `@micropython.native` / `@micropython.viper` for COBS and CRC

**Effort:** High | **CPU saving:** Largest available — 3–10× on inner loops |
**RAM saving:** Eliminates repeated heap allocation in COBS encode/decode

This extends the work described in `docs/URST-performance-improvements.md`.

- [ ] Add `@micropython.native` to `calculate_crc16`, `cobs_encode`, and
      `cobs_decode` as a low-risk first step (no API change, no pre-allocated
      buffers required).
- [ ] Add `try: from micropython import native, viper except ImportError: ...`
      shims so the code remains testable on CPython.
- [ ] Pre-allocate `self._cobs_buf = bytearray(MAX_PAYLOAD_SIZE + 4)` on
      `CodecLayer.__init__` for the Viper encode/decode target buffer.
- [ ] Implement Viper versions of `cobs_encode` and `cobs_decode` per the
      existing doc, updating their call sites in `protocol_layer.py`.
- [ ] Implement a Viper or lookup-table version of `calculate_crc16`.
- [ ] Benchmark before/after on target hardware (Pico / ESP32).

---

## Summary of Expected Savings

| Change                           | Flash saving      | RAM saving              | Effort  |
| -------------------------------- | ----------------- | ----------------------- | ------- |
| Pre-compile `.mpy` (P1)          | ~13 KB            | boot-time parse         | Low     |
| Delete `transport_layer.py` (P2) | 212–223 B         | 1 module dict           | Trivial |
| Remove `math` import (P3)        | ~100 B            | `math` module           | Trivial |
| `micropython.const()` (P4)       | none              | 14 attr lookups inlined | Low     |
| Hoist `_CONNECT_PAYLOAD` (P5)    | none              | 2 heap allocs/connect   | Trivial |
| `serialize_crc` bit ops (P6)     | ~30 B             | per-call import         | Trivial |
| Hoist set literals (P7)          | ~50 B             | repeated set allocs     | Low     |
| Slim logging shim (P8)           | ~500 B            | 4 fn objects            | Low     |
| Strip type annotations (P9)      | ~485 B (.py only) | none                    | Medium  |
| `deque` for queue (P10)          | none              | O(1) pop                | Low     |
| Viper/native emitters (P11)      | none              | GC pressure             | High    |

> Priorities 1–9 combined (excluding Viper) deliver approximately **14–15 KB**
> of flash savings on a direct `.py` deploy, or an additional **~1 KB** on top
> of a pre-compiled deploy, plus measurable RAM and CPU improvements on the hot
> receive path.
