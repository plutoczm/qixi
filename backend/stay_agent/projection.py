"""Project only known fields; source display quotes are not verified totals."""

from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
from urllib.parse import urlsplit

from .contracts import Cancellation, HotelSummary, Qualification, ReferenceGate, RoomQuote
from .pricing import QuoteInput, assess
from .qualification import (
    PRICE_INCLUDES_TAX,
    PRICE_UNIT,
    field_status,
    reference_gate,
)

UNKNOWN_ROOM = ["独立客房待核实", "独立卫浴待核实", "完整费用待核实"]


def text(value, maximum=1000):
    return value.strip()[:maximum] if isinstance(value, str) and value.strip() else None


def currency_code(value):
    code = text(value, 3)
    return (
        code.upper()
        if code and len(value.strip()) == 3 and code.isascii() and code.isalpha()
        else None
    )


def money_fen(value, currency):
    if currency != "CNY" or type(value) not in (int, float, str):
        return None
    try:
        amount = Decimal(str(value))
        minor = amount * 100
        if (
            not minor.is_finite()
            or minor < 0
            or minor > 100000000
            or minor != minor.to_integral_value()
        ):
            return None
        return int(minor)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def number(value, low=0, high=10000):
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
        return result if low <= result <= high else None
    except (OverflowError, ValueError):
        return None


def public_https(value, *, booking=False):
    value = text(value, 8192)
    if not value or "[REDACTED]" in value or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 443):
            return None
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return None
        except ValueError:
            pass
        if booking and not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in ("rollinggo.cn", "rollinggo.store")
        ):
            return None
        return value
    except ValueError:
        return None


def hotel_summary(raw):
    if (
        not isinstance(raw, dict)
        or type(raw.get("hotelId")) is not int
        or not 0 < raw["hotelId"] < 2**31
    ):
        return None
    price = raw.get("price")
    price = price if isinstance(price, dict) else {}
    currency = currency_code(price.get("currency"))
    amount = (
        money_fen(price.get("lowestPrice"), currency) if price.get("hasPrice") is True else None
    )
    return HotelSummary(
        id=raw["hotelId"],
        name=text(raw.get("name"), 200) or "住宿名称待核实",
        address=text(raw.get("address"), 500),
        image_url=public_https(raw.get("imageUrl")),
        hotel_stars=number(raw.get("starRating"), high=5),
        guest_score=None,
        reference_price_fen=amount,
        currency=currency,
        booking_url=public_https(raw.get("bookingUrl"), booking=True),
        unverified=["住客评分未取得", *UNKNOWN_ROOM],
    )


def room_quote(
    raw, index, *, nights=1, rooms=1, adults=2, snapshot_at=None,
    children=0, budget_fen=None, require_free_cancel=False,
    accepts_dormitory=False,
):
    """Project one rate plan, with the same reference gate every source uses.

    RollingGo returns no room count and no occupancy echo, so its reference
    gate is expected to stay blocked. That is the point: the card must show
    *which* field is missing instead of silently claiming "not evaluated".

    The P6 qualification is computed here from this one rate plan only, with
    the supplier's own room name -- a missing name must not borrow the
    whole-room default, otherwise a nameless plan would qualify on evidence it
    never had.
    """
    if not isinstance(raw, dict):
        return None
    info = raw.get("roomInfo")
    info = info if isinstance(info, dict) else {}
    currency = currency_code(raw.get("currency"))
    policy = text(raw.get("cancelPolicy"))
    can_cancel = raw.get("cancelable") if type(raw.get("cancelable")) is bool else None
    on_request = raw.get("isOnRequest") if type(raw.get("isOnRequest")) is bool else None
    capacity = info.get("maxOccupancy")
    if type(capacity) is not int or not 1 <= capacity <= 100:
        capacity = None
    identity = str(raw.get("ratePlanId", "")) + ":" + str(index)
    # Gate on the supplier's own room name, never on the display placeholder:
    # a missing name must not be able to borrow the whole-room default.
    observed_name = text(raw.get("roomName"), 200) or text(raw.get("roomNameCn"), 200)
    amount = money_fen(raw.get("averagePrice"), currency)
    # Read the tax/unit state from the one field table instead of asserting
    # "this source includes tax" here: a corrected status must not leave a
    # stale promise in this projection.
    stated = field_status("rollinggo", PRICE_INCLUDES_TAX) == "verified"
    unit_stated = field_status("rollinggo", PRICE_UNIT) == "verified"
    assessment = assess(QuoteInput(
        source_id="rollinggo",
        room_name=observed_name,
        amount_fen=amount,
        price_unit="per_room_night" if unit_stated else "unstated",
        tax_included=True if stated else None,
        on_request=on_request,
        nights=nights,
        rooms=rooms,
        adults=adults,
        children=children,
        max_occupancy=capacity,
        cancellation_text=policy,
        cancellation_verified=can_cancel,
        require_free_cancel=require_free_cancel,
        budget_fen=budget_fen,
        accepts_dormitory=accepts_dormitory,
    ))
    return RoomQuote(
        id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        room_name=observed_name or "房型名称待核实",
        plan_name=text(raw.get("ratePlanName"), 250),
        amount_fen=amount,
        currency=currency,
        bed_type=text(raw.get("bedTypeDescription"), 200),
        meal=text(raw.get("mealTypeStr"), 100),
        cancellation=Cancellation(text=policy or "取消规则待核实", can_cancel=can_cancel),
        on_request=on_request,
        max_occupancy=capacity,
        room_size_sqm=number(info.get("size")),
        reference=ReferenceGate.from_gate(
            reference_gate(
                "rollinggo",
                nights=nights,
                rooms=rooms,
                adults=adults,
                snapshot_at=snapshot_at,
                room_name=observed_name,
                accepts_dormitory=accepts_dormitory,
            )
        ),
        qualification=Qualification.from_assessment(assessment),
        unverified=[*UNKNOWN_ROOM, "库存与入住条件请在预订页复核"],
    )
