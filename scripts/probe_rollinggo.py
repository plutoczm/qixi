"""Bounded read-only RollingGo probe. Keys are never printed or persisted."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stay_agent.providers.data.rollinggo import ConfigurationError, business_summary, has_display_quote, load_api_key
from stay_agent.providers.data.rollinggo_client import RollingGoClient, RollingGoError

ROOT = Path(__file__).resolve().parents[1]
READ_TOOLS = ("searchHotels", "getHotelDetail", "getHotelSearchTags")




def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check-config", action="store_true", help="Only check local credential configuration.")
    group.add_argument("--discover", action="store_true", help="Live initialize and discover allowlisted tools.")
    group.add_argument("--tool", choices=READ_TOOLS, help="Live read-only tool call using an explicit input file.")
    parser.add_argument("--arguments-file", type=Path, help="JSON object containing official tool arguments.")
    args = parser.parse_args(argv)
    report = {"provider": "rollinggo", "read_only": True, "live_api_verified": False, "live_quote_received": False}
    try:
        key = load_api_key()
        report["credential_configured"] = True
        if not args.discover and not args.tool:
            report["status"] = "configured"
            report["network_called"] = False
            print(json.dumps(report, ensure_ascii=True))
            return 0
        arguments = {}
        if args.tool:
            if args.arguments_file is None:
                raise ConfigurationError("ARGUMENTS_FILE_REQUIRED")
            try:
                if args.arguments_file.stat().st_size > 65536:
                    raise ConfigurationError("ARGUMENTS_FILE_TOO_LARGE")
                arguments = json.loads(args.arguments_file.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                raise ConfigurationError("ARGUMENTS_FILE_INVALID") from None
            if not isinstance(arguments, dict):
                raise ConfigurationError("ARGUMENTS_OBJECT_REQUIRED")
        client = RollingGoClient(key)
        report["network_called"] = True
        tools = client.discover()
        report["tool_schemas"] = [
            {"name": tool.get("name"), "required": tool.get("inputSchema", {}).get("required", []),
             "properties": sorted(tool.get("inputSchema", {}).get("properties", {})), **({"input_schema": tool.get("inputSchema", {})} if args.discover else {})}
            for tool in tools
        ]
        if args.tool:
            result = client.call_tool(args.tool, arguments)
            report["operation"] = args.tool
            if result.get("isError"):
                report["status"] = "tool_error"
                report["error_code"] = "PROVIDER_TOOL_ERROR"
                print(json.dumps(client.redact(report), ensure_ascii=True, indent=2))
                return 4
            report["summary"] = business_summary(result)
            if report["summary"].get("provider_success") is False:
                report["status"] = "provider_error"
                report["error_code"] = "PROVIDER_BUSINESS_ERROR"
                report["protocol_version"] = client.protocol_version
                report["request_count"] = client.request_count
                print(json.dumps(client.redact(report), ensure_ascii=True, indent=2))
                return 4
            report["live_quote_received"] = args.tool == "getHotelDetail" and has_display_quote(result)
        else:
            report["operation"] = "tools/list"
        report["protocol_version"] = client.protocol_version
        report["request_count"] = client.request_count
        report["status"] = "complete"
        report["live_api_verified"] = True
        report["final_price_locked"] = False
        print(json.dumps(client.redact(report), ensure_ascii=True, indent=2))
        return 0
    except ConfigurationError as exc:
        report.update(status="blocked", error_code=exc.code)
        print(json.dumps(report, ensure_ascii=True))
        return 2
    except RollingGoError as exc:
        report.update(status="unavailable", error_code=exc.code, http_status=exc.status)
        print(json.dumps(report, ensure_ascii=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
