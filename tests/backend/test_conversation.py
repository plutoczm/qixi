"""Conversation workflow tests with explicit synthetic model proposals."""

from datetime import date
import json
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from stay_agent.contracts import InterpretRequest, CancelInterpretRequest
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore, ConversationError
from stay_agent.generation import ModelRoute
from stay_agent.providers.llm.adapters import AdapterResult, AdapterError
from stay_agent.main import create_app


@pytest.fixture(autouse=True)
def isolated_day_and_credentials(monkeypatch):
    for module in ("stay_agent.contracts", "stay_agent.condition_rules", "stay_agent.conversation"):
        monkeypatch.setattr(module + ".china_today", lambda: date(2026, 10, 17))
    monkeypatch.setattr("stay_agent.conversation.load_setting", lambda *a, **k: None)


def change(field, value, evidence):
    return {"field": field, "value": value, "evidence": evidence}


def raw(changes, notes=None):
    return json.dumps({"changes": changes, "notes": notes or []}, ensure_ascii=False)


def initial(city="广州"):
    return raw(
        [
            change("place", city, city),
            change("place_type", "城市", city),
            change("check_in", "2026-11-07", "2026年11月7日"),
            change("nights", 1, "住1晚"),
            change("adults", 2, "每房2成人"),
            change("rooms", 1, "1间房"),
            change("children_count", 0, "无儿童"),
            change("budget_fen", 20000, "每房每晚200元"),
            change("budget_basis", "room_night", "每房每晚200元"),
        ]
    )


def message(city="广州"):
    return city + "，2026年11月7日入住，住1晚，1间房，每房2成人，无儿童，每房每晚200元。"


class FakeRouter:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.lock = threading.Lock()

    def resolve(self, provider="deepseek"):
        if provider != "deepseek":
            raise AdapterError("PROVIDER_DISABLED")
        return ModelRoute("deepseek", "deepseek-flash")

    def generate(self, route, system, prompt, schema, cancelled, **kwargs):
        with self.lock:
            self.calls.append((route, prompt, cancelled))
            output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if callable(output):
            output = output(cancelled)
        return AdapterResult(route.provider, route.model, output, {}, "json_object")


@pytest.fixture
def service(tmp_path):
    instances = []

    def make(outputs):
        router = FakeRouter(outputs)
        svc = ConversationService(
            ConversationStore(tmp_path / (uuid4().hex + ".db")), router, data_mode="test"
        )
        instances.append(svc)
        return svc, router

    yield make
    for instance in instances:
        instance.close()


def request(text, revision=0, seq=1, opid=None, inputs=None, provider="deepseek"):
    return InterpretRequest(
        operation_id=opid or uuid4().hex,
        expected_revision=revision,
        client_sequence=seq,
        user_inputs=inputs or [text],
        provider=provider,
    )


def wait(svc, cid):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        state = svc.view(cid)
        if state.status not in ("queued", "running", "validating"):
            return state
        time.sleep(0.005)
    raise AssertionError("operation did not finish")


@pytest.mark.parametrize(
    "city,next_city",
    [
        ("广州", "杭州"),
        ("杭州", "成都"),
        ("成都", "重庆"),
        ("重庆", "武汉"),
        ("武汉", "西安"),
        ("西安", "南京"),
        ("南京", "上海"),
        ("上海", "深圳"),
        ("深圳", "北京"),
        ("北京", "广州"),
    ],
)
def test_ten_multi_turn_replays_preserve_dates_guests_and_rooms(service, city, next_city):
    second = "改去" + next_city + "，每房每晚150元"
    svc, router = service(
        [
            initial(city),
            raw(
                [
                    change("place", next_city, next_city),
                    change("budget_fen", 15000, "每房每晚150元"),
                ]
            ),
        ]
    )
    cid = svc.create().conversation_id
    svc.start(cid, request(message(city)))
    one = wait(svc, cid)
    assert one.revision == 1 and one.query.check_out.isoformat() == "2026-11-08"
    svc.start(cid, request(second, revision=1, seq=2))
    two = wait(svc, cid)
    assert two.query.place == next_city and two.query.budget_fen == 15000
    assert two.query.adults == 2 and two.query.rooms == 1 and two.query.children_ages == []
    assert two.query.check_in.isoformat() == "2026-11-07" and two.revision == 2
    assert len(router.calls) == 2 and two.data_mode == "test"


