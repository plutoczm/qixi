"""Offline tests; test credentials are synthetic and no network is used."""
import json
from pathlib import Path
import tempfile
import unittest

from stay_agent.providers.data.rollinggo import ConfigurationError, business_data, business_summary, load_api_key


class ProbeTests(unittest.TestCase):
    def test_process_key_has_precedence_and_is_not_in_error(self):
        key = load_api_key(Path("not-present"), {"ROLLINGGO_API_KEY": "synthetic-test-token"})
        self.assertEqual(key, "synthetic-test-token")

    def test_missing_key_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigurationError) as caught:
                load_api_key(Path(directory) / ".env", {})
            self.assertEqual(str(caught.exception), "MISSING_ROLLINGGO_API_KEY")

    def test_local_quotes_export_and_bearer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('export ROLLINGGO_API_KEY="Bearer synthetic-test-token"\n', encoding="utf-8")
            self.assertEqual(load_api_key(path, {}), "synthetic-test-token")
            path.write_text("ROLLINGGO_API_KEY=\nROLLINGGO_API_KEY=synthetic-test-token\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError) as caught:
                load_api_key(path, {})
            self.assertEqual(caught.exception.code, "DUPLICATE_ROLLINGGO_KEY")

    def test_invalid_key_is_not_echoed(self):
        bad = "synthetic secret\nheader"
        with self.assertRaises(ConfigurationError) as caught:
            load_api_key(environ={"ROLLINGGO_API_KEY": bad})
        self.assertNotIn(bad, str(caught.exception))

    def test_structured_and_json_text_wrappers(self):
        payload = {"hotelInformationList": [{"hotelId": 17, "price": {"lowestPrice": 88, "currency": "CNY"}}]}
        self.assertEqual(business_data({"structuredContent": {"data": payload}}), payload)
        self.assertEqual(business_data({"content": [{"type": "text", "text": json.dumps(payload)}]}), payload)
        self.assertEqual(business_data({"content": [{"type": "text", "text": "not json"}]})["content"][0]["text"], "not json")

    def test_reference_price_and_quote_are_not_totals(self):
        report = business_summary({"hotelInformationList": [{"hotelId": 17, "price": {"lowestPrice": 88}}]})
        self.assertEqual(report["hotels"][0]["price_scope"], "first_night_reference_only")
        quote = business_summary({"roomRatePlans": [{"averagePrice": 88, "currency": "CNY"}],
                                  "bookingUrl": "https://example.org/book?private=synthetic"})
        self.assertFalse(quote["stay_total_verified"])
        self.assertTrue(quote["booking_url_present"])
        self.assertEqual(quote["booking_host"], "example.org")
        self.assertNotIn("private=", json.dumps(quote))
        self.assertNotIn("total_cost_fen", quote)


if __name__ == "__main__":
    unittest.main()
