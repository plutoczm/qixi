"""Manual Tuniu source pagination, no live account/HTTP and no supplier queryId leaks."""
import json

from fastapi.testclient import TestClient
import pytest

from stay_agent.contracts import SearchInput
from stay_agent.providers.data.tuniu import TuniuProvider, TuniuFailure
from stay_agent.service import QuoteService
from stay_agent.main import create_app
from tests.backend.test_tuniu_default import app_with_source, search_body
from tests.backend.test_tuniu_offline import search_row


CURSOR = "synthetic-secret-queryId-DO-NOT-SEND-TO-BROWSER"


class PagedHotelTools:
    def __init__(self, *, first=None, second=None, third=None):
        self.calls = []
        self.first = first if first is not None else []
        self.second = second if second is not None else [search_row(hotel_id=72)]
        self.third = third if third is not None else [search_row(hotel_id=73, name="a distinct hotel")]

    def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        assert name == "tuniuHotelSearch", "This suite never queries detail or orders"
        page = args.get("pageNum", 1)
        if page == 1:
            assert "queryId" not in args
            records = self.first
        elif page in (2, 3):
            assert args == {"queryId": CURSOR, "pageNum": page}
            records = self.second if page == 2 else self.third
        else:
            raise AssertionError("unexpected source page")
        return {"structuredContent": {
            "success": True, "queryId": CURSOR,
            "currentPageNum": page, "totalPageNum": 3, "hotels": records,
        }}


def test_empty_first_page_explicit_second_and_third_same_request_membership(tmp_path):
    source = PagedHotelTools()
    client, _, store = app_with_source(tmp_path, source)
    with client:
        first = client.post("/api/search", json=search_body()).json()
        request_id = first["request_id"]
        assert first["current_page"] == 1 and first["next_page"] == 2
        assert first["hotels"] == [] and first["status"] == "partial"
        assert CURSOR not in json.dumps(first)
        assert len(source.calls) == 1

        second = client.post("/api/search/next", json={
            "request_id": request_id, "expected_page": 2,
        })
        assert second.status_code == 200
        response = second.json()
        assert response["current_page"] == 2
        assert response["next_page"] == 3
        assert response["request_id"] == request_id
        assert [h["id"] for h in response["hotels"]] == [72]
        assert CURSOR not in json.dumps(response)
        assert store.load(request_id, 72)["place"] == "广州"

        third = client.post("/api/search/next", json={
            "request_id": request_id, "expected_page": 3,
        }).json()
        assert third["current_page"] == 3 and third["next_page"] is None
        assert [h["id"] for h in third["hotels"]] == [72, 73]
        assert len(source.calls) == 3
        assert source.calls[1][1] == {"queryId": CURSOR, "pageNum": 2}
        assert source.calls[2][1] == {"queryId": CURSOR, "pageNum": 3}


def test_duplicate_old_page_and_wrong_page_rejected_without_source_call(tmp_path):
    source = PagedHotelTools()
    client, _, _ = app_with_source(tmp_path, source)
    with client:
        first = client.post("/api/search", json=search_body()).json()
        rid = first["request_id"]
        bad = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 3,
        })
        assert bad.status_code == 409
        assert len(source.calls) == 1
        invalid = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 4,
        })
        assert invalid.status_code == 422
        assert len(source.calls) == 1
        ok = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 2,
        })
        assert ok.status_code == 200
        again = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 2,
        })
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "PAGE_UNAVAILABLE"
        assert len(source.calls) == 2


def test_malformed_query_id_never_exposes_page_endpoint(tmp_path):
    class UnsafeCursor(PagedHotelTools):
        def call_tool(self, name, args):
            result = super().call_tool(name, args)
            result["structuredContent"]["queryId"] = "\nDANGER\n"
            return result

    source = UnsafeCursor()
    client, _, _ = app_with_source(tmp_path, source)
    with client:
        first = client.post("/api/search", json=search_body()).json()
        assert first["status"] == "partial"
        assert first["next_page"] is None
        result = client.post("/api/search/next", json={
            "request_id": first["request_id"], "expected_page": 2,
        })
        assert result.status_code == 409
        assert len(source.calls) == 1


