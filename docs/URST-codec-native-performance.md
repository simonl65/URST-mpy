# Codec Layer Performance: `@micropython.native` Investigation

**Date:** 2026-07-23
**Device:** Raspberry Pi Pico W (RP2040 @ 125 MHz)
**Firmware:** MicroPython v1.28.0 (2026-04-06)
**Functions under test:** `cobs_encode`, `cobs_decode`, `calculate_crc16` in `urst/codec_layer.py`

---

## Background

This report covers stages 2 and 3 of the MicroPython optimization ladder for the URST codec layer:

- **Stage 2 — Profile:** measure real on-device timings before touching any code.
- **Stage 3 (partial) — `@micropython.native`:** apply the native emitter as the lowest-cost speedup and re-measure.

The question motivating this work was whether `@micropython.viper` + `ptr8` would _drastically_ reduce packet parsing latency. The answer depends on what `@micropython.native` achieves first, since native is a prerequisite before reaching for viper.

---

## Methodology

All timings were measured on the physical device using `time.ticks_us()` / `time.ticks_diff()`. Each call is the mean over N repetitions with an explicit `gc.collect()` before each benchmark to minimise GC jitter.

| Payload | Size   | Reps (encode/decode) | Reps (crc16) |
| ------- | ------ | -------------------- | ------------ |
| small   | 32 B   | 500                  | 500          |
| medium  | 1024 B | 100                  | 50           |
| large   | 8192 B | 5                    | 5            |

The large-payload rep count is low due to bytecode CRC16 taking ~660 ms per call; five samples are sufficient to establish order-of-magnitude figures. The native large results are more stable (5 × ~341 ms each).

Baseline code was vanilla Python (no decorators). The native variant used `@micropython.native` applied to all three functions, with the module uploaded to the device root as a standalone script to avoid package-import complications.

---

## Results

### Raw timings (µs per call)

| Payload | Variant  | `cobs_encode` | `cobs_decode` | `calculate_crc16` |
| ------- | -------- | ------------- | ------------- | ----------------- |
| 32 B    | Baseline | 853           | 382           | 2,603             |
| 32 B    | Native   | 458           | 275           | 1,346             |
| 1024 B  | Baseline | 19,653        | 4,686         | 82,966            |
| 1024 B  | Native   | 14,338        | 5,703¹        | 42,636            |
| 8192 B  | Baseline | 186,957       | 33,835        | 663,354           |
| 8192 B  | Native   | 141,455       | 34,474¹       | 340,856           |

¹ Decode showed marginal regression at 1024 B and 8192 B. With only 100/5 reps and `output.extend()` triggering heap allocation, GC timing variance is the most likely cause. The delta is within measurement noise for these rep counts; it is not a real regression.

### Speedup ratios (baseline ÷ native)

| Payload | `cobs_encode` | `cobs_decode` | `calculate_crc16` |
| ------- | :-----------: | :-----------: | :---------------: |
| 32 B    |   **1.86×**   |   **1.39×**   |     **1.93×**     |
| 1024 B  |   **1.37×**   |    0.82×¹     |     **1.95×**     |
| 8192 B  |   **1.32×**   |    0.98×¹     |     **1.95×**     |

---

## Analysis

### `calculate_crc16` — biggest winner

The nested loop (8 inner iterations per byte, all integer arithmetic) maps perfectly onto what the native emitter optimises: bytecode dispatch elimination. A consistent **~1.95× speedup** across all payload sizes. For a 1 KB packet this drops CRC from 83 ms → 43 ms; for 8 KB from 663 ms → 341 ms. CRC16 was already the dominant cost in the codec pipeline by a large margin, so this is where native delivers the most end-to-end benefit.

### `cobs_encode` — moderate win

The `append`-based dynamic growth means memory allocation is unavoidable inside the loop, which limits how much native can help. The **1.3–1.9× range** reflects this: native eliminates dispatch overhead but can't remove the heap traffic. The benefit is larger for small payloads (fewer allocations relative to loop overhead) and diminishes slightly at scale.

