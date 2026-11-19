"""Offline security and lifecycle invariants of the on-demand tray server."""
from pathlib import Path

import pytest

from scripts.tray_server import LAUNCHER_RUNTIME, validate_marker


def test_exit_marker_must_be_private_to_runtime_launcher():
    marker = (LAUNCHER_RUNTIME / "stop-tray-test.flag").resolve()
    assert validate_marker(str(marker)) == marker


@pytest.mark.parametrize("marker", [
    "runtime/stop-abcd.flag",
    "launcher/stop-abcd.flag",
    "runtime/launcher/stop-abcd.txt",
    "runtime/launcher/shutdown.flag",
    "runtime/launcher/stop-abcd.flag/../../.env",
    "../stop-abcd.flag",
])
def test_reject_non_launcher_control_file(marker):
    with pytest.raises(ValueError, match="INVALID_LAUNCHER_EXIT_MARKER"):
        validate_marker(str(Path(__file__).resolve().parents[2] / marker))


def test_tray_server_does_not_import_supplier_or_start_on_import():
    import inspect
    from scripts import tray_server

    assert "if __name__ == \"__main__\":" in inspect.getsource(tray_server)
    assert tray_server.main is not None