def test_wrong_source_echo_is_rejected_and_cannot_be_replayed(tmp_path):
    class WrongEcho(PagedHotelTools):
        def call_tool(self, name, args):
            answer = super().call_tool(name, args)
            if args.get("pageNum") == 2:
                answer["structuredContent"]["queryId"] = "not-the-original-query"
            return answer

    source = WrongEcho()
    client, _, store = app_with_source(tmp_path, source)
    with client:
        first = client.post("/api/search", json=search_body()).json()
        rid = first["request_id"]
        bad = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 2,
        })
        assert bad.status_code == 409
        assert bad.json()["error"]["code"] == "CONTEXT_MISMATCH"
        again = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 2,
        })
        assert again.status_code == 409
        assert len(source.calls) == 2
        with pytest.raises(Exception):
            store.load(rid, 72)


def test_cross_page_hotel_id_conflict_is_removed_from_membership(tmp_path):
    source = PagedHotelTools(first=[search_row()],
                             second=[search_row(hotel_id=71, name="different identity")])
    client, _, store = app_with_source(tmp_path, source)
    with client:
        first = client.post("/api/search", json=search_body()).json()
        rid = first["request_id"]
        assert [h["id"] for h in first["hotels"]] == [71]
        second = client.post("/api/search/next", json={
            "request_id": rid, "expected_page": 2,
        }).json()
        assert second["hotels"] == []
        assert second["status"] == "partial"
        with pytest.raises(Exception):
            store.load(rid, 71)


def test_page_request_missing_search_cannot_call_supplier(tmp_path):
    source = PagedHotelTools()
    client, _, _ = app_with_source(tmp_path, source)
    with client:
        result = client.post("/api/search/next", json={
            "request_id": "rollinggo-old", "expected_page": 2,
        })
        assert result.status_code == 409
        assert len(source.calls) == 0


def test_limit_reached_disables_manual_page_without_hidden_fetch(tmp_path):
    source = PagedHotelTools(first=[search_row()])
    client, _, _ = app_with_source(tmp_path, source)
    with client:
        first = client.post("/api/search", json=search_body(limit=1)).json()
        assert first["next_page"] is None
        failed = client.post("/api/search/next", json={
            "request_id": first["request_id"], "expected_page": 2,
        })
        assert failed.status_code == 409
        assert len(source.calls) == 1


def test_original_rollinggo_provider_rejects_tuniu_paging(tmp_path):
    from stay_agent.storage import SessionStore
    app = create_app(
        service=QuoteService(), session_store=SessionStore(tmp_path / "old.sqlite3"),
        web_root=tmp_path / "web",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/search/next", json={
            "request_id": "old-search", "expected_page": 2,
        })
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SOURCE_WORKFLOW_UNAVAILABLE"


def test_page_projection_enforces_exact_page_and_source_query_id():
    conditions = SearchInput(**search_body())
    source = PagedHotelTools()
    projector = TuniuProvider(source.call_tool, mode="offline_test")
    first = projector.search(conditions, city="广州")
    assert first.query_id == CURSOR
    with pytest.raises(TuniuFailure):
        projector.page(first, page_num=3)
    next_page = projector.page(first, page_num=2)
    assert next_page.current_page == 2
    assert next_page.request_id == first.request_id
    assert len(source.calls) == 2


def test_same_hotel_new_price_on_another_page_keeps_original_observation(tmp_path):
    first_row = search_row(hotel_id=71, lowestPrice=180)
    second_row = search_row(hotel_id=71, lowestPrice=230)
    source = PagedHotelTools(first=[first_row], second=[second_row])
    client, _, store = app_with_source(tmp_path, source)
    with client:
        before = client.post("/api/search", json=search_body()).json()
        assert before["hotels"][0]["reference_price_fen"] == 18000
        after = client.post("/api/search/next", json={
            "request_id": before["request_id"], "expected_page": 2,
        }).json()
        assert [hotel["id"] for hotel in after["hotels"]] == [71]
        assert after["hotels"][0]["reference_price_fen"] == 18000
        assert store.load(before["request_id"], 71)["place"] == "广州"
