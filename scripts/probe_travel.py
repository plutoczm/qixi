"""Credential check or one explicit read-only discovery/search; never integrate fake data."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stay_agent.providers.data.travel_clients import (  # noqa: E402
    configuration,
    load_key,
    load_tencent_secret,
    MapProbe,
    DidiDiscovery,
    ProbeError,
)
from stay_agent.providers.data.rollinggo_client import RollingGoError  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check-config", action="store_true")
    group.add_argument("--map-search", action="store_true")
    group.add_argument("--didi-discover", action="store_true")
    parser.add_argument("--provider", choices=("amap", "tencent"))
    parser.add_argument("--keyword", default="海底捞")
    parser.add_argument("--city", default="广州")
    args = parser.parse_args()
    if not args.map_search and not args.didi_discover:
        result = {
            "network_called": False,
            "configuration": configuration(),
            "live_api_verified": False,
        }
        print(json.dumps(result, ensure_ascii=True))
        return 0 if all(p["configured"] for p in result["configuration"].values()) else 2
    provider = "didi" if args.didi_discover else args.provider
    if provider is None:
        parser.error("--provider is required with --map-search")
    result = {
        "provider": provider,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "network_called": False,
        "live_api_verified": False,
        "routes_verified": False,
    }
    try:
        key = load_key(provider)
        if args.didi_discover:
            client = DidiDiscovery(key)
            result["network_called"] = True
            tools = client.discover()
            result.update(
                status="discovered",
                tool_schemas=[
                    {"name": t["name"], "input_schema": t.get("inputSchema", {})} for t in tools
                ],
                request_count=client.http.request_count,
                live_api_verified=True,
                mode="official_documented_direct_tools_list",
            )
        else:
            client = MapProbe(provider, key)
            result["network_called"] = True
            points = client.search(args.keyword, args.city, page=1, page_size=10)
            result.update(
                status="complete",
                poi_count=len(points),
                request_count=client.http.request_count,
                live_api_verified=True,
                live_location_samples_received=bool(points),
                coordinates_persisted=False,
            )
        encoded = json.dumps(result, ensure_ascii=True)
        secret = load_tencent_secret() if provider == "tencent" else None
        if (
            key in encoded
            or quote_plus(key) in encoded
            or (secret and (secret in encoded or quote_plus(secret) in encoded))
        ):
            raise ProbeError("SECRET_EXPOSURE_BLOCKED")
        print(encoded)
        return 0
    except (ProbeError, RollingGoError) as error:
        result.update(status="blocked", error_code=error.code)
        print(json.dumps(result, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
