"""Production Tuniu-backed search/room reference service, with strict fail-closed routing.

All booking/purchase actions are absent. Provider snapshots and live MCP clients
stay in process memory, never in SQLite. Old RollingGo session IDs are not reused.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import re
import threading
import time

from .contracts import (
    ApiError, BookingGate, Cancellation, Coverage, HotelSummary, Qualification,
    QuotesResponse, ReferenceGate, RoomQuote, SearchResponse, Source,
)
from .pricing import QuoteInput, assess, lowest_qualified
from .qualification import (
    PRICE_INCLUDES_TAX,
    PRICE_UNIT,
    booking_gate,
    field_status,
    reference_gate,
)
from .providers.data.tuniu import TuniuFailure, TuniuProvider, TuniuSearchEvidence
from .storage import SessionError
from .providers.data.tuniu_mcp import (
    TuniuConfigurationError, TuniuMCPClient, auth_configuration,
)
from .service import now_iso

SOURCE = Source(id="tuniu", name="途牛")
WARNING = "途牛搜索与房型金额仅为来源参考；库存观察不证明最终可订、逐晚全部费用或独立卫浴。"
_MAX_ENTRIES = 96
# Map recall passes the missing city and the map address only as opaque context.
# Only the city segment is usable here; the address is NOT used to loosen the
# downstream exact name/address identity gate.
_CITY_HINT = re.compile(r"地图候选城市：([^；;]+)")


def hinted_city(origin_hint: str | None) -> str | None:
    if not origin_hint:
        return None
    match = _CITY_HINT.search(origin_hint)
    if match is None:
        return None
    city = match.group(1).strip()
    return city or None


@dataclass
class SearchRecord:
    evidence: TuniuSearchEvidence
    client: object
    expires: float
    lock: threading.Lock
    query: object
    attempted_page: int = 1


class TuniuQuoteService:
    source_id = "tuniu"
    data_mode = "live"

    def __init__(self, client_factory=None, *, clock=time.monotonic, data_mode="live"):
        # An injected supplier is a test double, not verified live provenance.
        if data_mode not in ("live", "test") or (client_factory is not None and data_mode != "test"):
            raise ValueError("INVALID_SOURCE_MODE")
        self.client_factory = client_factory or TuniuMCPClient
        self.clock = clock
        self.data_mode = data_mode
        self.capacity = threading.BoundedSemaphore(2)
        self._lock = threading.Lock()
        self._records: OrderedDict[str, SearchRecord] = OrderedDict()

    @staticmethod
    def credential_configured():
        try:
            auth_configuration()
            return True
        except TuniuConfigurationError:
            return False

    @staticmethod
    def error(code):
        messages = {
            "CONFIGURATION_MISSING": "途牛尚未授权；请先完成合法的本机认证配置。",
            "INVALID_CONFIGURATION": "途牛认证配置无效，请在本机检查配置。",
            "AUTHENTICATION_FAILED": "途牛认证已失效，请重新授权。",
            "ACCESS_DENIED": "当前途牛账号未获酒店查询权限。",
            "RATE_LIMITED": "途牛请求频率已达限制；本次没有自动重试。",
            "REQUEST_LIMIT": "途牛请求预算已达上限；本次没有自动继续。",
            "TIMEOUT": "途牛查询超时，本次未重试。",
            "REMOTE_UNAVAILABLE": "途牛服务连接失败或协议不兼容，未改查道旅。",
            "TOOL_NOT_AVAILABLE": "途牛当前账号未返回所需的只读酒店工具。",
            "UNSUPPORTED_PROTOCOL": "途牛当前协议版本暂不兼容。",
            "SOURCE_ERROR": "途牛返回业务错误，不能视为没有酒店。",
            "INVALID_RESPONSE": "途牛返回内容无法安全核实，未展示来源数据。",
            "INVALID_ARGUMENTS": "途牛查询条件无效，未发送。",
            "UNSUPPORTED_PLACE_TYPE": "途牛当前仅支持中国境内城市，或带地图城市线索的酒店名查询；其他地点类型尚未接通。",
            "UNSUPPORTED_REGION": "途牛当前来源未核实支持境外酒店；没有发送本次境外查询，请选择已支持的来源。",
            "BUSY": "途牛查询正在处理，请稍后手动重试。",
            "SEARCH_EXPIRED": "途牛酒店查询已过期或服务重启，请重新搜索。",
            "IDENTITY_MISMATCH": "来源酒店身份或入住条件不一致，已拒绝展示。",
            "AMBIGUOUS_IDENTITY": "来源酒店身份有歧义，已拒绝自动选择。",
            "CONTEXT_MISMATCH": "途牛回显的日期或人数不一致，已停止展示。",
            "PAGE_UNAVAILABLE": "当前分页已查询、失效或没有安全的后续页；请重新搜索。",
        }
        return ApiError(
            code=code if code in messages else "SOURCE_ERROR",
            message=messages.get(code, "途牛房价查询不可用，未自动回退其他平台。"),
        )

    def _prune(self):
        now = self.clock()
        for key in list(self._records):
            if self._records[key].expires <= now:
                self._records.pop(key)
        while len(self._records) >= _MAX_ENTRIES:
            self._records.popitem(last=False)

    def _record(self, request_id):
        with self._lock:
            self._prune()
            return self._records.get(request_id)

    @staticmethod
    def _summaries(hotels):
        views = []
        metadata = []
        for candidate in hotels:
            if candidate.hotel_id >= 2**31:
                continue
            views.append(HotelSummary(
                id=candidate.hotel_id, name=candidate.name,
                address=candidate.address,
                reference_price_fen=candidate.lowest_reference_fen,
                currency="CNY" if candidate.lowest_reference_fen is not None else None,
                hotel_stars=None, guest_score=None, booking_url=None,
                unverified=[
                    "途牛酒店星级名称不等于住客评分", "独立客房待核实",
                    "独立卫浴待核实", "库存与完整费用待核实",
                ],
            ))
            metadata.append({
                "id": candidate.hotel_id, "name": candidate.name,
                "address": candidate.address,
            })
        return views, metadata

    @staticmethod
    def _next_page(evidence):
        if (
            evidence.query_id
            and evidence.current_page < min(evidence.total_pages, 3)
            and len(evidence.hotels) < evidence.scope.limit
        ):
            return evidence.current_page + 1
        return None

    def search(self, query, store, *, client=None, origin_hint=None, source_context=None):
        if client is not None and self.data_mode != "test":
            raise ValueError("TEST_CLIENT_NOT_ALLOWED_IN_LIVE_MODE")
        base = dict(
            request_id="tuniu-unavailable",
            context=query, source=SOURCE, queried_at=now_iso(),
            data_mode=self.data_mode,
            coverage=Coverage(returned_count=0, requested_limit=query.limit, limited=False),
        )
        # Fail closed on an explicitly overseas destination before supplier access.
        # An absent country does not prove China; the UI explains the source boundary.
        if query.country_region and query.country_region.casefold() not in {
            "中国", "中国大陆", "中华人民共和国", "china", "mainland china", "prc"
        }:
            return SearchResponse(
                **base, status="unavailable",
                error=self.error("UNSUPPORTED_REGION"), warnings=[WARNING],
            )
        # City search is unchanged. A map-recalled hotel name is sent as a
        # ranking keyword plus the city carried by the map hint; the source's
        # keyword is only a hint (it returns unrelated hotels too), so the
        # existing exact name/address identity gate downstream stays in force.
        # Missing city context fails closed without any supplier request.
        keyword = None
        if query.place_type == "城市" and origin_hint is None:
            city = query.place
        elif query.place_type == "酒店":
            city = hinted_city(origin_hint)
            keyword = query.place
        else:
            city = None
        if city is None:
            return SearchResponse(
                **base, status="unavailable",
                error=self.error("UNSUPPORTED_PLACE_TYPE"),
                warnings=[WARNING],
            )
        if not self.capacity.acquire(blocking=False):
            return SearchResponse(
                **base, status="unavailable", error=self.error("BUSY"), warnings=[WARNING],
            )
        try:
            try:
                live_client = client if client is not None else self.client_factory()
                projector = TuniuProvider(live_client.call_tool, mode="offline_test")
                if source_context is None:
                    evidence = projector.search(query, city=city, keyword=keyword)
                else:
                    with live_client.request_window(source_context):
                        evidence = projector.search(query, city=city, keyword=keyword)
                hotels, metadata = self._summaries(evidence.hotels)
                request_id = "tuniu-" + evidence.request_id
                store.save(
                    request_id, query.model_dump(mode="json"),
                    [hotel.id for hotel in hotels], hotel_metadata=metadata,
                    supplier_source="tuniu",
                )
                with self._lock:
                    self._prune()
                    self._records[request_id] = SearchRecord(
                        evidence, live_client, self.clock() + 1800, threading.Lock(), query,
                    )
                skipped = len(evidence.hotels) - len(hotels)
                warnings = [
                    WARNING, "只取得途牛首次搜索页；没有自动翻页或跨源补价。",
                ]
                if keyword:
                    warnings.append(
                        "本次以酒店名作为来源排序提示；返回结果仍须名称与地址逐字一致才保留。"
                    )
                if skipped or evidence.excluded_count:
                    warnings.append("部分酒店来源身份无法核实或编号不兼容，已排除。")
                if evidence.limited:
                    warnings.append("更多酒店可能尚未查询；不可将已返回数量视为全城覆盖。")
                    if not hotels:
                        warnings.append(
                            "当前来源第一页没有可展示酒店，但仍有未查询的来源页；"
                            "本轮不自动翻页，也不能断言全城无房。"
                        )
                return SearchResponse(
                    **{**base, "request_id": request_id, "queried_at": evidence.observed_at,
                       "coverage": Coverage(
                           returned_count=len(hotels), requested_limit=query.limit,
                           limited=evidence.limited,
                       )},
                    status="partial" if evidence.status == "partial" or skipped else "complete",
                    hotels=hotels, warnings=warnings,
                    current_page=1, next_page=self._next_page(evidence),
                )
            except TuniuConfigurationError as error:
                return SearchResponse(**base, status="unavailable",
                                      error=self.error(error.code), warnings=[WARNING])
            except TuniuFailure as error:
                return SearchResponse(**base, status="unavailable",
                                      error=self.error(error.code), warnings=[WARNING])
        finally:
            self.capacity.release()

    def next_page(self, request_id: str, expected_page: int, store):
        """One manual page, same source snapshot and same original API request_id.

        Page attempts are consumed before supplier IO. No duplicate/retry on error.
        The opaque supplier queryId lives only in SearchRecord.evidence.
        """
        if not request_id.startswith("tuniu-"):
            raise TuniuFailure("SEARCH_EXPIRED")
        record = self._record(request_id)
        if record is None:
            raise TuniuFailure("SEARCH_EXPIRED")
        if not self.capacity.acquire(blocking=False):
            raise TuniuFailure("RATE_LIMITED")
        try:
            if not record.lock.acquire(blocking=False):
                raise TuniuFailure("PAGE_UNAVAILABLE")
            try:
                before = record.evidence
                if (type(expected_page) is not int or
                        expected_page != self._next_page(before) or
                        expected_page <= record.attempted_page):
                    raise TuniuFailure("PAGE_UNAVAILABLE")
                # Prevent racing duplicate clicks, even when the source times out.
                record.attempted_page = expected_page
                fetched = TuniuProvider(
                    record.client.call_tool, mode="offline_test",
                ).page(before, page_num=expected_page)
                merged_by_id = {hotel.hotel_id: hotel for hotel in before.hotels}
                rejected = set()
                for new_hotel in fetched.hotels:
                    previous = merged_by_id.get(new_hotel.hotel_id)
                    if previous is not None:
                        if (previous.name, previous.address, previous.city) != (
                            new_hotel.name, new_hotel.address, new_hotel.city
                        ):
                            rejected.add(new_hotel.hotel_id)
                        # Keep the first observation's reference price; a price
                        # change on another page is NOT an identity conflict.
                        continue
                    # A name/address collision under another ID is ambiguous too.
                    for old_hotel in before.hotels:
                        if (old_hotel.hotel_id != new_hotel.hotel_id and
                                (old_hotel.name, old_hotel.address, old_hotel.city) ==
                                (new_hotel.name, new_hotel.address, new_hotel.city)):
                            rejected.update((old_hotel.hotel_id, new_hotel.hotel_id))
                    merged_by_id[new_hotel.hotel_id] = new_hotel
                for identity in rejected:
                    merged_by_id.pop(identity, None)
                all_hotels = tuple(merged_by_id.values())
                over_limit = len(all_hotels) > before.scope.limit
                final_hotels = all_hotels[:before.scope.limit]
                excluded = before.excluded_count + fetched.excluded_count + len(rejected)
                limited = fetched.limited or over_limit
                # Keep earliest observation timestamp; new pages aren't proof
                # that first-page prices were refreshed.
                merged = replace(
                    before, hotels=final_hotels,
                    current_page=expected_page,
                    total_pages=fetched.total_pages,
                    excluded_count=excluded,
                    limited=limited,
                    status="partial" if limited or excluded else "complete",
                )
                summaries, metadata = self._summaries(merged.hotels)
                try:
                    store.replace_membership(
                        request_id, [hotel.id for hotel in summaries], metadata,
                    )
                except SessionError:
                    raise TuniuFailure("SEARCH_EXPIRED") from None
                # Do not change the absolute search session expiry.
                record.evidence = merged
                messages = [
                    WARNING,
                    "由用户手动查询途牛后续页；未自动翻页、重试或借用其他平台房价。",
                    "各页观察时间不同，首次查询时间不表示旧酒店价格已刷新。",
                ]
                if rejected or over_limit or excluded:
                    messages.append("部分来源身份冲突、不完整或超出当前展示上限，已排除。")
                if self._next_page(merged) is not None:
                    messages.append("仍有未查询的来源页，查询下一页将消耗额度。")
                return SearchResponse(
                    request_id=request_id, context=record.query, source=SOURCE,
                    queried_at=merged.observed_at, data_mode=self.data_mode,
                    coverage=Coverage(
                        returned_count=len(summaries),
                        requested_limit=before.scope.limit,
                        limited=limited,
                    ),
                    hotels=summaries, status=merged.status,
                    warnings=messages,
                    current_page=expected_page, next_page=self._next_page(merged),
                )
            finally:
                record.lock.release()
        finally:
            self.capacity.release()

    def quotes(self, request_id, hotel_id, query):
        base = dict(
            request_id=request_id, hotel_id=hotel_id, hotel_name="住宿",
            context=query, source=SOURCE, queried_at=now_iso(), data_mode=self.data_mode,
            booking_url=None, booking_status="unavailable",
            occupancy_status="unverified",
        )
        if not request_id.startswith("tuniu-"):
            return QuotesResponse(**base, status="unavailable",
                                  error=self.error("SEARCH_EXPIRED"), warnings=[WARNING])
        record = self._record(request_id)
        if record is None:
            return QuotesResponse(**base, status="unavailable",
                                  error=self.error("SEARCH_EXPIRED"), warnings=[WARNING])
        scope = record.evidence.scope
        if (
            query.check_in.isoformat() != scope.check_in
            or query.check_out.isoformat() != scope.check_out
            or query.adults != scope.adults_per_room
            or query.rooms != scope.rooms
            or tuple(query.children_ages) != scope.children_ages
        ):
            return QuotesResponse(**base, status="unavailable",
                                  error=self.error("CONTEXT_MISMATCH"), warnings=[WARNING])
        candidate = next(
            (h for h in record.evidence.hotels if h.hotel_id == hotel_id), None
        )
        if candidate is None:
            return QuotesResponse(**base, status="unavailable",
                                  error=self.error("IDENTITY_MISMATCH"), warnings=[WARNING])
        if not self.capacity.acquire(blocking=False):
            return QuotesResponse(**base, status="unavailable",
                                  error=self.error("BUSY"), warnings=[WARNING])
        try:
            if not record.lock.acquire(blocking=False):
                return QuotesResponse(**base, status="unavailable",
                                      error=self.error("BUSY"), warnings=[WARNING])
            try:
                projector = TuniuProvider(record.client.call_tool, mode="offline_test")
                result = projector.detail(
                    record.evidence, candidate, expected_request_id=record.evidence.request_id,
                )
                nights = (query.check_out - query.check_in).days
                entries = []
                for i, item in enumerate(result.quotes):
                    identity = (str(hotel_id) + ":" + item.room_type_id + ":"
                                + item.plan_name + ":" + str(i))
                    quote_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                    # The reference layer is evaluated per quote, because the
                    # room form (shared bathroom, dormitory) differs by quote
                    # and a single response-level verdict would hide that.
                    gate = reference_gate(
                        "tuniu", nights=nights, rooms=query.rooms,
                        adults=query.adults,
                        remaining=item.remaining_count_observed,
                        snapshot_at=result.observed_at,
                        room_name=item.room_name,
                        accepts_dormitory=query.accepts_dormitory,
                    )
                    # P6: qualification and money are computed by the same pure
                    # engine the tests exercise, per quote, from the fields the
                    # source actually returned. Nothing is looked up, nothing
                    # is invented: a missing field becomes ``unknown``.
                    verdict = assess(QuoteInput(
                        source_id="tuniu",
                        room_name=item.room_name,
                        amount_fen=item.price_fen,
                        # Tuniu states neither the unit nor the tax treatment;
                        # both come out of the field table, so an upgraded
                        # status would flow through automatically.
                        price_unit=(
                            "per_room_night"
                            if field_status("tuniu", PRICE_UNIT) == "verified"
                            else "unstated"
                        ),
                        tax_included=(
                            True
                            if field_status("tuniu", PRICE_INCLUDES_TAX) == "verified"
                            else None
                        ),
                        remaining=item.remaining_count_observed,
                        nights=nights,
                        rooms=query.rooms,
                        adults=query.adults,
                        children=len(query.children_ages),
                        max_occupancy=item.max_occupancy,
                        cancellation_text=item.cancellation_text,
                        budget_fen=query.budget_fen,
                        accepts_dormitory=query.accepts_dormitory,
                    ))
                    entries.append((RoomQuote(
                        id=quote_id, room_name=item.room_name, plan_name=item.plan_name,
                        amount_fen=item.price_fen,
                        currency="CNY" if item.price_fen is not None else None,
                        price_basis="supplier_reference",
                        cancellation=Cancellation(
                            text=item.cancellation_text or "取消规则待核实",
                            can_cancel=None,
                        ),
                        meal=item.meal, max_occupancy=item.max_occupancy,
                        source_inventory_observed=item.remaining_count_observed,
                        unverified=[
                            "整间客房与房内独卫按国内默认推定，未取得来源字段",
                            "到店另付未核实（押金／必付费用无报价级字段）",
                            "入住组合按请求参数＋剩余房量＋观察时间推定，来源无回显",
                            "库存仅来源观察、未锁价",
                            "来源详情无预订链接，不提供跳转",
                        ],
                        reference=ReferenceGate.from_gate(gate),
                        qualification=Qualification.from_assessment(verdict),
                    ), verdict))
                entries.sort(key=lambda entry: (
                    entry[0].amount_fen is None,
                    entry[0].amount_fen if entry[0].amount_fen is not None else 0,
                    entry[0].id,
                ))
                quotes = [quote for quote, _ in entries]
                qualified_best = lowest_qualified(tuple(v for _, v in entries))
                qualified_count = sum(1 for _, v in entries if v.tier == "qualified")
                reference_count = sum(1 for _, v in entries if v.tier == "reference")
                excluded_count = sum(1 for _, v in entries if v.tier == "excluded")
                messages = [
                    WARNING,
                    "以上为参考价（非可订实付价）：整间独卫按国内默认推定，"
                    "入住组合按请求参数推定，到店另付未核实，且不提供来源跳转。",
                ]
                if quotes:
                    messages.append(
                        f"本轮 {len(quotes)} 条报价：{qualified_count} 条合格（含推定标注）、"
                        f"{reference_count} 条仅参考、{excluded_count} 条已排除；"
                        "「合格」只表示资格与预算未发现问题，不代表可订。"
                    )
                return QuotesResponse(
                    **{**base, "hotel_name": candidate.name, "queried_at": result.observed_at},
                    status=result.status, quotes=quotes,
                    # Booking layer is evaluated once per response: occupancy
                    # echo, deposit and a verbatim link are response-level
                    # facts, not per-quote. It is the only licence for an
                    # "可订／实付价" claim, and it is false for every source.
                    bookability=BookingGate.from_gate(
                        booking_gate("tuniu", nights=nights, rooms=query.rooms)
                    ),
                    qualified_lowest_id=(
                        quotes[qualified_best].id if qualified_best is not None else None
                    ),
                    warnings=messages,
                )
            except TuniuFailure as error:
                return QuotesResponse(
                    **{**base, "hotel_name": candidate.name},
                    status="unavailable", error=self.error(error.code), warnings=[WARNING],
                )
            finally:
                record.lock.release()
        finally:
            self.capacity.release()
