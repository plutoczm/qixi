"""Real source adapter with an injected client factory for offline tests."""

from datetime import datetime, timezone
from contextlib import nullcontext
from decimal import Decimal
import threading
from uuid import uuid4

from stay_agent.providers.data.rollinggo import ConfigurationError, business_data, load_api_key
from stay_agent.providers.data.rollinggo_client import RollingGoClient, RollingGoError

from .contracts import ApiError, BookingGate, Coverage, QuotesResponse, SearchResponse
from .city_observation import CityObservationRegistry
from .pricing import lowest_qualified
from .qualification import booking_gate
from .projection import hotel_summary, room_quote, text

from .verification import booking_context_check, occupancy_context_check

BASE_WARNING = "展示报价未锁价；完整费用和库存以道旅预订页确认为准。"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class SourceFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class QuoteService:
    data_mode = "live"

    def __init__(self, client_factory=None):
        self.client_factory = client_factory or self._client
        self.capacity = threading.BoundedSemaphore(2)
        self.city_observations = CityObservationRegistry()

    @staticmethod
    def _client():
        return RollingGoClient(load_api_key(), timeout=20, total_timeout=60, max_requests=8)

    @staticmethod
    def credential_configured():
        try:
            load_api_key()
            return True
        except ConfigurationError:
            return False

    def _call(self, name, arguments, *, client=None, source_context=None):
        if not self.capacity.acquire(blocking=False):
            raise SourceFailure("BUSY")
        try:
            client = client if client is not None else self.client_factory()
            window = client.request_window(source_context) if source_context else nullcontext()
            with window:
                result = client.call_tool(name, arguments)
            data = business_data(client.redact(result))
            if data.get("success") is False or result.get("isError") is True:
                raise SourceFailure("PROVIDER_BUSINESS_ERROR")
            if data.get("success") is not True:
                raise SourceFailure("INVALID_DATA")
            return data
        except ConfigurationError:
            raise SourceFailure("CONFIGURATION_MISSING") from None
        except RollingGoError as error:
            raise SourceFailure(error.code) from None
        finally:
            self.capacity.release()

    @staticmethod
    def error(code):
        messages = {
            "CONFIGURATION_MISSING": "房价来源尚未配置，请先完成本机配置。",
            "AUTHENTICATION_FAILED": "房价来源认证失败，请检查本机配置。",
            "ACCESS_DENIED": "房价来源暂未授权此查询。",
            "TIMEOUT": "查询超时，请稍后重试。",
            "RATE_LIMITED": "查询较频繁，请稍后再试。",
            "BUSY": "当前查询较多，请稍后重试。",
            "CONTEXT_MISMATCH": "返回报价的入住条件不一致，请重新搜索。",
            "INVALID_DATA": "暂未取得可核实的房价数据，请重试。",
        }
        return ApiError(code=code, message=messages.get(code, "暂时无法获取报价，请稍后重试。"))

    def search(self, query, store, *, client=None, origin_hint=None, source_context=None):
        request_id = str(uuid4())
        base = dict(
            request_id=request_id,
            context=query,
            queried_at=now_iso(),
            data_mode=self.data_mode,
            coverage=Coverage(returned_count=0, requested_limit=query.limit, limited=False),
        )
        scope_place = query.place + (f" {query.country_region}" if query.country_region else " 中国" if query.place_type == "城市" else "")
        origin_query = f"{scope_place}住宿查询，{query.check_in}入住，{query.check_out}退房"
        # The map location is a search hint, not supplier location proof.
        # Only the map-first path supplies it; ordinary search is unchanged.
        if origin_hint is not None:
            if not isinstance(origin_hint, str) or not 0 < len(origin_hint) <= 550:
                raise ValueError("invalid bounded map context hint")
            origin_query = f"{origin_query}；{origin_hint}；须以返回的酒店完整名称与地址核验同店"
        arguments = {
            "originQuery": origin_query,
            "place": scope_place,
            "placeType": query.place_type,
            "checkInParam": {
                "checkInDate": query.check_in.isoformat(),
                "stayNights": (query.check_out - query.check_in).days,
                "adultCount": query.adults,
            },
            "size": query.limit,
        }
        if query.poi_name:
            # Text is a source search hint only. Never send map coordinates or
            # claim that the supplier honored the requested radius.
            arguments["place"] = f"{query.poi_name} {query.place}"
            arguments["placeType"] = "详细地址"
            arguments["originQuery"] += f"；仅查{query.poi_name}附近住宿，不回退全城"
            arguments["filterOptions"] = {"distanceInMeter": 2000}
        if query.budget_fen is not None:
            arguments["hotelTags"] = {"maxPricePerNight": float(Decimal(query.budget_fen) / 100)}
        try:
            data = self._call("searchHotels", arguments, client=client, source_context=source_context)
            raw = data.get("hotelInformationList")
            if not isinstance(raw, list):
                raise SourceFailure("INVALID_DATA")
            hotels, seen = [], set()
            for item in raw[: query.limit]:
                hotel = hotel_summary(item)
                if hotel and hotel.id not in seen:
                    seen.add(hotel.id)
                    hotels.append(hotel)
            if raw and not hotels:
                raise SourceFailure("INVALID_DATA")
            store.save(
                request_id,
                query.model_dump(mode="json"),
                [hotel.id for hotel in hotels],
                hotel_metadata=[
                    hotel.model_dump(include={"id", "name", "address"}) for hotel in hotels
                ],
                supplier_source="rollinggo",
            )
            base["queried_at"] = now_iso()
            if query.poi_name and query.place_type == "城市" and hasattr(store, "ensure_live"):
                self.city_observations.observe(
                    request_id, query.model_dump(mode="json"), raw,
                    [hotel.model_dump(include={"id", "name", "address"}) for hotel in hotels],
                    store, base["queried_at"],
                )
            base["coverage"] = Coverage(
                returned_count=len(hotels),
                requested_limit=query.limit,
                limited=len(raw) >= query.limit,
            )
            warnings = [
                BASE_WARNING,
                "搜索参考价口径未完全核实；房型报价会按完整人数与房间数重新查询。",
            ]
            if query.poi_name:
                warnings.append("目的地与2公里半径仅作为来源检索提示；来源可能忽略提示或返回较远酒店，须另外核对地图同店和实际步行路线。")
            partial = len(hotels) < min(len(raw), query.limit)
            if partial:
                warnings.append("部分来源记录无法核实，已省略。")
            return SearchResponse(
                **base,
                status="partial" if partial else "complete",
                hotels=hotels,
                warnings=warnings,
            )
        except SourceFailure as error:
            base["queried_at"] = now_iso()
            return SearchResponse(
                **base, status="unavailable", error=self.error(error.code), warnings=[BASE_WARNING]
            )

    def quotes(self, request_id, hotel_id, query):
        base = dict(
            request_id=request_id,
            hotel_id=hotel_id,
            hotel_name="住宿",
            context=query,
            queried_at=now_iso(),
            data_mode=self.data_mode,
        )
        arguments = {
            "hotelId": hotel_id,
            "dateParam": {
                "checkInDate": query.check_in.isoformat(),
                "checkOutDate": query.check_out.isoformat(),
            },
            "occupancyParam": {
                "adultCount": query.adults,
                "childCount": len(query.children_ages),
                "childAgeDetails": query.children_ages,
                "roomCount": query.rooms,
            },
        }
        try:
            data = self._call("getHotelDetail", arguments)
            if (
                data.get("hotelId") != hotel_id
                or data.get("checkIn") != query.check_in.isoformat()
                or data.get("checkOut") != query.check_out.isoformat()
            ):
                raise SourceFailure("CONTEXT_MISMATCH")
            base["occupancy_status"], base["occupancy_context_issues"] = occupancy_context_check(
                data, query
            )
            if base["occupancy_status"] == "mismatch":
                raise SourceFailure("CONTEXT_MISMATCH")
            booking_url, booking_status, booking_issues = booking_context_check(
                data.get("bookingUrl"), hotel_id, query
            )
            raw = data.get("roomRatePlans")
            if not isinstance(raw, list):
                raise SourceFailure("INVALID_DATA")
            quotes = [
                q
                for i, item in enumerate(raw[:500])
                if (
                    q := room_quote(
                        item,
                        i,
                        nights=(query.check_out - query.check_in).days,
                        rooms=query.rooms,
                        adults=query.adults,
                        snapshot_at=base["queried_at"],
                        children=len(query.children_ages),
                        budget_fen=query.budget_fen,
                        accepts_dormitory=query.accepts_dormitory,
                    )
                )
            ]
            if raw and not quotes:
                raise SourceFailure("INVALID_DATA")
            quotes.sort(key=lambda q: (q.amount_fen is None, q.amount_fen or 0, q.id))
            base["hotel_name"] = text(data.get("name"), 200) or "住宿名称待核实"
            base["queried_at"] = now_iso()
            warnings = [
                BASE_WARNING,
                "房型金额为供应商展示均价，未核实整住期总价、税费及足量库存。",
            ]
            verdicts = tuple(quote.qualification for quote in quotes)
            qualified_best = lowest_qualified(verdicts)
            if quotes:
                qualified_count = sum(1 for v in verdicts if v.tier == "qualified")
                reference_count = sum(1 for v in verdicts if v.tier == "reference")
                excluded_count = sum(1 for v in verdicts if v.tier == "excluded")
                warnings.append(
                    f"本轮 {len(quotes)} 条报价：{qualified_count} 条合格（含推定标注）、"
                    f"{reference_count} 条仅参考、{excluded_count} 条已排除；"
                    "「合格」只表示资格与预算未发现问题，不代表可订。"
                )
                if qualified_best is None and reference_count:
                    warnings.append("本轮没有合格报价：来源协议缺剩余房量，相关报价只作参考。")
            if base["occupancy_status"] == "unverified":
                warnings.append("请求已携带人数和房间数，但来源未完整回显，入住组合仍待核实。")
            if booking_status == "mismatch":
                warnings.append("来源预订链接与本次入住条件不一致，已停用跳转。")
            elif booking_status == "unverified":
                warnings.append("来源预订链接未能核实完整入住条件，暂不提供跳转。")
            partial = len(raw) > 500 or len(quotes) < len(raw)
            if partial:
                warnings.append("本次仅展示可核实的前500个报价记录。")
            return QuotesResponse(
                **base,
                status="partial" if partial else "complete",
                quotes=quotes,
                # Booking layer: response-level, evaluated by the same rule
                # module the reference layer uses. No source passes it, so the
                # page may never call this an all-fees, bookable price.
                bookability=BookingGate.from_gate(
                    booking_gate(
                        "rollinggo",
                        nights=(query.check_out - query.check_in).days,
                        rooms=query.rooms,
                        link_matched=booking_status == "matched",
                        occupancy_echo_matched=base["occupancy_status"] == "matched",
                    )
                ),
                qualified_lowest_id=(
                    quotes[qualified_best].id if qualified_best is not None else None
                ),
                booking_url=booking_url,
                booking_status=booking_status,
                booking_context_issues=booking_issues,
                warnings=warnings,
            )
        except SourceFailure as error:
            base["queried_at"] = now_iso()
            return QuotesResponse(
                **base, status="unavailable", error=self.error(error.code), warnings=[BASE_WARNING]
            )
