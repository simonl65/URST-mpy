# URST Protocol Specification

![Status](https://img.shields.io/badge/status-draft-orange)

**Version:** 0.3.3  
**Status:** Draft  
**Date:** 2025-10-23  
**Authors:** Simon R. Lincoln  
**Copyright:** © 2025 Simon R. Lincoln  
**License:** Specification is freely implementable; reference code under [Sustainable Use License](LICENSE.md)

---

## Abstract

This document specifies the **Universal Reliable Serial Transport** (URST) protocol, a lightweight - _**acknowledgment before next transmission**_ - communication protocol designed for reliable data transmission over serial connections in resource-constrained embedded systems. URST provides automatic retransmission, error detection via CRC-16/CCITT_FALSE, frame delimiting through COBS encoding, and support for message fragmentation.

---

## Status of This Memo

This document is a **draft** specification and is subject to change. It is provided for implementation and testing purposes.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Protocol Architecture](#2-protocol-architecture)
3. [Message Format Specification](#3-message-format-specification)
4. [Protocol State Machines](#4-protocol-state-machines)
5. [Protocol Operations](#5-protocol-operations)
6. [Fragmentation Protocol](#6-fragmentation-protocol)
7. [Conformance Requirements](#7-conformance-requirements)
8. [Security Considerations](#8-security-considerations)
9. [IANA Considerations](#9-iana-considerations)
10. [References](#10-references)
11. [Glossary](#11-glossary)

Appendices:

- [Appendix A. Specification Checklist](#appendix-a-specification-checklist)
- [Appendix B. Future Protocol Extensions](#appendix-b-future-protocol-extensions)
- [Appendix C. Questions & Answers](#appendix-c-questions-and-answers)
- [CHANGELOG](#appendix-d-change-log)

---

## 1. Introduction

### 1.1 Requirements Language

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in RFC 2119.

### 1.2 Terminology

- **Frame**: A complete unit of transmission including header, payload, CRC, and COBS encoding
- **Payload**: The application data carried within a frame (0-200 bytes)
- **Sequence Number**: An 8-bit counter used to detect duplicates and ordering
- **Fragment**: A portion of a larger message split for transmission
- **Stop-and-Wait**: A flow control mechanism requiring acknowledgment before next transmission

### 1.3 Protocol Overview

URST implements a four-layer architecture providing reliable delivery over unreliable serial connections. The protocol uses stop-and-wait flow control with automatic retransmission, CRC error detection, and COBS encoding for frame delimiting.

**Design Goals:**

- Reliable delivery with automatic retransmission
- Minimal overhead suitable for microcontrollers
- Zero-byte-free encoding for robust frame delimiting
- Support for large message fragmentation and reassembly
- Clear connection establishment and capability negotiation for robust operation

**Important:** URST is a strict stop-and-wait protocol. Implementations MUST enforce stop-and-wait semantics: a sender MUST NOT transmit a new DATA or FRAG frame until it has received the ACK/NAK (or an explicit READY when applicable) for the previously transmitted frame.

---

## 2. Protocol Architecture

### 2.1 Layer Model

```
┌───────────────────────────────────┐
│    Handler Layer (Application)    │  User API: send(), receive()
├───────────────────────────────────┤
│     Protocol Layer (Reliable)     │  CONNECT/ACK/NAK,
|                                   |  Retransmission
├───────────────────────────────────┤
│     Transport Layer (Framing)     │  Frame Type, Sequence Numbers
├───────────────────────────────────┤
│    Codec Layer (Encoding/IO)      │  COBS, CRC, UART
└───────────────────────────────────┘
```

### 2.2 Layer Responsibilities

#### 2.2.1 Handler Layer

The Handler Layer MUST provide:

- Simple `send()` and `receive()` interfaces for applications
- Automatic fragmentation of messages exceeding MAX_PAYLOAD_SIZE
- Reassembly of fragmented messages
- Queueing of complete messages for application retrieval
- Enforce "one concurrent fragment reassembly" policy (see §6.3.2)

#### 2.2.2 Protocol Layer

The Protocol Layer MUST provide:

- Reliable message delivery with acknowledgments
- Sequence number management for duplicate detection
- Retransmission logic with timeout and retry limits
- ACK and NAK frame generation
- Connection establishment and capability negotiation via CONNECT/CONNECT_ACK (see §5.6)

#### 2.2.3 Transport Layer

The Transport Layer MUST provide:

- Frame header construction (frame type and sequence number)
- Frame header parsing and validation
- Routing of frames to appropriate protocol handlers

#### 2.2.4 Codec Layer

The Codec Layer MUST provide:

- COBS encoding and decoding
- CRC-16/CCITT_FALSE calculation and verification
- Frame delimiter insertion and detection
- UART read/write operations
- Receive buffer management
- On startup: clear receive buffer and attempt resynchronization (see §5.6.2)

---

## 3. Message Format Specification

### 3.1 Frame Structure

A complete URST frame consists of the following components:

```
Logical Frame (before COBS encoding):

 HEADER (2 bytes)    PAYLOAD (0-200 bytes)    CRC (2 bytes)
+--------+--------+-------------------------+--------+------+
| Frame  | Seq    |                         | CRC    | CRC  |
| Type   | Number |    Application Data     | Low    | High |
+--------+--------+-------------------------+--------+------+
  Byte 0   Byte 1        Bytes 2 to N         Byte N+1 Byte N+2

Physical Frame (after COBS encoding):
+-------+---------------------------+-------+
| 0x00  |    COBS Encoded Data      | 0x00  |
+-------+---------------------------+-------+
 Delim        (no 0x00 bytes)        Delim
```

**Important:** The sequence number is part of the frame **header**, not the payload. All frame types include both Frame Type and Sequence Number in their 2-byte header unless otherwise specified (some control frames may carry capability payloads but still include the 2-byte header for CRC and addressing).

### 3.2 Frame Fields

All URST frames consist of a mandatory 2-byte header, optional payload, and 2-byte CRC:

```
Frame = [Frame Type][Sequence Number][Payload (0-200 bytes)][CRC]
        └────────── Header ─────────┘
```

#### 3.2.1 Frame Type (1 byte)

The Frame Type field identifies the purpose of the frame.

| Type        | Value     | Description                       | Payload Size | Required |
| ----------- | --------- | --------------------------------- | ------------ | -------- |
| DATA        | 0x01      | Application data frame            | 0-200 bytes  | MUST     |
| ACK         | 0x02      | Acknowledgment (success)          | 0 bytes      | MUST     |
| NAK         | 0x03      | Negative acknowledgment           | 0 bytes      | MUST     |
| FRAG        | 0x04      | Fragmented message chunk (see §6) | 0-200 bytes  | MUST     |
| CONNECT     | 0x05      | Connection establishment + caps   | 0-200 bytes  | MUST     |
| CONNECT_ACK | 0x06      | Connection acknowledgment + caps  | 0-200 bytes  | MUST     |
| ERROR       | 0x07      | Receiver error / capability info  | 0-200 bytes  | MUST     |
| ABORT       | 0x08      | Abort transmission of message     | 0-16 bytes   | MUST     |
| BUSY        | 0x09      | Receiver busy (pause sending)     | 0 bytes      | MUST     |
| READY       | 0x0A      | Receiver ready (resume sending)   | 0 bytes      | MUST     |
| Reserved    | 0x0B-0x20 | Reserved                          | -            | -        |
| Reserved    | 0x21-0xFF | Reserved for application use      | -            | -        |

**Requirements:**

- Implementations MUST support DATA, ACK, NAK, FRAG, CONNECT, CONNECT_ACK, ERROR, ABORT, BUSY and READY frame types.
- Implementations MUST silently discard frames with unknown frame types.
- Implementations MUST NOT use frame type 0x00.
- Implementations MAY use frame types marked "Reserved for application use" but MUST document any non-standard behavior.

#### 3.2.2 Sequence Number (1 byte)

The Sequence Number field is an 8-bit counter (0-255) used for:

- Duplicate frame detection
- Acknowledgment matching
- Frame ordering verification

**Requirements:**

- Sequence numbers MUST increment by 1 for each new DATA or FRAG frame transmission
- Sequence numbers MUST wrap from 255 to 0
- Retransmissions MUST use the same sequence number as the original
- ACK and NAK frames MUST use the sequence number of the frame being acknowledged
- On connection establishment (CONNECT/CONNECT_ACK) both sides' sequence numbers MUST be reset to 0 (see §5.6)

#### 3.2.3 Payload (0-200 bytes)

The Payload field contains application data or protocol-specific information.

**Requirements:**

- Payload size MUST NOT exceed MAX_PAYLOAD_SIZE (200 bytes)
- ACK, NAK, BUSY, and READY frames MUST have empty payloads (0 bytes after the 2-byte header)
- DATA frames MAY have empty payloads (header only)
- CONNECT/CONNECT_ACK/ERROR/ABORT frames carry structured payloads defined in their sections

#### 3.2.4 CRC (2 bytes)

The CRC field provides error detection for the frame.

**Requirements:**

- CRC MUST be calculated over Frame Type + Sequence Number + Payload (i.e., the full logical frame) BEFORE COBS encoding. The CRC bytes are appended to that logical frame and the combined bytes are then COBS-encoded. This is explicit: CRC is over the pre-COBS logical frame.
- CRC MUST use the algorithm specified in Section 3.4
- CRC MUST be serialized in little-endian byte order

### 3.3 Frame Encoding Process

The complete frame encoding process MUST follow these steps:

1. Construct Logical Frame:

   ```
   logical_frame = [frame_type] + [seq_num] + payload
   ```

2. Calculate CRC-16/CCITT_FALSE (over that logical_frame):

   ```
   crc16 = calculate_crc16(logical_frame)
   ```

3. Append CRC (little-endian):

   ```
   frame_with_crc = logical_frame + [crc16 & 0xFF] + [(crc16 >> 8) & 0xFF]
   ```

4. Apply COBS Encoding:

   ```
   encoded_data = cobs_encode(frame_with_crc)
   ```

5. Add Frame Delimiters:

   ```
   physical_frame = [0x00] + encoded_data + [0x00]
   ```

6. Transmit via UART

**Frame Size Calculations:**

- Minimum logical frame: 2 bytes (header only, no payload)
- Maximum logical frame: 202 bytes (header + 200 byte payload)
- Maximum frame with CRC: 204 bytes
- Maximum COBS overhead: 3 bytes (1 byte per 254 bytes + 1)
- Maximum physical frame: 209 bytes (including delimiters)

### 3.4 CRC-16/CCITT_FALSE Algorithm Specification

#### 3.4.1 Algorithm Parameters

| Parameter     | Value                         |
| ------------- | ----------------------------- |
| Algorithm     | CRC-16/CCITT_FALSE            |
| Polynomial    | 0x1021 (x¹⁶+x¹²+x⁵+1)         |
| Initial Value | 0xFFFF                        |
| Final XOR     | 0x0000 (none)                 |
| Bit Order     | MSB first                     |
| Byte Order    | Little-endian (serialization) |

#### 3.4.2 Calculation Procedure

```python
def calculate_crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) ^ CRC_TABLE[(crc >> 8) ^ byte]) & 0xFFFF
    return crc

def serialize_crc(crc):
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])
```

#### 3.4.3 Lookup Table Generation

```python
def build_crc_table():
    table = []
    polynomial = 0x1021
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ polynomial) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table
```

### 3.5 COBS Encoding Specification

#### 3.5.1 Overview

Consistent Overhead Byte Stuffing (COBS) is used to eliminate 0x00 bytes from the payload, allowing 0x00 to serve as an unambiguous frame delimiter.

#### 3.5.2 Encoding Algorithm

COBS encoding replaces each zero byte with the distance to the next zero byte:

```python
def cobs_encode(data):
    output = bytearray([0])  # Placeholder for first code
    code_index = 0
    code = 0x01

    for byte in data:
        if byte == 0x00:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 0x01
        else:
            output.append(byte)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 0x01

    output[code_index] = code
    return bytes(output)
```

#### 3.5.3 Decoding Algorithm

```python
def cobs_decode(data):
    output = bytearray()
    i = 0

    while i < len(data):
        code = data[i]
        if code == 0x00:
            return None  # Invalid COBS data

        # Copy next (code-1) bytes
        for j in range(1, code):
            if i + j >= len(data):
                break
            output.append(data[i + j])

        i += code
        if i < len(data) and code < 0xFF:
            output.append(0x00)

    return bytes(output)
```

#### 3.5.4 Properties

- Encoded data MUST NOT contain 0x00 bytes (except delimiters)
- Maximum overhead: 1 byte per 254 bytes of input + 1 byte
- Minimum overhead: 1 byte (for data with no zeros)
- Invalid COBS data (embedded 0x00) MUST return decoding failure

---

## 4. Protocol State Machines

### 4.1 Sender State Machine

```
        send()
          │
          ↓
    ┌──►IDLE
    │     │
    │     │ New frame to send
    │     ↓
    │   SENDING ──────────────┐
    │     │                   │ send_frame()
    │     │                   │
    │     ↓                   ↓
    │  WAITING_ACK ←────── (transmitted)
    │     │
    │     ├──►[ACK received] ──→ SUCCESS ──┐
    │     │                                │
    │     ├──►[NAK received] ──→ retry++   │
    │     │         │                      │
    │     │         └─────►[retry < MAX] ──┤
    │     │                       │        │
    │     │                  SENDING       │
    │     │                                │
    │     ├──►[BUSY received] ──→ PAUSED   │
    │     │         │                      │
    │     │         └───►[READY received] ─┘
    │     │
    │     ├──►[Timeout] ──────→ retry++    │
    │     │         │                      │
    │     │         └─────►[retry < MAX] ──┤
    │     │                                │
    │     └──►[retry >= MAX] ──→ FAILED    │
    │                              │       │
    └──────────────────────────────┴───────┘
                                   │
                                   ↓
                              Return to IDLE
```

**State Notes:**

- **IDLE**: Waiting for data to send
- **SENDING**: Transmitting frame to UART
- **WAITING_ACK**: Waiting for ACK/NAK or timeout
- **SUCCESS**: Frame acknowledged, operation complete
- **FAILED**: Maximum retries exceeded
- **BUSY** causes the sender to pause retransmit attempts; READY resumes.
- Stop-and-wait is strictly enforced: the sender MUST NOT send a new DATA or FRAG frame until ACK/NAK is received or the sender receives READY after BUSY.

### 4.2 Receiver State Machine

```
  LISTENING
      │
      │ Frame received
      ↓
  FRAME_RECEIVED
      │
      ├──►[Invalid COBS] ────────────┐
      │                              │
      ├──►[Invalid CRC] ─────────────┤
      │                              │
      │                              ├──► LISTENING
      ├──►[Unknown Frame Type] ──────┤    (discard)
      │                              │
      │                              │
      ├──►[ACK/NAK Frame] ───────────┴──► LISTENING
      │         │                         (process in protocol)
      │         │
      │    (pass to protocol)
      │
      ├──►[DATA Frame]
      │         │
      │         ├──►[seq == expected]
      |         |          |
      |         |          └──► SEND_ACK
      |         |                   |
      |         |                   └──► DELIVER ──→ LISTENING
      │         │                                    (advance seq)
      │         │
      │         ├──►[seq == last_received]
      │         |          |
      |         |          └──► SEND_ACK ──→ LISTENING
      │         │                            (duplicate, discard)
      │         │
      │         └──►[seq != expected]
      │                   |
      │                   └──►SEND_NAK ──→ LISTENING
      │                                    (out of order, discard)
      │
      └──► (continue)
```

**State Descriptions:**

- **LISTENING**: Waiting for incoming frames
- **FRAME_RECEIVED**: Frame extracted from buffer, validating
- **SEND_ACK**: Transmitting acknowledgment
- **SEND_NAK**: Transmitting negative acknowledgment
- **DELIVER**: Passing validated payload to application layer

---

## 5. Protocol Operations

### 5.1 Data Transmission

#### 5.1.1 Sender Requirements

When transmitting a DATA frame, the sender MUST:

1.  Assign a sequence number using the next value in sequence (0-255, wrapping)
2.  Construct the frame with DATA frame type
3.  Encode and transmit the frame
4.  Start a timeout timer (ACK_TIMEOUT_MS)
5.  Wait for ACK or NAK response (or READY after BUSY)
6.  On timeout or NAK: increment retry counter and retransmit if retry < MAX_RETRIES
7.  On ACK: consider transmission successful
8.  After MAX_RETRIES failures: report failure to application layer
9.  The sender MUST NOT advance its sequence number until a successful ACK
10. The sender MUST NOT send a new DATA or FRAG frame until the previous frame is acknowledged (strict stop-and-wait)

#### 5.1.2 Receiver Requirements

When receiving a DATA frame, the receiver MUST:

1. Validate COBS encoding (if invalid, silently discard)
2. Validate CRC (if invalid, silently discard)
3. Check sequence number:
   - If seq_num == expected_seq: Send ACK, deliver payload, advance expected_seq
   - If seq_num == last_received_seq: Send ACK, discard payload (duplicate)
   - Otherwise: Send NAK, discard payload (out of sequence)

### 5.2 Acknowledgment Protocol

#### 5.2.1 ACK Frame

An ACK frame MUST be sent when:

- A DATA frame is received with the expected sequence number
- A DATA frame is received that duplicates the last successfully received frame

ACK frames MUST:

- Use frame type ACK (0x02)
- Have an empty payload (0 bytes after the 2-byte header)
- Echo the sequence number of the acknowledged DATA frame in byte 1 of the header

#### 5.2.2 NAK Frame

A NAK frame MUST be sent when:

- A DATA frame is received with an unexpected sequence number (not expected, not duplicate)

NAK frames MUST:

- Use frame type NAK (0x03)
- Have an empty payload (0 bytes after the 2-byte header)
- Echo the sequence number of the rejected DATA frame in byte 1 of the header

NAK frames MUST NOT be sent for:

- CRC failures (silent discard)
- COBS decoding failures (silent discard)
- Unknown frame types (silent discard)

### 5.3 Error Handling

#### 5.3.1 CRC Failures

When a receiver detects a CRC mismatch:

- The receiver MUST silently discard the frame
- The receiver MUST NOT send a NAK response
- The receiver MUST NOT process the frame in any way
- The sender will detect the missing ACK via timeout and retransmit

**Rationale:** CRC failures indicate corruption during transmission. The frame header (including sequence number) may be corrupted, making NAK responses unreliable. Silent discard with sender timeout provides robust recovery.

#### 5.3.2 COBS Decoding Failures

When COBS decoding fails (e.g., embedded 0x00 byte in encoded data):

- The receiver MUST silently discard the frame
- The receiver MUST NOT send a NAK response
- The receiver MUST NOT attempt further processing
- The sender will detect the missing ACK via timeout and retransmit

If repeated consecutive COBS decode failures occur, both sides SHOULD log/resync and verify baud rate.

Implementations MUST implement a configurable threshold for consecutive COBS failures

- default threshold is 5
- when threshold reached, implementations SHOULD attempt resynchronization (clear buffers and perform CONNECT if supported)

#### 5.3.3 Sequence Number Mismatches

When a DATA frame has an unexpected sequence number:

**Case 1: Duplicate Frame** (seq_num == last_received_seq)

- Receiver MUST send ACK
- Receiver MUST NOT deliver payload again
- Receiver MUST NOT advance expected_seq
- This handles the case where sender retransmitted but receiver's ACK was lost

**Case 2: Out-of-Order Frame** (seq_num != expected_seq AND seq_num != last_received_seq)

- Receiver MUST send NAK
- Receiver MUST NOT deliver payload
- Receiver MUST NOT advance expected_seq
- This indicates protocol desynchronization

Note: Because URST is strictly stop-and-wait, out-of-order frames outside duplicate/expected cases indicate a sender or receiver desynchronization that MUST be resolved via connection re-establishment (CONNECT) or application-level recovery.

#### 5.3.4 Timeout Handling

When a sender does not receive an ACK or NAK within ACK_TIMEOUT_MS:

- The sender MUST increment its retry counter
- If retry_count < MAX_RETRIES: retransmit with the same sequence number
- If retry_count >= MAX_RETRIES: report failure to application layer
- The sender MUST NOT advance its sequence number until successful ACK

When the receiver sends BUSY, the sender MUST pause further retransmission attempts until it receives READY or until the normal timeout/retry logic applies. BUSY is not an ACK — the sender must not treat BUSY as successful delivery.

#### 5.3.5 Receive Buffer Overflow

When the receive buffer exceeds RX_BUFFER_SIZE:

- Implementations MUST discard data to prevent memory exhaustion
- Implementations MUST clear the entire buffer to avoid partial frame corruption
- Implementations MAY implement alternative strategies (e.g., discard oldest complete frame) but MUST document this deviation
- Lost frames will be recovered through sender retransmission after timeout

### 5.4 Flow Control

URST implements stop-and-wait flow control:

- Window size: 1 frame (strict)
- Sender MUST wait for ACK before sending next frame
- Receiver processes one frame at a time
- BUSY (0x09) and READY (0x10) are provided for application-level flow control:
  - BUSY indicates receiver cannot process application deliveries; the sender MUST pause sending new frames (and MAY pause retry attempts as described in §5.3.4).
  - READY indicates receiver can resume sending.
  - BUSY and READY frames have empty payloads and are NOT acknowledged.

**Rationale:** Stop-and-wait is simple, requires minimal state, and is appropriate for resource-constrained microcontrollers. The specification mandates strict stop-and-wait semantics for interoperability.

### 5.5 Protocol Constants

| Constant               | Value | Description                      | Configurable |
| ---------------------- | ----- | -------------------------------- | ------------ |
| MAX_RETRIES            | 3     | Maximum transmission attempts    | SHOULD       |
| ACK_TIMEOUT_MS         | 1000  | ACK timeout in milliseconds      | SHOULD       |
| MAX_PAYLOAD_SIZE       | 200   | Maximum payload bytes            | MAY          |
| RX_BUFFER_SIZE         | 512   | Receive buffer size in bytes     | MAY          |
| MAX_MSG_BYTES          | 8192  | Maximum message bytes advertised | MAY          |
| MAX_FRAGMENTS          | 32    | Maximum fragments per message    | MAY          |
| CONSECUTIVE_COBS_FAILS | 5     | Consecutive COBS fails threshold | MAY          |

**Requirements:**

- Implementations MUST use identical MAX_PAYLOAD_SIZE for interoperability
- Implementations SHOULD support at least 3 retries
- Implementations SHOULD use 1000ms timeout as default but MAY allow configuration
- Implementations MUST implement fragment timeout (see §6.3.4)
- Implementations MUST support CONNECT capability negotiation (see §5.6)

### 5.6 Connection Establishment and Capabilities

URST has a mandatory connection establishment handshake. The handshake MUST be used:

- Immediately upon first data transmission (sending endpoint MUST send CONNECT when ready to communicate)
- Whenever either side detects persistent desynchronization that cannot be resolved via existing ACK/NAK/timeout logic
- To negotiate capabilities before sending fragmented messages or large transfers

CONNECT (0x05) and CONNECT_ACK (0x06) frames carry a machine-readable capabilities payload.

#### 5.6.1 CONNECT Payload (machine-readable)

CONNECT payload structure:

| Bytes | Description                              | Meaning                                  |
| ----: | :--------------------------------------- | :--------------------------------------- |
|     0 | protocol_version (uint8)                 | e.g., 4 for 0.3.4                        |
|   1-2 | max_message_bytes (uint16 little-endian) | max bytes receiver can reassemble        |
|     3 | max_fragments_per_message (uint8)        | max fragments receiver will accept       |
|     4 | max_concurrent_message_ids (uint8)       | must be 1 for conformant implementations |
|   5-6 | ack_timeout_ms (uint16 LE)               | receiver's preferred timeout             |
|     7 | max_retries (uint8)                      | receiver's preferred max retries         |
|     8 | reserved                                 |                                          |

- Implementations MUST set max_concurrent_message_ids to 1 to be conformant.
- The capability exchange follows the "least capable wins" rule: after CONNECT/CONNECT_ACK, peers MUST use the minimum of the two sides' advertised limits for subsequent operations (e.g., max_fragments = min(local, remote)).

#### 5.6.2 CONNECT Sequence and Effects

- When a CONNECT is received and accepted, the recipient MUST respond with CONNECT_ACK containing its capabilities.
- Both sides MUST reset sequence numbers to 0 upon successful CONNECT/CONNECT_ACK exchange.
- Upon CONNECT, both sides MUST clear reassembly buffers and reset fragment state for all Message IDs.
- Implementations MUST send CONNECT on startup; if a peer does not respond within ACK_TIMEOUT_MS, implementations SHOULD retry CONNECT up to MAX_RETRIES before reporting failure to application.

#### 5.6.3 Capability Query Requirement

- Senders SHOULD query receiver capabilities using CONNECT before sending messages that could exceed default limits (e.g., large fragmented transfers) or when resynchronization is required.

### 5.7 ERROR, ABORT, BUSY and READY semantics

#### 5.7.1 ERROR Frame (0x07)

ERROR frames allow a receiver to communicate error conditions and capability limitations to the sender.

ERROR payload format (structured):

|             Bytes | Description                        | Meaning                            |
| ----------------: | :--------------------------------- | :--------------------------------- |
|                 0 | error_code (uint8)                 |                                    |
|               1-2 | max_message_bytes (uint16 LE)      | advertised capability; 0 = no info |
|                 3 | max_fragments_per_message (uint8)  | 0 = no info                        |
|                 4 | max_concurrent_message_ids (uint8) | 0 = no info                        |
|                 5 | text_len (uint8)                   |                                    |
| 6..(6+text_len-1) | UTF-8 text (human-readable)        |                                    |

- error_code 0x01 = CAPABILITY_EXCEEDED (used when sender attempted to exceed receiver's reassembly/capacity)
- Implementations MUST interpret max\_\* fields when non-zero and adjust behavior accordingly.
- ERROR frames MUST be sent when a receiver rejects a fragment set or other request due to capacity limits.
- ERROR frames use the same framing and CRC rules as other frames.

#### 5.7.2 ABORT Frame (0x08)

- ABORT is used by a sender or receiver to explicitly abort an in-progress fragmented message transfer.
- ABORT may carry a 1-byte reason code; payload length MUST be 0 or 1.
- ABORT frames MAY be acknowledged by an ACK but are not required to be acknowledged.
- Upon sending or receiving ABORT for Message ID X, both sides MUST discard all fragments and reassembly state for that Message ID.

#### 5.7.3 BUSY (0x09) and READY (0x10)

- BUSY indicates the receiver is temporarily unable to deliver incoming frames to the application layer.
- Upon receiving BUSY, the sender MUST pause further transmissions for new frames.
  - The sender SHOULD pause retries for the in-flight frame (implementation choice) and MUST NOT treat BUSY as an ACK.
- READY indicates the receiver can resume normal reception and processing.
- BUSY and READY frames have empty payloads and are not acknowledged.

---

## 6. Fragmentation Protocol

### 6.1 Overview

When application data exceeds the available payload space (accounting for protocol overhead), the Handler Layer MUST fragment the message for transmission and reassemble it on reception.

### 6.2 Fragment Frame Format

Fragmented messages use FRAG frames with a specific payload structure:

```

0 1 2 3
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+---------------+---------------+---------------+---------------+
|   Message ID  | Fragment Num  | Total Frags   |  Data Length  |
+---------------+---------------+---------------+---------------+
|                  Fragment Data (0-194 bytes)|                 |
+---------------------------------------------------------------+

```

**Field Descriptions:**

- **Message ID** (1 byte): Unique identifier for the complete message (0-255, wrapping)
- **Fragment Num** (1 byte): Zero-based index of this fragment (0 to Total-1)
- **Total Frags** (1 byte): Total number of fragments in the complete message
- **Data Length** (1 byte): Number of data bytes in this fragment
- **Fragment Data** (0-194 bytes): Portion of the original message

### 6.3 Fragmentation Rules

#### 6.3.1 Sender Requirements

When fragmenting a message, the sender MUST:

1. Check if message size exceeds (MAX_PAYLOAD_SIZE - 6) bytes
2. Assign a Message ID (incrementing counter, wrapping 0-255)
3. Calculate required fragments: `total_frags = ceil(msg_len / (MAX_PAYLOAD_SIZE - 6))`
4. For each fragment (i = 0 to total_frags - 1):
   - Extract fragment data: `data[i * (MAX_PAYLOAD_SIZE - 6) : (i+1) * (MAX_PAYLOAD_SIZE - 6)]`
   - Construct fragment header: `[msg_id][i][total_frags][len(fragment_data)]`
   - Send fragment + header in a FRAG frame using reliable delivery (ACK/NAK)
5. Only proceed to next fragment after successful ACK for the current fragment (strict sequential send)
6. Senders MUST NOT begin sending fragments for a new Message ID until the previous Message ID's fragments have been completed (MUST NOT interleave)
7. If a sender is unable to continue a fragmented transfer, it MUST send ABORT (0x08) for that Message ID

**Default Maximum Fragment Data Size:** MAX_PAYLOAD_SIZE - 6 = 194 bytes

#### 6.3.2 Receiver Requirements

When receiving fragments, the receiver MUST:

1. Only accept FRAG frames in the FRAG code path
2. Extract fragment header fields
3. Only accept fragments for one Message ID at a time (max_concurrent_message_ids == 1)
4. If a fragment arrives with a Message ID not equal to the currently open reassembly ID:
   - If no reassembly in progress, begin reassembly for that Message ID
   - If reassembly is in progress for a different Message ID, the incoming fragment MUST be rejected; receiver MUST send ERROR with error_code=CAPABILITY_EXCEEDED (0x01) and include max_concurrent_message_ids (1) in ERROR payload
5. Store fragment in reassembly buffer keyed by Message ID
6. Track received fragment count per Message ID
7. When all fragments received (received_count == total_frags):
   - Reassemble message by concatenating fragments in order (0 to total-1)
   - Deliver complete message to application
   - Clear reassembly buffer for this Message ID

#### 6.3.3 Fragment Ordering

- Fragments MUST be transmitted in order (0, 1, 2, ...)
- Receivers SHOULD NOT assume fragments arrive in order, but because senders MUST send sequentially and wait for ACK, in practice fragments should arrive in order
- Receivers MUST store fragments and reassemble based on Fragment Num

#### 6.3.4 Incomplete Message Timeout

Implementations MUST implement a timeout mechanism for incomplete fragmented messages:

- If fragments for a Message ID are not completed within `fragment_timeout`, discard them
- Required default timeout calculation:

```
fragment_timeout = total_frags * (MAX_RETRIES + 1) * ACK_TIMEOUT_MS
Example for 10 fragments:
fragment_timeout = 10 * (3 + 1) \_ 1000ms = 40,000ms = 40 seconds

```

- This prevents memory exhaustion from incomplete messages
- If the last fragment is never received and the sender exhausts retransmissions, the fragment timeout MUST cause receiver to discard the incomplete message and free memory

**Rationale:** Each fragment requires up to `(MAX_RETRIES + 1) * ACK_TIMEOUT_MS` in the worst case (initial transmission plus all retries). The total timeout should accommodate all expected fragments.

### 6.4 Fragment Detection

Single-frame messages (< 194 bytes payload) MUST NOT use fragment headers. Receivers MUST distinguish fragments from regular data by frame type only:

1. FRAG: contains the fragment sub-header and chunk data (process per §6.3)
2. DATA: opaque application payload (MUST NOT be heuristically parsed as fragments)

---

## 7. Conformance Requirements

### 7.1 Minimal Conformant Implementation

A conformant URST implementation MUST:

1. Implement all four protocol layers (Codec, Transport, Protocol, Handler)
2. Support DATA, ACK, NAK, FRAG, CONNECT, CONNECT_ACK, ERROR, ABORT, BUSY, READY frame types
3. Implement COBS encoding/decoding as specified in Section 3.5
4. Implement CRC-16/CCITT_FALSE calculation as specified in Section 3.4
5. Implement strict stop-and-wait flow control with retransmission (the sender MUST NOT send a new frame until previous is ACKed or READY is received after BUSY)
6. Support MAX_PAYLOAD_SIZE identical for each participating device (default 200 bytes)
7. Implement sequence number management (0-255, wrapping) and reset sequence to 0 on successful CONNECT
8. Support at least MAX_RETRIES (3) transmission attempts
9. Implement timeout-based retransmission
10. Handle CRC failures by silent discard
11. Handle sequence mismatches per Section 5.3.3
12. Implement fragmentation and reassembly per Section 6, including:

- Only one concurrent Message ID supported
- Fragments MUST NOT be interleaved
- Fragment timeout MUST be implemented

13. Implement CONNECT/CONNECT_ACK capability negotiation on startup and on resynchronization
14. Clear receive buffers on initialization and attempt resynchronization by discarding until the next 0x00 delimiter

### 7.2 Optional Features

Conformant implementations MAY optionally:

1. Implement configurable timeout values
2. Implement configurable retry counts
3. Implement receive buffer sizes larger than 512 bytes
4. Define custom frame types in reserved range (0x21-0xFF) for application-specific purposes (non-interoperable)

### 7.3 Interoperability Requirements

For interoperability between implementations:

- All implementations MUST use MAX_PAYLOAD_SIZE = 200 bytes
- All implementations MUST implement identical COBS encoding
- All implementations MUST implement identical CRC calculation
- All implementations MUST use little-endian byte order for CRC serialization
- All implementations MUST use 0x00 as FRAME_DELIMITER
- CONNECT capability negotiation MUST be used and the "least capable wins" rule applied

### 7.4 Non-Conformant Behavior

The following behaviors are explicitly NON-CONFORMANT:

- Sending frames with payload > 200 bytes
- Using sequence numbers > 255
- Sending NAK in response to CRC failures
- Accepting frames with invalid CRC
- Modifying frame type values 0x01-0x20
- Using frame type 0x00
- Sending ACK/NAK frames with non-empty payloads
- Failing to implement COBS encoding
- Implementing different CRC algorithms
- Using big-endian byte order for CRC serialization
- Fragment interleaving (starting a new Message ID before previous is complete)
- Accepting more than one concurrent Message ID

---

## 8. Security Considerations

### 8.1 Threat Model

URST is designed for point-to-point serial communication and assumes:

- Physical security of the communication medium
- Trusted endpoints
- No adversarial tampering with serial data

### 8.2 Lack of Encryption

URST does NOT provide:

- **Confidentiality**: All data is transmitted in plaintext
- **Authentication**: No verification of sender identity
- **Integrity protection**: CRC-16/CCITT_FALSE detects accidental corruption, not intentional tampering

**Recommendation:** Applications requiring security MUST implement encryption and authentication at a higher layer.

### 8.3 Denial of Service

Potential DoS vulnerabilities:

- **Retransmission storms:** Faulty sender could continuously retransmit, consuming receiver resources
- **Fragment flooding:** Attacker could send fragments with different Message IDs to exhaust memory
- **Buffer overflow:** Malicious sender could attempt to overflow receive buffers

**Mitigations:**

- Implementations SHOULD implement rate limiting where possible
- Implementations MUST implement fragment timeouts and single concurrent Message ID to limit memory usage
- Receive buffer overflow protection is REQUIRED (Section 5.3.5)

### 8.4 Frame Injection

An attacker with access to the serial line could:

- Inject arbitrary frames
- Replay captured frames
- Modify frames in transit

**Recommendation:** Use physically secured serial connections or implement cryptographic authentication at a higher layer.

### 8.5 CRC-16/CCITT_FALSE Limitations

CRC-16/CCITT_FALSE provides error detection but NOT cryptographic integrity:

- Detects accidental corruption with high probability
- Does NOT protect against intentional modification
- An attacker can modify data and recalculate valid CRC

**Recommendation:** Use a mechanism (perhaps similar to JWT) to detect data tampering.

### 8.6 Sequence Number Prediction

The 8-bit sequence number is predictable:

- Sequences are sequential and wrap at 255
- An attacker could inject frames with predicted sequence numbers
- This is mitigated by using CONNECT handshakes on resynchronization

### 8.7 Recommendations for Secure Applications

Applications requiring security SHOULD:

1. Implement encryption (e.g., AES) at application layer
2. Implement authentication (e.g., HMAC) at application layer
3. Use physically secured serial connections
4. Implement sequence number validation beyond URST's basic duplicate detection
5. Implement application-level timeouts and rate limiting
6. Consider adding timestamps to detect replay attacks
7. Implement message authentication codes (MAC) for integrity

---

## 9. IANA Considerations

This protocol does not require IANA registrations. Frame type values are defined in Section 3.2.1.

Frame types 0x0B-0x20 and 0x21-0xFF are reserved for future use. Implementations MUST NOT reuse 0x00 and MUST implement the defined frame types for interoperability.

---

## 10. References

### 10.1 Normative References

**[RFC2119]**
Bradner, S., "Key words for use in RFCs to Indicate Requirement Levels", BCP 14, RFC 2119, March 1997.
https://www.rfc-editor.org/rfc/rfc2119

**[COBS]**
Cheshire, S. and Baker, M., "Consistent Overhead Byte Stuffing", IEEE/ACM Transactions on Networking, Vol. 7, No. 2, April 1999.
https://en.wikipedia.org/wiki/Consistent_Overhead_Byte_Stuffing

### 10.2 Informative References

**[CRC]**
"Cyclic Redundancy Check", Wikipedia.
https://en.wikipedia.org/wiki/Cyclic_redundancy_check

---

## 11. Glossary

| Term            | Definition                                                    |
| --------------- | ------------------------------------------------------------- |
| ACK             | Acknowledgment frame indicating successful reception          |
| COBS            | Consistent Overhead Byte Stuffing encoding algorithm          |
| CRC             | Cyclic Redundancy Check for error detection                   |
| Delimiter       | 0x00 byte marking frame boundaries                            |
| Fragment        | Portion of a larger message split for transmission            |
| Frame           | Complete unit of transmission (header + payload + CRC)        |
| Handler         | High-level API layer providing send/receive interface         |
| NAK             | Negative acknowledgment indicating rejection                  |
| Payload         | Application data carried in frame (0-200 bytes)               |
| Sequence Number | 8-bit counter for duplicate detection (0-255)                 |
| Stop-and-Wait   | Flow control requiring ACK before next transmission           |
| UART            | Universal Asynchronous Receiver-Transmitter (serial hardware) |
| URST            | Universal Reliable Serial Transport                           |

---

## Appendix A. Specification Checklist

Use this checklist when implementing URST:

### A.1 Codec Layer

- [ ] CRC-16/CCITT_FALSE implemented with correct polynomial (0x1021)
- [ ] CRC initial value is 0xFFFF
- [ ] CRC serialized as little-endian
- [ ] COBS encoding handles empty data (returns 0x01)
- [ ] COBS encoding handles all-zero data correctly
- [ ] COBS decoding rejects embedded 0x00 bytes
- [ ] Frame delimiters are 0x00
- [ ] Receive buffer implements overflow protection
- [ ] Implementation clears receive buffer on init and attempts resync

### A.2 Transport Layer

- [ ] Frame header is exactly 2 bytes [type][seq]
- [ ] Non-reserved frame type values implemented as required
- [ ] Unknown frame types silently discarded
- [ ] Frame type 0x00 never used
- [ ] Sequence numbers wrap correctly (255 → 0)
- [ ] Sequence number management implemented
- [ ] CONNECT capability negotiation implemented

### A.3 Protocol Layer

- [ ] ACK frames have empty payload
- [ ] NAK frames have empty payload
- [ ] ACK/NAK echo sequence number in header
- [ ] CRC failures result in silent discard (no NAK)
- [ ] COBS failures result in silent discard
- [ ] Expected sequence number tracked
- [ ] Last received sequence number tracked
- [ ] Duplicate frames send ACK but discard payload
- [ ] Out-of-sequence frames send NAK
- [ ] Timeout mechanism implemented (1000ms default)
- [ ] Retry counter implemented (MAX_RETRIES=3)
- [ ] Sender waits for ACK before next frame
- [ ] BUSY/READY flow-control implemented

### A.4 Handler Layer

- [ ] Fragmentation threshold calculated correctly (194 bytes)
- [ ] Fragment header format: [msg_id][frag_num][total][len]
- [ ] Each fragment sent with reliable delivery
- [ ] Fragment reassembly buffer implemented
- [ ] Complete message detection works
- [ ] Fragment timeout implemented (REQUIRED)
- [ ] Non-fragmented messages detected correctly
- [ ] Only one concurrent Message ID supported

### A.5 Interoperability

- [ ] MAX_PAYLOAD_SIZE = 200 bytes
- [ ] Works with reference implementation
- [ ] Cross-platform tested (if applicable)
- [ ] CONNECT handshake and capability negotiation validated

---

## Appendix B. Future Protocol Extensions

This section describes potential extensions for future versions of URST. These are NOT part of the current specification.

### B.1 Potential Future Version Features

#### B.1.1 Sliding Window Flow Control

Replace stop-and-wait with selective repeat ARQ:

- Window size negotiation
- Out-of-order delivery support
- Reduced latency for bulk transfers

#### B.1.2 Compression

Optional payload compression:

- Frame type indicating compressed data
- Negotiated compression algorithm

#### B.1.3 Timestamps

Add optional timestamp field for latency measurement and replay detection.

---

## Appendix C. Questions and Answers

### Q1: Why use COBS?

**A:** COBS has predictable overhead (max 0.4%) and guarantees no 0x00 bytes in output, making frame boundaries unambiguous.

### Q2: Why CRC-16 instead of CRC-32?

**A:** CRC-16 provides error detection with low overhead and is sufficient for typical serial links for this lightweight protocol.

### Q3: Why stop-and-wait instead of sliding window?

**A:** Stop-and-wait is simple and minimal-state; this specification mandates strict stop-and-wait semantics for version compatibility. Sliding window is a possible future extension.

### Q4: Can I use URST over other transports (USB, TCP, etc.)?

**A:** Yes, but it's designed for serial UART.

### Q5: What happens if fragment reassembly fails?

**A:** Implementations MUST implement fragment timeouts. If timeout expires, incomplete fragments are discarded.

### Q6: Is there a connection handshake?

**A:** Yes — CONNECT (0x05) and CONNECT_ACK (0x06) are mandatory and carry capability information. Sequence numbers are reset to 0 upon successful CONNECT.

### Q7: How do I detect if the other end has disconnected?

**A:** URST doesn't have built-in keepalive. Applications should:

- Implement periodic heartbeat messages
- Consider lack of response to heartbeat as disconnection

### Q8: Can multiple devices share a serial bus?

**A:** No, URST is strictly point-to-point. It has no addressing mechanism. For multi-drop serial buses, consider Modbus or implement an addressing layer on top of URST.

### Q9: What's the maximum message size?

**A:** With fragmentation, theoretically unlimited. Practically limited by:

- Available RAM for fragment buffers
- Timeout for fragment reassembly
- Application requirements

### Q10: How do I implement firmware updates over URST?

**A:** This is partly why this specification has been developed as it will be used in a companion application for such a purpose. In short, it's application-specific, but typical approach would be:

1. Send metadata (file size, CRC, version)
2. Fragment binary data (use URST fragmentation)
3. Each fragment is reliably delivered
4. Verify complete file CRC
5. Trigger bootloader

---

## Appendix D. Change Log

| Version | Date       | Description                                                                                                           |
| :------ | :--------- | :-------------------------------------------------------------------------------------------------------------------- |
| 0.3.3   | 2025-10-23 | Added CONNECT/CONNECT_ACK handshake, ERROR, ABORT, BUSY, READY frames;                                                |
|         |            | Made fragment timeout & single Message ID mandatory                                                                   |
|         |            | Clarified CRC/COBS ordering                                                                                           |
|         |            | Enforced strict stop-and-wait semantics.                                                                              |
| 0.3.2   | 2025-10-13 | Added FRAG frame type to mitigate edge case where a DATA frame's content _could_ have been interpreted as a fragment. |

---

**End of Specification**

---

```

```

```

```
