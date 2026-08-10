try:
    import logging
except ImportError:
    # Minimal MicroPython fallback: swallow all log calls via __getattr__.
    class _NoLog:
        def __getattr__(self, _):
            return lambda *a, **k: None

    class _NoLogging:
        def getLogger(self, _):
            return _NoLog()

    logging = _NoLogging()

from .codec_layer import cobs_decode, cobs_encode
from .core_handler import Urst

__all__ = ["Urst", "cobs_decode", "cobs_encode"]
