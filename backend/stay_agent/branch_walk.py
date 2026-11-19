"""Fresh same-provider identity and walking checks for authorized recall pairs."""

from dataclasses import dataclass
import threading
import time
from uuid import uuid4

from stay_agent.providers.data.travel_clients import MapProbe, SecureHTTP, load_key, load_tencent_secret
from .contracts import (
    ApiError,
    BranchWalkPair,
    BranchWalkBatchResponse,
    DrivingEvidence,
    PlaceOption,
    MatchAssessment,
    SearchInput,
)
from .recall import Operation
from .travel import TravelService, short_query, now
from .travel_rules import assessment


class BranchWalkError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class PairContext:
    request_id: str
    operation_id: str
    client: object
    lock: object
    cancelled: threading.Event
    expires: float
    pair: BranchWalkPair
    hotels: dict
    restaurants: dict


def options(client, points):
    result, refs = [], {}
    for point in points:
        if len(point.title) > 300:
            continue
        meta = client.metadata.get(point.poi_id, {})
        address = meta.get("address")
        city = meta.get("city")
        item = PlaceOption(
            id=uuid4().hex,
            name=point.title,
            address=address if isinstance(address, str) and 0 < len(address) <= 300 else None,
            city=city if isinstance(city, str) and 0 < len(city) <= 50 else None,
        )
        result.append(item)
        refs[item.id] = point
    return result, refs


def missing_match():
    return MatchAssessment(status="not_found", explanation="尚未取得可核实的同店点位。")


