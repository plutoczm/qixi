"""FlyAI discovery adapter for the source-isolated orchestrator.

FlyAI answers exactly one question: "which hotels does this source show for this
city?" It gives ``shId``, name, address, a display price string, coordinates, a
star label and a "near some metro station" hint. It does **not** give currency,
room type, inventory, occupancy echo, or booking context.

So a FlyAI result is a *candidate list only*:

* no money amount (the display string carries no currency field),
* no room, no inventory, no occupancy and no booking link,
* no cross-source merge, dedupe or ranking -- the orchestrator already forbids
  those, and this adapter never sets ``merged``,
* coordinates are kept inside the candidate's own source and are never mixed
  with a map anchor from another source.

One cooperative ``ctx.spend()`` per run, i.e. one bounded ``search-hotel`` call;
there is no detail, retry, pagination or fallback here.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping

from .contracts import SearchInput
from .multi_source import (
    SourceCandidate, SourceContext, SourceResult, SourceStatus,
)
from .providers.data.flyai import FlyAIDiscoveryProvider, FlyAIHotelHint
from .providers.data.flyai_client import FlyAICliClient, FlyAIMCPClient, load_key, search_callback
from .providers.data.tuniu import TuniuFailure

SOURCE_ID = "flyai"

#: What a FlyAI hint can never prove, stated once so callers cannot forget it.
ALWAYS_UNVERIFIED: tuple[str, ...] = (
    "币种未注明",
    "仅展示价字符串",
    "无房型",
    "无容量与入住回显",
    "无足量库存",
    "无含费总价",
    "无逐夜价",
    "无可核对预订链接",
    "同店身份未核实",
    "坐标不用于距离或路线",
)

_FAILURE_MAP: dict[str, SourceStatus] = {
    "PROVIDER_DISABLED": "unconfigured",
    "NOT_CONNECTED": "unconfigured",
    "INVALID_ARGUMENTS": "unavailable",
    "INVALID_RESPONSE": "unavailable",
    "SOURCE_ERROR": "unavailable",
    "TIMEOUT": "unavailable",
}

_SAFE_REASON: dict[str, str] = {
    "unconfigured": "CONFIGURATION_MISSING",
    "unavailable": "SOURCE_ERROR",
}


def _candidate(hint: FlyAIHotelHint, observed_at: str) -> SourceCandidate:
    return SourceCandidate(
        source_id=SOURCE_ID,
        source_hotel_id=hint.opaque_id,
        name=hint.name,
        address=hint.address,
        currency="UNSTATED",
        price_scope="unknown",
        observed_at=observed_at,
        identity_status="unverified",
        booking_status="unverified",
        occupancy_status="unverified",
        reference_price_fen=None,
        price_text=hint.price_text,
        star=hint.star,
        nearby_transit=hint.nearby_transit,
        coordinates_present=hint.latitude is not None and hint.longitude is not None,
        unverified=list(ALWAYS_UNVERIFIED),
    )


def make_flyai_adapter(
    provider: FlyAIDiscoveryProvider | None = None,
) -> Callable[[SearchInput, SourceContext], SourceResult]:
    """Build the orchestrator adapter. ``provider`` is injected for tests."""

    def adapter(query: SearchInput, ctx: SourceContext) -> SourceResult:
        if query.place_type != "城市":
            # FlyAI search takes a city destination only; never guess a city
            # from a hotel name, address or POI.
            return SourceResult(
                id=SOURCE_ID, status="unavailable",
                unavailable_reason="INVALID_DATA",
            )
        if not ctx.spend():
            reason = (
                "CANCELLED" if ctx.cancelled.is_set()
                else "DEADLINE_EXCEEDED" if ctx.clock() >= ctx.deadline
                else "BUDGET_EXHAUSTED"
            )
            return SourceResult(
                id=SOURCE_ID, status="unavailable", unavailable_reason=reason,
            )
        active = provider or FlyAIDiscoveryProvider()
        try:
            evidence = active.search(query, city=query.place)
        except TuniuFailure as error:
            failed = _FAILURE_MAP.get(error.code, "unavailable")
            reason = error.code if error.code in {"TIMEOUT", "RATE_LIMITED", "AUTHENTICATION_FAILED"} else _SAFE_REASON[failed]
            return SourceResult(
                id=SOURCE_ID, status=failed, http_used=1,
                unavailable_reason=reason,
            )
        candidates = [_candidate(hint, evidence.observed_at) for hint in evidence.hints]
        status: SourceStatus = (
            "partial" if evidence.status == "partial" or evidence.limited
            else "complete"
        )
        return SourceResult(
            id=SOURCE_ID, status=status, http_used=1,
            observed_at=evidence.observed_at,
            candidates=candidates,
        )

    return adapter


def parallel_flyai_adapter(query: SearchInput, ctx: SourceContext) -> SourceResult:
    """Use the authorized official CLI by default, or the explicitly selected MCP transport."""
    mode = os.environ.get("XUNQI_FLYAI_MODE", "cli")
    key = load_key() if mode in {"mcp", "cli"} else ""
    if not key:
        return SourceResult(id=SOURCE_ID, status="unconfigured", unavailable_reason="CONFIGURATION_MISSING")
    remaining = ctx.deadline - ctx.clock()
    if remaining <= 0:
        return SourceResult(id=SOURCE_ID, status="expired", unavailable_reason="DEADLINE_EXCEEDED")
    options = {"timeout": min(30, remaining), "max_requests": 1}
    client = (FlyAICliClient(key, guard_network=True, **options) if mode == "cli"
              else FlyAIMCPClient(key, **options))
    provider = FlyAIDiscoveryProvider(search_callback(client), mode=mode)
    return make_flyai_adapter(provider)(query, ctx)


def describe_hint(hint: FlyAIHotelHint) -> Mapping[str, object]:
    """Sanitized, display-oriented view for logs and docs (no URL, no secret)."""
    return {
        "opaque_id_present": bool(hint.opaque_id),
        "name": hint.name,
        "address": hint.address,
        "price_text": hint.price_text,
        "star": hint.star,
        "nearby_transit": hint.nearby_transit,
        "has_coordinates": hint.latitude is not None and hint.longitude is not None,
        "detail_url_present": hint.detail_url_present,
    }