def test_missing_fields_never_produce_a_query_or_call_price_service(service):
    svc, _ = service([raw([change("place", "广州", "广州")])])
    cid = svc.create().conversation_id
    svc.start(cid, request("广州"))
    state = wait(svc, cid)
    assert state.query is None and "入住日期" in state.missing


def test_stop_before_start_is_a_tombstone_and_prevents_inference(service):
    svc, router = service([initial()])
    cid = svc.create().conversation_id
    opid = uuid4().hex
    svc.cancel(cid, CancelInterpretRequest(operation_id=opid, client_sequence=1))
    svc.start(cid, request(message(), opid=opid))
    assert svc.view(cid).status == "cancelled" and not router.calls


def test_stop_while_provider_ignores_abort_keeps_last_committed_draft(service):
    entered = threading.Event()
    release = threading.Event()

    def blocked(cancelled):
        entered.set()
        release.wait(2)
        return initial()

    svc, router = service([blocked])
    cid = svc.create().conversation_id
    req = request(message())
    svc.start(cid, req)
    assert entered.wait(1)
    svc.cancel(cid, CancelInterpretRequest(operation_id=req.operation_id, client_sequence=1))
    release.set()
    state = wait(svc, cid)
    assert state.status == "cancelled" and state.revision == 0
    assert state.draft.place is None and router.calls[0][2].is_set()


def test_replacement_discards_old_completion_and_retains_combined_user_intent(service):
    entered = threading.Event()
    release = threading.Event()

    def blocked(cancelled):
        entered.set()
        release.wait(2)
        return initial()

    newest = json.loads(initial())
    newest["changes"][0] = change("place", "杭州", "改去杭州")
    svc, router = service([blocked, json.dumps(newest, ensure_ascii=False)])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    assert entered.wait(1)
    svc.start(cid, request("改去杭州", seq=2, inputs=[message(), "改去杭州"]))
    state = wait(svc, cid)
    release.set()
    assert state.query.place == "杭州" and state.query.adults == 2 and state.revision == 1
    assert router.calls[0][2].is_set()
    assert svc.store.load(cid)["revision"] == 1


def test_network_reorder_does_not_let_older_client_sequence_supersede_newer(service):
    entered = threading.Event()
    release = threading.Event()

    def blocked(cancelled):
        entered.set()
        release.wait(2)
        return initial()

    svc, router = service([blocked])
    cid = svc.create().conversation_id
    svc.start(cid, request(message(), seq=2))
    assert entered.wait(1)
    with pytest.raises(ConversationError, match="STALE_OPERATION"):
        svc.start(cid, request("旧指令", seq=1))
    assert not router.calls[0][2].is_set()
    release.set()


def test_duplicate_request_is_idempotent_and_conflicting_payload_is_rejected(service):
    entered = threading.Event()
    release = threading.Event()

    def blocked(cancelled):
        entered.set()
        release.wait(2)
        return initial()

    svc, router = service([blocked])
    cid = svc.create().conversation_id
    req = request(message())
    svc.start(cid, req)
    assert entered.wait(1)
    assert svc.start(cid, req).operation_id == req.operation_id
    with pytest.raises(ConversationError, match="OPERATION_CONFLICT"):
        svc.start(cid, request("不同指令", opid=req.operation_id))
    assert len(router.calls) == 1
    release.set()


def test_invalid_output_repair_is_same_provider_bounded_and_keeps_old_conditions(service):
    svc, router = service([initial(), "bad JSON", "bad JSON"])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    assert wait(svc, cid).revision == 1
    svc.start(cid, request("改预算", revision=1, seq=2))
    state = wait(svc, cid)
    assert state.status == "failed" and state.error.code == "INVALID_MODEL_OUTPUT"
    assert state.query.budget_fen == 20000 and state.revision == 1
    assert len(router.calls) == 3 and all(c[0].provider == "deepseek" for c in router.calls)


