"""Regression for observed child/room booking-context defects."""

from datetime import date
from urllib.parse import urlencode

import pytest

from stay_agent.contracts import SearchInput
from stay_agent.verification import booking_context, occupancy_status


def query(**changes):
    return SearchInput(place="广州", check_in="2026-11-06", check_out="2026-11-09", **changes)


def link(query, **changes):
    fields = {
        "id": "17",
        "checkInDate": str(query.check_in),
        "checkOutDate": str(query.check_out),
        "adultCount": str(query.adults),
        "childCount": str(len(query.children_ages)),
        "roomCount": str(query.rooms),
    }
    fields.update(changes)
    return "https://rollinggo.cn/book?" + urlencode(fields)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("stay_agent.contracts.china_today", lambda: date(2026, 10, 16))


def test_correct_original_link_preserved_without_occupancy_price_claim():
    q = query()
    url = link(q)
    assert booking_context(url, 17, q) == (url, "matched")
    assert occupancy_status({}, q) == "unverified"


@pytest.mark.parametrize(
    "changed",
    [
        {"roomCount": "1"},
        {"childCount": "0"},
        {"checkOutDate": "2026-11-07"},
        {"adultCount": "1"},
        {"id": "18"},
        {"hotelId": "18"},
    ],
)
def test_observed_wrong_handoff_is_blocked_not_rewritten(changed):
    q = query(rooms=2, children_ages=[5])
    assert booking_context(link(q, childAgeDetails="[5]", **changed), 17, q) == (None, "mismatch")


def test_missing_ages_or_opaque_urls_remain_unverified():
    q = query(children_ages=[5])
    assert booking_context(link(q), 17, q) == (None, "unverified")
    assert booking_context("https://rollinggo.cn/opaque", 17, q) == (None, "unverified")


def test_duplicate_or_mismatched_age_values_cannot_match():
    q = query(children_ages=[5])
    assert booking_context(link(q, childAgeDetails="[5]") + "&roomCount=1", 17, q) == (
        None,
        "mismatch",
    )
    assert booking_context(link(q, childAgeDetails="[12]"), 17, q) == (None, "mismatch")
    assert booking_context(link(q, childAgeDetails="[5]"), 17, q)[1] == "matched"


def test_explicit_source_occupancy_is_checked_without_boolean_coercion():
    q = query(children_ages=[1])
    expected = {"adultCount": 2, "childCount": 1, "childAgeDetails": [1], "roomCount": 1}
    assert occupancy_status({"occupancyParam": expected}, q) == "matched"
    assert (
        occupancy_status({"occupancyParam": {**expected, "childAgeDetails": [True]}}, q)
        == "mismatch"
    )
    assert occupancy_status({"roomCount": 2}, q) == "mismatch"



def test_context_diagnostics_are_fixed_codes_without_link_or_guest_data():
    from stay_agent.verification import booking_context_check, occupancy_context_check

    q = query(rooms=2, children_ages=[5])
    raw = "https://rollinggo.cn/book?token=secret-only-source&roomCount=1&adultCount=1"
    url, status, reasons = booking_context_check(raw, 17, q)
    assert url is None
    assert status == "mismatch"
    assert reasons == [
        "checkInDate_missing", "checkOutDate_missing",
        "adultCount_mismatch", "childCount_missing",
        "roomCount_mismatch", "hotelId_missing", "childAgeDetails_missing",
    ]
    assert "secret-only-source" not in str(reasons)
    occ, missing = occupancy_context_check(
        {"occupancyParam": {"adultCount": 2, "roomCount": 2}}, q
    )
    assert occ == "unverified"
    assert missing == ["childCount_missing", "childAgeDetails_missing"]


def test_detail_context_diagnostic_marks_only_real_mismatches_or_missing_fields():
    from stay_agent.verification import booking_context_check, occupancy_context_check

    q = query()
    raw = link(q, adultCount="1", childCount="0", roomCount="1") + "&secret=redacted-here"
    url, status, reasons = booking_context_check(raw, 17, q)
    assert (url, status, reasons) == (None, "mismatch", ["adultCount_mismatch"])
    assert "redacted-here" not in str(reasons)
    good = link(q)
    assert booking_context_check(good, 17, q) == (good, "matched", [])
    assert booking_context_check("http://localhost:8000/test", 17, q) == (
        None, "unavailable", ["link_absent_or_unsafe"]
    )
    assert booking_context_check("https://rollinggo.cn/opaque", 17, q) == (
        None, "unverified",
        ["checkInDate_missing", "checkOutDate_missing", "adultCount_missing",
         "childCount_missing", "roomCount_missing", "hotelId_missing"],
    )
    occ, reasons = occupancy_context_check(
        {"occupancyParam": {"adultCount": 1, "childCount": 0,
                             "roomCount": 1, "childAgeDetails": []}}, q
    )
    assert occ == "mismatch" and reasons == ["adultCount_mismatch"]



@pytest.mark.parametrize("age_parameter", ["[]", "[ ]", " [  ] ", "\n[]\t"])
def test_zero_children_accepts_only_semantically_empty_json_array_without_url_rewriting(
    age_parameter,
):
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[])
    original = link(q, childAgeDetails=age_parameter) + "&source=original-only"
    assert booking_context(original, 17, q) == (original, "matched")
    assert booking_context_check(original, 17, q) == (original, "matched", [])


