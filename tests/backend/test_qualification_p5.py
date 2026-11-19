"""P5: evidence semantics must stay assumptions, never become guarantees."""
import pytest

from stay_agent import qualification as q


# --- room form / bathroom ---------------------------------------------------

@pytest.mark.parametrize("name", ["标准大床房", "双床房", "高级双床房", "家庭房"])
def test_ordinary_room_types_use_the_approved_defaults(name):
    form = q.room_form(name)
    bath = q.private_bathroom(name)
    assert form.satisfied and bath.satisfied
    assert form.verified is False and bath.verified is False
    assert "推定" in bath.label


def test_room_name_saying_private_bathroom_is_not_positive_evidence():
    bath = q.private_bathroom("独卫大床房")
    assert bath.satisfied is True
    assert bath.verified is False
    assert bath.basis == "domestic_default_private_bathroom"


@pytest.mark.parametrize(
    "name", ["标准间（公共卫浴）", "单人间(公卫)", "四人间 共用卫生间", "无独卫双床房"],
)
def test_explicit_shared_bathroom_is_excluded(name):
    bath = q.private_bathroom(name)
    assert bath.satisfied is False
    assert bath.basis.startswith("explicit_shared_marker:")
    assert bath.verified is False


@pytest.mark.parametrize("name", ["青旅四人床位房", "宿舍上铺床位", "六人间 通铺"])
def test_bed_place_is_not_a_whole_room(name):
    form = q.room_form(name)
    bath = q.private_bathroom(name)
    assert form.satisfied is False
    assert bath.satisfied is False


def test_missing_room_name_cannot_borrow_the_default():
    for name in (None, "", "   "):
        assert q.room_form(name).satisfied is False
        assert q.private_bathroom(name).satisfied is False


def test_full_width_parentheses_are_normalised():
    assert q.private_bathroom("标准间（公共卫浴）").satisfied is False


# --- inventory --------------------------------------------------------------

def test_remaining_count_meets_only_when_it_covers_requested_rooms():
    assert q.inventory_from_remaining(3, 2).status == "sufficient"
    assert q.inventory_from_remaining(3, 2).meets is True
    assert q.inventory_from_remaining(2, 2).status == "sufficient"
    assert q.inventory_from_remaining(1, 2).status == "insufficient"
    assert q.inventory_from_remaining(0, 1).status == "insufficient"
    assert q.inventory_from_remaining(None, 1).status == "unknown"


def test_remaining_count_rejects_bool_and_negative_values():
    assert q.inventory_from_remaining(True, 1).status == "unknown"
    assert q.inventory_from_remaining(-1, 1).status == "unknown"


def test_instant_booking_flag_is_never_a_room_count():
    single = q.inventory_from_instant_booking(False, 1)
    assert single.status == "instant_unquantified"
    assert single.meets is False
    assert "剩余房量" in single.detail
    assert q.inventory_from_instant_booking(True, 1).status == "unknown"
    assert q.inventory_from_instant_booking(None, 1).status == "unknown"


def test_inventory_requires_positive_rooms():
    with pytest.raises(ValueError):
        q.inventory_from_remaining(5, 0)
    with pytest.raises(ValueError):
        q.inventory_from_instant_booking(False, 0)


# --- money ------------------------------------------------------------------

def test_only_a_stated_per_night_amount_may_be_multiplied():
    assert q.stay_total_fen(18800, 2, 1, source_id="rollinggo") == 37600
    assert q.stay_total_fen(18800, 2, 2, source_id="rollinggo") == 75200
    # Tuniu states "价格（元）" only; the unit is unstated.
    assert q.stay_total_fen(66200, 2, 1, source_id="tuniu") is None
    assert q.stay_total_fen(66200, 1, 1, source_id="flyai") is None


def test_money_rejects_bool_none_and_negative():
    assert q.stay_total_fen(None, 1, 1, source_id="rollinggo") is None
    assert q.stay_total_fen(True, 1, 1, source_id="rollinggo") is None
    assert q.stay_total_fen(-1, 1, 1, source_id="rollinggo") is None
    with pytest.raises(ValueError):
        q.stay_total_fen(100, 0, 1, source_id="rollinggo")


def test_price_semantics_are_recorded_per_source():
    assert q.PRICE_SEMANTICS["rollinggo"].tax_included is True
    assert q.PRICE_SEMANTICS["rollinggo"].per_room_night is True
    assert q.PRICE_SEMANTICS["tuniu"].tax_included is None
    assert q.PRICE_SEMANTICS["flyai"].tax_included is None