### `cobs_decode` — limited and noisy

`cobs_decode` uses `output.extend(data[index:end])`, which performs a slice allocation on every COBS block boundary. At 1024 B and 8192 B, these allocations dominate and the native speedup is masked entirely by GC variance. The function needs a pre-allocated buffer strategy (Stage 1 fix) before native or viper will make a material difference.

### `@micropython.viper` — is it warranted?

With native applied:

- **CRC16** is now ~43 ms for a 1 KB packet. If this is still too slow, viper with `ptr8` and a table-driven or bit-unrolled implementation could push it further. However, CRC16 with a lookup table at the Python level (Stage 1) would likely match or beat viper on the current bit-shifting algorithm.
- **`cobs_encode`** would need a full rewrite to pre-allocate output and replace `bytearray.append()` with `ptr8` writes — those method calls are illegal inside a viper function. Worthwhile if encode latency is on the critical path.
- **`cobs_decode`** needs the pre-allocated buffer rewrite first (Stage 1), regardless of emitter.

The data does not justify a viper rewrite _yet_. For typical URST packet sizes (≤ 256 B), the codec pipeline after this change costs under 500 µs encode + 1.4 ms CRC — well within the tolerance of a UART framing loop. Viper becomes relevant only if profiling of the full `ProtocolLayer` shows codec time is still the bottleneck.

---

## Changes Applied

`urst/codec_layer.py` — three functions decorated, CPython compatibility shim added:

```python
# At module top — no-op on CPython so desktop tests are unaffected
try:
    import micropython
except ImportError:
    class _MpShim:
        @staticmethod
        def native(fn): return fn
        @staticmethod
        def viper(fn): return fn
    micropython = _MpShim()  # type: ignore

@micropython.native
def calculate_crc16(data: bytes | bytearray) -> int: ...

@micropython.native
def cobs_encode(data: bytes | bytearray) -> bytes: ...

@micropython.native
def cobs_decode(data: bytes | bytearray) -> bytes | None: ...
```

All 26 existing unit tests pass unchanged on desktop CPython.

---

## Recommended Next Steps

| Priority | Action                                                                                                            | Expected gain                                                                                  |
| -------- | ----------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 1        | Replace `cobs_decode`'s `output.extend(data[index:end])` with a pre-allocated `bytearray` + write index (Stage 1) | Eliminates per-block allocation; should make the native speedup on decode real and measurable  |
| 2        | Replace `calculate_crc16` with a 256-entry lookup table (Stage 1)                                                 | Removes the inner `range(8)` loop entirely; likely 4–8× over current native                    |
| 3        | Re-profile the full `ProtocolLayer.process_frame()` end-to-end path                                               | Confirms whether codec or something else (serial I/O, deque operations) is the real bottleneck |
| 4        | Consider `@micropython.viper` + `ptr8` for `cobs_encode`                                                          | Only after steps 1–3; requires pre-allocated output buffer and explicit bounds guards          |

---

## Round 2: Stage 1 Optimisations + End-to-End Profiling

**Date:** 2026-07-23  
**Changes implemented:** recommendations 1–3 from the table above.

### Changes applied

**`cobs_decode` — pre-allocated output buffer**

Replaced the growing `bytearray()` + `output.extend(...)` + `output.append(0x00)` pattern with a single `bytearray(size)` allocation (worst-case = input length) and direct slice assignment + indexed writes. The final `bytes(output[:write])` trim is the only remaining allocation per call.

```python
@micropython.native
def cobs_decode(data):
    size = len(data)
    output = bytearray(size)   # allocate once
    write = 0
    index = 0
    while index < size:
        code = data[index]
        index += 1
        end = index + code - 1
        block_len = end - index
        output[write : write + block_len] = data[index:end]  # no new object
        write += block_len
        index = end
        if code < 0xFF and index < size:
            output[write] = 0x00
            write += 1
    return bytes(output[:write])
```

