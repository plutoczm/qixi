"""Pure Tuniu hotel search/detail projector; no network or key access here.

The same bounded projection consumes explicit synthetic callbacks in offline tests
or a pinned two-tool TuniuMCPClient callback in TuniuQuoteService. The service,
not the projector, decides live/test provenance. No booking/order operation.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
from typing import Any, Callable, Mapping
from uuid import uuid4

from stay_agent.contracts import SearchInput

SOURCE = "tuniu"
SEARCH_TOOL = "tuniuHotelSearch"
DETAIL_TOOL = "tuniuHotelDetail"
_READ_TOOLS = frozenset((SEARCH_TOOL, DETAIL_TOOL))
# Official hotel API docs: tuniuHotelSearch and tuniuHotelCreateOrder responses
# carry `success`, while tuniuHotelDetail does not. Detail is accepted without
# the flag only when the documented detail markers are present.
_SUCCESS_OPTIONAL_TOOLS = frozenset((DETAIL_TOOL,))
_DETAIL_MARKERS = ("hotelId", "roomTypes")
# Documented business-object keys; their presence means the mapping is already
# the payload rather than an MCP wrapper holding a `content` text block.
_BUSINESS_MARKERS = frozenset(("hotels", "queryId", "hotelId", "roomTypes"))
_VALID_MODES = frozenset(("disabled", "offline_test"))
_PRICE = re.compile(r"^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?$")


class TuniuFailure(Exception):
    """A fixed error code; never expose raw response/exception/credentials."""

    def __init__(self, code: str):
        safe_codes = {
            "PROVIDER_DISABLED", "NOT_CONNECTED", "INVALID_ARGUMENTS",
            "TOOL_DENIED", "SOURCE_ERROR", "INVALID_RESPONSE",
            "IDENTITY_MISMATCH", "AMBIGUOUS_IDENTITY", "STALE_REQUEST",
            "CONTEXT_MISMATCH", "AUTHENTICATION_FAILED", "ACCESS_DENIED",
            "RATE_LIMITED", "TIMEOUT", "REQUEST_LIMIT", "REMOTE_UNAVAILABLE",
            "TOOL_NOT_AVAILABLE", "UNSUPPORTED_PROTOCOL", "SESSION_EXPIRED",
            "PAGE_UNAVAILABLE",
        }
        self.code = code if code in safe_codes else "SOURCE_ERROR"
        super().__init__(self.code)


@dataclass(frozen=True)
class StayScope:
    city: str
    check_in: str
    check_out: str
    adults_per_room: int
    rooms: int
    children_ages: tuple[int, ...]
    budget_fen: int | None
    limit: int
    poi_name: str | None = None

    @classmethod
    def from_query(cls, query: SearchInput, city: str) -> StayScope:
        if not isinstance(query, SearchInput):
            raise TuniuFailure("INVALID_ARGUMENTS")
        return cls(
            city=city, check_in=query.check_in.isoformat(),
            check_out=query.check_out.isoformat(), adults_per_room=query.adults,
            rooms=query.rooms, children_ages=tuple(query.children_ages),
            budget_fen=query.budget_fen, limit=query.limit,
            poi_name=query.poi_name,
        )


@dataclass(frozen=True)
class TuniuHotel:
    hotel_id: int
    name: str
    address: str
    city: str
    lowest_reference_fen: int | None
    star_label: str | None
    # The API publishes a commentScore; its 5-point scale has not been verified
    # for this account, so it cannot drive the product's rating threshold.
    supplier_score: float | None
    provider: str = SOURCE


@dataclass(frozen=True)
class TuniuSearchEvidence:
    request_id: str
    scope: StayScope
    observed_at: str
    hotels: tuple[TuniuHotel, ...]
    status: str
    limited: bool
    excluded_count: int
    # Opaque source pagination ID never enters HTTP DTO, SQLite or logs.
    query_id: str | None = dataclass_field(default=None, repr=False)
    current_page: int = 1
    total_pages: int = 1
    data_mode: str = "test"
    provider: str = SOURCE


@dataclass(frozen=True)
class TuniuRateReference:
    room_type_id: str
    room_name: str
    plan_name: str
    price_fen: int | None
    max_occupancy: int | None
    remaining_count_observed: int | None
    meal: str | None
    cancellation_text: str | None
    capacity_rejected: bool | None
    # Never represent observed stock, price or capacity as complete bookability.
    verified_bookable: bool = False
    full_stay_mandatory_fees_verified: bool = False
    private_bathroom_verified: bool = False
    occupancy_echo_verified: bool = False


@dataclass(frozen=True)
class TuniuDetailEvidence:
    request_id: str
    hotel: TuniuHotel
    scope: StayScope
    observed_at: str
    quotes: tuple[TuniuRateReference, ...]
    status: str
    # Official read-only detail does not establish a validated booking URL.
    booking_url: None = None
    occupancy_status: str = "unverified"
    booking_status: str = "unavailable"
    data_mode: str = "test"
    provider: str = SOURCE


def _text(value: Any, limit: int = 180) -> str | None:
    if type(value) is not str or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    value = value.strip()
    if not value or len(value) > limit:
        return None
    return value


def _positive_id(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 2**53 - 1 else None


def _query_id(value: Any) -> str | None:
    # Source-only ephemeral key. ASCII printable, capped to prevent abuse.
    if type(value) is not str or not 1 <= len(value) <= 256:
        return None
    return value if all(33 <= ord(ch) <= 126 for ch in value) else None


def _yuan_to_fen(value: Any) -> int | None:
    """Exact CNY reference conversion. Never infer currency or round money."""
    if type(value) not in (str, int, float, Decimal):
        return None
    try:
        raw = str(value)
    except (ValueError, OverflowError):
        return None
    if len(raw) > 32 or not _PRICE.fullmatch(raw):
        return None
    try:
        fen = Decimal(raw) * 100
    except InvalidOperation:
        return None
    return int(fen) if fen == fen.to_integral_value() and 0 <= fen <= 100000000 else None


def _score(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if 0 <= number <= 5:
        return number
    return None


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 100000 else None


def _source_call(tool: str, arguments: dict, callback: Callable | None) -> Mapping:
    if tool not in _READ_TOOLS:
        raise TuniuFailure("TOOL_DENIED")
    if callback is None:
        raise TuniuFailure("NOT_CONNECTED")
    try:
        # The callback is always explicitly injected. No fallback or retry.
        result = callback(tool, arguments)
    except TuniuFailure:
        raise
    except Exception as error:
        from .rollinggo_client import RollingGoError
        if isinstance(error, RollingGoError):
            code = error.code
            raise TuniuFailure(code if code in (
                "AUTHENTICATION_FAILED", "ACCESS_DENIED", "RATE_LIMITED", "TIMEOUT",
                "REQUEST_LIMIT", "TOOL_NOT_AVAILABLE", "UNSUPPORTED_PROTOCOL",
                "SESSION_EXPIRED",
            ) else "REMOTE_UNAVAILABLE") from None
        raise TuniuFailure("SOURCE_ERROR") from None
    if not isinstance(result, Mapping):
        raise TuniuFailure("INVALID_RESPONSE")
    # MCP `isError` marks a tool-level execution failure, not a malformed body.
    if result.get("isError") is True:
        raise TuniuFailure("SOURCE_ERROR")
    # Some wrappers add `success` next to `content`; the business payload is
    # still inside the text block. Only skip unwrapping when the top level
    # already carries documented business markers (hotels/queryId/hotelId/
    # roomTypes).
    if result.get("success") is False:
        raise TuniuFailure("SOURCE_ERROR")
    if isinstance(result.get("structuredContent"), Mapping):
        result = result["structuredContent"]
    elif "content" in result and not any(
        marker in result for marker in _BUSINESS_MARKERS
    ):
        content = result.get("content")
        if not isinstance(content, list) or len(content) > 8:
            raise TuniuFailure("INVALID_RESPONSE")
        texts = [item.get("text") for item in content
                 if isinstance(item, Mapping) and item.get("type") == "text"]
        if len(texts) != 1 or type(texts[0]) is not str or len(texts[0]) > 600000:
            raise TuniuFailure("INVALID_RESPONSE")
        try:
            result = json.loads(texts[0])
        except (ValueError, TypeError):
            raise TuniuFailure("INVALID_RESPONSE") from None
        if not isinstance(result, dict):
            raise TuniuFailure("INVALID_RESPONSE")
    # Tool-level errors arrive as HTTP 200 with `{"error": "..."}` in the text
    # block (possibly with isError). Never retain or surface that text.
    if type(result.get("error")) is str and result["error"].strip() \
            and result.get("success") is not True:
        raise TuniuFailure("SOURCE_ERROR")
    if result.get("success") is False:
        raise TuniuFailure("SOURCE_ERROR")
    if result.get("success") is not True:
        if tool not in _SUCCESS_OPTIONAL_TOOLS or not any(
            marker in result for marker in _DETAIL_MARKERS
        ):
            raise TuniuFailure("INVALID_RESPONSE")
    return result


def _parse_search_rows(raw: Mapping, scope: StayScope) -> tuple[tuple[TuniuHotel, ...], int]:
    records = raw.get("hotels")
    if not isinstance(records, list) or len(records) > 200:
        raise TuniuFailure("INVALID_RESPONSE")
    seen: dict[int, TuniuHotel] = {}
    conflicted: set[int] = set()
    excluded = 0
    for item in records:
        if not isinstance(item, Mapping):
            excluded += 1
            continue
        hotel_id = _positive_id(item.get("hotelId"))
        name = _text(item.get("hotelName"))
        address = _text(item.get("address"))
        origin_city = _text(item.get("cityName"), 40)
        if hotel_id is None or name is None or address is None or origin_city != scope.city:
            excluded += 1
            continue
        hotel = TuniuHotel(
            hotel_id=hotel_id, name=name, address=address, city=origin_city,
            lowest_reference_fen=_yuan_to_fen(item.get("lowestPrice")),
            star_label=_text(item.get("starName"), 60),
            supplier_score=_score(item.get("commentScore")),
        )
        if hotel_id in seen:
            if seen[hotel_id] != hotel:
                conflicted.add(hotel_id)
            continue
        seen[hotel_id] = hotel
    hotels = tuple(hotel for ident, hotel in seen.items() if ident not in conflicted)
    return hotels, excluded + len(conflicted)


def _total_pages(raw: Mapping) -> int:
    value = raw.get("totalPageNum", 1)
    if type(value) is not int or not 1 <= value <= 10000:
        raise TuniuFailure("INVALID_RESPONSE")
    return value


class TuniuProvider:
    """Bounded response projector using only an explicitly injected callback.

    Mode defaults to disabled regardless of credentials. The legacy mode name
    'offline_test' means callback injection; production live/test labeling is
    owned by TuniuQuoteService. There is no built-in network or order tool.
    """

    def __init__(
        self,
        call_tool: Callable[[str, dict], Mapping] | None = None,
        *,
        mode: str | None = None,
    ):
        selected = os.environ.get("XUNQI_TUNIU_MODE", "disabled") if mode is None else mode
        if selected not in _VALID_MODES:
            raise TuniuFailure("PROVIDER_DISABLED")
        self.mode = selected
        self._callback = call_tool

    def _call(self, name: str, args: dict) -> Mapping:
        if self.mode != "offline_test":
            raise TuniuFailure("PROVIDER_DISABLED")
        return _source_call(name, args, self._callback)

    def search(
        self, query: SearchInput, *, city: str, keyword: str | None = None,
    ) -> TuniuSearchEvidence:
        if not (city := _text(city, 40)):
            raise TuniuFailure("INVALID_ARGUMENTS")
        if keyword is not None and (keyword := _text(keyword, 120)) is None:
            raise TuniuFailure("INVALID_ARGUMENTS")
        scope = StayScope.from_query(query, city)
        args = {
            "cityName": city, "checkIn": scope.check_in, "checkOut": scope.check_out,
            "adultNum": scope.adults_per_room, "childNum": len(scope.children_ages),
        }
        if scope.children_ages:
            # Official docs: childAges count must match childNum.
            args["childAges"] = list(scope.children_ages)
        if keyword:
            args["keyword"] = keyword
        if scope.poi_name:
            # Official optional anchor: hotels around this POI. The source
            # radius and result ordering remain unverified until a real
            # response is checked.
            args["poiName"] = scope.poi_name
        if scope.budget_fen is not None:
            args["prices"] = "0-" + str(Decimal(scope.budget_fen) / 100)
        raw = self._call(SEARCH_TOOL, args)
        if ("currentPageNum" in raw and
            (type(raw["currentPageNum"]) is not int or raw["currentPageNum"] != 1)):
            raise TuniuFailure("INVALID_RESPONSE")
        all_hotels, excluded = _parse_search_rows(raw, scope)
        total_pages = _total_pages(raw)
        query_id = _query_id(raw.get("queryId"))
        limited = len(all_hotels) > scope.limit or total_pages > 1
        hotels = all_hotels[:scope.limit]
        status = "partial" if excluded or limited else "complete"
        return TuniuSearchEvidence(
            request_id=str(uuid4()), scope=scope,
            observed_at=datetime.now(timezone.utc).isoformat(),
            hotels=hotels, status=status, limited=limited,
            excluded_count=excluded, query_id=query_id,
            current_page=1, total_pages=total_pages,
        )

    def page(self, search: TuniuSearchEvidence, *, page_num: int) -> TuniuSearchEvidence:
        """Fetch exactly one explicitly requested page using only server-side source queryId."""
        if (not isinstance(search, TuniuSearchEvidence)
                or search.provider != SOURCE
                or type(page_num) is not int
                or page_num != search.current_page + 1
                or not 2 <= page_num <= min(search.total_pages, 3)
                or _query_id(search.query_id) is None):
            raise TuniuFailure("INVALID_ARGUMENTS")
        raw = self._call(SEARCH_TOOL, {
            "queryId": search.query_id,
            "pageNum": page_num,
        })
        if (type(raw.get("currentPageNum")) is not int
                or raw["currentPageNum"] != page_num
                or raw.get("queryId") != search.query_id):
            raise TuniuFailure("CONTEXT_MISMATCH")
        page_total = _total_pages(raw)
        if page_total < page_num:
            raise TuniuFailure("CONTEXT_MISMATCH")
        if ("cityInfo" in raw and isinstance(raw["cityInfo"], Mapping)
                and "cityName" in raw["cityInfo"]
                and _text(raw["cityInfo"].get("cityName"), 40) != search.scope.city):
            raise TuniuFailure("CONTEXT_MISMATCH")
        hotels, excluded = _parse_search_rows(raw, search.scope)
        return TuniuSearchEvidence(
            request_id=search.request_id, scope=search.scope,
            observed_at=datetime.now(timezone.utc).isoformat(),
            hotels=hotels, excluded_count=excluded,
            status="partial" if excluded or page_num < page_total else "complete",
            limited=page_num < page_total,
            query_id=search.query_id, current_page=page_num,
            total_pages=page_total,
        )

    def detail(
        self, search: TuniuSearchEvidence, candidate: TuniuHotel, *,
        expected_request_id: str,
    ) -> TuniuDetailEvidence:
        if (
            not isinstance(search, TuniuSearchEvidence)
            or not isinstance(candidate, TuniuHotel)
            or search.provider != SOURCE or candidate.provider != SOURCE
            or search.request_id != expected_request_id
        ):
            raise TuniuFailure("STALE_REQUEST")
        # Require the exact member of this in-memory snapshot, not a copied
        # value with a matching hotelId from an older request or other source.
        if not any(candidate is member for member in search.hotels):
            raise TuniuFailure("IDENTITY_MISMATCH")
        if sum(
            1 for hotel in search.hotels
            if (hotel.name, hotel.address, hotel.city) ==
               (candidate.name, candidate.address, candidate.city)
        ) != 1:
            raise TuniuFailure("AMBIGUOUS_IDENTITY")
        scope = search.scope
        detail_args = {
            "hotelId": candidate.hotel_id,
            "checkIn": scope.check_in, "checkOut": scope.check_out,
            "roomNum": scope.rooms, "adultNum": scope.adults_per_room,
        }
        if scope.children_ages:
            # Keep the detail request on the same occupancy as the search.
            detail_args["childNum"] = len(scope.children_ages)
            detail_args["childAges"] = list(scope.children_ages)
        raw = self._call(DETAIL_TOOL, detail_args)
        if (
            _positive_id(raw.get("hotelId")) != candidate.hotel_id
            or _text(raw.get("hotelName")) != candidate.name
        ):
            raise TuniuFailure("IDENTITY_MISMATCH")
        # Official docs list `address` in the detail response, but the r51c
        # live response omitted it. Absence is tolerated only with an exact
        # same-snapshot ID + name match; a present but different address still
        # fails closed.
        echoed_address = _text(raw.get("address"))
        if echoed_address is not None and echoed_address != candidate.address:
            raise TuniuFailure("IDENTITY_MISMATCH")
        if "cityName" in raw and _text(raw.get("cityName"), 40) != candidate.city:
            raise TuniuFailure("IDENTITY_MISMATCH")
        for field, expected in (
            ("checkIn", scope.check_in), ("checkOut", scope.check_out),
            ("adultNum", scope.adults_per_room), ("roomNum", scope.rooms),
        ):
            if field in raw and (type(raw[field]) is not type(expected)
                                 or raw[field] != expected):
                raise TuniuFailure("CONTEXT_MISMATCH")
        rooms = raw.get("roomTypes")
        if not isinstance(rooms, list) or len(rooms) > 80:
            raise TuniuFailure("INVALID_RESPONSE")
        references: list[TuniuRateReference] = []
        for room in rooms:
            if not isinstance(room, Mapping):
                continue
            room_id = _text(room.get("roomTypeId"), 120)
            room_name = _text(room.get("roomTypeName"))
            plans = room.get("ratePlans")
            if not (room_id and room_name and isinstance(plans, list)):
                continue
            if len(plans) > 80:
                raise TuniuFailure("INVALID_RESPONSE")
            occupancy = _count(room.get("maxOccupancy"))
            if occupancy == 0:
                occupancy = None
            for plan in plans:
                if not isinstance(plan, Mapping):
                    continue
                # Important: NEVER access, retain or return 'preBookParam'.
                references.append(TuniuRateReference(
                    room_type_id=room_id, room_name=room_name,
                    plan_name=_text(plan.get("ratePlanName")) or "来源报价方案",
                    price_fen=_yuan_to_fen(plan.get("rmbPrices")),
                    max_occupancy=occupancy,
                    remaining_count_observed=_count(plan.get("count")),
                    meal=_text(plan.get("mealText")),
                    cancellation_text=_text(plan.get("cancelDesc")),
                    capacity_rejected=(
                        scope.adults_per_room > occupancy
                        if occupancy is not None and scope.rooms == 1
                           and not scope.children_ages else None
                    ),
                ))
        if len(references) > 200:
            raise TuniuFailure("INVALID_RESPONSE")
        return TuniuDetailEvidence(
            request_id=search.request_id, hotel=candidate, scope=scope,
            observed_at=datetime.now(timezone.utc).isoformat(),
            quotes=tuple(references),
            status="complete" if references else "partial",
        )