# --- gate -------------------------------------------------------------------

def test_no_source_is_ready_for_a_full_decision_tonight():
    for source in ("rollinggo", "tuniu", "flyai"):
        assert q.gate_status(source, nights=1, rooms=1).ready is False


def test_tuniu_single_night_gate_names_the_real_blockers():
    gate = q.gate_status("tuniu", nights=1, rooms=1)
    assert set(gate.defaulted) == {
        q.ROOM_EXCLUSIVITY, q.PRIVATE_BATHROOM, q.PRICE_INCLUDES_TAX,
    }
    assert set(gate.blocked) == {
        q.DEPOSIT_AND_FEES, q.OCCUPANCY_ECHO, q.BOOKING_LINK,
    }
    assert q.INVENTORY_QUANTITY in gate.verified


def test_rollinggo_blocks_on_quantity_not_on_the_link():
    gate = q.gate_status("rollinggo", nights=1, rooms=1)
    assert q.INVENTORY_QUANTITY in gate.blocked
    assert q.BOOKING_LINK in gate.verified
    # A one-night quote makes the price unit irrelevant, so it is waived
    # rather than counted as verified or blocked.
    assert q.PRICE_UNIT not in gate.blocked
    assert q.PRICE_UNIT not in gate.verified
    assert q.FIELD_STATUS["rollinggo"][q.PRICE_UNIT][0] == "verified"


def test_multi_night_keeps_the_nightly_breakdown_requirement():
    gate = q.gate_status("tuniu", nights=2, rooms=1)
    assert q.NIGHTLY_BREAKDOWN in gate.blocked
    assert q.PRICE_UNIT in gate.blocked


def test_gate_rejects_unknown_source_and_bad_nights():
    with pytest.raises(ValueError):
        q.gate_status("ctrip", nights=1)
    with pytest.raises(ValueError):
        q.gate_status("tuniu", nights=0)


# --- matrix invariants ------------------------------------------------------

def test_every_matrix_cell_has_an_allowed_status_and_a_basis():
    allowed = {"verified", "unknown", "unsupported", "blocked"}
    for source, fields in q.FIELD_STATUS.items():
        assert set(fields) == set(q.REQUIRED_FIELDS) | {
            q.INSTANT_BOOKING, q.CANCELLATION, q.GUEST_SCORE,
        }, source
        for field, (status, basis) in fields.items():
            assert status in allowed, (source, field)
            assert isinstance(basis, str) and basis, (source, field)


def test_no_field_is_verified_without_a_quoted_appearance():
    """A verified cell must cite a rule-level basis, not a vague marketing label."""
    forbidden = ("obvious", "trust", "marketing", "looks_like", "as_usual")
    for fields in q.FIELD_STATUS.values():
        for _field, (status, basis) in fields.items():
            if status == "verified":
                assert not any(word in basis for word in forbidden)


def test_permitted_use_never_claims_a_written_supplier_licence():
    assert q.USE_STATUS["display_local_personal"][0] == "permitted"
    assert q.USE_STATUS["cache_raw_response"][0] == "prohibited"
    assert q.USE_STATUS["cross_source_price_merge"][0] == "prohibited"
    assert q.USE_STATUS["public_deployment_or_commercial_use"][0] == "out_of_scope"
    for _key, (status, basis) in q.USE_STATUS.items():
        assert status in {"permitted", "prohibited", "out_of_scope", "unstated"}
        assert basis


# --- approved presumptions (user 2026-11-05) --------------------------------

SNAPSHOT = "2026-11-05T18:30:00+08:00"


def test_occupancy_combo_needs_request_count_and_snapshot():
    ok = q.occupancy_combo(
        adults=2, rooms=1, remaining=5, snapshot_at=SNAPSHOT,
    )
    assert ok.satisfied is True
    assert ok.basis == "request_params_plus_remaining_count_plus_snapshot"
    assert "推定" in ok.label and "非来源确认" in ok.label


@pytest.mark.parametrize(
    "kwargs, basis",
    [
        ({"adults": 0, "rooms": 1, "remaining": 5, "snapshot_at": SNAPSHOT},
         "request_params_missing"),
        ({"adults": 2, "rooms": 0, "remaining": 5, "snapshot_at": SNAPSHOT},
         "request_params_missing"),
        ({"adults": 2, "rooms": 1, "remaining": None, "snapshot_at": SNAPSHOT},
         "remaining_count_missing"),
        ({"adults": 2, "rooms": 1, "remaining": 5, "snapshot_at": None},
         "snapshot_time_missing"),
        ({"adults": 2, "rooms": 1, "remaining": 5, "snapshot_at": "昨天"},
         "snapshot_time_missing"),
    ],
)
def test_occupancy_presumption_is_refused_without_all_three_inputs(kwargs, basis):
    combo = q.occupancy_combo(**kwargs)
    assert combo.satisfied is False
    assert combo.basis == basis


