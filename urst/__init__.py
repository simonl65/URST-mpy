try:
    import logging
except ImportError:
    # Minimal fallback for MicroPython if logging is not installed
    class MockLogger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pass

    class MockLogging:
        def getLogger(self, name):
            return MockLogger()

        def basicConfig(self, *args, **kwargs):
            pass

    logging = MockLogging()

from .core_handler import Urst

__version__ = "1.0.0"
__all__ = ["Urst"]

logger = logging.getLogger(__name__)
