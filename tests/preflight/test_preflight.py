"""Meaningful safety and gate tests for the offline register audit."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.preflight import (
    CREDENTIAL_VARIABLES,
    PROVIDERS,
    audit_register,
    report_exit_code,
)


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = self.root / "evidence" / "live.json"
        evidence.parent.mkdir()
        evidence.write_text('{"redacted": true}', encoding="utf-8")
        self.data = {
            "version": 1,
            "as_of": "2026-10-15",
            "providers": [
                {
                    "id": provider_id,
                    "category": category,
                    "required_for_v1": True,
                    "status": "verified",
                    "permissions": {
                        "authorization_verified": True,
                        "display_allowed": True,
                        "comparison_allowed": True,
                        "cache_allowed": None,
                        "retention_policy": "No raw-response retention.",
                    },
                    "capabilities": {
                        "required_service": {
                            "state": "verified",
                            "required": True,
                            "public_documented": True,
                            "evidence_refs": ["https://official.example/documentation"],
                            "live_evidence_refs": ["evidence/live.json"],
                            "minimum_live_samples": 1,
                            "live_sample_count": 1,
                        }
                    },
                }
                for provider_id, category in PROVIDERS.items()
                if provider_id in {"amap", "tencent", "ctrip", "meituan", "didi"}
            ],
        }

    def audit(self, data=None, **kwargs):
        return audit_register(self.data if data is None else data, self.root, environ={}, **kwargs)

    @property
    def capability(self):
        return self.data["providers"][0]["capabilities"]["required_service"]

    def test_verified_register_only_passes_with_explicit_caveats(self):
        report = self.audit()
        self.assertEqual(report_exit_code(report), 0)
        self.assertTrue(report["report_record_integrity"]["valid"])
        self.assertTrue(report["gate_registered_ready"])
        self.assertEqual(report["audit_scope"], "offline_register_only")
        self.assertTrue(any("G2" in text for text in report["reminders"]))
        self.assertTrue(any("Dual-model" in text for text in report["reminders"]))
        self.assertFalse(any(report["credentials_presence"].values()))

    def test_invalid_schema_and_non_boolean_values_are_configuration_errors(self):
        for path, bad_value in (("version", True), ("providers", {})):
            data = copy.deepcopy(self.data)
            data[path] = bad_value
            with self.subTest(path=path):
                report = self.audit(data)
                self.assertEqual(report_exit_code(report), 3)
                self.assertFalse(report["report_record_integrity"]["valid"])
        self.capability["required"] = "yes"
        self.assertEqual(report_exit_code(self.audit()), 3)

    def test_missing_and_duplicate_provider_are_rejected(self):
        data = copy.deepcopy(self.data)
        data["providers"].pop()
        self.assertEqual(report_exit_code(self.audit(data)), 3)
        data = copy.deepcopy(self.data)
        data["providers"].append(copy.deepcopy(data["providers"][0]))
        self.assertEqual(report_exit_code(self.audit(data)), 3)

    def test_provider_status_cannot_hide_unverified_required_capability(self):
        self.capability["state"] = "blocked"
        report = self.audit()
        self.assertTrue(report["report_record_integrity"]["valid"])
        self.assertFalse(report["gate_registered_ready"])
        self.assertEqual(report_exit_code(report), 2)
        self.assertIn("CAPABILITY_NOT_VERIFIED", {item["code"] for item in report["blockers"]})

    def test_public_documents_do_not_replace_live_evidence(self):
        self.capability["live_evidence_refs"] = []
        report = self.audit()
        self.assertEqual(report_exit_code(report), 2)
        self.assertIn("LIVE_EVIDENCE_REQUIRED", {item["code"] for item in report["blockers"]})

    def test_missing_live_file_blocks_without_reading_evidence(self):
        self.capability["live_evidence_refs"] = ["evidence/not-created.json"]
        self.assertEqual(report_exit_code(self.audit()), 2)
        # Presence is the only file check: contents are deliberately not inspected.
        (self.root / "evidence/live.json").write_text("opaque redacted evidence", encoding="utf-8")
        self.capability["live_evidence_refs"] = ["evidence/live.json"]
        self.assertEqual(report_exit_code(self.audit()), 0)

    def test_unknown_or_denied_required_permissions_block(self):
        for flag in ("authorization_verified", "display_allowed", "comparison_allowed"):
            for value in (None, False):
                data = copy.deepcopy(self.data)
                data["providers"][0]["permissions"][flag] = value
                with self.subTest(flag=flag, value=value):
                    report = self.audit(data)
                    self.assertTrue(report["report_record_integrity"]["valid"])
                    self.assertEqual(report_exit_code(report), 2)
        self.data["providers"][0]["permissions"]["retention_policy"] = None
        self.assertEqual(report_exit_code(self.audit()), 2)

    def test_registered_live_samples_must_reach_minimum(self):
        self.capability["minimum_live_samples"] = 3
        self.capability["live_sample_count"] = 2
        report = self.audit()
        self.assertEqual(report_exit_code(report), 2)
        self.assertIn("LIVE_SAMPLES_INSUFFICIENT", {item["code"] for item in report["blockers"]})
        self.capability["live_sample_count"] = 3
        self.assertEqual(report_exit_code(self.audit()), 0)
        self.capability["live_sample_count"] = True
        self.assertEqual(report_exit_code(self.audit()), 3)

    def test_cache_forbidden_or_unknown_does_not_block(self):
        for value in (False, None, True):
            self.data["providers"][0]["permissions"]["cache_allowed"] = value
            with self.subTest(value=value):
                self.assertEqual(report_exit_code(self.audit()), 0)

    def test_evidence_cannot_escape_project_or_use_public_url(self):
        for reference in ("../outside.json", "https://official.example/api", "file:///evidence.json"):
            self.capability["live_evidence_refs"] = [reference]
            with self.subTest(reference=reference):
                report = self.audit()
                self.assertEqual(report_exit_code(report), 3)
                self.assertFalse(report["gate_registered_ready"])

    def test_optional_capability_is_not_a_gate_but_paths_still_confined(self):
        optional = copy.deepcopy(self.capability)
        optional.update(state="blocked", required=False, live_evidence_refs=[], minimum_live_samples=0)
        self.data["providers"][0]["capabilities"]["optional_service"] = optional
        self.assertEqual(report_exit_code(self.audit()), 0)
        optional["live_evidence_refs"] = ["../outside.json"]
        self.assertEqual(report_exit_code(self.audit()), 3)

    def test_environment_enforcement_is_explicit_and_independent_of_test_python(self):
        report = self.audit(python_version=(3, 11), prefix=str(self.root / "other"))
        self.assertEqual(report_exit_code(report), 0)
        report = self.audit(require_environment=True, python_version=(3, 11), prefix=str(self.root / "xunqi"))
        self.assertEqual(report_exit_code(report), 2)
        report = self.audit(require_environment=True, python_version=(3, 12), prefix=str(self.root / "xunqi"))
        self.assertEqual(report_exit_code(report), 0)

    def run_cli(self, register_text, extra_environment=None):
        register = self.root / "register.json"
        register.write_text(register_text, encoding="utf-8")
        environment = os.environ.copy()
        environment.update(extra_environment or {})
        script = Path(__file__).resolve().parents[2] / "scripts/preflight.py"
        return subprocess.run(
            [sys.executable, "-B", str(script), "--json", "--registry", str(register)],
            capture_output=True, text=True, encoding="utf-8", env=environment, check=False,
        )

    def test_cli_reports_invalid_json_and_duplicate_json_keys(self):
        for text in ('{"version":', '{"version":1,"version":1}', json.dumps({**self.data, "non_json": float("nan")})):
            with self.subTest(text=text):
                result = self.run_cli(text)
                self.assertEqual(result.returncode, 3)
                self.assertFalse(json.loads(result.stdout)["report_record_integrity"]["valid"])

    def test_cli_never_echoes_secret_values_or_reads_credentials_files(self):
        sentinel = "SECRET_SENTINEL_DO_NOT_OUTPUT_7e1d"
        credentials = self.root / "credentials-secret.json"
        credentials.write_text(sentinel, encoding="utf-8")
        variables = {name: sentinel for name in CREDENTIAL_VARIABLES}
        for name in CREDENTIAL_VARIABLES:
            if name.endswith("_FILE"):
                variables[name] = str(credentials)
        data = copy.deepcopy(self.data)
        data["providers"][0]["capabilities"]["required_service"]["live_evidence_refs"] = []
        result = self.run_cli(json.dumps(data), variables)
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(sentinel, combined)
        self.assertNotIn(str(credentials), combined)
        report = json.loads(result.stdout)
        self.assertEqual(report["credentials_presence"], {name: True for name in CREDENTIAL_VARIABLES})
        self.assertTrue(all(type(value) is bool for value in report["credentials_presence"].values()))

    def active_data(self, include_historical=True):
        data = copy.deepcopy(self.data)
        data.update(version=2, profile="rollinggo_maps_v1")
        for provider in data["providers"]:
            provider["required_for_v1"] = provider["id"] in {"amap", "tencent", "didi"}
            if not provider["required_for_v1"]:
                provider.update(status="blocked", block_reason="Historical, not an active requirement.")
                provider["permissions"] = {
                    "authorization_verified": None, "display_allowed": None,
                    "comparison_allowed": None, "cache_allowed": None,
                    "retention_policy": None,
                }
                capability = provider["capabilities"]["required_service"]
                capability.update(state="blocked", live_evidence_refs=[], live_sample_count=0)
        rollinggo = copy.deepcopy(data["providers"][0])
        rollinggo.update(id="rollinggo", category="hotels", required_for_v1=True)
        template = copy.deepcopy(rollinggo["capabilities"]["required_service"])
        rollinggo["capabilities"] = {
            name: copy.deepcopy(template)
            for name in ("authorization", "search_hotels", "dated_room_quotes", "currency", "quote_context")
        }
        data["providers"].insert(0, rollinggo)
        if not include_historical:
            data["providers"] = [provider for provider in data["providers"] if provider["required_for_v1"]]
        return data

    def price_data(self):
        data = self.active_data()
        data["profile"] = "rollinggo_price_v1"
        for provider in data["providers"]:
            provider["required_for_v1"] = provider["id"] == "rollinggo"
            if not provider["required_for_v1"]:
                provider["status"] = "blocked"
        return data

    def test_price_profile_only_requires_rollinggo_and_preserves_other_blockers(self):
        data = self.price_data()
        report = self.audit(data)
        self.assertEqual(report_exit_code(report), 0)
        self.assertEqual(report["required_providers"], ["rollinggo"])
        self.assertTrue(all(p["status"] == "blocked" for p in data["providers"] if p["id"] != "rollinggo"))

    def test_price_profile_cannot_omit_rollinggo_or_its_quote_capabilities(self):
        for name in ("authorization", "search_hotels", "dated_room_quotes", "currency", "quote_context"):
            data = self.price_data()
            del data["providers"][0]["capabilities"][name]
            self.assertEqual(report_exit_code(self.audit(data)), 3)
        data = self.price_data()
        data["providers"][0]["required_for_v1"] = False
        self.assertEqual(report_exit_code(self.audit(data)), 3)
        data = self.price_data()
        data["providers"][0]["capabilities"]["dated_room_quotes"]["state"] = "blocked"
        self.assertEqual(report_exit_code(self.audit(data)), 2)

    def test_legacy_profile_remains_explicitly_reported_and_supported(self):
        report = self.audit()
        self.assertEqual(report["profile"], "legacy_full_v1")
        self.assertEqual(report["scope"], "legacy_full_v1")
        self.assertEqual(set(report["required_providers"]), {"amap", "tencent", "ctrip", "meituan", "didi"})
        for version in (1, 2):
            data = copy.deepcopy(self.data)
            data.update(version=version, profile="legacy_full_v1")
            with self.subTest(version=version):
                self.assertEqual(report_exit_code(self.audit(data)), 0)

    def test_profile_must_be_known_and_match_registry_version(self):
        cases = [
            {"version": 2},
            {"version": 2, "profile": None},
            {"version": 2, "profile": []},
            {"version": 2, "profile": "unapproved_profile"},
            {"version": 1, "profile": "unapproved_profile"},
            {"version": 1, "profile": "rollinggo_maps_v1"},
            {"version": 3, "profile": "rollinggo_maps_v1"},
        ]
        for changes in cases:
            data = copy.deepcopy(self.data)
            data.update(changes)
            with self.subTest(changes=changes):
                report = self.audit(data)
                self.assertEqual(report_exit_code(report), 3)
                self.assertFalse(report["gate_registered_ready"])
                self.assertIsNone(report["profile"])

    def test_active_profile_ignores_historical_blockers_without_mutating_them(self):
        data = self.active_data()
        before = copy.deepcopy(data)
        report = self.audit(data)
        self.assertEqual(report_exit_code(report), 0)
        self.assertEqual(report["scope"], "rollinggo_maps_v1")
        self.assertEqual(set(report["required_providers"]), {"rollinggo", "amap", "tencent", "didi"})
        self.assertEqual(data, before)
        self.assertEqual({p["status"] for p in data["providers"] if not p["required_for_v1"]}, {"blocked"})
        self.assertEqual(report_exit_code(self.audit(self.active_data(include_historical=False))), 0)

    def test_profile_required_flags_cannot_be_edited_to_bypass_gate(self):
        for provider_id in ("rollinggo", "amap", "tencent", "didi", "ctrip", "meituan"):
            data = self.active_data()
            provider = next(p for p in data["providers"] if p["id"] == provider_id)
            provider["required_for_v1"] = not provider["required_for_v1"]
            with self.subTest(provider_id=provider_id):
                self.assertEqual(report_exit_code(self.audit(data)), 3)
        data = self.active_data()
        data["providers"][0]["required_for_v1"] = 1
        self.assertEqual(report_exit_code(self.audit(data)), 3)

    def test_active_required_provider_missing_or_unknown_is_invalid(self):
        for missing in ("rollinggo", "amap", "tencent", "didi"):
            data = self.active_data()
            data["providers"] = [p for p in data["providers"] if p["id"] != missing]
            with self.subTest(missing=missing):
                self.assertEqual(report_exit_code(self.audit(data)), 3)
        data = self.active_data()
        data["providers"][0]["id"] = "not_approved"
        self.assertEqual(report_exit_code(self.audit(data)), 3)

    def test_rollinggo_minimum_capabilities_cannot_be_removed_or_made_optional(self):
        for name in ("authorization", "search_hotels", "dated_room_quotes", "currency", "quote_context"):
            for remove in (False, True):
                data = self.active_data()
                capabilities = data["providers"][0]["capabilities"]
                if remove:
                    del capabilities[name]
                else:
                    capabilities[name]["required"] = False
                with self.subTest(name=name, remove=remove):
                    self.assertEqual(report_exit_code(self.audit(data)), 3)

    def test_active_unverified_permissions_and_live_samples_still_block(self):
        for flag in ("authorization_verified", "display_allowed", "comparison_allowed"):
            data = self.active_data()
            data["providers"][0]["permissions"][flag] = None
            with self.subTest(flag=flag):
                report = self.audit(data)
                self.assertEqual(report_exit_code(report), 2)
                self.assertIn("PERMISSION_NOT_VERIFIED", {item["code"] for item in report["blockers"]})
        data = self.active_data()
        capability = data["providers"][0]["capabilities"]["dated_room_quotes"]
        capability.update(state="unverified", live_evidence_refs=[], live_sample_count=0)
        report = self.audit(data)
        self.assertEqual(report_exit_code(report), 2)
        self.assertEqual(
            {item["code"] for item in report["blockers"]},
            {"CAPABILITY_NOT_VERIFIED", "LIVE_SAMPLES_INSUFFICIENT", "LIVE_EVIDENCE_REQUIRED"},
        )
        self.assertTrue(all(item["path"].startswith("rollinggo.") for item in report["blockers"]))

    def test_historical_optional_providers_remain_structurally_and_path_validated(self):
        data = self.active_data()
        historical = next(p for p in data["providers"] if p["id"] == "ctrip")
        historical["permissions"]["cache_allowed"] = "yes"
        self.assertEqual(report_exit_code(self.audit(data)), 3)
        historical["permissions"]["cache_allowed"] = None
        historical["capabilities"]["required_service"]["live_evidence_refs"] = ["../outside.json"]
        self.assertEqual(report_exit_code(self.audit(data)), 3)

    def test_unknown_room_and_rating_data_do_not_become_verified_by_profile_switch(self):
        data = self.active_data()
        capabilities = data["providers"][0]["capabilities"]
        for name in ("private_room", "ensuite", "nightly_prices", "stay_total_and_mandatory_fees", "source_rating"):
            optional = copy.deepcopy(capabilities["authorization"])
            optional.update(state="unverified", required=False, live_evidence_refs=[], minimum_live_samples=0, live_sample_count=0, data_status="unknown")
            capabilities[name] = optional
        report = self.audit(data)
        self.assertEqual(report_exit_code(report), 0)
        for name in ("private_room", "ensuite", "nightly_prices", "stay_total_and_mandatory_fees", "source_rating"):
            self.assertEqual(capabilities[name]["state"], "unverified")
            self.assertEqual(capabilities[name]["data_status"], "unknown")
        self.assertTrue(any("G2" in reminder for reminder in report["reminders"]))

    def test_cli_reports_active_scope_and_new_credential_presence_without_secret_values(self):
        sentinel = "PROFILE_SECRET_DO_NOT_OUTPUT_91f2"
        data = self.active_data()
        data["providers"][0]["capabilities"]["dated_room_quotes"]["live_evidence_refs"] = []
        result = self.run_cli(json.dumps(data), {"ROLLINGGO_API_KEY": sentinel, "DIDI_MCP_KEY": sentinel})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["profile"], "rollinggo_maps_v1")
        self.assertTrue(report["credentials_presence"]["ROLLINGGO_API_KEY"])
        self.assertTrue(report["credentials_presence"]["DIDI_MCP_KEY"])
        self.assertTrue(any("never loads .env" in item for item in report["reminders"]))

    def test_active_profile_cannot_change_legacy_requirements_without_version_change(self):
        data = copy.deepcopy(self.data)
        data["providers"] = [p for p in data["providers"] if p["id"] not in {"ctrip", "meituan"}]
        self.assertEqual(report_exit_code(self.audit(data)), 3)


if __name__ == "__main__":
    unittest.main()
