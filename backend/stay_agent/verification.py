"""Verify source echoes and original booking context without rewriting links."""

import json
from urllib.parse import parse_qs, urlsplit

from .projection import public_https


def occupancy_context_check(data, query):
    expected = {
        "adultCount": query.adults,
        "childCount": len(query.children_ages),
        "childAgeDetails": query.children_ages,
        "roomCount": query.rooms,
    }
    echoed = data.get("occupancyParam")
    echoed = echoed if isinstance(echoed, dict) else data
    statuses = []
    issues = []
    for key, value in expected.items():
        if key not in echoed:
            statuses.append("unverified")
            issues.append(key + "_missing")
        elif (
            type(echoed[key]) is not type(value)
            or echoed[key] != value
            or (key == "childAgeDetails" and any(type(age) is not int for age in echoed[key]))
        ):
            statuses.append("mismatch")
            issues.append(key + "_mismatch")
        else:
            statuses.append("matched")
    status = (
        "mismatch"
        if "mismatch" in statuses
        else "unverified"
        if "unverified" in statuses
        else "matched"
    )
    return status, issues


def occupancy_status(data, query):
    return occupancy_context_check(data, query)[0]


def _age_parameter_check(ages, expected_ages):
    """Return (status, fixed_reason) without exposing or rewriting provider values.

    Missing child ages are sufficient only when the user has NO children.
    A present value must be a single JSON integer array. JSON whitespace is
    insignificant; non-array/invalid encodings are UNKNOWN, not proof of match.

    A blank value ("") carries no ages at all, so it is treated exactly like an
    absent parameter: sufficient only when the user has no children, UNKNOWN
    otherwise. This is a narrow, user-approved policy change (2026-11-04) to
    cover suppliers that always emit an empty `childAgeDetails=` when the guest
    brings no children (observed on RollingGo). Any non-blank value keeps the
    original strict rules.
    """
    if ages is None:
        return ("unverified", "childAgeDetails_missing") if expected_ages else ("matched", None)
    if len(ages) != 1:
        return "mismatch", "childAgeDetails_mismatch"
    if not str(ages[0]).strip():
        return ("unverified", "childAgeDetails_missing") if expected_ages else ("matched", None)
    try:
        parsed = json.loads(ages[0])
    except (ValueError, TypeError, RecursionError):
        return "unverified", "childAgeDetails_unparseable"
    if not isinstance(parsed, list):
        return "unverified", "childAgeDetails_unparseable"
    if any(type(age) is not int for age in parsed) or parsed != expected_ages:
        return "mismatch", "childAgeDetails_mismatch"
    return "matched", None


def booking_context(raw_url, hotel_id, query):
    original = public_https(raw_url, booking=True)
    if not original:
        return None, "unavailable"
    params = parse_qs(urlsplit(original).query, keep_blank_values=True)
    expected = {
        "checkInDate": query.check_in.isoformat(),
        "checkOutDate": query.check_out.isoformat(),
        "adultCount": str(query.adults),
        "childCount": str(len(query.children_ages)),
        "roomCount": str(query.rooms),
    }
    unknown = False
    mismatch = False
    for key, expected_value in expected.items():
        if key not in params:
            unknown = True
        elif params[key] != [expected_value]:
            mismatch = True
    ids = [key for key in ("id", "hotelId") if key in params]
    if not ids:
        unknown = True
    elif any(params[key] != [str(hotel_id)] for key in ids):
        mismatch = True
    age_status, _ = _age_parameter_check(params.get("childAgeDetails"), query.children_ages)
    if age_status == "mismatch":
        mismatch = True
    elif age_status == "unverified":
        unknown = True
    status = "mismatch" if mismatch else "unverified" if unknown else "matched"
    return original if status == "matched" else None, status



def booking_context_check(raw_url, hotel_id, query):
    """Original link gate plus sanitized field-by-field reasons.

    Never returns the rejected URL, query values, tokens, host, or supplier IDs.
    Issue codes come only from fixed, known keys; no raw provider data is copied.
    """
    safe_url, status = booking_context(raw_url, hotel_id, query)
    if status == "matched":
        return safe_url, status, []
    original = public_https(raw_url, booking=True)
    if original is None:
        return None, status, ["link_absent_or_unsafe"]
    params = parse_qs(urlsplit(original).query, keep_blank_values=True)
    expected = {
        "checkInDate": query.check_in.isoformat(),
        "checkOutDate": query.check_out.isoformat(),
        "adultCount": str(query.adults),
        "childCount": str(len(query.children_ages)),
        "roomCount": str(query.rooms),
    }
    issues = []
    for key, value in expected.items():
        if key not in params:
            issues.append(key + "_missing")
        elif params[key] != [value]:
            issues.append(key + "_mismatch")
    id_keys = [key for key in ("id", "hotelId") if key in params]
    if not id_keys:
        issues.append("hotelId_missing")
    elif any(params[key] != [str(hotel_id)] for key in id_keys):
        issues.append("hotelId_mismatch")
    _, age_issue = _age_parameter_check(params.get("childAgeDetails"), query.children_ages)
    if age_issue is not None:
        issues.append(age_issue)
    return None, status, issues or ["link_context_unknown"]