class BranchWalkService:
    def __init__(self, recall, sessions, map_factory=None, clock=time.monotonic, data_mode="live"):
        self.recall, self.sessions = recall, sessions
        self.factory = map_factory or self._map
        self.clock, self.data_mode = clock, data_mode
        self.mutex = threading.RLock()
        self.capacity = threading.BoundedSemaphore(2)
        self.operations = {}
        self.contexts = {}

    @staticmethod
    def _map(provider, deadline, cancelled):
        http = SecureHTTP(
            provider,
            load_key(provider),
            timeout=15,
            total_timeout=60,
            max_requests=12,
            secret=load_tencent_secret() if provider == "tencent" else None,
        )
        http.deadline = deadline
        http.cancelled = cancelled
        return MapProbe(provider, http.key, http=http)

    def job(self, operation_id, start=False):
        with self.mutex:
            self.operations = {
                key: value for key, value in self.operations.items() if value.expires > self.clock()
            }
            value = self.operations.get(operation_id)
            if value is None:
                if len(self.operations) >= 128:
                    raise BranchWalkError("WALK_BUSY")
                value = Operation(threading.Event(), self.clock() + 1800)
                self.operations[operation_id] = value
            if start:
                if value.started:
                    raise BranchWalkError("WALK_OPERATION_CONFLICT")
                value.started = True
            else:
                value.cancelled.set()
            return value

    def cancel(self, operation_id):
        self.job(operation_id)
        return {"accepted": True}

    def close(self):
        with self.mutex:
            for operation in self.operations.values():
                operation.cancelled.set()

    def pairs(self, request_id):
        record = self.recall.association(request_id)
        result = []
        branches = record["branches"]
        if any(b.get("role") != "restaurant_body" for b in branches):
            raise BranchWalkError("WALK_RESTAURANT_ROLE_REQUIRED")
        for index in range(max((len(b["hotel_ids"]) for b in branches), default=0)):
            for branch in branches:
                if index < len(branch["hotel_ids"]):
                    hotel_id = branch["hotel_ids"][index]
                    self.sessions.load(request_id, hotel_id)
                    result.append((branch, record["hotels"][hotel_id]))
        SearchInput.model_validate(record["query"])
        return record, result

    def _check(self, cancelled, deadline=None, expires=None):
        if cancelled.is_set():
            raise BranchWalkError("CANCELLED")
        if deadline is not None and self.clock() >= deadline:
            raise BranchWalkError("TIMEOUT")
        if expires is not None and self.clock() >= expires:
            raise BranchWalkError("WALK_CONTEXT_EXPIRED")

    @staticmethod
    def _failure(error):
        if isinstance(error, BranchWalkError):
            labels = {
                "CANCELLED": "已停止本次核对。",
                "TIMEOUT": "已达到本轮截止，未继续追加查询。",
                "WALK_CONTEXT_EXPIRED": "点位已过期，请重新批量检索。",
                "WALK_IDENTITY_REQUIRED": "请分别核对住宿与召回门店身份。",
            }
            return ApiError(
                code=error.code, message=labels.get(error.code, "当前核对未完成，请重新检索。")
            )
        return TravelService.error(error)

    def _empty(self, branch, hotel, status="skipped", error=None):
        anchor = branch.get("hotel_targets", {}).get(hotel["id"])
        return BranchWalkPair(
            recall_hotel=PlaceOption(**{k: anchor[k] for k in ("id", "name", "address", "city")})
            if anchor
            else None,
            branch_id=branch["id"],
            hotel_id=hotel["id"],
            original_hotel_name=hotel["name"],
            original_hotel_address=hotel.get("address"),
            original_restaurant=PlaceOption(
                id=branch["id"],
                name=branch["name"],
                address=branch.get("address"),
                city=branch.get("city"),
            ),
            status=status,
            hotel_matching=missing_match(),
            restaurant_matching=missing_match(),
            queried_at=now(),
            error=error,
            data_mode=self.data_mode,
        )

    def batch(self, request):
        if not self.capacity.acquire(blocking=False):
            raise BranchWalkError("WALK_BUSY")
        try:
            return self._batch(request)
        finally:
            self.capacity.release()

    def _batch(self, request):
        started = self.clock()
        deadline = started + 60
        operation = self.job(request.operation_id, start=True)
        record, all_pairs = self.pairs(request.request_id)
        if request.offset >= len(all_pairs) and all_pairs:
            raise BranchWalkError("WALK_OFFSET_INVALID")
        selected = all_pairs[request.offset : request.offset + 3]
        output = []
        client = None
        error = None
        cache = {}
        batch_lock = threading.Lock()
        halted = False
        with self.mutex:
            self.contexts = {
                key: value for key, value in self.contexts.items() if value.expires > self.clock()
            }
            if len(self.contexts) + len(selected) > 64:
                raise BranchWalkError("WALK_BUSY")
        for branch, hotel in selected:
            pair = self._empty(branch, hotel)
            if halted:
                pair.error = error
                output.append(pair)
                continue
            try:
                self._check(operation.cancelled, deadline)
                if client is None:
                    client = self.factory(record["provider"], deadline, operation.cancelled)
                hkey = ("hotel", hotel["id"])
                if hkey not in cache:
                    cache[hkey] = client.search(
                        short_query(hotel["name"]), record["query"]["place"], page_size=5
                    )
                self._check(operation.cancelled, deadline)
                rkey = ("restaurant", branch["id"])
                if rkey not in cache:
                    cache[rkey] = client.search(
                        short_query(branch["name"]), record["query"]["place"], page_size=5
                    )
                self._check(operation.cancelled, deadline)
                hoptions, hrefs = options(client, cache[hkey])
                roptions, rrefs = options(client, cache[rkey])
                city = record["query"]["place"]
                hrows, hmatch = assessment(hotel, [o.model_dump() for o in hoptions], city, city)
                rrows, rmatch = assessment(
                    branch, [o.model_dump() for o in roptions], branch.get("city"), city
                )
                if (
                    rmatch["status"] == "matched"
                    and rrefs[rmatch["selected_id"]].poi_id != branch["source_id"]
                ):
                    rmatch = {
                        "status": "unverified",
                        "selected_id": None,
                        "explanation": "名称地址城市一致，但来源地点编号不同，请确认原召回门店。",
                    }
                anchor = branch.get("hotel_targets", {}).get(hotel["id"])
                if anchor and hmatch["status"] == "matched":
                    selected = next(row for row in hrows if row["id"] == hmatch["selected_id"])
                    _, anchor_match = assessment(anchor, [selected], city, city)
                    if (
                        hrefs[hmatch["selected_id"]].poi_id != anchor["source_id"]
                        or anchor_match["status"] != "matched"
                    ):
                        hmatch = {
                            "status": "unverified",
                            "selected_id": None,
                            "explanation": "本次酒店点位与召回时地图酒店参考不一致，请核对身份。",
                        }
                # On map-first recall, the original RollingGo hotel has no
                # independently returned city. A new map point in the requested
                # city cannot fill that evidence gap. Require user confirmation
                # even when its name/address and original POI anchor are equal.
                if anchor and hmatch["status"] == "matched":
                    hmatch = {
                        "status": "unverified",
                        "selected_id": None,
                        "explanation": "道旅未独立回显酒店城市；即使名称、地址及地图锚点一致，仍需人工确认同店。",
                    }
                pair.hotels = [PlaceOption(**row) for row in hrows]
                pair.restaurants = [PlaceOption(**row) for row in rrows]
                pair.hotel_matching = MatchAssessment(**hmatch)
                pair.restaurant_matching = MatchAssessment(**rmatch)
                pair.context_id = uuid4().hex
                pair.status = "needs_confirmation" if hrefs and rrefs else "unavailable"
                if not hrefs or not rrefs:
                    pair.error = ApiError(
                        code="PLACE_NOT_FOUND", message="本次范围缺少点位候选，不能计算步行。"
                    )
                context = PairContext(
                    request.request_id,
                    request.operation_id,
                    client,
                    batch_lock,
                    operation.cancelled,
                    started + 120,
                    pair.model_copy(deep=True),
                    hrefs,
                    rrefs,
                )
                if hmatch["status"] == "matched" and rmatch["status"] == "matched":
                    pair = self._compute(
                        context,
                        hmatch["selected_id"],
                        rmatch["selected_id"],
                        False,
                        False,
                        deadline,
                    )
                self._check(operation.cancelled, deadline)
                pair.expires_in_seconds = max(0, int(context.expires - self.clock()))
                with self.mutex:
                    self._check(operation.cancelled, deadline)
                    if len(self.contexts) >= 64:
                        raise BranchWalkError("WALK_BUSY")
                    context.pair = pair.model_copy(deep=True)
                    self.contexts[pair.context_id] = context
            except Exception as failure:
                error = self._failure(failure)
                pair.walk = None
                pair.walk_limit_status = "unverified"
                pair.drive = None
                pair.drive_limit_status = "unverified"
                pair.transport_status = "unknown_or_partial"
                halted = error.code in {
                    "WALK_BUSY",
                    "AUTHENTICATION_FAILED",
                    "ACCESS_DENIED",
                    "SOURCE_DAILY_LIMIT",
                    "RATE_LIMITED",
                    "REQUEST_LIMIT",
                    "MISSING_CREDENTIAL",
                    "INVALID_CREDENTIAL",
                    "PROVIDER_REJECTED",
                }
                pair.status = "skipped" if isinstance(failure, BranchWalkError) else "unavailable"
                pair.error = error
                with self.mutex:
                    if pair.context_id not in self.contexts:
                        pair.context_id = None
            pair.queried_at = now()
            output.append(pair)
        requests = client.http.request_count if client is not None else 0
        cancelled = operation.cancelled.is_set()
        if cancelled:
            error = self._failure(BranchWalkError("CANCELLED"))
            for pair in output:
                pair.walk = None
                pair.walk_limit_status = "unverified"
                pair.drive = None
                pair.drive_limit_status = "unverified"
                pair.transport_status = "unknown_or_partial"
                pair.status = "skipped"
                pair.error = error
        processed = sum(p.status != "skipped" for p in output)
        status = (
            "complete"
            if output and all(p.status == "route_verified" for p in output)
            else "partial"
            if any(p.context_id for p in output)
            else "unavailable"
        )
        if cancelled:
            status = "unavailable"
        for pair in output:
            pair.source_http_requests = requests
        return BranchWalkBatchResponse(
            request_id=request.request_id,
            operation_id=request.operation_id,
            provider=record["provider"],
            status=status,
            offset=request.offset,
            total_pairs=len(all_pairs),
            next_offset=request.offset + 3 if request.offset + 3 < len(all_pairs) else None,
            processed_pairs=processed,
            map_http_requests=requests,
            pairs=output,
            queried_at=now(),
            warnings=[
                "仅核对当前来源地图点位与餐厅→住宿步行/驾车预计；夜间出入口、营业和全部住宿资格仍未验证。",
                "交通满足：步行实际路线≤1500米，或驾车预计≤900秒；打车接驾等待和未来路况未知，不将缺失路线当作超限。",
                "同店有歧义需分别确认；点位120秒过期后重新查源。",
                "每处地点只检索前5条；长名称可能用截短关键词，但仍核对完整名称、地址和城市；未检出不代表不存在。",
            ],
            error=error,
            data_mode=self.data_mode,
        )

    def _compute(
        self, context, hotel_id, restaurant_id, confirm_hotel, confirm_restaurant, deadline
    ):
        self._check(context.cancelled, deadline, context.expires)
        if hotel_id not in context.hotels or restaurant_id not in context.restaurants:
            raise BranchWalkError("WALK_OPTION_MISMATCH")
        pair = context.pair.model_copy(deep=True)
        hmatch = pair.hotel_matching
        rmatch = pair.restaurant_matching
        hexact = hmatch.status == "matched" and hmatch.selected_id == hotel_id
        rexact = rmatch.status == "matched" and rmatch.selected_id == restaurant_id
        if (not hexact and not confirm_hotel) or (not rexact and not confirm_restaurant):
            raise BranchWalkError("WALK_IDENTITY_REQUIRED")
        origin = context.restaurants[restaurant_id]
        destination = context.hotels[hotel_id]
        walk = TravelService._walk(origin, destination, context.client.walk(origin, destination))
        self._check(context.cancelled, deadline, context.expires)
        pair.walk = walk
        pair.walk_limit_status = (
            "within_1500" if walk.distance_meters <= 1500 else "over_1500"
        ) if walk is not None else "unverified"
        # Avoid an extra paid map request if walking already satisfies the OR rule.
        # If walking is unavailable or over 1500m, driving may still qualify.
        if pair.walk_limit_status != "within_1500":
            drive_route = TravelService._walk(
                origin, destination, context.client.drive(origin, destination)
            )
            self._check(context.cancelled, deadline, context.expires)
            if drive_route is not None:
                pair.drive = DrivingEvidence(
                    origin_name=drive_route.origin_name,
                    destination_name=drive_route.destination_name,
                    distance_meters=drive_route.distance_meters,
                    duration_seconds=drive_route.duration_seconds,
                )
                pair.drive_limit_status = (
                    "within_900" if drive_route.duration_seconds <= 900 else "over_900"
                )
        if walk is None and pair.drive is None:
            raise BranchWalkError("WALK_NO_ROUTE")
        pair.transport_status = (
            "pass_walk_or_drive"
            if pair.walk_limit_status == "within_1500" or pair.drive_limit_status == "within_900"
            else "rejected_both_modes_over_limit"
            if pair.walk_limit_status == "over_1500" and pair.drive_limit_status == "over_900"
            else "unknown_or_partial"
        )
        pair.status = "route_verified"
        pair.hotel_match_basis = "exact_fields" if hexact else "user_confirmed"
        pair.restaurant_match_basis = "exact_fields" if rexact else "user_confirmed"
        pair.queried_at = now()
        pair.error = None
        pair.expires_in_seconds = max(0, int(context.expires - self.clock()))
        pair.source_http_requests = context.client.http.request_count
        return pair

    def route(self, request):
        with self.mutex:
            context = self.contexts.get(request.context_id)
        if context is None:
            raise BranchWalkError("WALK_CONTEXT_NOT_FOUND")
        if context.request_id != request.request_id:
            raise BranchWalkError("WALK_CONTEXT_MISMATCH")
        self.sessions.load(context.request_id, context.pair.hotel_id)
        record = self.recall.association(context.request_id)
        branch = next((b for b in record["branches"] if b["id"] == context.pair.branch_id), None)
        if branch is None or branch.get("role") != "restaurant_body":
            raise BranchWalkError("WALK_RESTAURANT_ROLE_REQUIRED")
        SearchInput.model_validate(record["query"])
        self._check(context.cancelled, expires=context.expires)
        if not context.lock.acquire(blocking=False):
            raise BranchWalkError("WALK_BUSY")
        if not self.capacity.acquire(blocking=False):
            context.lock.release()
            raise BranchWalkError("WALK_BUSY")
        try:
            self._check(context.cancelled, expires=context.expires)
            deadline = self.clock() + 60
            context.client.http.deadline = deadline
            try:
                pair = self._compute(
                    context,
                    request.hotel_option_id,
                    request.restaurant_option_id,
                    request.confirm_hotel,
                    request.confirm_restaurant,
                    deadline,
                )
                with self.mutex:
                    self._check(context.cancelled, expires=context.expires)
                    context.pair = pair.model_copy(deep=True)
                return pair
            except BranchWalkError:
                raise
            except Exception as failure:
                pair = context.pair.model_copy(deep=True)
                pair.walk = None
                pair.walk_limit_status = "unverified"
                pair.drive = None
                pair.drive_limit_status = "unverified"
                pair.transport_status = "unknown_or_partial"
                pair.status = "unavailable"
                pair.error = self._failure(failure)
                pair.queried_at = now()
                pair.source_http_requests = context.client.http.request_count
                return pair
        finally:
            self.capacity.release()
            context.lock.release()
