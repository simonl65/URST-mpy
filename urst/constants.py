# fmt: off
try:
    from micropython import const
except ImportError:
    def const(x): return x  # noqa: E731

# Protocol Constants
MAX_RETRIES             = const(3)      # Maximum transmission attempts
ACK_TIMEOUT_MS          = const(1000)   # ACK timeout in milliseconds
MAX_PAYLOAD_SIZE        = const(200)    # Maximum payload bytes
RX_BUFFER_SIZE          = const(512)    # Receive buffer size in bytes
MAX_MSG_BYTES           = const(8192)   # Maximum message bytes advertised
MAX_FRAGMENTS           = const(32)     # Maximum fragments per message
CONSECUTIVE_COBS_FAILS  = const(5)      # Consecutive COBS fails threshold

# Frame Types
FRAME_DATA          = const(0x01)  # Application data frame
FRAME_ACK           = const(0x02)  # Acknowledgment (success)
FRAME_NAK           = const(0x03)  # Negative acknowledgment
FRAME_FRAG          = const(0x04)  # Fragmented message chunk
FRAME_CONNECT       = const(0x05)  # Connection establishment + caps
FRAME_CONNECT_ACK   = const(0x06)  # Connection acknowledgment + caps
FRAME_ERROR         = const(0x07)  # Receiver error / capability info
FRAME_ABORT         = const(0x08)  # Abort transmission of message
FRAME_BUSY          = const(0x09)  # Receiver busy (pause sending)
FRAME_READY         = const(0x0A)  # Receiver ready (resume sending)

FRAME_DELIMITER     = const(0x00)