def test_provider_timeout_is_not_retried_and_has_no_false_success(service):
    svc, router = service([AdapterError("TIMEOUT")])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    state = wait(svc, cid)
    assert state.status == "timed_out" and state.revision == 0 and len(router.calls) == 1


def test_wrong_conversation_cannot_reuse_or_cancel_an_operation(service):
    svc, router = service([initial()])
    cid = svc.create().conversation_id
    other = svc.create().conversation_id
    req = request(message())
    svc.start(cid, req)
    wait(svc, cid)
    with pytest.raises(ConversationError, match="OPERATION_CONFLICT"):
        svc.start(other, req)
    with pytest.raises(ConversationError, match="OPERATION_CONFLICT"):
        svc.cancel(other, CancelInterpretRequest(operation_id=req.operation_id, client_sequence=1))
    assert len(router.calls) == 1


def test_restart_marks_incomplete_work_cancelled_without_replay(service):
    entered = threading.Event()
    release = threading.Event()

    def blocked(cancelled):
        entered.set()
        release.wait(2)
        return initial()

    svc, router = service([blocked])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    assert entered.wait(1)
    svc.store.recover()
    release.set()
    state = wait(svc, cid)
    assert (
        state.status == "cancelled"
        and state.revision == 0
        and state.error.code == "SERVER_RESTARTED"
    )
    assert len(router.calls) == 1


def test_gpt_deferred_and_sensitive_inputs_are_rejected_before_inference(service):
    svc, router = service([initial()])
    cid = svc.create().conversation_id
    with pytest.raises(AdapterError, match="PROVIDER_DISABLED"):
        svc.start(cid, request(message(), provider="pi_chatgpt"))
    with pytest.raises(ConversationError, match="SENSITIVE_INPUT"):
        svc.start(cid, request("我的手机号13812345678"))
    assert not router.calls and svc.view(cid).revision == 0


