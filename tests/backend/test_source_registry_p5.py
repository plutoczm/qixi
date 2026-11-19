"""P5/r61: the source registry must stay a truthful, exhaustive list."""

from __future__ import annotations

import pytest

from stay_agent import source_registry as reg


def test_every_record_declares_a_known_kind_and_status():
    for record in reg.REGISTRY:
        assert record.kind in reg.KINDS, record.source_id
        assert record.status in reg.STATUSES, record.source_id
        assert record.display_name and record.channel
        assert record.provides, record.source_id
        # A channel with no stated limit is a channel that will be misused.
        assert record.cannot, record.source_id
        assert record.evidence.startswith("docs/")


def test_source_ids_are_unique():
    ids = [record.source_id for record in reg.REGISTRY]
    assert len(ids) == len(set(ids))


def test_only_quote_sources_may_carry_a_price():
    assert {r.source_id for r in reg.quote_sources()} == {"tuniu", "rollinggo"}
    for record in reg.REGISTRY:
        if record.kind in ("discovery", "manual", "map", "transit", "llm"):
            assert record.kind != "quote"


def test_flyai_is_discovery_only_and_never_a_price_source():
    record = reg.by_id("flyai")
    assert record is not None
    assert record.kind == "discovery"
    assert record not in reg.quote_sources()
    text = " ".join(record.cannot)
    assert "币种" in text
    assert "房型" in text
    assert "跳转壳" in text


def test_meituan_is_registered_as_manual_not_as_a_discovery_source():
    record = reg.by_id("meituan")
    assert record is not None
    assert record.kind == "manual"
    assert record.status == "needs_credential"
    assert record not in reg.discovery_sources()
    assert record not in reg.quote_sources()
    # The redacted price placeholder is the reason it can never be a quote.
    assert any("脱敏" in line for line in record.cannot)


def test_manual_channels_are_listed_so_the_user_still_gets_a_route():
    manual = {r.source_id for r in reg.manual_sources()}
    assert {"meituan", "ctrip", "qunar"} <= manual


def test_unknown_source_is_none_rather_than_a_guess():
    assert reg.by_id("booking") is None
    assert reg.by_id("") is None


def test_summary_counts_every_record_exactly_once():
    counts = reg.summary()
    assert set(counts) == set(reg.STATUSES)
    assert sum(counts.values()) == len(reg.REGISTRY)


def test_default_enabled_quote_source_is_rollinggo_only():
    enabled_quotes = [
        r.source_id for r in reg.quote_sources() if r.status == "enabled"
    ]
    assert enabled_quotes == ["rollinggo"]


@pytest.mark.parametrize("source_id", ["tuniu", "rollinggo", "flyai"])
def test_quote_and_discovery_sources_name_their_credential(source_id):
    record = reg.by_id(source_id)
    assert record is not None
    assert record.credential and record.credential != "—"
