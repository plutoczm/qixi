"""Regression tests for business failures and quote receipt; no network calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stay_agent.providers.data.rollinggo import has_display_quote
from scripts.probe_rollinggo import main


class ProbeBusinessTests(unittest.TestCase):
    def test_room_count_is_not_proof_of_a_monetary_quote(self):
        for plan in [{}, {"averagePrice": True, "currency": "CNY"},
                     {"averagePrice": "NaN", "currency": "CNY"},
                     {"averagePrice": -1, "currency": "CNY"},
                     {"averagePrice": 10}, {"averagePrice": 10, "currency": "?"}]:
            self.assertFalse(has_display_quote({"roomRatePlans": [plan]}))
        self.assertTrue(has_display_quote({"roomRatePlans": [{"averagePrice": "333.00", "currency": "CNY"}]}))

    def test_http_and_mcp_success_do_not_hide_business_failure(self):
        class FakeClient:
            protocol_version = "2025-06-18"
            request_count = 4
            def __init__(self, key): pass
            def discover(self): return []
            def call_tool(self, name, arguments):
                return {"structuredContent": {"success": False, "code": 4100, "message": "No allowed inventory"}}
            def redact(self, value): return value
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "args.json"
            path.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with patch("scripts.probe_rollinggo.load_api_key", return_value="synthetic-token"), patch(
                "scripts.probe_rollinggo.RollingGoClient", FakeClient
            ), contextlib.redirect_stdout(output):
                result = main(["--tool", "searchHotels", "--arguments-file", str(path)])
            report = json.loads(output.getvalue())
            self.assertEqual(result, 4)
            self.assertEqual(report["status"], "provider_error")
            self.assertFalse(report["live_api_verified"])
            self.assertFalse(report["live_quote_received"])


if __name__ == "__main__":
    unittest.main()
