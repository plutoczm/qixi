"""AC14: the bed-place relaxation the user explicitly asks for.

AC14 reads "default is whole-room candidates only; only after the user
explicitly accepts a bed place does the room form relax". Three properties are
worth pinning, because each one is a way the feature could lie:

1. Default must still exclude a dormitory bed. Accepting is a user decision,
   never a silent default.
2. Acceptance relaxes exactly two conditions and only for a name that says it
   is a bed place -- it must not widen an ordinary hotel room.
3. The relaxed rows are reported as ``accepted``, never as verified or as our
   own presumption, and the money is multiplied per person, not per room.
"""

from stay_agent import pricing as p
from stay_agent.qualification import gate_items, reference_gate

BED = "青旅六人床位房（公共卫浴）"
DORM_WITHOUT_BED = "青年旅舍多人间"
ORDINARY = "独卫大床房"
SNAPSHOT = "2026-11-07T12:00:00+00:00"


def base(**overrides):
    values = dict(
        source_id="tuniu",
        room_name=BED,
        amount_fen=6000,
        price_unit="unstated",
        remaining=5,
        nights=1,
        rooms=1,
        adults=2,
    )
    values.update(overrides)
    return p.QuoteInput(**values)


def rows(assessment):
    return {row.condition: row for row in assessment.conditions}


# --- default stays strict ---------------------------------------------------

def test_ac14_default_still_excludes_a_dormitory_bed():
    result = p.assess(base())
    assert result.tier == "excluded"
    assert rows(result)["room_form"].verdict == "fail"
    assert rows(result)["private_bathroom"].verdict == "fail"
    # Nothing was accepted, so nothing may be reported as accepted.
    assert not any(row.accepted for row in result.conditions)


def test_ac14_accepting_is_off_unless_the_query_says_so():
    assert p.QuoteInput(source_id="tuniu", room_name=BED, amount_fen=6000).accepts_dormitory is False


# --- acceptance relaxes exactly two rows ------------------------------------

def test_ac14_acceptance_relaxes_room_form_and_bathroom_only():
    result = p.assess(base(accepts_dormitory=True))
    relaxed = {row.condition for row in result.conditions if row.accepted}
    assert relaxed == {"room_form", "private_bathroom"}
    assert rows(result)["room_form"].verdict == "pass"
    assert rows(result)["private_bathroom"].verdict == "pass"


def test_ac14_relaxed_rows_are_neither_verified_nor_presumed():
    result = p.assess(base(accepts_dormitory=True))
    for row in result.conditions:
        if row.accepted:
            # "The supplier confirmed it" and "we guessed it" are both false
            # here: the user chose it for this query.
            assert row.presumed is False
            assert row.basis.startswith("user_accepted")


def test_ac14_acceptance_never_widens_an_ordinary_room():
    result = p.assess(base(room_name=ORDINARY, accepts_dormitory=True))
    assert rows(result)["room_form"].verdict == "pass"
    assert rows(result)["private_bathroom"].verdict == "pass"
    # The ordinary room passes on the approved domestic default, not because
    # the user accepted a bed.
    assert not any(row.accepted for row in result.conditions)
    assert rows(result)["room_form"].presumed is True


def test_ac14_a_missing_room_name_cannot_be_relaxed():
    result = p.assess(base(room_name=None, accepts_dormitory=True))
    assert rows(result)["room_form"].verdict == "fail"
    assert rows(result)["private_bathroom"].verdict == "fail"
    assert not any(row.accepted for row in result.conditions)


def test_ac14_acceptance_does_not_clear_other_failed_conditions():
    result = p.assess(base(accepts_dormitory=True, remaining=0))
    assert rows(result)["inventory"].verdict == "fail"
    assert result.tier == "excluded"


# --- money: a bed is priced per person --------------------------------------

def test_ac14_bed_price_is_multiplied_per_person_not_per_room():
    result = p.assess(base(accepts_dormitory=True, adults=2, rooms=1, nights=1))
    charge = next(line for line in result.fees.lines if line.kind == "room_charge")
    assert result.fees.room_charge_fen == 6000 * 2
    assert charge.per == "person_night"
    assert charge.counted is True


