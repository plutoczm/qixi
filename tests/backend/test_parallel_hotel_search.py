from datetime import timedelta
import threading
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from stay_agent.contracts import (
    Coverage, HotelSummary, QuotesResponse, SearchInput, SearchResponse, Source, TravelPlacesResponse, MatchAssessment, china_today,
)
from stay_agent.main import create_app
from stay_agent.multi_source import SourceCandidate, SourceResult
from stay_agent.parallel_search import ParallelSearch, ParallelSearchRequest
from stay_agent.storage import SessionError, SessionStore
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore
import pytest


def request():
    today = china_today()
    return ParallelSearchRequest(operation_id=uuid4().hex, query=SearchInput(
        place="广州", check_in=today + timedelta(days=2), check_out=today + timedelta(days=3),
    ))


class Supplier:
    def __init__(self, source, join, delay=None):
        self.source = source
        self.join = join
        self.delay = delay
        self.quote_calls = []

    @property
    def identity(self):
        return Source(id=self.source, name={"rollinggo": "道旅", "tuniu": "途牛"}[self.source])

    def search(self, query, store, *, source_context=None):
        self.join()
        if self.delay:
            self.delay.wait(2)
        hotel = HotelSummary(id=1, name=self.source, address="广州越秀区中山五路1号")
        store.save(self.source, query.model_dump(mode="json"), [hotel.id],
                   [hotel.model_dump()], supplier_source=self.source)
        return SearchResponse(
            request_id=self.source, context=query, queried_at="2026-11-18T10:00:00+08:00",
            source=self.identity,
            status="complete", hotels=[hotel],
            coverage=Coverage(returned_count=1, requested_limit=query.limit, limited=False),
        )

    def quotes(self, request_id, hotel_id, query):
        self.quote_calls.append((request_id, hotel_id))
        return QuotesResponse(request_id=request_id, hotel_id=hotel_id, hotel_name=self.source,
                              status="complete", context=query, source=self.identity,
                              queried_at="2026-11-18T10:00:00+08:00")


def test_runs_three_sources_in_parallel_and_keeps_same_numeric_ids_separate(tmp_path):
    barrier = threading.Barrier(3, timeout=1)
    store = SessionStore(tmp_path / "search.db")

    def flyai(query, context):
        barrier.wait()
        return SourceResult(id="flyai", status="unconfigured", unavailable_reason="CONFIGURATION_MISSING")

    service = ParallelSearch(Supplier("rollinggo", barrier.wait), Supplier("tuniu", barrier.wait), flyai)
    result = service.search(request(), store)
    assert [(source.id, source.status) for source in result.sources] == [
        ("rollinggo", "complete"), ("tuniu", "complete"), ("flyai", "unconfigured"),
    ]
    assert result.sources[0].search.hotels[0].id == result.sources[1].search.hotels[0].id
    assert store.load("rollinggo", 1, include_source=True)["_supplier_source"] == "rollinggo"
    assert store.load("tuniu", 1, include_source=True)["_supplier_source"] == "tuniu"


def test_timeout_preserves_finished_source_without_publishing_late_session(tmp_path):
    delay = threading.Event()
    store = SessionStore(tmp_path / "search.db")
    service = ParallelSearch(
        Supplier("rollinggo", lambda: None), Supplier("tuniu", lambda: None, delay),
        lambda *_: SourceResult(id="flyai", status="complete"),
        deadline_seconds=0.15,
    )
    try:
        result = service.search(request(), store)
        assert [source.status for source in result.sources] == ["complete", "expired", "complete"]
        assert store.load("rollinggo", 1)
        with pytest.raises(SessionError, match="SEARCH_NOT_FOUND"):
            store.load("tuniu", 1)
    finally:
        delay.set()


def test_production_routes_publish_sessions_and_route_each_quote_to_its_source(tmp_path):
    rollinggo, tuniu = (Supplier(source, lambda: None) for source in ("rollinggo", "tuniu"))
    hint = SourceCandidate(source_id="flyai", source_hotel_id="private-shid", name="发现的酒店",
                           currency="unknown", price_scope="supplier_display_quote",
                           observed_at="2026-11-18T10:00:00+08:00", price_text="¥99")
    service = ParallelSearch(rollinggo, tuniu, lambda *_: SourceResult(
        id="flyai", status="complete", candidates=[hint],
    ))
    travel = Mock()
    app = create_app(
        travel_service=travel,
        service=rollinggo, parallel_service=service, session_store=SessionStore(tmp_path / "search.db"),
        conversation_service=ConversationService(ConversationStore(tmp_path / "conversations.db")),
        web_root=tmp_path,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        operation = request()
        response = client.post("/api/search/parallel", json=operation.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "complete"
        assert response.json()["sources"][2]["candidates"][0]["price_text"] == "¥99"
        assert "private-shid" not in response.text
        travel_request = {
            "request_id": "flyai:" + operation.operation_id, "city": "广州",
            "provider": "amap", "destination_keyword": "永庆坊",
        }
        travel.places.return_value = TravelPlacesResponse(
            request_id=travel_request["request_id"], hotel_id=1, hotel_name=hint.name,
            provider="amap", status="partial", queried_at=hint.observed_at,
            matching=MatchAssessment(status="not_found", explanation="地图没有返回酒店"),
            coverage_note="测试",
        )
        assert client.post("/api/hotels/2/travel/places", json=travel_request).status_code == 404
        travel.places.assert_not_called()
        response = client.post("/api/hotels/1/travel/places", json=travel_request)
        assert response.status_code == 200, response.text
        assert travel.places.call_args.args[:3] == (1, {"name": hint.name, "address": None}, operation.query)
        assert client.post("/api/hotels/1/quotes", json={"request_id": travel_request["request_id"]}).status_code == 404
        for supplier in (rollinggo, tuniu):
            response = client.post("/api/hotels/1/quotes", json={"request_id": supplier.source})
            assert response.status_code == 200, response.text
            assert response.json()["source"]["id"] == supplier.source
            assert supplier.quote_calls == [(supplier.source, 1)]
        cancelled = request()
        assert client.post(f"/api/search/parallel/{cancelled.operation_id}/cancel").status_code == 200
        response = client.post("/api/search/parallel", json=cancelled.model_dump(mode="json"))
        assert response.json()["status"] == "cancelled"
        assert all(item["search"] is None for item in response.json()["sources"])
