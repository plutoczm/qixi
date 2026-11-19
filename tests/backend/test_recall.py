"""Same recall pipeline with injected, explicitly synthetic source adapters."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from stay_agent.contracts import RecallInput, SearchInput, china_today
from stay_agent.recall import RecallService, RecallError
from stay_agent.service import QuoteService
from stay_agent.storage import SessionStore
from stay_agent.providers.data.rollinggo_client import RollingGoError
from stay_agent.providers.data.travel_clients import ProbeError


def query(**kwargs):
    day = china_today() + timedelta(days=7)
    return SearchInput(
        place="广州",
        check_in=day,
        check_out=day + timedelta(days=2),
        rooms=2,
        children_ages=[5],
        budget_fen=20000,
        **kwargs,
    )


def request(**kwargs):
    return RecallInput(operation_id=uuid4().hex, query=query(), **kwargs)


def points(start, count):
    return [
        SimpleNamespace(poi_id=str(i), title=f"测试餐厅{i}店") for i in range(start, start + count)
    ]


class Maps:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.metadata = {}
        self.last_search = {}

    def search(self, keyword, city, page, page_size):
        self.calls.append(page)
        rows = self.pages[page - 1] if page <= len(self.pages) else []
        if isinstance(rows, Exception):
            raise rows
        self.metadata.update(
            {p.poi_id: {"address": f"测试路{p.poi_id}号", "city": "广州市", "typecode": "050117", "category": "美食:火锅"} for p in rows}
        )
        self.last_search = {"raw_count": len(rows), "valid_count": len(rows), "total_hint": None}
        return rows


class Quotes:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.arguments = []
        self.request_count = 0

    def call_tool(self, name, arguments):
        self.request_count += 1
        self.arguments.append(arguments)
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value()
        return {"structuredContent": {"success": True, "hotelInformationList": value}}

    def redact(self, value):
        return value


def hotel(ident, name=None, address=None, price=100):
    return {
        "hotelId": ident,
        "name": name or f"测试酒店{ident}",
        "address": address or f"测试街{ident}号",
        "price": {"hasPrice": True, "currency": "CNY", "lowestPrice": price},
    }


def setup(tmp_path, pages, outputs, clock=None):
    maps = Maps(pages)
    source = Quotes(outputs)
    svc = RecallService(
        quote_service=QuoteService(),
        map_factory=lambda provider, deadline: maps,
        quote_factory=lambda deadline: source,
        clock=clock or __import__("time").monotonic,
        data_mode="test",
    )
    store = SessionStore(tmp_path / "search.db")
    return svc, maps, source, store


@pytest.mark.parametrize("provider", ["amap", "tencent"])
def test_second_third_branches_get_equal_queries_and_context_is_not_lost(tmp_path, provider):
    svc, maps, source, store = setup(tmp_path, [points(1, 3)], [[hotel(1)], [hotel(2)], [hotel(3)]])
    req = request(provider=provider)
    response = svc.search(req, store)
    assert response.status == "complete" and len(response.hotels) == 3
    assert response.recall_coverage.queried_branches == 3
    assert len(source.arguments) == 3 and maps.calls == [1]
    for index, argument in enumerate(source.arguments, 1):
        assert f"测试餐厅{index}店" in argument["place"] and argument["placeType"] == "详细地址"
        assert (
            argument["checkInParam"]["stayNights"] == 2
            and argument["hotelTags"]["maxPricePerNight"] == 200
        )
    assert store.load(response.request_id, 2)["children_ages"] == [5]
    assert (
        store.load(response.request_id, 2)["rooms"] == 2
        and store.hotel(response.request_id, 2)["name"] == "测试酒店2"
    )


def test_two_pages_cap_branch_budget_and_explicit_uncovered_reasons(tmp_path):
    svc, maps, source, store = setup(
        tmp_path, [points(1, 10), points(11, 10)], [[hotel(1)], [hotel(2)], [hotel(3)]]
    )
    result = svc.search(request(), store)
    assert maps.calls == [1, 2] and len(result.branches) == 20
    assert (
        result.status == "partial" and result.recall_coverage.discovery_stop_reason == "page_limit"
    )
    assert (
        sum(b.reason == "branch_budget" for b in result.branches) == 17
        and len(source.arguments) == 3
    )


def test_repeated_last_page_stops_without_duplicate_branches(tmp_path):
    page = points(1, 10)
    svc, maps, source, store = setup(tmp_path, [page, page], [[hotel(1)], [hotel(2)], [hotel(3)]])
    result = svc.search(request(), store)
    assert (
        len(result.branches) == 10
        and result.recall_coverage.discovery_stop_reason == "repeated_page"
    )
    assert result.recall_coverage.raw_places == 20


def test_union_interleaves_and_merges_only_same_id_with_exact_identity(tmp_path):
    svc, _, _, store = setup(
        tmp_path,
        [points(1, 3)],
        [[hotel(7), hotel(8)], [hotel(7), hotel(9)], [hotel(8, name="同编号冲突分店"), hotel(10)]],
    )
    result = svc.search(request(), store)
    assert [h.id for h in result.hotels] == [7, 9, 10]
    assert (
        result.recall_coverage.duplicate_hotels == 1
        and result.recall_coverage.identity_conflicts == 1
    )
    assert 8 not in result.branches[2].hotel_ids
    assert result.branches[0].hotel_ids == [7] and result.branches[1].hotel_ids == [7, 9]


def test_same_name_different_provider_ids_stay_separate(tmp_path):
    svc, _, _, store = setup(
        tmp_path, [points(1, 2)], [[hotel(1, name="同名")], [hotel(2, name="同名")]]
    )
    assert len(svc.search(request(), store).hotels) == 2


def test_one_failed_branch_keeps_other_branches_and_empty_is_not_failure(tmp_path):
    svc, _, _, store = setup(tmp_path, [points(1, 3)], [RollingGoError("TIMEOUT"), [], [hotel(3)]])
    result = svc.search(request(), store)
    assert result.status == "partial" and [b.status for b in result.branches] == [
        "unavailable",
        "complete",
        "complete",
    ]
    assert result.branches[1].returned_count == 0 and [h.id for h in result.hotels] == [3]


def test_all_sources_failed_is_unavailable_not_a_successful_empty_search(tmp_path):
    svc, _, _, store = setup(tmp_path, [points(1, 3)], [RollingGoError("TIMEOUT")] * 3)
    assert svc.search(request(), store).status == "unavailable"


def test_auth_failure_stops_remaining_quote_calls_without_fallback(tmp_path):
    svc, _, source, store = setup(tmp_path, [points(1, 3)], [RollingGoError("ACCESS_DENIED")])
    result = svc.search(request(), store)
    assert len(source.arguments) == 1 and result.status == "unavailable"
    assert result.branches[1].reason == "quote_source_blocked"


def test_later_map_page_failure_preserves_first_page_candidates(tmp_path):
    svc, _, _, store = setup(
        tmp_path, [points(1, 10), ProbeError("TIMEOUT")], [[hotel(1)], [hotel(2)], [hotel(3)]]
    )
    result = svc.search(request(), store)
    assert result.status == "partial" and len(result.hotels) == 3
    assert result.recall_coverage.discovery_stop_reason == "source_failure"


def test_cancel_before_start_tombstone_prevents_any_source_calls(tmp_path):
    svc, maps, source, store = setup(tmp_path, [points(1, 3)], [])
    req = request()
    svc.cancel(req.operation_id)
    result = svc.search(req, store)
    assert result.error.code == "CANCELLED" and not maps.calls and not source.arguments


def test_mid_query_cancel_never_starts_next_branch_or_commits_union(tmp_path):
    svc, _, source, store = setup(tmp_path, [points(1, 3)], [])
    req = request()

    def stop():
        svc.cancel(req.operation_id)
        return [hotel(1)]

    source.outputs = iter([stop])
    result = svc.search(req, store)
    assert len(source.arguments) == 1 and not result.hotels and result.error.code == "CANCELLED"
    from stay_agent.storage import SessionError

    with pytest.raises(SessionError):
        store.load(result.request_id, 1)


def test_deadline_keeps_previous_results_and_skips_next_calls(tmp_path):
    clock = [0.0]
    svc, _, source, store = setup(tmp_path, [points(1, 3)], [], clock=lambda: clock[0])

    def slow():
        clock[0] = 61.0
        return [hotel(2)]

    source.outputs = iter([[hotel(1)], slow])
    result = svc.search(request(), store)
    assert result.status == "partial" and [h.id for h in result.hotels] == [1]
    assert len(source.arguments) == 2 and result.branches[2].reason == "deadline"
    assert result.error.code == "TIMEOUT"


def test_duplicate_operation_is_rejected_not_replayed(tmp_path):
    svc, _, source, store = setup(tmp_path, [[]], [])
    req = request()
    svc.search(req, store)
    with pytest.raises(RecallError, match="RECALL_OPERATION_CONFLICT"):
        svc.search(req, store)
    assert not source.arguments


def test_api_contract_and_local_guards(tmp_path):
    from stay_agent.main import create_app

    svc, _, _, store = setup(tmp_path, [points(1, 3)], [[hotel(1)], [hotel(2)], [hotel(3)]])
    conv = SimpleNamespace(recover=lambda: None, close=lambda: None)
    app = create_app(
        session_store=store,
        service=QuoteService(),
        recall_service=svc,
        conversation_service=conv,
        web_root=tmp_path / "web",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/recall", json=request().model_dump(mode="json"))
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json()["data_mode"] == "test"
        assert client.post("/api/recall/not-an-id/cancel").status_code == 422


def test_first_source_reference_is_preserved_even_when_union_order_interleaves(tmp_path):
    svc, _, _, store = setup(
        tmp_path, [points(1, 2)], [[hotel(1), hotel(2, price=150)], [hotel(2, price=90)]]
    )
    result = svc.search(request(), store)
    assert next(h for h in result.hotels if h.id == 2).reference_price_fen == 15000


def test_shared_transport_deadline_cannot_be_extended_and_expired_cannot_call(monkeypatch):
    from stay_agent.providers.data.rollinggo_client import RollingGoClient

    monkeypatch.setattr("stay_agent.providers.data.rollinggo_client.time.monotonic", lambda: 100)
    client = RollingGoClient("synthetic-test-key")
    client.limit_deadline(90)
    client.limit_deadline(200)
    with pytest.raises(RollingGoError, match="TIMEOUT"):
        client.discover()
    assert client.request_count == 0


def test_identical_branch_sets_warn_without_extra_filler_calls(tmp_path):
    svc, _, source, store = setup(tmp_path, [points(1, 3)], [[hotel(1), hotel(2)]] * 3)
    result = svc.search(request(), store)
    assert len(source.arguments) == 3 and len(result.hotels) == 2
    assert result.recall_coverage.identical_branch_sets == 2
    assert any("完全相同" in value for value in result.warnings)


def test_five_branch_option_never_adds_a_sixth_query(tmp_path):
    svc, _, source, store = setup(
        tmp_path, [points(1, 10), points(11, 10)], [[hotel(i)] for i in range(1, 6)]
    )
    result = svc.search(request(branch_limit=5), store)
    assert len(source.arguments) == 5 and result.recall_coverage.queried_branches == 5
    assert sum(b.reason == "branch_budget" for b in result.branches) == 15


def test_raw_page_size_and_hint_do_not_hide_later_branches(tmp_path):
    svc, maps, source, store = setup(
        tmp_path, [points(1, 10), points(11, 10)], [[hotel(1)], [hotel(2)], [hotel(3)]]
    )
    original = maps.search

    def sparse(*args, **kwargs):
        rows = original(*args, **kwargs)
        maps.last_search["total_hint"] = 10
        if kwargs["page"] == 1:
            rows = rows[:1]
            maps.last_search["valid_count"] = 1
        return rows

    maps.search = sparse
    result = svc.search(request(), store)
    assert maps.calls == [1, 2] and len(source.arguments) == 3
    assert result.recall_coverage.invalid_places == 9 and len(result.branches) == 11
    assert result.recall_coverage.total_hint == 10 and not result.recall_coverage.discovery_complete


def test_union_hotel_quotes_use_original_stay_context_not_a_branch_or_another_hotel(tmp_path):
    from stay_agent.main import create_app

    svc, _, _, store = setup(tmp_path, [points(1, 3)], [[hotel(1)], [hotel(2)], [hotel(3)]])
    result = svc.search(request(), store)
    calls = []

    class Detail:
        def call_tool(self, name, args):
            calls.append(args)
            return {
                "structuredContent": {
                    "success": True,
                    "hotelId": args["hotelId"],
                    "checkIn": args["dateParam"]["checkInDate"],
                    "checkOut": args["dateParam"]["checkOutDate"],
                    "name": "测试酒店2",
                    "roomRatePlans": [
                        {"roomName": "测试房型", "averagePrice": 100, "currency": "CNY"}
                    ],
                }
            }

        def redact(self, data):
            return data

    quote = QuoteService(client_factory=Detail)
    conv = SimpleNamespace(recover=lambda: None, close=lambda: None)
    app = create_app(
        service=quote,
        session_store=store,
        recall_service=svc,
        conversation_service=conv,
        web_root=tmp_path / "none",
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/hotels/2/quotes", json={"request_id": result.request_id})
        assert response.status_code == 200 and response.json()["status"] == "complete"
        assert calls[0]["hotelId"] == 2 and calls[0]["occupancyParam"] == {
            "adultCount": 2,
            "childCount": 1,
            "childAgeDetails": [5],
            "roomCount": 2,
        }
        assert response.json()["occupancy_status"] == "unverified"
        assert (
            client.post(
                "/api/hotels/999/quotes", json={"request_id": result.request_id}
            ).status_code
            == 404
        )