def test_tuniu_single_night_reaches_the_reference_layer_with_labels():
    gate = q.reference_gate(
        "tuniu", remaining=5, snapshot_at=SNAPSHOT, room_name="标准大床房",
    )
    assert gate.ready is True and gate.blocked == ()
    assert gate.layer == q.REFERENCE_LAYER
    assert gate.verified == (q.INVENTORY_QUANTITY,)
    assert set(gate.presumed) == {
        q.ROOM_EXCLUSIVITY, q.PRIVATE_BATHROOM,
        q.PRICE_INCLUDES_TAX, q.OCCUPANCY_ECHO,
    }
    # Deposit and link stay caveats: a reference card claims neither.
    assert set(gate.caveats) == {q.DEPOSIT_AND_FEES, q.BOOKING_LINK}
    assert any("到店另付未核实" in text for text in gate.labels)
    assert any("不提供跳转" in text for text in gate.labels)


def test_reference_layer_still_blocks_on_insufficient_count():
    gate = q.reference_gate(
        "tuniu", rooms=2, remaining=1, snapshot_at=SNAPSHOT,
        room_name="标准大床房",
    )
    assert gate.ready is False
    assert q.INVENTORY_QUANTITY in gate.blocked


def test_reference_layer_blocks_when_the_snapshot_is_missing():
    gate = q.reference_gate("tuniu", remaining=5, room_name="标准大床房")
    assert gate.ready is False
    assert q.OCCUPANCY_ECHO in gate.blocked


@pytest.mark.parametrize("name", ["标准间（公共卫浴）", "青旅四人床位房"])
def test_reference_layer_excludes_shared_or_bed_place_rooms(name):
    gate = q.reference_gate(
        "tuniu", remaining=5, snapshot_at=SNAPSHOT, room_name=name,
    )
    assert gate.ready is False
    assert q.PRIVATE_BATHROOM in gate.blocked


def test_a_shared_bathroom_room_is_still_a_whole_room_but_not_qualified():
    """公共卫浴 says nothing about exclusivity; only the bathroom fails."""
    gate = q.reference_gate(
        "tuniu", remaining=5, snapshot_at=SNAPSHOT,
        room_name="标准间（公共卫浴）",
    )
    assert q.ROOM_EXCLUSIVITY in gate.presumed
    assert q.PRIVATE_BATHROOM in gate.blocked


def test_a_bed_place_fails_both_exclusivity_and_bathroom():
    gate = q.reference_gate(
        "tuniu", remaining=5, snapshot_at=SNAPSHOT, room_name="青旅四人床位房",
    )
    assert q.ROOM_EXCLUSIVITY in gate.blocked
    assert q.PRIVATE_BATHROOM in gate.blocked


def test_reference_layer_refuses_a_missing_room_name():
    gate = q.reference_gate("tuniu", remaining=5, snapshot_at=SNAPSHOT)
    assert gate.ready is False
    assert q.ROOM_EXCLUSIVITY in gate.blocked


def test_tuniu_multi_night_presumes_the_average_but_says_so():
    gate = q.reference_gate(
        "tuniu", nights=2, remaining=5, snapshot_at=SNAPSHOT,
        room_name="标准大床房",
    )
    assert gate.ready is True
    assert q.PRICE_UNIT in gate.presumed
    assert q.NIGHTLY_BREAKDOWN in gate.presumed
    assert any("无法排除" in text for text in gate.labels)


def test_rollinggo_never_reaches_the_reference_layer_without_a_room_count():
    gate = q.reference_gate(
        "rollinggo", remaining=None, snapshot_at=SNAPSHOT, room_name="标准大床房",
    )
    assert gate.ready is False
    assert q.INVENTORY_QUANTITY in gate.blocked
    # Its verified link does not compensate for the missing quantity.
    assert q.BOOKING_LINK in gate.verified


def test_flyai_is_discovery_only_in_both_layers():
    gate = q.reference_gate("flyai", room_name=None)
    assert gate.ready is False
    assert q.INVENTORY_QUANTITY in gate.blocked


