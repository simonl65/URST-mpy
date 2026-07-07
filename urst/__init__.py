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

from .core_handler import Urst

__version__ = "1.0.2"
__all__ = ["Urst"]
