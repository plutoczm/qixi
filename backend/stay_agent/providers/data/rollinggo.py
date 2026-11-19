"""RollingGo configuration and response projection shared by production and CLI."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[4]

class ConfigurationError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_api_key(path: Path = ROOT / ".env", environ: Mapping[str, str] | None = None) -> str:
    environment = os.environ if environ is None else environ
    value = environment.get("ROLLINGGO_API_KEY", "").strip()
    if not value:
        try:
            if path.stat().st_size > 65536:
                raise ConfigurationError("ENV_FILE_TOO_LARGE")
            with path.open(encoding="utf-8-sig") as stream:
                found = []
                for line in stream:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:].strip()
                    name, separator, candidate = line.partition("=")
                    if separator and name.strip() == "ROLLINGGO_API_KEY":
                        candidate = candidate.strip()
                        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in ("'", '"'):
                            candidate = candidate[1:-1]
                        found.append(candidate)
                if len(found) > 1:
                    raise ConfigurationError("DUPLICATE_ROLLINGGO_KEY")
                value = found[0].strip() if found else ""
        except FileNotFoundError:
            value = ""
        except (OSError, UnicodeError):
            raise ConfigurationError("ENV_FILE_UNREADABLE") from None
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value or value in {"YOUR_API_KEY", "CHANGE_ME", "<YOUR_API_KEY>"}:
        raise ConfigurationError("MISSING_ROLLINGGO_API_KEY")
    if not value.isascii() or any(char.isspace() for char in value):
        raise ConfigurationError("INVALID_ROLLINGGO_KEY_FORMAT")
    return value


def business_data(result: dict) -> dict:
    value = result
    for _ in range(8):
        if not isinstance(value, dict):
            return {}
        if any(key in value for key in ("hotelInformationList", "roomRatePlans", "tags")):
            return value
        nested = value.get("structuredContent")
        if isinstance(nested, dict):
            value = nested
            continue
        for wrapper in ("data", "result"):
            nested = value.get(wrapper)
            if isinstance(nested, dict):
                value = nested
                break
        else:
            contents = value.get("content", [])
            for item in contents if isinstance(contents, list) else []:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        nested = json.loads(item.get("text", ""))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(nested, dict):
                        value = nested
                        break
            else:
                return value
            continue
        continue
    return {}


def business_summary(result: dict) -> dict:
    data = business_data(result)
    summary: dict = {"top_level_fields": sorted(data)}
    summary["returned_context"] = {key: data[key] for key in ("hotelId", "checkIn", "checkOut") if key in data}
    if type(data.get("success")) is bool:
        summary["provider_success"] = data["success"]
    if type(data.get("code")) in (str, int):
        summary["provider_code"] = data["code"]
    if isinstance(data.get("message"), str):
        summary["provider_message"] = data["message"][:300]
    hotels = data.get("hotelInformationList")
    if isinstance(hotels, list):
        summary["hotel_count"] = len(hotels)
        summary["hotels"] = []
        for hotel in hotels[:20]:
            if not isinstance(hotel, dict):
                continue
            item = {key: hotel[key] for key in ("hotelId", "name", "hotelName", "nameCn") if key in hotel}
            item["available_fields"] = sorted(hotel)
            item["booking_url_present"] = isinstance(hotel.get("bookingUrl"), str) and bool(hotel["bookingUrl"])
            price = hotel.get("price")
            if isinstance(price, dict):
                item["search_price"] = {key: price[key] for key in ("hasPrice", "currency", "lowestPrice") if key in price}
                item["price_scope"] = "first_night_reference_only"
            summary["hotels"].append(item)
    plans = data.get("roomRatePlans")
    if isinstance(plans, list):
        summary["rate_plan_count"] = len(plans)
        summary["rate_plans"] = [
            {key: plan[key] for key in (
                "ratePlanId", "roomName", "ratePlanName", "roomNameCn", "averagePrice", "currency", "bedTypeDescription",
                "mealAmount", "mealTypeStr", "isOnRequest", "cancelPolicy"
            ) if key in plan}
            for plan in plans[:10] if isinstance(plan, dict)
        ]
        summary["price_scope"] = "supplier_display_quote_not_locked_final_price"
        summary["stay_total_verified"] = False
    link = data.get("bookingUrl")
    summary["booking_url_present"] = isinstance(link, str) and bool(link.strip())
    summary["mini_program_present"] = bool(data.get("miniProgramInfo"))
    if isinstance(link, str) and link:
        try:
            summary["booking_host"] = urlsplit(link).hostname
        except ValueError:
            summary["booking_host"] = None
    return summary


def has_display_quote(result: dict) -> bool:
    plans = business_data(result).get("roomRatePlans", [])
    if not isinstance(plans, list):
        return False
    for plan in plans:
        if not isinstance(plan, dict) or type(plan.get("averagePrice")) not in (str, int, float):
            continue
        currency = plan.get("currency")
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            continue
        try:
            price = Decimal(str(plan["averagePrice"]))
        except InvalidOperation:
            continue
        if price.is_finite() and price >= 0:
            return True
    return False