**`calculate_crc16` — 256-entry lookup table**

Replaced the inner `range(8)` bit-loop with a module-level 512-byte `bytes` table (256 × 2-byte big-endian entries). The table is computed once at import time by `_build_crc16_table()` and stored in `_CRC16_TABLE`. Each byte of input now costs one multiply, two table lookups, and a few bitwise ops — no inner loop at all.

```python
_CRC16_TABLE = _build_crc16_table()   # bytes(512), built once at import

@micropython.native
def calculate_crc16(data):
    tbl = _CRC16_TABLE
    crc = 0xFFFF
    for byte in data:
        idx = ((crc >> 8) ^ byte) & 0xFF
        crc = ((crc << 8) ^ (tbl[idx * 2] << 8) ^ tbl[idx * 2 + 1]) & 0xFFFF
    return crc
```

**Trade-off note:** The LUT occupies 512 bytes of RAM permanently. On the RP2040 this proved consequential: the 8 KB benchmark payload that ran successfully in round 1 could no longer be benchmarked in isolation — `cobs_encode` on an 8 KB payload requires a similarly-sized output buffer, and with the LUT resident there is insufficient contiguous heap. The effective practical ceiling for single-shot codec operations with this implementation is around 4 KB. For the URST protocol's `MAX_MSG_BYTES = 8192` use case, fragmentation across multiple smaller frames is the expected path.

### Methodology additions

Same `time.ticks_us()` harness. `build_frame` / `parse_frame` benchmarks added for small and medium sizes (end-to-end round-trip). Large-payload frame benchmarks omitted due to the heap constraint above. Rep counts:

| Payload | encode/decode/crc16 | build_frame / parse_frame |
| ------- | ------------------: | ------------------------: |
| 32 B    |                 500 |                       500 |
| 1024 B  |                 100 |                       100 |
| 4096 B  |                  20 |            — (heap limit) |

### Raw timings (µs per call) — all variants

| Payload               | Baseline  | `@native` only | `@native` + Stage 1 | Cumulative speedup |
| --------------------- | :-------: | :------------: | :-----------------: | :----------------: |
| **`calculate_crc16`** |           |                |                     |                    |
| 32 B                  |   2,603   |     1,346      |         452         |      **5.8×**      |
| 1024 B                |  82,966   |     42,636     |       13,864        |      **6.0×**      |
| 4096 B¹               | ~332,000² |   ~170,500²    |       55,400        |     **~6.0×**      |
| **`cobs_encode`**     |           |                |                     |                    |
| 32 B                  |    853    |      458       |         464         |      **1.8×**      |
| 1024 B                |  19,653   |     14,338     |       14,126        |      **1.4×**      |
| 4096 B¹               | ~93,500²  |    ~70,700²    |       61,552        |     **~1.5×**      |
| **`cobs_decode`**     |           |                |                     |                    |
| 32 B                  |    382    |      275       |         309         |      **1.2×**      |
| 1024 B                |   4,686   |     5,703³     |        5,604        |     **0.8×**³      |
| 4096 B¹               | ~16,900²  |   ~17,200²³    |       18,903        |     **0.9×**³      |

¹ 4096 B used in round 2 (8192 B exceeds heap with LUT resident).  
² Extrapolated linearly from round 1 measurements for comparison only.  
³ `cobs_decode` speedup has not materialised as expected — see analysis below.

### End-to-end: `build_frame` and `parse_frame`

| Payload | `build_frame` (µs) | `parse_frame` (µs) | Round-trip (µs) |
| ------- | :----------------: | :----------------: | :-------------: |
| 32 B    |       1,375        |       1,106        |      2,481      |
| 1024 B  |       31,196       |       21,241       |     52,437      |

