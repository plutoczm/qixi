"""On-demand candidate discovery through the source-isolated orchestrator.

FlyAI is wired into the product as a **discovery** source only. It answers
"which hotel names does this source show for this city", and nothing else:

* no room type, no inventory, no occupancy echo, no booking link;
* the display price stays a verbatim string, never a money amount;
* coordinates are reduced to a presence flag and never reach a map anchor;
* the result is never merged, deduped or ranked against the quote source.

One user click costs exactly one bounded HTTP call, so nothing here runs
automatically after a quote search.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
import threading
import time

from .contracts import (
    ApiError,
    DiscoveryCandidate,
    DiscoveryResponse,
    SearchInput,
    Source,
)
from .flyai_source import SOURCE_ID, make_flyai_adapter
from .multi_source import MultiSourceOrchestrator, MultiSourceRequest

SOURCE = Source(id="flyai", name="飞猪")

#: Fixed, source-level limits. Stated on every response so no caller has to
#: remember what a discovery hint can and cannot prove.
LIMITATIONS: tuple[str, ...] = (
    "飞猪结果是候选名单：没有房型、没有剩余房量、没有入住人数回显，也没有可核对的预订链接。",
    "展示价是来源原样文本（例如“¥144”），币种与是否含税均未注明，未解析为金额，不参与比价。",
    "不与途牛／道旅报价合并、去重或排序；同一家店的身份也未核实。",
    "坐标仅记录是否存在，不用于距离或路线。",
    "来源接口只接受城市、日期与可选预算；人数与房间数没有传给飞猪，返回结果与本次入住组合无关。",
    "本页不提供预订入口；如需预订请自行前往来源核对。",
)

_STATUS_REASON: dict[str, str] = {
    "unconfigured": "飞猪凭据未配置，本次没有发起请求。",
    "unavailable": "飞猪本次未返回可用结果；这不代表该城市没有住宿。",
    "expired": "飞猪查询在截止时间前未完成，本次没有取得结果。",
    "cancelled": "本次飞猪查询已停止。",
    "partial": "飞猪只返回了部分候选，不能当作完整名单。",
    "limited": "飞猪本次返回条数已达上限，不能当作完整名单。",
}


class DiscoveryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DiscoveryService:
    """Single-source discovery. Never touches quote state or the session store."""

    def __init__(
        self,
        adapter=None,
        *,
        clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = 45.0,
    ):
        self.clock = clock
        self.orchestrator = MultiSourceOrchestrator(
            {SOURCE_ID: adapter or make_flyai_adapter()},
            clock=clock,
            deadline_seconds=deadline_seconds,
            max_workers=1,
        )

    def search(
        self, query: SearchInput, *, cancelled: threading.Event | None = None
    ) -> DiscoveryResponse:
        if query.place_type != "城市":
            # FlyAI search takes a city destination only; never guess a city
            # from a hotel name, address or POI.
            raise DiscoveryError("DISCOVERY_CITY_ONLY")
        outcome = self.orchestrator.run(
            MultiSourceRequest(query=query, sources=[SOURCE_ID], revision=0, max_http_per_source=1),
            cancelled=cancelled,
            commit=False,
        )
        result = outcome.sources[0]
        scope = (
            f"本次按「{query.place}」{query.check_in.isoformat()} 至 "
            f"{query.check_out.isoformat()} 查询"
            + ("，并带上预算上限" if query.budget_fen is not None else "")
            + "；人数与房间数没有传给飞猪。"
        )
        limitations = [scope, *LIMITATIONS]
        # The adapter reports a pre-emptive cancel as "unavailable" because it
        # never spent a call; the product must still say it was cancelled.
        status = (
            "cancelled"
            if result.status == "unavailable" and result.unavailable_reason == "CANCELLED"
            else result.status
        )
        reason = _STATUS_REASON.get(status)
        if reason:
            limitations.insert(0, reason)
        return DiscoveryResponse(
            source=SOURCE,
            status=status,
            queried_at=now_iso(),
            observed_at=result.observed_at,
            http_used=result.http_used,
            candidates=[
                DiscoveryCandidate(
                    name=candidate.name,
                    address=candidate.address,
                    price_text=candidate.price_text,
                    star=candidate.star,
                    nearby_transit=candidate.nearby_transit,
                    coordinates_present=candidate.coordinates_present,
                )
                for candidate in result.candidates
            ],
            limitations=limitations,
            error=(
                ApiError(code=result.unavailable_reason or status, message=reason)
                if status in ("unavailable", "unconfigured", "expired", "cancelled")
                else None
            ),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