@pytest.mark.parametrize(
    ("age_parameter", "expected_status", "reason"),
    [
        ("null", "unverified", "childAgeDetails_unparseable"),
        ("0", "unverified", "childAgeDetails_unparseable"),
        ("[", "unverified", "childAgeDetails_unparseable"),
        ("[5]", "mismatch", "childAgeDetails_mismatch"),
        ("[true]", "mismatch", "childAgeDetails_mismatch"),
        ("[null]", "mismatch", "childAgeDetails_mismatch"),
        ("[0.0]", "mismatch", "childAgeDetails_mismatch"),
    ],
)
def test_zero_children_invalid_or_nonzero_supplier_ages_never_unblock_link(
    age_parameter, expected_status, reason,
):
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[])
    original = link(q, childAgeDetails=age_parameter) + "&token=not-for-logs"
    assert booking_context(original, 17, q) == (None, expected_status)
    safe, status, issues = booking_context_check(original, 17, q)
    assert safe is None
    assert status == expected_status
    assert issues == [reason]
    assert "not-for-logs" not in str(issues)


@pytest.mark.parametrize("age_parameter", ["", " ", "\t "])
def test_zero_children_blank_age_value_counts_as_absent(age_parameter):
    """Suppliers that always emit an empty childAgeDetails= must not be blocked
    when the guest brings no children; every other field is still checked."""
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[])
    original = link(q, childAgeDetails=age_parameter) + "&source=original-only"
    assert booking_context(original, 17, q) == (original, "matched")
    assert booking_context_check(original, 17, q) == (original, "matched", [])


def test_blank_age_value_never_opens_link_when_another_field_conflicts():
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[])
    for changed in ({"adultCount": "1"}, {"childCount": "1"}, {"id": "18"}, {"roomCount": "2"}):
        safe, status, issues = booking_context_check(link(q, childAgeDetails="", **changed), 17, q)
        assert safe is None
        assert status == "mismatch"
        assert len(issues) == 1
    blank = link(q, childAgeDetails="")
    assert booking_context_check(blank, 17, q) == (blank, "matched", [])


def test_blank_age_value_stays_unknown_when_children_are_travelling():
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[5])
    for age_parameter in ("", "   "):
        assert booking_context_check(link(q, childAgeDetails=age_parameter), 17, q) == (
            None, "unverified", ["childAgeDetails_missing"],
        )


def test_missing_zero_child_age_key_is_allowed_only_with_exact_other_context():
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[])
    original = link(q)
    assert booking_context_check(original, 17, q) == (original, "matched", [])
    changed = link(q, adultCount="1")
    assert booking_context_check(changed, 17, q) == (
        None, "mismatch", ["adultCount_mismatch"],
    )


def test_duplicate_ages_never_match_even_if_both_arrays_are_empty():
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[])
    original = link(q, childAgeDetails="[]") + "&childAgeDetails=%5B%5D"
    assert booking_context(original, 17, q) == (None, "mismatch")
    assert booking_context_check(original, 17, q) == (
        None, "mismatch", ["childAgeDetails_mismatch"],
    )


def test_nonzero_children_require_exact_integer_age_array_and_source_guest_count():
    from stay_agent.verification import booking_context_check

    q = query(children_ages=[5])
    original = link(q, childAgeDetails="[ 5 ]")
    assert booking_context_check(original, 17, q) == (original, "matched", [])
    for age in ("[]", "[5.0]", "[true]", "[6]"):
        current = link(q, childAgeDetails=age)
        assert booking_context_check(current, 17, q)[0] is None
        assert booking_context_check(current, 17, q)[1] == "mismatch"
    absent = link(q)
    assert booking_context_check(absent, 17, q) == (
        None, "unverified", ["childAgeDetails_missing"],
    )


def test_room_capacity_and_matching_url_never_masquerade_as_occupancy_echo():
    from stay_agent.verification import booking_context_check, occupancy_context_check

    q = query(adults=2, rooms=1)
    # Historical RollingGo examples return roomInfo.maxOccupancy, but no 2+1 echo.
    provider = {
        "hotelId": 17, "checkIn": str(q.check_in), "checkOut": str(q.check_out),
        "roomRatePlans": [{"roomInfo": {"maxOccupancy": 2}}],
        "bookingUrl": link(q),
    }
    assert occupancy_context_check(provider, q) == (
        "unverified",
        ["adultCount_missing", "childCount_missing",
         "childAgeDetails_missing", "roomCount_missing"],
    )
    safe_url, status, _ = booking_context_check(provider["bookingUrl"], 17, q)
    assert safe_url == provider["bookingUrl"] and status == "matched"
    # Booking link context alone is not a supplier quote/room occupancy assertion.
    with_echo = {
        **provider,
        "occupancyParam": {
            "adultCount": 2, "childCount": 0, "childAgeDetails": [], "roomCount": 1,
        },
    }
    assert occupancy_context_check(with_echo, q) == ("matched", [])
    assert occupancy_context_check(
        {**with_echo, "occupancyParam": {**with_echo["occupancyParam"], "adultCount": 1}},
        q,
    ) == ("mismatch", ["adultCount_mismatch"])