@pytest.mark.parametrize("source", ["tuniu", "rollinggo", "flyai"])
def test_booking_layer_stays_blocked_for_every_source(source):
    gate = q.booking_gate(source)
    assert gate.ready is False
    assert gate.layer == q.BOOKING_LAYER
    assert gate.presumed == ()
    assert q.OCCUPANCY_ECHO in gate.blocked
    assert q.DEPOSIT_AND_FEES in gate.blocked


def test_averaged_total_is_presumed_not_quoted():
    total = q.presumed_stay_total_fen(19900, 2, 1, source_id="tuniu")
    assert total.amount_fen == 39800
    assert total.official_unit is False
    assert "推定" in total.label and "偏高" in total.label
    one_night = q.presumed_stay_total_fen(19900, 1, 1, source_id="tuniu")
    assert one_night.amount_fen == 19900


def test_rollinggo_total_keeps_its_official_unit():
    total = q.presumed_stay_total_fen(19900, 2, 2, source_id="rollinggo")
    assert total.amount_fen == 79600
    assert total.official_unit is True
    assert "并非逐夜实价" in total.label


@pytest.mark.parametrize("amount", [None, -1])
def test_missing_amount_never_produces_a_total(amount):
    total = q.presumed_stay_total_fen(amount, 2, 1, source_id="tuniu")
    assert total.amount_fen is None
    assert total.basis == "amount_missing"


def test_flyai_total_is_refused_for_an_unconfirmed_currency():
    total = q.presumed_stay_total_fen(19900, 1, 1, source_id="flyai")
    assert total.amount_fen is None
    assert total.basis == "currency_unconfirmed"


def test_strict_total_still_refuses_an_unstated_unit():
    """The strict helper keeps its old contract; presumption is opt-in."""
    assert q.stay_total_fen(19900, 2, 1, source_id="tuniu") is None
    assert q.stay_total_fen(19900, 2, 1, source_id="rollinggo") == 39800


def test_single_night_needs_no_nightly_breakdown():
    nightly = q.nightly_breakdown_presumption("tuniu", 1)
    assert nightly.satisfied is True
    assert nightly.basis == "not_required_single_night"


def test_matrix_status_allows_presumed_only_through_the_gate():
    """A presumable cell is still recorded as unknown in the raw matrix."""
    assert q.FIELD_STATUS["tuniu"][q.PRICE_INCLUDES_TAX][0] == "unknown"
    assert q.FIELD_STATUS["tuniu"][q.OCCUPANCY_ECHO][0] == "unsupported"


# --- r62: the gate must be renderable without further interpretation --------


def test_every_field_has_a_chinese_label_and_a_reason():
    for field in q.FIELD_LABELS:
        assert q.FIELD_LABELS[field]
        assert q.FIELD_NOTES[field], field
    # Every field a gate can emit must be nameable, or the page would show a code.
    for field in q.REQUIRED_FIELDS:
        assert field in q.FIELD_LABELS, field


def test_gate_items_keep_presumptions_out_of_the_verified_bucket():
    gate = q.reference_gate(
        "tuniu", remaining=5, snapshot_at=SNAPSHOT, room_name="标准双床房",
    )
    items = {row.field: row.status for row in q.gate_items(gate)}
    assert items[q.INVENTORY_QUANTITY] == "verified"
    assert items[q.ROOM_EXCLUSIVITY] == "presumed"
    assert items[q.PRIVATE_BATHROOM] == "presumed"
    # Deposit and link do not block a reference card, but must stay visible.
    assert items[q.DEPOSIT_AND_FEES] == "unknown"
    assert items[q.BOOKING_LINK] == "unknown"
    assert "verified" not in (items[q.ROOM_EXCLUSIVITY], items[q.PRIVATE_BATHROOM])


def test_gate_items_report_blocked_room_forms_explicitly():
    gate = q.reference_gate(
        "tuniu", remaining=5, snapshot_at=SNAPSHOT, room_name="青旅四人床位房",
    )
    items = {row.field: row.status for row in q.gate_items(gate)}
    assert items[q.ROOM_EXCLUSIVITY] == "blocked"
    assert items[q.PRIVATE_BATHROOM] == "blocked"
    # Stock can still be real while the room form disqualifies the quote.
    assert items[q.INVENTORY_QUANTITY] == "verified"
    assert gate.ready is False


def test_gate_items_are_stable_and_drop_nothing():
    gate = q.reference_gate(
        "tuniu", nights=2, rooms=2, remaining=1, snapshot_at=SNAPSHOT,
        room_name="标准双床房",
    )
    rows = q.gate_items(gate)
    fields = [row.field for row in rows]
    assert len(fields) == len(set(fields))
    emitted = set(gate.verified) | set(gate.presumed) | set(gate.blocked) | set(gate.caveats)
    assert set(fields) == emitted
    # Two rooms against one remaining unit must block inventory.
    assert {row.field: row.status for row in rows}[q.INVENTORY_QUANTITY] == "blocked"
    assert gate.ready is False


