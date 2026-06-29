# URST COBS Codec Performance Improvements (Stage 5)

This document outlines how to optimize the byte-stuffing and decoding bottlenecks in the underlying `urst` communication dependency using MicroPython's **Stage 5 Viper Code Emitter** (`@micropython.viper`).

COBS (Consistent Overhead Byte Stuffing) is a highly CPU-bound, loop-intensive, byte-by-byte operation. Optimizing this layer provides the largest system-wide latency reduction for OTA packet transmission.

---

## 1. COBS Decoding: Bytecode vs. Viper

Below is a comparison of a standard bytecode COBS decoder implementation versus an optimized Stage 5 Viper implementation.

### A. Standard Bytecode Implementation (Python)
```python
def cobs_decode(data: bytes) -> bytearray:
    """Decode a COBS-encoded byte sequence."""
    out = bytearray()
    idx = 0
    length = len(data)
    
    while idx < length:
        code = data[idx]
        if code == 0:
            raise ValueError("Zero byte in COBS frame")
            
        # Copy data bytes
        for i in range(1, code):
            if idx + i >= length:
                raise ValueError("Unexpected end of frame")
            out.append(data[idx + i])
            
        idx += code
        if code < 0xff and idx < length:
            out.append(0)  # Re-insert the zero byte
            
    return out
```

### B. Optimized Viper Implementation (Stage 5)
```python
import micropython

@micropython.viper
def cobs_decode_viper(data_in, out_buf) -> int:
    """
    Decode a COBS frame in-place or into a pre-allocated output buffer.
    Returns the size of the decoded payload.
    """
    # 1. Cast Python objects to Viper pointers once at entry (zero overhead inside loop)
    src = ptr8(data_in)
    dst = ptr8(out_buf)
    
    src_len = int(len(data_in))
    src_idx = 0
    dst_idx = 0
    
    while src_idx < src_len:
        code = int(src[src_idx])
        if code == 0:
            return -1  # Error: invalid frame marker
            
        # Copy data bytes directly (Machine-word pointer arithmetic, no bounds checking)
        for i in range(1, code):
            if src_idx + i >= src_len:
                return -2  # Error: out of bounds
            dst[dst_idx] = src[src_idx + i]
            dst_idx += 1
            
        src_idx += code
        if code < 0xff and src_idx < src_len:
            dst[dst_idx] = 0
            dst_idx += 1
            
    return dst_idx  # Return number of decoded bytes written
```

---

## 2. Why the Viper Implementation is Faster

1. **Elimination of Object Allocations**: 
   - The standard implementation uses `out.append()`, which repeatedly allocates heap memory as the `bytearray` grows.
   - The Viper version writes directly into a pre-allocated buffer (`out_buf`) using `ptr8`, completely avoiding runtime allocations and GC spikes during reception.
2. **Direct Memory Access (`ptr8`)**:
   - `ptr8` bypasses the virtual machine's array lookup overhead and safety checks. Reading `src[idx]` and writing `dst[idx]` compile directly to native CPU load/store instructions.
3. **Machine Word Math**:
   - Integer increments and boundary checks are performed using the CPU's native word width, bypassing Python's arbitrary-precision integer wrapper.
4. **Hoisted Casts**:
   - Type hints like `data_in` and `out_buf` are resolved to pointer addresses once at function entry. Inside the `while` loop, zero Python object references are looked up.

---

## 3. Implementation Steps in `urst`

To apply this to your `URST-mpy` repository:

1. Import `micropython` at the top of `codec_layer.py`.
2. Pre-allocate an RX bytearray buffer (e.g., `self.rx_buffer = bytearray(256)`) in the transport layer initialization.
3. Replace the `decode` and `encode` methods in the codec layer with `@micropython.viper` decorated methods.
4. Pass the pre-allocated buffer to `cobs_decode_viper` to receive the data without garbage collection pauses.