def test_ac14_bed_price_scales_with_nights_and_people():
    result = p.assess(base(accepts_dormitory=True, adults=2, rooms=2, nights=3))
    # Two rooms of two adults = four beds per night, three nights.
    assert result.fees.room_charge_fen == 6000 * 3 * 4


def test_ac14_bed_price_note_says_it_is_a_presumption():
    result = p.assess(base(accepts_dormitory=True))
    charge = next(line for line in result.fees.lines if line.kind == "room_charge")
    assert "你已接受床位" in charge.note
    assert "偏高" in charge.note


def test_ac14_a_stated_unit_beats_the_bed_presumption():
    result = p.assess(base(accepts_dormitory=True, price_unit="per_room_night"))
    # The supplier described its own number; our per-person reading must not
    # override it.
    assert result.fees.room_charge_fen == 6000


def test_ac14_dormitory_without_the_word_bed_is_priced_as_unknown():
    result = p.assess(base(room_name=DORM_WITHOUT_BED, accepts_dormitory=True))
    assert result.fees.room_charge_fen is None
    assert any("房费计价口径未定" in item for item in result.fees.unknown_items)
    # The room form itself is relaxed; only the money refuses to be guessed.
    assert rows(result)["room_form"].accepted is True


def test_ac14_unknown_bed_price_never_becomes_zero():
    result = p.assess(base(room_name=DORM_WITHOUT_BED, accepts_dormitory=True))
    assert result.fees.total_known_fen is None
    assert result.tier == "reference"


# --- reference gate ---------------------------------------------------------

def gate(**overrides):
    values = dict(
        nights=1, rooms=1, adults=2, remaining=5, snapshot_at=SNAPSHOT,
        room_name=BED,
    )
    values.update(overrides)
    return reference_gate("tuniu", **values)


def test_ac14_reference_gate_blocks_a_bed_by_default():
    result = gate()
    assert "room_exclusivity" in result.blocked
    assert "private_bathroom" in result.blocked
    assert result.ready is False


def test_ac14_reference_gate_reports_accepted_not_blocked():
    result = gate(accepts_dormitory=True)
    assert set(result.accepted) == {"room_exclusivity", "private_bathroom"}
    assert "room_exclusivity" not in result.blocked
    assert "private_bathroom" not in result.blocked
    assert result.ready is True


def test_ac14_gate_rows_surface_the_accepted_status_to_the_page():
    items = {item.field: item for item in gate_items(gate(accepts_dormitory=True))}
    assert items["room_exclusivity"].status == "accepted"
    assert "你已明确接受床位" in items["room_exclusivity"].note
    assert items["private_bathroom"].status == "accepted"


def test_ac14_gate_does_not_accept_a_bed_for_an_ordinary_room():
    result = gate(room_name=ORDINARY, accepts_dormitory=True)
    assert result.accepted == ()
    assert "room_exclusivity" in result.presumed


def test_ac14_booking_layer_never_accepts_a_relaxed_condition():
    # Accepting a bed is the user's decision, not supplier evidence, so the
    # booking layer must stay exactly as blocked as before.
    from stay_agent.qualification import booking_gate

    result = booking_gate("tuniu", nights=1, rooms=1)
    assert result.ready is False


# --- wire contract ----------------------------------------------------------

def test_ac14_wire_keeps_accepted_separate_from_presumed():
    from stay_agent.contracts import Qualification

    wire = Qualification.from_assessment(p.assess(base(accepts_dormitory=True)))
    flags = {row.condition: (row.presumed, row.accepted) for row in wire.conditions}
    assert flags["room_form"] == (False, True)
    assert flags["private_bathroom"] == (False, True)


def test_ac14_search_input_defaults_to_not_accepting():
    from stay_agent.contracts import SearchInput

    assert SearchInput.model_validate(dict(place="广州", check_in="2026-11-13", check_out="2026-11-14"), context={"today": __import__("datetime").date(2026,11,10)}).accepts_dormitory is False