def test_gate_rows_carry_the_sentence_this_verdict_produced():
    gate = q.reference_gate(
        "tuniu", rooms=1, remaining=3,
        snapshot_at="2026-11-05T11:00:00+00:00", room_name="标准大床房",
    )
    rows = {row.field: row for row in q.gate_items(gate)}
    assert rows["inventory_quantity"].status == "verified"
    assert "剩余 3 间" in rows["inventory_quantity"].note
    assert "按整间过夜客房处理" in rows["room_exclusivity"].note


def test_presumption_accepts_the_timestamp_the_adapter_actually_emits():
    """r66 live run: real quotes were all blocked as "no observation time".

    The adapters emit ``datetime.now(timezone.utc).isoformat()``, i.e. with
    microseconds. A second-precision fixture hid this, so the reference layer
    could never pass on live Tuniu data.
    """
    from datetime import datetime, timezone

    emitted = datetime.now(timezone.utc).isoformat()
    assert "." in emitted  # the real format really does carry fractions
    combo = q.occupancy_combo(
        adults=2, rooms=1, remaining=3, snapshot_at=emitted,
    )
    assert combo.satisfied is True
    assert combo.basis == "request_params_plus_remaining_count_plus_snapshot"


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-11-07T00:33:50.123456+00:00",
        "2026-11-07T00:33:50.1Z",
        "2026-11-07T00:33:50+00:00",
        "2026-11-07T00:33:50",
        "2026-11-07",
    ],
)
def test_snapshot_formats_that_are_auditable(stamp):
    assert q.occupancy_combo(
        adults=2, rooms=1, remaining=3, snapshot_at=stamp,
    ).satisfied is True


@pytest.mark.parametrize(
    "stamp",
    ["2026-11-07T00:33:50.123456.", "2026-13-07", "not-a-time", "  ", "2026-11-07T00:33:50+8"],
)
def test_snapshot_formats_that_are_not_auditable(stamp):
    assert q.occupancy_combo(
        adults=2, rooms=1, remaining=3, snapshot_at=stamp,
    ).satisfied is False


# --- r67: the booking layer must be *called*, not merely available -----------

def test_runtime_link_failure_demotes_a_statically_verified_link():
    """RollingGo's table says "verified link" from r56; this response may not."""
    gate = q.booking_gate("rollinggo", link_matched=False, occupancy_echo_matched=False)
    assert gate.ready is False
    assert q.BOOKING_LINK in gate.blocked
    assert q.BOOKING_LINK not in gate.verified
    notes = dict(gate.notes)
    assert "未通过参数核对" in notes[q.BOOKING_LINK]
    assert "未回显一致" in notes[q.OCCUPANCY_ECHO]


def test_runtime_flags_can_only_demote_never_promote():
    """Passing True cannot make a source bookable; the table still blocks it."""
    gate = q.booking_gate("tuniu", link_matched=True, occupancy_echo_matched=True)
    assert gate.ready is False
    assert q.OCCUPANCY_ECHO in gate.blocked
    assert q.BOOKING_LINK in gate.blocked
    # Tuniu really does report remaining stock, so this row stays verified.
    assert q.INVENTORY_QUANTITY in gate.verified


def test_a_verified_row_never_borrows_another_sources_caveat():
    """RollingGo returns a link; printing "returns no link" next to it is a lie.

    This is the same misattribution class as r64's field notes, one layer up.
    """
    gate = q.booking_gate("rollinggo")
    assert q.BOOKING_LINK in gate.verified
    rows = {row.field: row for row in q.gate_items(gate)}
    link = rows[q.BOOKING_LINK]
    assert link.status == "verified"
    assert "不返回预订链接" not in link.note
    assert "原样预订链接" in link.note


@pytest.mark.parametrize("source", ["tuniu", "rollinggo", "flyai"])
def test_every_source_reports_its_booking_blockers_with_a_reason(source):
    """A blocked row without a sentence is a silent failure on the page."""
    rows = q.gate_items(q.booking_gate(source, nights=2, rooms=2))
    blocked = [row for row in rows if row.status == "blocked"]
    assert blocked, "no source may pass the booking layer yet"
    assert all(row.note for row in blocked)
    assert all(row.label for row in blocked)
