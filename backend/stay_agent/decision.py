"""Assemble P7 decisions from evidence available inside a single response.

The two response types carry disjoint evidence:

* ``QuotesResponse`` has trusted stay amounts (P6) but no route/fare evidence.
* ``TravelRouteResponse`` has trusted route/fare evidence but no stay amount,
  guest score or review count.

The project deliberately does not persist prices (spec section 4) and does not
cross-join supplier responses. Therefore each builder fills in ONLY its own
response's evidence and leaves every other field ``None`` -> ``unknown``.
Neither builder invents a value, re-queries a source, or reads a saved price.
"""

from __future__ import annotations

from . import return_trip as rt
from .contracts import (
    DecisionWire,
    PlanAssessmentWire,
    QuotesResponse,
    TravelRouteResponse,
)

EVIDENCE_BINDING_QUOTES = (
    "住宿金额与资格来自本次房型报价响应；返程路线、车费、夜间与评分证据不在本响应内，"
    "一律按未取得处理，不跨响应拼接、不读取已存价格。"
)
EVIDENCE_BINDING_ROUTE = (
    "交通与车费来自本次路线响应；住宿金额、住客评分与评价人数不在本响应内，"
    "一律按未取得处理，不跨响应拼接、不读取已存价格。"
)


def build_quote_decision(response: QuotesResponse) -> DecisionWire:
    """P7 gate for each quote of one hotel, using only P6 amounts."""
    plans: list[rt.PlanInput] = []
    for quote in response.quotes:
        qualification = quote.qualification
        fees = qualification.fees if qualification is not None else None
        plans.append(
            rt.PlanInput(
                plan_id=quote.id,
                per_room_night_fen=fees.per_room_night_fen if fees else None,
                stay_known_fen=fees.total_known_fen if fees else None,
                upstream_qualified=(
                    qualification is not None and qualification.tier == "qualified"
                ),
            )
        )
    assessments = [rt.assess(plan) for plan in plans]
    result = rt.rank(plans)
    return DecisionWire.from_result(
        result,
        assessments,
        evidence_binding=EVIDENCE_BINDING_QUOTES,
        data_mode="test" if response.data_mode == "test" else "live",
    )


def build_route_assessment(response: TravelRouteResponse) -> PlanAssessmentWire:
    """P7 gate for one route, using only this response's route/fare evidence."""
    walk = response.return_walk
    drive = response.return_drive
    lower: int | None = None
    upper: int | None = None
    fare_state: rt.FareState = "unknown"
    if response.fares:
        fare = next(
            (item for item in response.fares if item.name == "快车"),
            response.fares[0],
        )
        lower, upper = fare.lower_fen, fare.upper_fen
        if lower is not None and upper is not None and lower <= upper:
            fare_state = "known" if lower == upper else "range"
        else:
            lower, upper = None, None
    plan = rt.PlanInput(
        plan_id=f"route:{response.hotel_id}",
        walk_meters=walk.distance_meters if walk else None,
        drive_seconds=drive.duration_seconds if drive else None,
        fare_lower_fen=lower,
        fare_upper_fen=upper,
        fare_state=fare_state,
        night_access_verified=bool(walk and walk.night_access_verified),
        future_opening_verified=False,
        subway_required=False,
        branch_label=response.restaurant_name,
    )
    return PlanAssessmentWire.from_assessment(rt.assess(plan))
