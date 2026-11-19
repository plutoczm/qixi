"""R105 authorized guest evidence, offline synthetic fixtures only."""

from datetime import datetime, timedelta, timezone
import pytest
from stay_agent.review_evidence import (
    ReviewCatalog,
    load_local_review_catalog,
    elong_document_example,
)

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def sample():
    grant = {
        "schema": 1,
        "supplier_source": "rollinggo",
        "source_name": "测试专用授权方",
        "grant_id": "TEST-GRANT",
        "agreement_ref": "TEST-NOT-REAL",
        "confirmed_by": "TEST",
        "expires_at": (NOW + timedelta(days=5)).isoformat(),
        "score_semantics": "verified_guest_mean_5",
        "count_semantics": "verified_stay_reviews",
        "allowed_uses": ["display_aggregates", "rank_hotels", "store_aggregates"],
    }
    feed = {
        "schema": 1,
        "grant_id": "TEST-GRANT",
        "hotels": [
            {
                "supplier_hotel_id": 101,
                "review_hotel_id": "test-review-1",
                "hotel_name": "花园酒店上海迪士尼店",
                "hotel_city": "上海",
                "hotel_address": "浦东新区繁荣路12号",
                "identity_evidence_ref": "TEST-MANUAL-ID",
                "guest_score": 4.6,
                "review_count": 137,
                "observed_at": (NOW - timedelta(days=2)).isoformat(),
            }
        ],
    }
    return grant, feed


def test_official_doc_ratio_is_not_guest_star_rating():
    assert elong_document_example({"ReviewCount": 3071, "ReviewScore": "0.98"}) == {
        "review_count": 3071,
        "guest_score": None,
    }


def test_default_production_has_no_review_data(monkeypatch):
    monkeypatch.delenv("XUNQI_REVIEW_GRANT_FILE", raising=False)
    monkeypatch.delenv("XUNQI_REVIEW_AGGREGATES_FILE", raising=False)
    assert load_local_review_catalog() is None


def test_grant_expiry_and_permission_fail_closed():
    g, f = sample()
    for bad in (
        {"allowed_uses": ["display_aggregates"]},
        {"score_semantics": "positive_ratio"},
        {"count_semantics": "all_comments"},
        {"agreement_ref": ""},
        {"expires_at": (NOW - timedelta(days=1)).isoformat()},
    ):
        assert ReviewCatalog.parse({**g, **bad}, f, now=NOW) is None
    assert ReviewCatalog.parse(g, {**f, "grant_id": "WRONG"}, now=NOW) is None


@pytest.mark.parametrize(
    "change",
    [
        {"guest_score": 99},
        {"guest_score": "NaN"},
        {"review_count": True},
        {"review_count": 0},
        {"observed_at": "2020-01-01T00:00:00Z"},
        {"review_hotel_id": ""},
    ],
)
def test_bad_aggregates_never_promote(change):
    g, f = sample()
    f["hotels"][0].update(change)
    assert ReviewCatalog.parse(g, f, now=NOW) is None


def test_unique_map_supplier_and_review_identity_required():
    g, f = sample()
    c = ReviewCatalog.parse(g, f, now=NOW)
    assert c is not None
    source = [{"id": 101, "name": "花园酒店上海迪士尼店", "address": "浦东新区繁荣路12号"}]
    row = {
        "name": "花园酒店上海迪士尼店",
        "address": "浦东新区繁荣路12号",
        "source_match": "matched",
        "source_hotel_id": 101,
    }
    assert c.apply([row], source, "上海", supplier_source="rollinggo", now=NOW) == 1
    assert row["guest_review"]["review_count"] == 137
    for bad in (
        {"source_match": "ambiguous"},
        {"source_hotel_id": 102},
        {"address": "另外一个地址"},
    ):
        other = {**row, **bad, "guest_review": None}
        assert c.apply([other], source, "上海", supplier_source="rollinggo", now=NOW) == 0
        assert other["guest_review"] is None


def test_duplicate_source_id_ignored():
    g, f = sample()
    f["hotels"].append({**f["hotels"][0], "review_hotel_id": "different"})
    assert ReviewCatalog.parse(g, f, now=NOW) is None


def test_duplicate_review_id_rejects_all_mappings():
    g, f = sample()
    f["hotels"].append({**f["hotels"][0], "supplier_hotel_id": 102})
    assert ReviewCatalog.parse(g, f, now=NOW) is None


def test_malformed_permission_array_is_unavailable():
    g, f = sample()
    g["allowed_uses"] = [{}]
    assert ReviewCatalog.parse(g, f, now=NOW) is None


def test_loaded_catalog_checks_expiry_and_supplier_on_every_apply():
    g, f = sample()
    c = ReviewCatalog.parse(g, f, now=NOW)
    source = [{"id": 101, "name": "花园酒店上海迪士尼店", "address": "浦东新区繁荣路12号"}]
    row = {**source[0], "source_match": "matched", "source_hotel_id": 101}
    for supplier, now in [("tuniu", NOW), ("rollinggo", NOW + timedelta(days=6))]:
        assert c.apply([row], source, "上海", supplier_source=supplier, now=now) == 0
        assert "guest_review" not in row


