# fmt: off

# Protocol Constants
MAX_RETRIES             = 3     # Maximum transmission attempts
ACK_TIMEOUT_MS          = 1000  # ACK timeout in milliseconds
MAX_PAYLOAD_SIZE        = 200   # Maximum payload bytes
RX_BUFFER_SIZE          = 512   # Receive buffer size in bytes
MAX_MSG_BYTES           = 8192  # Maximum message bytes advertised
MAX_FRAGMENTS           = 32    # Maximum fragments per message
CONSECUTIVE_COBS_FAILS  = 5     # Consecutive COBS fails threshold

# Frame Types
FRAME_DATA          = 0x01  # Application data frame
FRAME_ACK           = 0x02  # Acknowledgment (success)
FRAME_NAK           = 0x03  # Negative acknowledgment
FRAME_FRAG          = 0x04  # Fragmented message chunk
FRAME_CONNECT       = 0x05  # Connection establishment + caps
FRAME_CONNECT_ACK   = 0x06  # Connection acknowledgment + caps
FRAME_ERROR         = 0x07  # Receiver error / capability info
FRAME_ABORT         = 0x08  # Abort transmission of message
FRAME_BUSY          = 0x09  # Receiver busy (pause sending)
FRAME_READY         = 0x0A  # Receiver ready (resume sending)

FRAME_DELIMITER     = 0x00
