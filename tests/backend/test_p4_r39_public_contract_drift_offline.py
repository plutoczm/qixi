"""No-network r39 test: published doc drift is visible, not auto-migrated."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from scripts.p4_r39_public_contract_drift_offline import (
    ROOT, OFFICIAL_MAX_ONE_PAGE, analyze_contract, check,
)


def test_current_app_snapshot_flags_max_ten_vs_default_twenty_without_auto_fix():
    data = check()
    assert OFFICIAL_MAX_ONE_PAGE == 10
    assert data["local_search_default"] == 20
    assert data["local_search_max"] == 20
    assert data["production_sends_size_from_requested_limit"] is True
    assert data["production_uses_next_page_token"] is False
    assert data["local_limit_exceeds_new_public_documented_max"] is True
    assert data["details_requests_all_occupancy_fields"] is True
    assert data["actual_account_version_verified"] is False
    assert data["safe_auto_fix_permitted"] is False
    assert data["changes_production_behavior"] is False
    assert data["source_business_http_calls"] == 0
    assert data["p4_status"] == "BLOCKED"


def test_synthetic_new_limit_ten_is_reported_without_changing_current_repo(tmp_path):
    schema_path = tmp_path / "contracts.py"
    service_path = tmp_path / "service.py"
    old = (ROOT / "backend/stay_agent/contracts.py").read_text(encoding="utf-8")
    snippet = "Field(ge=1, le=20)] = 20"
    assert old.count(snippet) == 1
    schema_path.write_text(old.replace(snippet, "Field(ge=1, le=10)] = 10"), encoding="utf-8")
    service_path.write_text(
        (ROOT / "backend/stay_agent/service.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    checked = analyze_contract(schema_path, service_path)
    assert checked["local_search_default"] == 10
    assert checked["local_search_max"] == 10
    assert checked["local_limit_exceeds_new_public_documented_max"] is False
    assert checked["production_uses_next_page_token"] is False
    assert checked["actual_account_version_verified"] is False


def test_unrecognized_pagination_or_removed_occupancy_binding_fails_closed(tmp_path):
    contracts = ROOT / "backend/stay_agent/contracts.py"
    original = (ROOT / "backend/stay_agent/service.py").read_text(encoding="utf-8")
    altered = [
        ('"size": query.limit', '"size": 5'),
        ('"childAgeDetails": query.children_ages', '"adultsWithoutChildAges": query.children_ages'),
    ]
    for pos, (before, after) in enumerate(altered):
        assert original.count(before) == 1
        path = tmp_path / f"bad-service-{pos}.py"
        path.write_text(original.replace(before, after), encoding="utf-8")
        with pytest.raises(ValueError):
            analyze_contract(contracts, path)


def test_cli_exposes_only_explicit_offline_check_without_private_or_live_data():
    path = ROOT / "scripts/p4_r39_public_contract_drift_offline.py"
    good = subprocess.run(
        [sys.executable, str(path), "--check"],
        cwd=ROOT, capture_output=True, check=True,
        encoding="utf-8", errors="replace",
    )
    output = json.loads(good.stdout)
    assert output["source_business_http_calls"] == 0
    for secret in ("Bearer ", "ROLLINGGO_API_KEY", "Mcpsessionid", "api_key"):
        assert secret not in good.stdout
    bad = subprocess.run(
        [sys.executable, str(path), "--check", "--live"],
        cwd=ROOT, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    assert bad.returncode != 0
    assert "unrecognized arguments" in bad.stderr


def test_missing_file_does_not_fabricate_success(tmp_path):
    with pytest.raises(ValueError, match="SOURCE_MISSING_OR_OVERSIZED"):
        analyze_contract(tmp_path / "absent.py", Path(__file__).resolve())