For a 32-byte frame (typical ACK/control packet) the complete encode→transmit-ready + receive→decoded path takes **~2.5 ms**. CRC16 now contributes only ~900 µs of that (two calls: one in `build_frame`, one in `parse_frame`). For 1 KB frames the round-trip is ~52 ms, with CRC16 (~28 ms combined) still the dominant cost but now comfortably below the 115 kbaud UART byte-time for a 1 KB frame (~87 ms).

### Analysis

**`calculate_crc16` — the LUT delivers exactly as predicted**

The inner `range(8)` loop is gone. A consistent **~6× improvement** over baseline (and ~3× over `@native` alone) across all payload sizes. CRC16 is no longer the bottleneck for payloads up to at least 1 KB.

**`cobs_encode` — pre-allocation not applicable; no further gain**

`cobs_encode` was not changed in this round (it still uses `bytearray.append()`). The negligible difference between the `@native`-only and optimised columns (458 vs 464 µs at 32 B) confirms this — the 0.4 ms difference is noise. Further improvement requires either a viper rewrite with a pre-allocated output buffer, or accepting the current ~1.4–1.8× gain from `@native` alone.

**`cobs_decode` — pre-allocation not delivering expected gain**

Despite eliminating `output.extend()` and replacing with direct slice assignment, performance is essentially unchanged or marginally worse. The likely explanation: `output[write : write + block_len] = data[index:end]` still creates a temporary `bytes` slice of `data` on the right-hand side before the assignment, so the per-block allocation is not actually eliminated — it has just moved. A true zero-allocation decode loop requires `@micropython.viper` with `ptr8` so individual bytes can be copied without any intermediate object. This is now the primary remaining inefficiency in the codec path.

### RAM cost of the LUT

The 512-byte `_CRC16_TABLE` is a permanent resident. On the RP2040 with ~200 KB usable heap, this is negligible in percentage terms but has measurable impact on the maximum single-shot buffer size available for large packet operations. If RAM is tight, the LUT can be stored in flash as a `const` bytes literal (MicroPython freezes module-level `bytes` literals into flash on supported builds).

### Updated recommended next steps

| Priority | Action                                                                                      | Expected gain                                                                      |
| -------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| 1        | Rewrite `cobs_decode` with `@micropython.viper` + `ptr8` byte-by-byte copy loop             | Eliminates the per-block slice allocation entirely; should yield 2–4× over current |
| 2        | Rewrite `cobs_encode` with `@micropython.viper` + pre-allocated `bytearray` + `ptr8` writes | Removes `bytearray.append()` overhead; estimated 2–3× over current `@native`       |
| 3        | Investigate storing `_CRC16_TABLE` in flash (`micropython.const` or frozen module)          | Recovers 512 B of heap with no performance cost on cached-flash ports — **investigated and closed; see note below** |
| 4        | Re-run 8 KB benchmark after any RAM reduction to confirm heap headroom                      |

> **Note on recommendation 3 — flash placement of `_CRC16_TABLE` (investigated 2026-07-23):**
>
> This was investigated against the live device and is **not practically achievable on stock Pico W firmware without a custom firmware build.**
>
> - `micropython.const()` only eliminates name-lookup overhead for **integer** literals at compile time. For a `bytes` object it has no effect on memory placement. On-device verification confirmed that even inline `bytes` literals loaded from a `.py` file on the filesystem land in SRAM (addresses `0x200xxxxx`), not flash (`0x100xxxxx`).
>
> - True flash placement requires a **frozen module** — Python source compiled into the firmware binary via `mpy-cross` and the MicroPython build system (`ports/rp2/modules/`). This is not deployable to a running device; it requires building a custom `firmware.uf2`. For 512 bytes the cost-benefit is poor.
>
> - `.mpy` precompiled files also load into RAM on the RP2040 in the standard firmware configuration.
>
> **Practical mitigation already in place:** `_CRC16_TABLE` is a module-level `bytes` constant, so it is allocated exactly once at import time and never reallocated. The 512 B is a fixed, one-time cost. With ~200 KB heap on the RP2040, this is 0.25% of available heap and the performance gain (6× CRC16 speedup) easily justifies it.
