"""The r71 discovery probe must stay offline by default and never call tools."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import r71_tools_surface as probe  # noqa: E402


def test_default_run_sends_no_provider_http():
    result = probe.run(live=False)
    assert result["status"] == "LOCAL_ONLY_NO_HTTP"
    assert result["raw_http_attempts"] == 0
    assert "tools" not in result


def test_probe_is_bounded_to_handshake_methods_only():
    # A discovery probe must never reach tools/call: the third Tuniu tool is
    # create-order, and even a read-only intent must not be able to invoke it.
    assert "tools/call" not in probe._ALLOWED_METHODS
    assert probe._ALLOWED_METHODS == frozenset(
        ("initialize", "notifications/initialized", "tools/list")
    )
    assert probe._MAX_RAW == 3


def test_projection_keeps_only_shape_not_payload():
    tool = {
        "name": "tuniuHotelDetail",
        "description": "x" * 40,
        "inputSchema": {
            "type": "object",
            "properties": {"roomNum": {"type": "number"}, "hotelId": {}},
            "required": ["hotelId"],
        },
        "secretLookingField": "must not be echoed",
    }
    row = probe._project(tool)
    assert row == {
        "name": "tuniuHotelDetail",
        "description_chars": 40,
        "input_fields": ["hotelId", "roomNum"],
        "required_fields": ["hotelId"],
    }


def test_projection_survives_a_tool_without_input_schema():
    assert probe._project({"name": "bare"}) == {
        "name": "bare",
        "description_chars": 0,
        "input_fields": [],
        "required_fields": [],
    }