def test_local_loader_valid_json_and_revocation(tmp_path, monkeypatch):
    import json
    import stay_agent.review_evidence as module

    # Isolate all files; never read the real runtime grant or aggregates.
    monkeypatch.setattr(module, "__file__", str(tmp_path / "backend/stay_agent/review_evidence.py"))
    base = tmp_path / "runtime/reviews"
    base.mkdir(parents=True)
    grant, feed = sample()
    now = datetime.now(timezone.utc)
    grant["expires_at"] = (now + timedelta(days=1)).isoformat()
    feed["hotels"][0]["observed_at"] = now.isoformat()
    gp, fp = base / "grant.json", base / "feed.json"
    gp.write_text(json.dumps(grant), encoding="utf-8")
    fp.write_text(json.dumps(feed), encoding="utf-8")
    monkeypatch.setenv("XUNQI_REVIEW_GRANT_FILE", str(gp))
    monkeypatch.setenv("XUNQI_REVIEW_AGGREGATES_FILE", str(fp))
    assert load_local_review_catalog() is not None
    gp.write_text('{"schema":1,"schema":1}', encoding="utf-8")
    assert load_local_review_catalog() is None
    monkeypatch.setenv("XUNQI_REVIEW_GRANT_FILE", str(tmp_path / "outside.json"))
    assert load_local_review_catalog() is None


def test_nearby_service_applies_catalog_without_extra_provider_calls():
    from test_poi_nearby import MapStub, SourceSessions
    from stay_agent.poi_nearby import NearbyPlaceService
    from stay_agent.contracts import (
        NearbyResolveRequest,
        NearbyExploreRequest,
        NearbyExploreResponse,
    )

    now = datetime.now(timezone.utc)
    g, f = sample()
    g["expires_at"] = (now + timedelta(days=1)).isoformat()
    f["hotels"][0]["observed_at"] = now.isoformat()
    catalog = ReviewCatalog.parse(g, f, now=now)
    maps = MapStub()
    svc = NearbyPlaceService(
        factories={"amap": lambda: maps},
        data_mode="test",
        review_catalog=catalog,
        supplier_source="rollinggo",
    )
    resolved = svc.resolve(NearbyResolveRequest(provider="amap", city="上海", keyword="迪士尼"))

    class BoundSessions(SourceSessions):
        def list_hotels(self, request_id):
            query, rows = super().list_hotels(request_id)
            return {**query, "_supplier_source": "rollinggo"}, rows

    response = svc.explore(
        NearbyExploreRequest(
            context_id=resolved["context_id"],
            anchor_id=resolved["options"][0]["id"],
            request_id="test-search-1",
        ),
        BoundSessions(),
    )
    result = NearbyExploreResponse.model_validate(response)
    matched = next(h for h in result.hotels if h.source_match == "matched")
    assert matched.guest_review.score == 4.6
    assert matched.review_count == 137 and matched.stayed_count is None
    assert len(maps.calls) == 2


@pytest.mark.parametrize("source", [None, "tuniu"])
def test_legacy_or_other_supplier_session_cannot_receive_reviews(source):
    from test_poi_nearby import MapStub, SourceSessions
    from stay_agent.poi_nearby import NearbyPlaceService
    from stay_agent.contracts import NearbyResolveRequest, NearbyExploreRequest

    now = datetime.now(timezone.utc)
    grant, feed = sample()
    grant["expires_at"] = (now + timedelta(days=1)).isoformat()
    feed["hotels"][0]["observed_at"] = now.isoformat()
    catalog = ReviewCatalog.parse(grant, feed, now=now)

    class BoundSessions(SourceSessions):
        def list_hotels(self, request_id):
            query, rows = super().list_hotels(request_id)
            return {**query, "_supplier_source": source}, rows

    maps = MapStub()
    svc = NearbyPlaceService(
        factories={"amap": lambda: maps},
        data_mode="test",
        review_catalog=catalog,
        supplier_source="rollinggo",
    )
    resolved = svc.resolve(NearbyResolveRequest(provider="amap", city="上海", keyword="迪士尼"))
    result = svc.explore(
        NearbyExploreRequest(
            context_id=resolved["context_id"],
            anchor_id=resolved["options"][0]["id"],
            request_id="test-search-1",
        ),
        BoundSessions(),
    )
    assert all(h.get("guest_review") is None for h in result["hotels"])


def test_session_source_provenance_survives_reload_but_is_not_query_input(tmp_path):
    from stay_agent.storage import SessionStore

    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    store.save("r105", {"place": "上海"}, [101], supplier_source="rollinggo")
    restored = SessionStore(path)
    assert restored.list_hotels("r105")[0]["_supplier_source"] == "rollinggo"
    assert restored.load("r105", 101) == {"place": "上海"}
