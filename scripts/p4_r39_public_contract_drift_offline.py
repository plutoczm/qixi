"""Offline P4 r39 documented-vs-local RollingGo API contract drift check.

Official page reviewed: https://docs.rollinggo.store/zh/guides/tools
The constants represent a manually reviewed public snapshot (2026-11-02).
This tool only parses local Python source; it cannot prove a live account's
current MCP version or business API behavior. No credentials or network.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_MAX_ONE_PAGE = 10
DOC_DATE = "2026-11-02"


def _read_ast(path: Path) -> ast.Module:
    if not path.is_file() or path.stat().st_size > 300_000:
        raise ValueError("SOURCE_MISSING_OR_OVERSIZED")
    return ast.parse(path.read_text(encoding="utf-8"))


def _named_class(module: ast.Module, name: str) -> ast.ClassDef:
    matches = [node for node in module.body if isinstance(node, ast.ClassDef)
               and node.name == name]
    if len(matches) != 1:
        raise ValueError("CLASS_STRUCTURE_CHANGED")
    return matches[0]


def _named_method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    matches = [node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name == name]
    if len(matches) != 1:
        raise ValueError("METHOD_STRUCTURE_CHANGED")
    return matches[0]


def analyze_contract(contracts_path: Path, service_path: Path) -> dict:
    schema = _named_class(_read_ast(contracts_path), "SearchInput")
    limit_defs = [node for node in schema.body if isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name) and node.target.id == "limit"]
    if len(limit_defs) != 1 or not isinstance(limit_defs[0].value, ast.Constant):
        raise ValueError("LIMIT_STRUCTURE_CHANGED")
    limit_default = limit_defs[0].value.value
    limit_field = [node for node in ast.walk(limit_defs[0].annotation)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "Field"]
    max_values = [keyword.value.value for field in limit_field for keyword in field.keywords
                  if keyword.arg == "le" and isinstance(keyword.value, ast.Constant)]
    if type(limit_default) is not int or len(max_values) != 1 or type(max_values[0]) is not int:
        raise ValueError("LIMIT_VALIDATION_NOT_PROVABLE")
    max_local = max_values[0]

    service_tree = _read_ast(service_path)
    service_cls = _named_class(service_tree, "QuoteService")
    search_func = _named_method(service_cls, "search")
    details_func = _named_method(service_cls, "quotes")
    requested_size = [
        node for node in ast.walk(search_func) if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "size"
        and isinstance(value, ast.Attribute) and value.attr == "limit"
        and isinstance(value.value, ast.Name) and value.value.id == "query"
    ]
    if len(requested_size) != 1:
        raise ValueError("SEARCH_SIZE_ARGUMENT_NOT_RECOGNIZED")
    search_constants = {
        node.value for node in ast.walk(search_func)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    details_constants = {
        node.value for node in ast.walk(details_func)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    needed = {"adultCount", "childCount", "childAgeDetails", "roomCount", "dateParam"}
    if not needed.issubset(details_constants):
        raise ValueError("OCCUPANCY_REQUEST_BINDING_NOT_PROVABLE")

    return {
        "mode": "offline_local_ast_against_manual_public_doc_snapshot",
        "public_doc_url": "https://docs.rollinggo.store/zh/guides/tools",
        "document_reviewed_on": DOC_DATE,
        "official_documented_one_page_max": OFFICIAL_MAX_ONE_PAGE,
        "local_search_default": limit_default,
        "local_search_max": max_local,
        "production_sends_size_from_requested_limit": True,
        "production_uses_next_page_token": "nextPageToken" in search_constants,
        "details_requests_all_occupancy_fields": True,
        "local_limit_exceeds_new_public_documented_max": (
            limit_default > OFFICIAL_MAX_ONE_PAGE or max_local > OFFICIAL_MAX_ONE_PAGE
        ),
        "production_automatically_paginates": False,
        "actual_account_version_verified": False,
        "safe_auto_fix_permitted": False,
        "changes_production_behavior": False,
        "source_business_http_calls": 0,
        "p4_status": "BLOCKED",
    }


def check() -> dict:
    return analyze_contract(
        ROOT / "backend/stay_agent/contracts.py",
        ROOT / "backend/stay_agent/service.py",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(check(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
