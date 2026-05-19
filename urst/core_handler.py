try:
    import logging
except ImportError:
    from . import logging
import math
import sys

# MicroPython compatibility for typing
try:
    from typing import TYPE_CHECKING, Any
except ImportError:
    TYPE_CHECKING = False
    # Minimal fallback
    pass

from . import constants
from .codec_layer import CodecLayer
from .protocol_layer import ProtocolLayer

if TYPE_CHECKING:
    from serial import Serial  # type: ignore

logger = logging.getLogger(__name__)


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

            if isinstance(port, machine.UART):
                self.ser = port
            else:
                # port could be id (int)
                self.ser = machine.UART(port, baudrate=baud)
        else:
            # Desktop implementation
            if hasattr(port, "write") and hasattr(port, "read"):
                # Already a serial-like object (e.g. mock or already opened serial)
                self.ser = port
            else:
                try:
                    from serial import Serial as SerialImpl  # type: ignore

                    self.ser = SerialImpl(port=port, baudrate=baud, timeout=timeout)
                except ImportError as exc:
                    raise RuntimeError(
                        "pyserial is required to use Urst on desktop Python"
                    ) from exc

        self.codec = CodecLayer(self.ser)
        self.protocol = ProtocolLayer(self.codec)
        self._msg_id = 0
        self._reassembly: dict[int, Any] = {}

    def send(self, data: bytes) -> int:
        """
        Send data over the URST transport with automatic fragmentation and reliability.
        """
        max_frag_data = constants.MAX_PAYLOAD_SIZE - 6 # 194 bytes
        
        if len(data) <= max_frag_data:
            if self.protocol.send_reliable(constants.FRAME_DATA, data):
                return len(data)
            return 0
        
        total_frags = math.ceil(len(data) / max_frag_data)
        msg_id = self._msg_id
        self._msg_id = (self._msg_id + 1) & 0xFF
        
        for i in range(total_frags):
            chunk = data[i * max_frag_data : (i + 1) * max_frag_data]
            # Fragment payload structure (§6.2)
            header = bytes([msg_id, i, total_frags, len(chunk)])
            if not self.protocol.send_reliable(constants.FRAME_FRAG, header + chunk):
                return i * max_frag_data
        
        return len(data)

    def read(self, bytes_to_read: int = -1) -> bytes:
        """
        Read a complete URST message (reassembled if necessary).
        """
        while True:
            frame = self.protocol.receive_frame()
            if not frame:
                return b"" # Timeout or duplicate frame (already ACKed)

            frame_type = frame["type"]
            payload = frame["payload"]

            if frame_type == constants.FRAME_DATA:
                return payload

            if frame_type == constants.FRAME_FRAG:
                if len(payload) < 4:
                    continue
                
                msg_id = payload[0]
                frag_num = payload[1]
                total = payload[2]
                data_len = payload[3]
                data = payload[4 : 4 + data_len]

                if msg_id not in self._reassembly:
                    self._reassembly[msg_id] = {"total": total, "fragments": {}}
                
                self._reassembly[msg_id]["fragments"][frag_num] = data
                
                if len(self._reassembly[msg_id]["fragments"]) == total:
                    # Reassemble message
                    msg = b"".join(
                        self._reassembly[msg_id]["fragments"][j]
                        for j in range(total)
                    )
                    del self._reassembly[msg_id]
                    return msg
            
            # Handle other frame types or continue waiting
            if frame_type in {constants.FRAME_CONNECT, constants.FRAME_CONNECT_ACK}:
                continue