def test_api_conflict_disabled_route_and_no_store_headers(service, tmp_path):
    svc, _ = service([initial()])

    class Quote:
        def credential_configured(self):
            return False

    app = create_app(service=Quote(), web_root=tmp_path / "none", conversation_service=svc)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        state = client.post("/api/conversations").json()
        cid = state["conversation_id"]
        denied = client.post(
            f"/api/conversations/{cid}/operations",
            json=request(message(), provider="pi_chatgpt").model_dump(),
        )
        assert denied.status_code == 422 and denied.json()["error"]["code"] == "PROVIDER_DISABLED"
        response = client.post(
            f"/api/conversations/{cid}/operations", json=request(message()).model_dump()
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        wait(svc, cid)
        conflict = client.post(
            f"/api/conversations/{cid}/operations", json=request("改条件", seq=2).model_dump()
        )
        assert conflict.status_code == 409

def test_queue_time_counts_toward_deadline_without_provider_call(service, monkeypatch):
    svc, router = service([initial()])
    queued = []
    monkeypatch.setattr(svc.pool, "submit", lambda function, *args: queued.append((function, args)))
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    function, args = queued[0]
    function(*args[:-1], 0)
    state = svc.view(cid)
    assert state.status == "timed_out" and state.error.code == "TIMEOUT"
    assert state.revision == 0 and not router.calls


def test_rejected_storage_commit_preserves_draft_and_ends_generation(service, monkeypatch):
    svc, _ = service([initial()])
    monkeypatch.setattr(svc.store, "commit", lambda *args: False)
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    state = wait(svc, cid)
    assert state.status == "failed" and state.revision == 0 and state.draft.place is None
    assert svc.fences[cid].snapshot()["status"] == "failed"

def test_provider_returning_after_deadline_cannot_commit(service, monkeypatch):
    from types import SimpleNamespace

    def overdue(cancelled):
        monkeypatch.setattr("stay_agent.conversation.time", SimpleNamespace(monotonic=lambda: 2))
        return initial()

    svc, router = service([overdue])
    queued = []
    monkeypatch.setattr(svc.pool, "submit", lambda function, *args: queued.append((function, args)))
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    monkeypatch.setattr("stay_agent.conversation.time", SimpleNamespace(monotonic=lambda: 0))
    function, args = queued[0]
    function(*args[:-1], 1)
    state = svc.view(cid)
    assert len(router.calls) == 1 and state.status == "timed_out"
    assert state.revision == 0 and state.draft.place is None


@pytest.mark.parametrize(
    "text,changes",
    [
        ("改成深圳，2间房", [change("place", "深圳", "改成深圳"), change("rooms", 2, "2间房")]),
        ("取消预算", [change("budget_fen", None, "取消预算")]),
    ],
)
def test_semantic_omission_repaired_once_on_same_provider(service, text, changes):
    svc, router = service([initial(), raw([]), raw(changes)])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    assert wait(svc, cid).revision == 1
    svc.start(cid, request(text, revision=1, seq=2))
    result = wait(svc, cid)
    assert result.status == "succeeded" and result.revision == 2
    assert len(router.calls) == 3
    assert all(call[0].provider == "deepseek" for call in router.calls)
    if text.startswith("改成"):
        assert result.draft.place == "深圳" and result.draft.rooms == 2
    else:
        assert result.draft.budget_fen is None


def test_semantic_omission_twice_fails_without_mutating_last_conditions(service):
    svc, router = service([initial(), raw([]), raw([])])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    assert wait(svc, cid).revision == 1
    svc.start(cid, request("改成深圳，2间房", revision=1, seq=2))
    failed = wait(svc, cid)
    assert failed.status == "failed" and failed.error.code == "SEMANTIC_OMISSION"
    assert "未能完整识别" in failed.error.message
    assert failed.revision == 1 and failed.draft.place == "广州"
    assert failed.draft.rooms == 1 and len(router.calls) == 3


@pytest.mark.parametrize(
    "code,expected",
    [
        ("MISSING_CREDENTIAL", "API Key"),
        ("AUTHENTICATION_FAILED", "认证失败"),
        ("ACCESS_DENIED", "访问权限"),
        ("INSUFFICIENT_BALANCE", "余额不足"),
        ("RATE_LIMITED", "速率上限"),
        ("PROVIDER_UNAVAILABLE", "服务暂时不可用"),
        ("MODEL_UNAVAILABLE", "连接或处理未完成"),
        ("TIMEOUT", "超时"),
        ("INVALID_MODEL_OUTPUT", "结构或证据检查"),
        ("STATE_UNAVAILABLE", "本机状态库"),
        ("STATE_NOT_COMMITTED", "会话状态已变化"),
        ("INTERNAL_ERROR", "本地处理未完成"),
    ],
)
def test_errors_have_actionable_safe_feedback_and_are_not_retried(service, code, expected):
    svc, router = service([AdapterError(code)])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    failed = wait(svc, cid)
    assert failed.error.code == code
    assert expected in failed.error.message
    assert "未应用新条件" in failed.error.message
    assert failed.revision == 0 and failed.draft.place is None
    assert len(router.calls) == 1


def test_local_storage_failure_is_never_reported_as_model_failure(service, monkeypatch):
    import sqlite3

    svc, _ = service([initial()])
    cid = svc.create().conversation_id

    def readonly(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(svc.store, "commit", readonly)
    svc.start(cid, request(message()))
    failed = wait(svc, cid)
    assert failed.error.code == "STATE_UNAVAILABLE"
    assert "模型" not in failed.error.message
    assert "状态库" in failed.error.message
    assert failed.revision == 0 and failed.draft.place is None


def test_uncommitted_change_is_not_reported_as_model_failure(service, monkeypatch):
    svc, _ = service([initial()])
    cid = svc.create().conversation_id
    monkeypatch.setattr(svc.store, "commit", lambda *args, **kwargs: False)
    svc.start(cid, request(message()))
    failed = wait(svc, cid)
    assert failed.error.code == "STATE_NOT_COMMITTED"
    assert "模型" not in failed.error.message
    assert failed.revision == 0 and failed.draft.place is None


@pytest.mark.parametrize(
    "upstream,public",
    [
        ("MISSING_CREDENTIAL", "MISSING_CREDENTIAL"),
        ("INVALID_CREDENTIAL", "INVALID_CREDENTIAL"),
        ("ENV_UNREADABLE", "INVALID_CONFIGURATION"),
    ],
)
def test_local_credential_failures_are_safe_and_actionable(service, upstream, public):
    from stay_agent.providers.data.travel_clients import ProbeError

    svc, router = service([ProbeError(upstream)])
    cid = svc.create().conversation_id
    svc.start(cid, request(message()))
    state = wait(svc, cid)
    assert state.status == "failed" and state.error.code == public
    assert "本机配置" in state.error.message
    assert state.revision == 0 and len(router.calls) == 1


def test_model_extracts_city_restaurant_and_sight_as_distinct_visit_intents(service):
    """LLM proposal is synthetic; service still validates, commits and restores it."""
    text = message() + "还想去海底捞吃火锅、广州塔玩，住海底捞附近"
    proposal = json.loads(initial())
    proposal["visits"] = [
        {
            "name": "海底捞", "category": "餐饮", "area_hint": None,
            "stay_near": True, "action": "add",
            "evidence": "海底捞吃火锅、广州塔玩，住海底捞附近",
        },
        {
            "name": "广州塔", "category": "景点", "area_hint": None,
            "stay_near": False, "action": "add", "evidence": "广州塔玩",
        },
    ]
    svc, router = service([json.dumps(proposal, ensure_ascii=False)])
    cid = svc.create().conversation_id
    svc.start(cid, request(text))
    result = wait(svc, cid)
    assert result.status == "succeeded" and result.query.place == "广州"
    assert result.query.poi_name is None
    assert [visit.name for visit in result.draft.visits] == ["海底捞", "广州塔"]
    assert result.draft.visits[0].stay_near is True
    assert any("门店" in warning for warning in result.warnings)
    assert svc.store.load(cid)["draft"]["visits"][1]["category"] == "景点"
    assert len(router.calls) == 1


def test_model_can_qualify_one_restaurant_branch_then_continue_other_conditions(service):
    first = message() + "想去海底捞吃火锅"
    first_output = json.loads(initial())
    first_output["visits"] = [{
        "name": "海底捞", "category": "餐饮", "area_hint": None,
        "stay_near": False, "action": "add", "evidence": "海底捞吃火锅",
    }]
    second = "是荔胜广场那家"
    second_output = {
        "changes": [], "notes": [], "visits": [{
            "name": "海底捞", "category": "餐饮", "area_hint": "荔胜广场",
            "stay_near": False, "action": "update", "evidence": second,
        }],
    }
    third = "还想去广州塔玩"
    third_output = {"changes": [], "notes": [], "visits": [{
        "name": "广州塔", "category": "景点", "area_hint": None,
        "stay_near": False, "action": "add", "evidence": "广州塔玩",
    }]}
    svc, _ = service([
        json.dumps(first_output, ensure_ascii=False),
        json.dumps(second_output, ensure_ascii=False),
        json.dumps(third_output, ensure_ascii=False),
    ])
    cid = svc.create().conversation_id
    svc.start(cid, request(first))
    assert wait(svc, cid).revision == 1
    svc.start(cid, request(second, revision=1, seq=2))
    two = wait(svc, cid)
    assert two.revision == 2
    assert two.draft.visits[0].area_hint == "荔胜广场"
    assert two.query.place == "广州"
    svc.start(cid, request(third, revision=2, seq=3))
    three = wait(svc, cid)
    assert three.revision == 3
    assert [visit.name for visit in three.draft.visits] == ["海底捞", "广州塔"]
