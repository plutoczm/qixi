"""Real MapProbe deadline arithmetic with offline HTTP responses and a fake clock."""
import threading
import pytest
from stay_agent.contracts import NearbyResolveRequest, NearbyExploreRequest, NearbyWalkRequest
from stay_agent.poi_nearby import NearbyPlaceService
from stay_agent.providers.data.travel_clients import MapProbe, SecureHTTP, ProbeError

ANCHOR = {"id":"anchor", "name":"测试目的地", "location":"113.25,23.10", "cityname":"广州市", "address":"测试路1号"}
HOTEL = {"id":"hotel", "name":"测试酒店", "location":"113.251,23.10", "cityname":"广州市", "address":"测试路2号", "type":"住宿服务"}

class OfflineHTTP(SecureHTTP):
    def __init__(self):
        super().__init__("amap", "offline-fixture-key", max_requests=3)
        self.calls = []
        self.force_timeout = False
        self.advance = None

    def json(self, operation, params):
        self.remaining()
        if self.request_count >= self.max_requests:
            raise ProbeError("REQUEST_LIMIT")
        self.request_count += 1
        self.calls.append(operation)
        if self.force_timeout:
            raise ProbeError("TIMEOUT")
        if self.advance:
            self.advance()
            self.remaining()
        return ({"status":"1", "route":{"paths":[{"distance":"500", "duration":"400"}]}}
                if operation=="walk" else {"status":"1", "pois":[ANCHOR if operation=="search" else HOTEL]})

@pytest.fixture
def scene(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("stay_agent.providers.data.travel_clients.time.monotonic", lambda: now[0])
    http = OfflineHTTP()
    probe = MapProbe("amap", "offline-fixture-key", http=http)
    service = NearbyPlaceService(factories={"amap": lambda: probe},clock=lambda: now[0],data_mode="test")
    resolved = service.resolve(NearbyResolveRequest(provider="amap",city="广州",keyword="测试目的地"))
    request = NearbyExploreRequest(context_id=resolved["context_id"],anchor_id=resolved["options"][0]["id"])
    return service,http,now,request

def test_waiting_90_seconds_does_not_consume_new_explicit_operation_deadline(scene):
    service,http,now,request=scene
    now[0]+=90
    result=service.explore(request,object())
    assert result["status"]=="complete" and len(result["hotels"])==1
    assert http.calls==["search","around"] and http.request_count==2
    assert http.deadline<=1120
    assert service.contexts[request.context_id].expires==1120

def test_later_manual_walk_has_own_deadline_and_preserves_counts(scene):
    service,http,now,request=scene
    now[0]+=55
    found=service.explore(request,object())
    now[0]+=35
    walk=service.walk(NearbyWalkRequest(context_id=request.context_id,hotel_option_id=found["hotels"][0]["id"]))
    assert walk["status"]=="complete" and walk["distance_meters"]==500
    assert http.request_count==3 and http.calls==["search","around","walk"]
    assert service.contexts[request.context_id].expires==1120

@pytest.mark.parametrize("elapsed",[120,121])
def test_expired_context_cannot_restart_transport_or_send_request(scene,elapsed):
    service,http,now,request=scene
    old_deadline=http.deadline
    now[0]+=elapsed
    result=service.explore(request,object())
    assert result["error"].code=="CONTEXT_EXPIRED"
    assert http.calls==["search"] and http.deadline==old_deadline

def test_timeout_remains_explicit_and_does_not_retry(scene):
    service,http,_,request=scene
    http.force_timeout=True
    result=service.explore(request,object())
    assert result["error"].code=="TIMEOUT"
    assert "超时" in result["error"].message
    assert http.calls==["search","around"]

def test_new_step_does_not_reset_transport_request_limit(scene):
    service,http,now,request=scene
    http.request_count=http.max_requests
    now[0]+=90
    result=service.explore(request,object())
    assert result["error"].code=="REQUEST_LIMIT"
    assert http.request_count==http.max_requests and http.calls==["search"]

def test_new_step_does_not_clear_cancellation(scene):
    service,http,now,request=scene
    http.cancelled=threading.Event()
    http.cancelled.set()
    now[0]+=90
    result=service.explore(request,object())
    assert result["status"]=="unavailable" and http.calls==["search"]
    assert http.cancelled.is_set()

def test_response_after_original_context_expiry_is_not_accepted(scene):
    service,http,now,request=scene
    now[0]+=90
    http.advance=lambda: now.__setitem__(0,1121)
    result=service.explore(request,object())
    assert result["status"]=="unavailable" and result["hotels"]==[]
    assert service.contexts[request.context_id].expires==1120
