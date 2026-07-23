import logging
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def _load_minimum_module(monkeypatch) -> ModuleType:
    config_module = ModuleType("config")
    config_module.XBEE_BASE_PORT = "/dev/ttyUSB0"  # pyright: ignore[reportAttributeAccessIssue]
    config_module.SERIAL_BAUDRATE = 9600  # pyright: ignore[reportAttributeAccessIssue]
    config_module.SERIAL_TIMEOUT = 1.0  # pyright: ignore[reportAttributeAccessIssue]

    class FailingUrst:
        def __init__(self, *args, **kwargs):
            raise OSError("could not open serial port")

    urst_module = ModuleType("urst")
    urst_module.Urst = FailingUrst  # pyright: ignore[reportAttributeAccessIssue]

    monkeypatch.setitem(sys.modules, "config", config_module)
    monkeypatch.setitem(sys.modules, "urst", urst_module)

    module_path = Path(__file__).resolve().parent.parent / "minimum.py"
    spec = spec_from_file_location("_test_minimum_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    minimum_module = module_from_spec(spec)
    spec.loader.exec_module(minimum_module)
    return minimum_module

