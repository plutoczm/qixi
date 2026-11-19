"""Bounded multi-branch candidate recall; no proximity or qualification is inferred."""

from dataclasses import dataclass
import copy
import threading
import time
from uuid import uuid4

from stay_agent.providers.data.rollinggo import load_api_key
from stay_agent.providers.data.rollinggo_client import RollingGoClient
from stay_agent.providers.data.travel_clients import MapProbe, SecureHTTP, load_key, load_tencent_secret
from .contracts import Coverage, RecallBranch, RecallCoverage, RecallResponse, ApiError
from .service import QuoteService, now_iso
from .travel import TravelService
from .map_recall import map_hotel_recall
from .named_hotel_recall import named_hotel_recall
from .recall_continuation import RecallContinuation
from .restaurant_roles import classify_restaurant


class RecallError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class Operation:
    cancelled: threading.Event
    expires: float
    started: bool = False


class RecallService:
    def __init__(
        self,
        quote_service=None,
        map_factory=None,
        quote_factory=None,
        clock=time.monotonic,
        data_mode="live",
    ):
        self.quotes = quote_service or QuoteService()
        self.map_factory = map_factory or self._map
        self.quote_factory = quote_factory or self._quote
        self.clock = clock
        self.data_mode = data_mode
        self.operations = {}
        self.associations = {}
        self.lock = threading.Lock()
        self.capacity = threading.BoundedSemaphore(2)
        self.continuation = RecallContinuation(self)

    @staticmethod
    def _map(provider, deadline):
        key = load_key(provider)
        http = SecureHTTP(
            provider,
            key,
            timeout=15,
            total_timeout=60,
            max_requests=7,
            secret=load_tencent_secret() if provider == "tencent" else None,
        )
        http.deadline = deadline
        return MapProbe(provider, key, http=http)

    @staticmethod
    def _quote(deadline):
        client = RollingGoClient(load_api_key(), timeout=20, total_timeout=60, max_requests=8)
        client.limit_deadline(deadline)
        return client

    def _operation(self, operation_id, start=False):
        with self.lock:
            self.operations = {
                key: op for key, op in self.operations.items() if op.expires > self.clock()
            }
            operation = self.operations.get(operation_id)
            if operation is None:
                if len(self.operations) >= 128:
                    raise RecallError("RECALL_OPERATION_LIMIT")
                operation = Operation(threading.Event(), self.clock() + 1800)
                self.operations[operation_id] = operation
            if start:
                if operation.started:
                    raise RecallError("RECALL_OPERATION_CONFLICT")
                operation.started = True
            else:
                operation.cancelled.set()
            return operation

    def association(self, request_id):
        with self.lock:
            value = self.associations.get(request_id)
            if value is None:
                raise RecallError("RECALL_ASSOCIATION_NOT_FOUND")
            if value["expires"] <= self.clock():
                self.associations.pop(request_id, None)
                raise RecallError("RECALL_ASSOCIATION_EXPIRED")
            return copy.deepcopy(value)

    def close(self):
        with self.lock:
            for operation in self.operations.values():
                operation.cancelled.set()

    def cancel(self, operation_id):
        self._operation(operation_id)
        return {"accepted": True}

    def search(self, request, sessions):
        if not self.capacity.acquire(blocking=False):
            raise RecallError("RECALL_BUSY")
        try:
            operation = self._operation(request.operation_id, start=True)
            return self._search(request, sessions, operation.cancelled)
        finally:
            self.capacity.release()

    def _search(self, request, sessions, cancelled):
        deadline = self.clock() + 60
        query = request.query
        stats = RecallCoverage(
            discovery_stop_reason="page_limit", branch_limit=request.branch_limit
        )
        branches = []
        source_ids = {}
        branch_points = {}
        map_client = None
        target_refs = {}
        extra_conflicts = set()
        map_progress = None
        error = None

        def interrupted():
            return (
                "cancelled"
                if cancelled.is_set()
                else "deadline"
                if self.clock() >= deadline
                else None
            )

        if interrupted():
            stats.discovery_stop_reason = interrupted()
        else:
            seen = set()
            try:
                map_client = self.map_factory(request.provider, deadline)
                if hasattr(map_client, "http"):
                    map_client.http.cancelled = cancelled
                for page in range(1, 3):
                    if interrupted():
                        stats.discovery_stop_reason = interrupted()
                        break
                    stats.pages_requested += 1
                    points = map_client.search(
                        request.restaurant_keyword, city=query.place, page=page, page_size=10
                    )
                    if interrupted():
                        stats.discovery_stop_reason = interrupted()
                        break
                    meta = map_client.last_search
                    raw_count = meta["raw_count"]
                    stats.raw_places += raw_count
                    stats.invalid_places += max(0, raw_count - len(points))
                    stats.total_hint = meta.get("total_hint")
                    progress = 0
                    for point in points:
                        if point.poi_id in seen:
                            continue
                        seen.add(point.poi_id)
                        progress += 1
                        metadata = map_client.metadata.get(point.poi_id, {})
                        address = metadata.get("address")
                        address = (
                            address
                            if isinstance(address, str) and 0 < len(address) <= 300
                            else None
                        )
                        city = metadata.get("city")
                        city = city if isinstance(city, str) and 0 < len(city) <= 50 else None
                        role = classify_restaurant(request.provider, point.title, metadata)
                        branch = RecallBranch(
                            role=role.role,
                            role_reason=role.reason,
                            source_category=role.source_category,
                            id=uuid4().hex,
                            name=point.title,
                            address=address,
                            city=city,
                            reason="branch_budget",
                        )
                        location = (
                            f"{query.place} {address} {point.title}"
                            if isinstance(address, str) and address.strip()
                            else None
                        )
                        if (
                            city not in (query.place, query.place.removesuffix("市") + "市")
                            or not location
                            or (request.strategy == "branch_place" and len(location) > 120)
                        ):
                            branch.reason = "location_unverified"
                        elif role.role != "restaurant_body":
                            branch.reason = "restaurant_role_" + role.role
                        else:
                            branch.query_place = location
                        branches.append(branch)
                        source_ids[branch.id] = point.poi_id
                        branch_points[branch.id] = point
                    if raw_count == 0 or raw_count < 10:
                        stats.discovery_stop_reason = "exhausted"
                        stats.discovery_complete = stats.invalid_places == 0
                        break
                    if progress == 0:
                        stats.discovery_stop_reason = "repeated_page" if points else "invalid_page"
                        break
            except Exception as failure:
                error = TravelService.error(failure)
                stats.discovery_stop_reason = "source_failure"
        stats.unique_places = len(branches)
        stats.role_restaurant_bodies = sum(b.role == "restaurant_body" for b in branches)
        stats.role_auxiliary = sum(b.role == "auxiliary" for b in branches)
        stats.role_unknown = sum(b.role == "unknown" for b in branches)
        results = []
        quote_client = None
        quote_stop = None
        if request.strategy == "named_hotel":
            results, error, target_refs, extra_conflicts, map_progress = named_hotel_recall(
                self, request, branches, branch_points, map_client,
                stats, deadline, interrupted, cancelled,
            )
        elif request.strategy == "map_hotels":
            results, error, target_refs, extra_conflicts, map_progress = map_hotel_recall(
                self,
                request,
                branches,
                branch_points,
                map_client,
                stats,
                deadline,
                interrupted,
                error,
            )
        else:
            for branch in branches:
                stop = interrupted()
                if stop or quote_stop:
                    if branch.query_place:
                        branch.reason = stop or quote_stop
                    continue
                if not branch.query_place or stats.queried_branches >= request.branch_limit:
                    continue
                stats.queried_branches += 1
                branch.reason = None
                try:
                    if quote_client is None:
                        quote_client = self.quote_factory(deadline)
                        quote_client.cancelled = cancelled
                    branch_query = query.model_copy(
                        update={"place": branch.query_place, "place_type": "详细地址"}
                    )
                    response = self.quotes.search(branch_query, sessions, client=quote_client)
                    branch.status = response.status
                    branch.queried_at = response.queried_at
                    branch.returned_count = response.coverage.returned_count
                    branch.limited = response.coverage.limited
                    branch.error = response.error
                    if response.status != "unavailable" and not interrupted():
                        results.append((branch, response.hotels))
                    elif response.error:
                        error = response.error
                        if response.error.code in {
                            "AUTHENTICATION_FAILED",
                            "ACCESS_DENIED",
                            "RATE_LIMITED",
                            "REQUEST_LIMIT",
                        }:
                            quote_stop = "quote_source_blocked"
                except Exception as failure:
                    branch.status = "unavailable"
                    branch.error = TravelService.error(failure)
                    error = branch.error
                    quote_stop = "quote_source_blocked"
        if quote_client is not None:
            stats.quote_http_requests = quote_client.request_count
        if map_client is not None and hasattr(map_client, "http"):
            stats.map_http_requests = map_client.http.request_count
        canonical = {}
        conflicts = set(extra_conflicts)
        for _, rows in results:
            for hotel in rows:
                previous = canonical.setdefault(hotel.id, hotel)
                if (previous.name, previous.address) != (hotel.name, hotel.address):
                    conflicts.add(hotel.id)
        stats.identity_conflicts = len(conflicts)
        union = {}
        for index in range(max((len(hotels) for _, hotels in results), default=0)):
            for branch, hotels in results:
                if index >= len(hotels):
                    continue
                hotel = hotels[index]
                if hotel.id in conflicts:
                    continue
                if hotel.id in union:
                    stats.duplicate_hotels += 1
                else:
                    union[hotel.id] = canonical[hotel.id]
                if hotel.id not in branch.hotel_ids:
                    branch.hotel_ids.append(hotel.id)
        branch_sets = set()
        for branch in branches:
            if branch.hotel_ids:
                signature = tuple(sorted(branch.hotel_ids))
                if signature in branch_sets:
                    stats.identical_branch_sets += 1
                branch_sets.add(signature)
        stop = interrupted()
        if stop == "cancelled":
            union.clear()
            error = ApiError(code="CANCELLED", message="已请求停止；没有提交本次候选结果。")
        elif stop == "deadline":
            error = ApiError(code="TIMEOUT", message="已达到查询截止，保留截止前取得的候选。")
        request_id = str(uuid4())
        hotels = list(union.values())
        successful = bool(results)
        registered = False
        with self.lock:
            if cancelled.is_set():
                stop = "cancelled"
                union.clear()
                hotels = []
                error = ApiError(code="CANCELLED", message="已请求停止；没有提交本次候选结果。")
            elif successful or (not any(b.query_place for b in branches) and stats.discovery_complete):
                sessions.save(
                    request_id,
                    query.model_dump(mode="json"),
                    [h.id for h in hotels],
                    hotel_metadata=[
                        h.model_dump(include={"id", "name", "address"}) for h in hotels
                    ],
                    supplier_source="rollinggo",
                )
                self.associations = {
                    key: value
                    for key, value in self.associations.items()
                    if value["expires"] > self.clock()
                }
                if hotels and len(self.associations) < 64:
                    self.associations[request_id] = {
                        "expires": self.clock() + 1800,
                        "provider": request.provider,
                        "query": query.model_dump(mode="json"),
                        "hotels": {
                            h.id: h.model_dump(include={"id", "name", "address"}) for h in hotels
                        },
                        "branches": [
                            {
                                **b.model_dump(
                                    include={"id", "name", "address", "city", "hotel_ids", "role"}
                                ),
                                "source_id": source_ids[b.id],
                                "hotel_targets": target_refs.get(b.id, {}),
                            }
                            for b in branches
                            if b.hotel_ids
                        ],
                    }
                    registered = True
        incomplete = (
            not stats.discovery_complete
            or any(b.status in {"skipped", "partial", "unavailable"} for b in branches)
            or stats.identity_conflicts > 0
            or stop is not None
        )
        status = (
            ("partial" if incomplete else "complete")
            if successful or (not any(b.query_place for b in branches) and stats.discovery_complete)
            else "unavailable"
        )
        if stop == "cancelled":
            status = "unavailable"
        warnings = [
            "仅本次地图可见门店与道旅候选范围，不代表全城完整覆盖。",
            "仅来源类别与名称共同支持的餐厅本体参与自动查询；未知及附属点已阻止，地点检索不证明实际步行或夜间返程。",
            "参考价采用首次召回该住宿的查询值，不是最低合格价；未知资格与完整费用不放行。",
        ]
        if request.strategy == "named_hotel":
            warnings[1] = (
                "用户点名酒店先按完整名称、地址、来源类别和城市核对地图身份，再查道旅一次；"
                "道旅没有独立城市字段时仍需人工逐项核实，不自动视为可订。"
            )
            warnings.append(
                "点名模式不依赖周边前10条，也不自动查其他酒店、分页或续查；"
                "未得到唯一完整同店时不会向道旅借用其他酒店的价格。"
            )
        if request.strategy == "map_hotels":
            warnings[1] = (
                "仅已识别餐厅本体执行酒店反查；酒店候选仍须同店核对，实际步行/夜间未验证。"
            )
            warnings.append(
                "首次及每次显式续查最多5次不同酒店名称；已发名称不自动重试，未查/空/排除均显示。"
            )
            warnings.append("地图周边覆盖高德2000米/腾讯1000米，仅为候选范围，不是额外距离硬条件。")
        if stats.identical_branch_sets:
            warnings.append(
                "多个门店地点关联相同酒店集合；可能为地图周边范围重合，门店用途和实际路线仍须分别核对。"
                if request.strategy == "map_hotels"
                else "不同门店返回完全相同的住宿集合，位置响应能力待核实；不能据此认定都在门店周边。"
            )
        if stats.identity_conflicts:
            warnings.append("同一道旅编号出现身份冲突，已排除该住宿候选，请核对来源身份。")
        response = RecallResponse(
            request_id=request_id,
            strategy=request.strategy,
            retained_total=len(hotels),
            attempted_hotel_names=sorted(map_progress["attempted_names"]) if map_progress else [],
            plan_queried_at=now_iso() if request.strategy in ("map_hotels", "named_hotel") else None,
            hotel_observations=[
                {"hotel_id": h.id, "queried_at": map_progress["observations"].get(h.id, now_iso())}
                for h in hotels
            ]
            if map_progress
            else [],
            status=status,
            context=query,
            queried_at=now_iso(),
            data_mode=self.data_mode,
            hotels=hotels,
            coverage=Coverage(
                returned_count=len(hotels),
                requested_limit=(
                    1 if request.strategy == "named_hotel"
                    else query.limit * (5 if request.strategy == "map_hotels" else request.branch_limit)
                ),
                limited=incomplete,
            ),
            warnings=warnings,
            error=error if incomplete or status == "unavailable" else None,
            walk_check_available=registered,
            operation_id=request.operation_id,
            map_provider=request.provider,
            restaurant_keyword=request.restaurant_keyword,
            branches=branches,
            recall_coverage=stats,
        )
        response.recall_coverage.total_quote_http_requests = (
            response.recall_coverage.quote_http_requests
        )
        if request.strategy == "map_hotels" and map_progress is not None and response.status != "unavailable":
            map_progress["restaurant_ids"] = source_ids
            self.continuation.remember(request, response, map_progress, sessions)
        return response
