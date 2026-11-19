"""Offline audit of the provider register; never calls providers or loads .env."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

PROVIDERS = {
    "amap": "map",
    "tencent": "map",
    "ctrip": "hotels",
    "meituan": "hotels",
    "didi": "fare",
    "rollinggo": "hotels",
}
PROFILE_REQUIRED_PROVIDERS = {
    "legacy_full_v1": frozenset({"amap", "tencent", "ctrip", "meituan", "didi"}),
    "rollinggo_maps_v1": frozenset({"rollinggo", "amap", "tencent", "didi"}),
    "rollinggo_price_v1": frozenset({"rollinggo"}),
}
PROFILE_REQUIRED_CAPABILITIES = {
    "rollinggo_maps_v1": {
        "rollinggo": frozenset({
            "authorization", "search_hotels", "dated_room_quotes",
            "currency", "quote_context",
        }),
    },
}
PROFILE_REQUIRED_CAPABILITIES["rollinggo_price_v1"] = PROFILE_REQUIRED_CAPABILITIES["rollinggo_maps_v1"]
STATES = {"unverified", "verified", "blocked", "unsupported"}
CREDENTIAL_VARIABLES = (
    "AMAP_API_KEY",
    "ROLLINGGO_API_KEY",
    "DIDI_MCP_KEY",
    "TENCENT_MAP_KEY",
    "CTRIP_CREDENTIALS_FILE",
    "MEITUAN_CREDENTIALS_FILE",
    "DIDI_CREDENTIALS_FILE",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
)
PERMISSION_FLAGS = (
    "authorization_verified",
    "display_allowed",
    "comparison_allowed",
    "cache_allowed",
)
REMINDERS = [
    "Offline register audit only: existing local evidence references are not live API revalidation.",
    "Credential presence is configuration metadata, not proof of authentication or permission.",
    "Minimum live sample counts and evidence contents require separate human verification.",
    "Dual-model integration and all G2 real-data acceptance checks must be verified separately.",
    "Only the explicit register profile is audited; historical optional providers are not verified by omission.",
    "rollinggo_price_v1 covers quote browsing only; maps, fares, LLM and full eligibility remain separate.",
    "This CLI never loads .env; authorized probes may check local .env configuration separately without exposing values.",
]


def issue(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _register_profile(data: Any) -> str | None:
    """Resolve only valid version/profile combinations; never trust a mutable required flag."""
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    if type(version) is not int or version not in (1, 2):
        return None
    profile = data.get("profile", "legacy_full_v1" if version == 1 else None)
    if not isinstance(profile, str) or profile not in PROFILE_REQUIRED_PROVIDERS:
        return None
    if version == 1 and profile != "legacy_full_v1":
        return None
    return profile


def validate_register(data: Any) -> list[dict[str, str]]:
    """Validate structure without echoing arbitrary register values."""
    errors: list[dict[str, str]] = []

    def error(path: str, message: str) -> None:
        errors.append(issue(path, "INVALID_SCHEMA", message))

    if not isinstance(data, dict):
        error("$", "Register must be a JSON object.")
        return errors
    version = data.get("version")
    if type(version) is not int or version not in (1, 2):
        error("version", "Register version must be integer 1 or 2.")
    declared_profile = data.get("profile", "legacy_full_v1" if version == 1 else None)
    if not isinstance(declared_profile, str) or declared_profile not in PROFILE_REQUIRED_PROVIDERS:
        error("profile", "Register version 2 requires an explicit known profile.")
    elif version == 1 and declared_profile != "legacy_full_v1":
        error("profile", "Register version 1 only supports the historical legacy profile.")
    profile = _register_profile(data)
    required_providers = PROFILE_REQUIRED_PROVIDERS.get(profile, frozenset())
    as_of = data.get("as_of")
    try:
        if not isinstance(as_of, str) or dt.date.fromisoformat(as_of).isoformat() != as_of:
            raise ValueError
    except ValueError:
        error("as_of", "as_of must be an ISO calendar date (YYYY-MM-DD).")
    providers = data.get("providers")
    if not isinstance(providers, list):
        error("providers", "providers must be a list.")
        return errors
    found: set[str] = set()
    for index, provider in enumerate(providers):
        base = f"providers[{index}]"
        if not isinstance(provider, dict):
            error(base, "Each provider must be an object.")
            continue
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or provider_id not in PROVIDERS:
            error(base + ".id", "Unknown or missing provider id.")
        elif provider_id in found:
            error(base + ".id", "Duplicate provider id.")
        else:
            found.add(provider_id)
        if isinstance(provider_id, str) and provider_id in PROVIDERS:
            if provider.get("category") != PROVIDERS[provider_id]:
                error(base + ".category", "Provider category does not match its id.")
        if type(provider.get("required_for_v1")) is not bool:
            error(base + ".required_for_v1", "required_for_v1 must be boolean.")
        elif profile is not None and isinstance(provider_id, str) and provider_id in PROVIDERS:
            if provider["required_for_v1"] is not (provider_id in required_providers):
                error(base + ".required_for_v1", "Required providers are fixed by the selected profile.")
        if not isinstance(provider.get("status"), str) or provider["status"] not in STATES:
            error(base + ".status", "Invalid provider state.")
        permissions = provider.get("permissions")
        if not isinstance(permissions, dict):
            error(base + ".permissions", "permissions must be an object.")
        else:
            for flag in PERMISSION_FLAGS:
                if flag not in permissions or (
                    permissions[flag] is not None and type(permissions[flag]) is not bool
                ):
                    error(base + ".permissions." + flag, "Permission must be boolean or null.")
            retention = permissions.get("retention_policy")
            if "retention_policy" not in permissions or (
                retention is not None and (
                    not isinstance(retention, str) or not retention.strip()
                )
            ):
                error(base + ".permissions.retention_policy", "Retention policy must be nonempty text or null.")
        capabilities = provider.get("capabilities")
        if not isinstance(capabilities, dict) or not capabilities:
            error(base + ".capabilities", "capabilities must be a nonempty object.")
            continue
        required_count = 0
        for cap_index, (capability_id, capability) in enumerate(capabilities.items()):
            cap_path = base + f".capabilities[{cap_index}]"
            if not isinstance(capability_id, str) or not capability_id.strip():
                error(cap_path, "Capability id must be nonempty text.")
            if not isinstance(capability, dict):
                error(cap_path, "Capability must be an object.")
                continue
            if not isinstance(capability.get("state"), str) or capability["state"] not in STATES:
                error(cap_path + ".state", "Invalid capability state.")
            if type(capability.get("required")) is not bool:
                error(cap_path + ".required", "required must be boolean.")
            elif capability["required"]:
                required_count += 1
            if type(capability.get("public_documented")) is not bool:
                error(cap_path + ".public_documented", "public_documented must be boolean.")
            for ref_key in ("evidence_refs", "live_evidence_refs"):
                refs = capability.get(ref_key)
                if not isinstance(refs, list) or any(
                    not isinstance(ref, str) or not ref.strip() for ref in refs
                ):
                    error(cap_path + "." + ref_key, "Evidence references must be a list of nonempty strings.")
            count = capability.get("live_sample_count")
            if type(count) is not int or count < 0:
                error(cap_path + ".live_sample_count", "Live sample count must be a nonnegative integer.")
            samples = capability.get("minimum_live_samples")
            if type(samples) is not int or samples < 0 or (
                capability.get("required") is True and samples < 1
            ):
                error(cap_path + ".minimum_live_samples", "Required capabilities need a positive integer sample minimum.")
        if isinstance(provider_id, str) and provider_id in required_providers:
            if required_count == 0:
                error(base + ".capabilities", "Each active provider needs at least one required capability.")
            mandatory = PROFILE_REQUIRED_CAPABILITIES.get(profile, {}).get(provider_id, frozenset())
            for capability_id in sorted(mandatory):
                capability = capabilities.get(capability_id)
                if not isinstance(capability, dict) or capability.get("required") is not True:
                    error(base + ".capabilities." + capability_id, "The selected profile requires this capability.")
    for provider_id in sorted(required_providers):
        if provider_id not in found:
            error("providers", f"Required provider missing: {provider_id}.")
    return errors


def _local_evidence(
    reference: str, project_root: Path
) -> tuple[bool, str | None]:
    # Resolve links before containment checking; never read evidence contents.
    if "://" in reference or reference.startswith(("data:", "file:", "http:", "https:")):
        return False, "Evidence must be a local project file, not a URL."
    try:
        root = project_root.resolve()
        candidate = Path(reference)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(root):
            return False, "Evidence path must remain inside the project."
        return candidate.is_file(), None
    except (OSError, RuntimeError, ValueError):
        return False, "Evidence path is invalid."


def audit_register(
    data: Any,
    project_root: Path,
    *,
    require_environment: bool = False,
    environ: Mapping[str, str] | None = None,
    python_version: tuple[int, int] | None = None,
    prefix: str | None = None,
) -> dict[str, Any]:
    errors = validate_register(data)
    profile = _register_profile(data)
    required_providers = PROFILE_REQUIRED_PROVIDERS.get(profile, frozenset())
    blockers: list[dict[str, str]] = []
    environment = os.environ if environ is None else environ
    version = sys.version_info[:2] if python_version is None else python_version
    environment_name = Path(sys.prefix if prefix is None else prefix).name
    environment_matches = tuple(version) == (3, 12) and environment_name.casefold() == "xunqi"

    if not errors:
        for provider in data["providers"]:
            base = provider["id"]
            active = base in required_providers
            if active:
                if provider["status"] != "verified":
                    blockers.append(issue(base, "PROVIDER_NOT_VERIFIED", "Provider status is not verified."))
                for flag in PERMISSION_FLAGS[:3]:
                    if provider["permissions"][flag] is not True:
                        blockers.append(issue(base + ".permissions." + flag, "PERMISSION_NOT_VERIFIED", "Permission must be explicitly true."))
                if provider["permissions"]["retention_policy"] is None:
                    blockers.append(issue(base + ".permissions.retention_policy", "RETENTION_NOT_RECORDED", "Record the permitted retention policy."))
            for capability_id, capability in provider["capabilities"].items():
                cap_path = base + ".capabilities." + capability_id
                # All recorded live references must obey local-path confinement.
                for index, reference in enumerate(capability["live_evidence_refs"]):
                    exists, invalid = _local_evidence(reference, project_root)
                    if invalid:
                        errors.append(issue(cap_path + f".live_evidence_refs[{index}]", "INVALID_EVIDENCE_PATH", invalid))
                    elif active and capability["required"] and not exists:
                        blockers.append(issue(cap_path, "LIVE_EVIDENCE_MISSING", "A referenced live evidence file does not exist."))
                if not active or not capability["required"]:
                    continue
                if capability["state"] != "verified":
                    blockers.append(issue(cap_path, "CAPABILITY_NOT_VERIFIED", "Required capability is not verified."))
                if capability["live_sample_count"] < capability["minimum_live_samples"]:
                    blockers.append(issue(cap_path, "LIVE_SAMPLES_INSUFFICIENT", "Registered live sample count is below the required minimum."))
                if not capability["live_evidence_refs"]:
                    blockers.append(issue(cap_path, "LIVE_EVIDENCE_REQUIRED", "Required capability needs local live evidence; public documentation is insufficient."))
    if require_environment and not environment_matches:
        blockers.append(issue("runtime", "ENVIRONMENT_MISMATCH", "Run with Python 3.12 in the xunqi project environment."))

    return {
        "report_version": 2,
        "audit_scope": "offline_register_only",
        "profile": profile,
        "scope": profile,
        "required_providers": sorted(required_providers),
        "as_of": data.get("as_of") if isinstance(data, dict) and isinstance(data.get("as_of"), str) else None,
        "report_record_integrity": {"valid": not errors, "errors": errors},
        "gate_registered_ready": not errors and not blockers,
        "blockers": blockers,
        "credentials_presence": {name: bool(environment.get(name)) for name in CREDENTIAL_VARIABLES},
        "runtime": {
            "environment_check_required": require_environment,
            "python_version": ".".join(map(str, version)),
            "environment_name": environment_name,
            "matches_project_environment": environment_matches,
        },
        "reminders": list(REMINDERS),
    }


def report_exit_code(report: dict[str, Any]) -> int:
    if not report["report_record_integrity"]["valid"]:
        return 3
    return 0 if report["gate_registered_ready"] else 2


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError("Non-standard JSON constant.")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit the complete machine-readable audit report.")
    parser.add_argument("--registry", type=Path, default=root / "scripts/provider-register.json")
    parser.add_argument("--require-environment", action="store_true", help="Require Python 3.12 and sys.prefix ending in xunqi.")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.registry.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_json_constant)
        report = audit_register(data, root, require_environment=args.require_environment)
    except (OSError, UnicodeError, ValueError, RecursionError):
        report = audit_register(None, root, require_environment=args.require_environment)
        report["report_record_integrity"]["errors"] = [
            issue("registry", "INVALID_REGISTER_FILE", "Register file is missing, unreadable, or invalid JSON.")
        ]
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        integrity = "valid" if report["report_record_integrity"]["valid"] else "invalid"
        print(f"Offline register integrity: {integrity}")
        print(f"Register profile: {report['profile']}")
        print(f"Registered readiness gate: {report['gate_registered_ready']}")
        for item in report["report_record_integrity"]["errors"] + report["blockers"]:
            print(f"- {item['code']}: {item['path']}: {item['message']}")
        for reminder in report["reminders"]:
            print(f"- {reminder}")
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
