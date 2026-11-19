"""Fixed-endpoint read-only travel clients; no transactions."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import http.client
import hashlib
import json
import math
import os
from pathlib import Path
import ssl
import time
import threading
from urllib.parse import urlencode

from stay_agent.providers.data.rollinggo_client import RollingGoClient, RollingGoError, TransportResponse, redact

ROOT = Path(__file__).resolve().parents[4]
KEY_NAMES = {"amap": "AMAP_API_KEY", "tencent": "TENCENT_MAP_KEY", "didi": "DIDI_MCP_KEY"}
ENDPOINTS = {
    ("amap", "search"): ("restapi.amap.com", "/v5/place/text"),
    ("amap", "around"): ("restapi.amap.com", "/v5/place/around"),
    ("amap", "detail"): ("restapi.amap.com", "/v5/place/detail"),
    ("amap", "geocode"): ("restapi.amap.com", "/v3/geocode/geo"),
    ("amap", "walk"): ("restapi.amap.com", "/v3/direction/walking"),
    ("amap", "drive"): ("restapi.amap.com", "/v3/direction/driving"),
    ("tencent", "search"): ("apis.map.qq.com", "/ws/place/v1/search"),
    ("tencent", "walk"): ("apis.map.qq.com", "/ws/direction/v1/walking/"),
    ("tencent", "drive"): ("apis.map.qq.com", "/ws/direction/v1/driving/"),
    ("didi", "rpc"): ("mcp.didichuxing.com", "/mcp-servers"),
}


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


# Amap returns "门址" for a resolved doorplate (verified on a live call
# 2026-11-05). "门牌号" is kept as a tolerant alias; anything coarser (区县,
# 道路, 乡镇 ...) must never count as identity evidence.
GEOCODE_PRECISE_LEVELS = frozenset(
    {"门址", "门牌号", "兴趣点", "单元号", "楼栋号", "道路交叉路口"}
)


def distance_meters(lng1, lat1, lng2, lat2):
    """Great-circle distance; both endpoints always come from the same map source."""
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def load_key(provider, path=ROOT / ".env", environ=None):
    if provider not in KEY_NAMES:
        raise ProbeError("UNKNOWN_PROVIDER")
    return load_setting(KEY_NAMES[provider], path, environ)


def load_tencent_secret(path=ROOT / ".env", environ=None):
    return load_setting("TENCENT_MAP_SECRET", path, environ, required=False)


def load_setting(name, path=ROOT / ".env", environ=None, required=True):
    value = (os.environ if environ is None else environ).get(name, "").strip()
    if not value:
        try:
            if path.stat().st_size > 65536:
                raise ProbeError("ENV_TOO_LARGE")
            found = []
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                key, sep, candidate = line.partition("=")
                if sep and key.strip() == name:
                    candidate = candidate.strip()
                    if (
                        len(candidate) >= 2
                        and candidate[0] == candidate[-1]
                        and candidate[0] in ("'", '"')
                    ):
                        candidate = candidate[1:-1]
                    found.append(candidate.strip())
            if len(found) > 1:
                raise ProbeError("DUPLICATE_CREDENTIAL")
            value = found[0] if found else ""
        except FileNotFoundError:
            value = ""
        except (UnicodeError, OSError):
            raise ProbeError("ENV_UNREADABLE") from None
    if not value and not required:
        return None
    if not value or value.upper() in (
        "YOUR_API_KEY",
        "YOUR_MCP_KEY",
        "YOUR_KEY",
        "CHANGE_ME",
        "<YOUR_API_KEY>",
        "YOUR_SECRET_KEY",
        "YOUR_SK",
    ):
        raise ProbeError("MISSING_CREDENTIAL")
    if (
        len(value) > 4096
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
        or "://" in value
        or value.lower().startswith(("key=", "bearer "))
    ):
        raise ProbeError("INVALID_CREDENTIAL")
    return value


def configuration():
    result = {}
    for provider in KEY_NAMES:
        try:
            load_key(provider)
            result[provider] = {"configured": True, "live_verified": False}
            if provider == "tencent":
                result[provider]["signing_secret_configured"] = load_tencent_secret() is not None
        except ProbeError as error:
            result[provider] = {"configured": False, "code": error.code, "live_verified": False}
    return result


class Body:
    def __init__(self, response, connection, sock):
        self.response, self.connection, self.sock = response, connection, sock

    def read1(self, n):
        return self.response.read1(n)

    def set_timeout(self, value):
        if callable(getattr(self.response, "isclosed", None)) and self.response.isclosed():
            return
        self.sock.settimeout(value)

    def close(self):
        try:
            self.response.close()
        finally:
            self.connection.close()


def tencent_signature(path, params, secret):
    # Tencent GET signing uses the sorted, unencoded values; URL encoding happens after hashing.
    if (
        not isinstance(path, str)
        or not path.startswith("/ws/")
        or "?" in path
        or not isinstance(secret, str)
        or not secret
        or "sig" in params
        or "key" not in params
        or any(not isinstance(k, str) for k in params)
        or any(type(v) not in (str, int, float) for v in params.values())
    ):
        raise ProbeError("INVALID_SIGNING_INPUT")
    canonical = path + "?" + "&".join(k + "=" + str(params[k]) for k in sorted(params)) + secret
    return hashlib.md5(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


class RequestPacer:
    """Process-wide pacing per provider, including separate search contexts."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.locks = {provider: threading.Lock() for provider in KEY_NAMES}
        self.last = {}

    def wait(self, provider, interval, remaining):
        lock = self.locks[provider]
        if not lock.acquire(timeout=remaining()):
            raise ProbeError("TIMEOUT")
        try:
            remaining()
            last = self.last.get(provider)
            delay = 0 if last is None else interval - (self.clock() - last)
            if delay > 0:
                if delay >= remaining():
                    raise ProbeError("TIMEOUT")
                self.sleep(delay)
            remaining()
            self.last[provider] = self.clock()
        finally:
            lock.release()


DEFAULT_PACER = RequestPacer()


class SecureHTTP:
    def __init__(
        self, provider, key, timeout=15, total_timeout=60, max_requests=24, secret=None, pacer=None
    ):
        if provider not in KEY_NAMES:
            raise ProbeError("UNKNOWN_PROVIDER")
        if not isinstance(key, str) or not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ProbeError("INVALID_CREDENTIAL")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v <= 0
            for v in (timeout, total_timeout)
        ):
            raise ProbeError("INVALID_BUDGET")
        if type(max_requests) is not int or not 1 <= max_requests <= 32:
            raise ProbeError("INVALID_BUDGET")
        if secret is not None and (
            provider != "tencent"
            or not isinstance(secret, str)
            or not secret
            or len(secret) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in secret)
        ):
            raise ProbeError("INVALID_CREDENTIAL")
        self._secret = secret
        self.provider, self.key = provider, key
        self.timeout, self.total_timeout, self.max_requests = timeout, total_timeout, max_requests
        self.request_count = 0
        self.deadline = None
        self.last_response_metadata = {}
        self.pacer = DEFAULT_PACER if pacer is None else pacer
        self.min_interval = 1.1

    def remaining(self):
        if getattr(self, "cancelled", None) is not None and self.cancelled.is_set():
            raise ProbeError("CANCELLED")
        if self.deadline is None:
            self.deadline = time.monotonic() + self.total_timeout
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("TIMEOUT")
        return min(self.timeout, remaining)

    def open(self, operation, params=None, body=None, headers=None):
        endpoint = ENDPOINTS.get((self.provider, operation))
        if endpoint is None or (self.provider == "didi") != (body is not None):
            raise ProbeError("OPERATION_DENIED")
        if self.request_count >= self.max_requests:
            raise ProbeError("REQUEST_LIMIT")
        params = dict(params or {})
        if "key" in params:
            raise ProbeError("CREDENTIAL_OVERRIDE_DENIED")
        params["key"] = self.key
        host, path = endpoint
        request_headers = dict(headers or {})
        if self.provider == "tencent" and self._secret is not None:
            if "sig" in params:
                raise ProbeError("SIGNATURE_OVERRIDE_DENIED")
            params["sig"] = tencent_signature(path, params, self._secret)
            request_headers = {
                k: v for k, v in request_headers.items() if k.lower() != "x-legacy-url-decode"
            }
            request_headers["x-legacy-url-decode"] = "no"
        self.pacer.wait(self.provider, self.min_interval, self.remaining)
        connection = None
        try:
            connection = http.client.HTTPSConnection(
                host, 443, timeout=self.remaining(), context=ssl.create_default_context()
            )
            self.request_count += 1
            connection.request(
                "POST" if body is not None else "GET",
                path + "?" + urlencode(params),
                body=body,
                headers=request_headers,
            )
            sock = connection.sock
            response = connection.getresponse()
            if sock is None:
                raise ProbeError("TRANSPORT_ERROR")
            status = response.status
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            content_type = response_headers.get("content-type", "").split(";")[0].strip().lower()
            self.last_response_metadata = {
                "operation": operation,
                "http_status": status,
                "content_type": content_type
                if content_type in ("application/json", "text/xml", "application/xml", "text/plain")
                else "other",
                "content_encoding": response_headers.get("content-encoding", "identity")
                if response_headers.get("content-encoding", "identity")
                in ("identity", "gzip", "deflate")
                else "other",
            }
            quota = response_headers.get("x-limit")
            parsed_quota = {}
            if quota:
                try:
                    obj = json.loads(quota)
                    if isinstance(obj, dict):
                        for field in ("current_pv", "limit_pv", "current_qps", "limit_qps"):
                            val = obj.get(field)
                            if type(val) is int or isinstance(val, str) and val.isdigit():
                                parsed_quota[field] = int(val)
                except ValueError:
                    pass
            self.last_response_metadata["quota_header_present"] = bool(quota)
            self.last_response_metadata["quota"] = parsed_quota
            if not 200 <= status < 300:
                code = {
                    401: "AUTHENTICATION_FAILED",
                    403: "ACCESS_DENIED",
                    429: "RATE_LIMITED",
                }.get(status, "REDIRECT_DENIED" if 300 <= status < 400 else "HTTP_ERROR")
                raise ProbeError(code)
            return TransportResponse(
                status, dict(response.getheaders()), Body(response, connection, sock)
            )
        except ProbeError:
            if connection:
                connection.close()
            raise
        except TimeoutError:
            if connection:
                connection.close()
            raise ProbeError("TIMEOUT") from None
        except Exception:
            if connection:
                connection.close()
            raise ProbeError("TRANSPORT_ERROR") from None

    def json(self, operation, params):
        response = self.open(operation, params)
        raw = bytearray()
        try:
            while True:
                response.body.set_timeout(self.remaining())
                chunk = response.body.read1(min(4096, 1048577 - len(raw)))
                self.remaining()
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 1048576:
                    raise ProbeError("RESPONSE_LIMIT")

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            def reject(value):
                raise ValueError

            stripped = bytes(raw).lstrip()
            self.last_response_metadata.update(
                response_bytes=len(raw),
                body_kind="json"
                if stripped.startswith((b"{", b"["))
                else "xml"
                if stripped.startswith(b"<")
                else "bom"
                if stripped.startswith(b"\xef\xbb\xbf")
                else "other",
            )
            value = json.loads(
                raw.decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=reject
            )
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ProbeError:
            raise
        except TimeoutError:
            raise ProbeError("TIMEOUT") from None
        except Exception:
            raise ProbeError("INVALID_RESPONSE") from None
        finally:
            response.close()


@dataclass(frozen=True)
class Point:
    provider: str
    poi_id: str
    title: str
    lng: float
    lat: float


def point_from(provider, row):
    if not isinstance(row, dict):
        return None
    try:
        if provider == "amap":
            pieces = row["location"].split(",")
            if len(pieces) != 2:
                return None
            lng, lat = map(float, pieces)
            identity, title = row["id"], row["name"]
        else:
            identity, title = row["id"], row["title"]
            location = row["location"]
            if type(location["lng"]) not in (int, float) or type(location["lat"]) not in (
                int,
                float,
            ):
                return None
            lng, lat = float(location["lng"]), float(location["lat"])
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            return None
        if not isinstance(identity, str) or not identity or not isinstance(title, str) or not title:
            return None
        return Point(provider, identity, title[:200], lng, lat)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def route_summary(provider, data):
    if provider not in ("amap", "tencent"):
        raise ProbeError("UNKNOWN_PROVIDER")
    parent = data.get("route" if provider == "amap" else "result")
    if not isinstance(parent, dict):
        raise ProbeError("INVALID_ROUTES")
    routes = parent.get("paths" if provider == "amap" else "routes")
    if not isinstance(routes, list):
        raise ProbeError("INVALID_ROUTES")
    factor = 1 if provider == "amap" else 60
    result = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        try:
            if type(route.get("duration")) not in (str, int, float) or type(
                route.get("distance")
            ) not in (str, int, float):
                continue
            seconds = Decimal(str(route["duration"])) * factor
            meters = Decimal(str(route["distance"]))
            if not seconds.is_finite() or not meters.is_finite() or min(seconds, meters) < 0:
                continue
            result.append(
                {
                    "duration_seconds": int(seconds.to_integral_value(rounding=ROUND_CEILING)),
                    "distance_meters": int(meters.to_integral_value(rounding=ROUND_CEILING)),
                    "source_duration_unit": "seconds" if factor == 1 else "minutes",
                }
            )
        except (InvalidOperation, ValueError, OverflowError):
            continue
    return result


class MapProbe:
    def __init__(self, provider, key, http=None):
        if provider not in ("amap", "tencent"):
            raise ProbeError("UNKNOWN_PROVIDER")
        self.provider = provider
        self.http = (
            http
            if http is not None
            else SecureHTTP(
                provider, key, secret=load_tencent_secret() if provider == "tencent" else None
            )
        )
        self._redaction_secrets = tuple(
            v for v in (key, getattr(self.http, "_secret", None)) if isinstance(v, str) and v
        )
        self.points = {}
        self.metadata = {}
        self.last_search = {}
        self.last_geocode = {}

    def _get(self, operation, params):
        if self.provider == "amap":
            params = {**params, "output": "json"}
        data = self.http.json(operation, params)
        data = redact(data, secrets=self._redaction_secrets)
        success = (
            data.get("status") == "1"
            if self.provider == "amap"
            else type(data.get("status")) is int and data["status"] == 0
        )
        if not success:
            error = ProbeError("PROVIDER_REJECTED")
            code = data.get("infocode") if self.provider == "amap" else data.get("status")
            error.provider_code = (
                str(code) if type(code) in (str, int) and str(code).isdigit() else None
            )
            raise error
        return data

    def search(self, keyword, city="广州", page=1, page_size=10):
        if (
            not isinstance(keyword, str)
            or not keyword
            or len(keyword.encode()) > 96
            or not isinstance(city, str)
            or not city.strip()
            or any(c in city for c in "(),\r\n")
        ):
            raise ProbeError("INVALID_ARGUMENTS")
        if (
            type(page) is not int
            or not 1 <= page <= 20
            or type(page_size) is not int
            or not 1 <= page_size <= 20
        ):
            raise ProbeError("INVALID_ARGUMENTS")
        params = (
            {
                "keywords": keyword,
                "region": city,
                "city_limit": "true",
                "page_num": page,
                "page_size": page_size,
                "show_fields": "business,navi,children",
            }
            if self.provider == "amap"
            else {
                "keyword": keyword,
                "boundary": f"region({city},0)",
                "page_index": page,
                "page_size": page_size,
            }
        )
        data = self._get("search", params)
        rows = data.get("pois" if self.provider == "amap" else "data")
        if not isinstance(rows, list):
            raise ProbeError("INVALID_POIS")
        points = self._remember(rows)
        hint = data.get("count")
        if isinstance(hint, str) and hint.isascii() and hint.isdecimal():
            hint = int(hint) if len(hint) < 9 else None
        self.last_search = {
            "raw_count": len(rows),
            "valid_count": len(points),
            "total_hint": hint if type(hint) is int and 0 <= hint <= 10000000 else None,
        }
        return points

    def geocode(self, address, city="广州"):
        """Resolve a free-text address inside this same map source.

        Only precise levels are useful for identity: a district or city centroid
        must never be mistaken for the actual doorplate.
        """
        if self.provider != "amap":
            raise ProbeError("UNSUPPORTED_OPERATION")
        if (
            not isinstance(address, str)
            or not address.strip()
            or len(address.encode()) > 96
            or not isinstance(city, str)
            or not city.strip()
            or any(c in city for c in "(),\r\n")
        ):
            raise ProbeError("INVALID_ARGUMENTS")
        data = self._get("geocode", {"address": address, "city": city})
        rows = data.get("geocodes")
        if not isinstance(rows, list):
            raise ProbeError("INVALID_GEOCODE")
        results = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            location = row.get("location")
            if not isinstance(location, str):
                continue
            pieces = location.split(",")
            if len(pieces) != 2:
                continue
            try:
                lng, lat = float(pieces[0]), float(pieces[1])
            except ValueError:
                continue
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                continue
            level = row.get("level") if isinstance(row.get("level"), str) else None
            results.append({"level": level, "lng": lng, "lat": lat})
        self.last_geocode = {
            "raw_count": len(rows),
            "precise_count": sum(1 for r in results if r["level"] in GEOCODE_PRECISE_LEVELS),
        }
        return results

    def _remember(self, rows):
        points = []
        for row in rows:
            point = point_from(self.provider, row)
            if point is None:
                continue
            self.points[point.poi_id] = point
            business = row.get("business") if isinstance(row.get("business"), dict) else {}
            ad_info = row.get("ad_info") if isinstance(row.get("ad_info"), dict) else {}
            navi = row.get("navi") if isinstance(row.get("navi"), dict) else {}
            self.metadata[point.poi_id] = {
                "address": row.get("address") if isinstance(row.get("address"), str) else None,
                "type": row.get("type") if self.provider == "amap" else None,
                "typecode": row.get("typecode") if self.provider == "amap" else None,
                "category": row.get("category") if self.provider == "tencent" else None,
                "city": row.get("cityname") or ad_info.get("city"),
                "province": row.get("pname") or ad_info.get("province"),
                "adcode": row.get("adcode") or ad_info.get("adcode"),
                # Amap POI 2.0 documents business.rating for some hotel POIs.
                # This is NOT a supplier guest score or a review-count field.
                "map_business_rating": business.get("rating") if self.provider == "amap" else None,
                "open_today": business.get("opentime_today"),
                "open_week": business.get("opentime_week"),
                "entrance_present": bool(navi.get("entr_location")),
                "exit_present": bool(navi.get("exit_location")),
                "field_names": sorted(row),
            }
            points.append(point)
        return points

    def around(self, point, keyword, limit=3, page=1):
        if not isinstance(point, Point) or self.points.get(point.poi_id) != point:
            raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")
        if (
            type(limit) is not int
            or not 1 <= limit <= 10
            or type(page) is not int
            or page not in (1, 2)
            or not isinstance(keyword, str)
            or not keyword.strip()
        ):
            raise ProbeError("INVALID_ARGUMENTS")
        # The scoped A/B follow-up authorizes only Amap's explicit page=2.
        # Keep Tencent's existing around behavior unchanged.
        if self.provider != "amap" and page != 1:
            raise ProbeError("OPERATION_DENIED")
        if self.provider == "amap":
            data = self._get(
                "around",
                {
                    "keywords": keyword,
                    "location": f"{point.lng:.6f},{point.lat:.6f}",
                    "radius": 2000,
                    "sortrule": "distance",
                    "page_size": limit,
                    "page_num": page,
                    "show_fields": "business,navi,children",
                },
            )
            rows = data.get("pois")
        else:
            data = self._get(
                "search",
                {
                    "keyword": keyword,
                    "boundary": f"nearby({point.lat:.6f},{point.lng:.6f},1000,0)",
                    "page_size": limit,
                    "page_index": 1,
                },
            )
            rows = data.get("data")
        if not isinstance(rows, list):
            raise ProbeError("INVALID_POIS")
        return self._remember(rows)

    def detail(self, point):
        if not isinstance(point, Point) or self.points.get(point.poi_id) != point:
            raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")
        if self.provider != "amap":
            raise ProbeError("OPERATION_DENIED")
        data = self._get("detail", {"id": point.poi_id, "show_fields": "business,navi,children"})
        rows = data.get("pois")
        if not isinstance(rows, list):
            raise ProbeError("INVALID_POIS")
        points = self._remember(rows)
        return next((p for p in points if p.poi_id == point.poi_id), None)

    def walk(self, origin, destination):
        if not all(isinstance(p, Point) for p in (origin, destination)):
            raise ProbeError("INVALID_ARGUMENTS")
        if any(
            p.provider != self.provider or self.points.get(p.poi_id) != p
            for p in (origin, destination)
        ):
            raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")

        def native(point):
            values = (point.lng, point.lat) if self.provider == "amap" else (point.lat, point.lng)
            return ",".join(f"{v:.6f}" for v in values)

        params = (
            {
                "origin": native(origin),
                "destination": native(destination),
                "origin_id": origin.poi_id,
                "destination_id": destination.poi_id,
            }
            if self.provider == "amap"
            else {"from": native(origin), "to": native(destination)}
        )
        return route_summary(self.provider, self._get("walk", params))

    def drive(self, origin, destination):
        """Same-provider driving estimate, excluding taxi pickup/wait and future guarantees."""
        if not all(isinstance(point, Point) for point in (origin, destination)):
            raise ProbeError("INVALID_ARGUMENTS")
        if any(
            point.provider != self.provider or self.points.get(point.poi_id) != point
            for point in (origin, destination)
        ):
            raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")
        params = (
            {
                "origin": f"{origin.lng:.6f},{origin.lat:.6f}",
                "destination": f"{destination.lng:.6f},{destination.lat:.6f}",
                "strategy": 0,
                "extensions": "base",
            }
            if self.provider == "amap"
            else {
                "from": f"{origin.lat:.6f},{origin.lng:.6f}",
                "to": f"{destination.lat:.6f},{destination.lng:.6f}",
            }
        )
        return route_summary(self.provider, self._get("drive", params))


class DidiRPC(RollingGoClient):
    def _matched_result(self, message, request_id, *, stream):
        if isinstance(message, dict) and isinstance(message.get("error"), dict):
            error = message["error"]
            code = error.get("code")
            text = str(error.get("message", "")).lower()
            category = (
                "rate_limited"
                if any(word in text for word in ("limit", "频繁", "频率", "限流", "次数"))
                else "arguments"
                if code == -32602
                or any(word in text for word in ("参数", "argument", "invalid param"))
                else "provider_rpc_error"
            )
            self.last_rpc_error = {
                "code": code if type(code) is int else None,
                "category": category,
            }
        return super()._matched_result(message, request_id, stream=stream)


class DidiDiscovery:
    """Documented direct JSON-RPC, restricted to place lookup and estimates."""

    def __init__(self, key, http=None):
        self.http = http or SecureHTTP("didi", key, max_requests=8)

        def transport(body, headers, timeout):
            safe = {k: v for k, v in headers.items() if k.lower() != "authorization"}
            try:
                return self.http.open("rpc", body=body, headers=safe)
            except ProbeError as error:
                raise RollingGoError(error.code) from None

        self.rpc = DidiRPC(key, transport=transport, total_timeout=60, max_requests=8)
        self._tools = None
        self.points = {}
        self.metadata = {}
        self.last_search = {}

    def discover(self):
        if self._tools is not None:
            return self.rpc.redact(self._tools)
        result, _ = self.rpc._rpc("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise ProbeError("INVALID_TOOLS")
        if result.get("nextCursor") is not None:
            raise ProbeError("TOOL_LIST_TRUNCATED")
        selected = [
            tool
            for tool in tools
            if isinstance(tool, dict) and tool.get("name") in ("maps_textsearch", "taxi_estimate")
        ]
        if len({tool["name"] for tool in selected}) != len(selected):
            raise ProbeError("INVALID_TOOLS")
        if {tool["name"] for tool in selected} != {"maps_textsearch", "taxi_estimate"}:
            raise ProbeError("REQUIRED_TOOL_MISSING")
        if any(not isinstance(tool.get("inputSchema"), dict) for tool in selected):
            raise ProbeError("INVALID_TOOLS")
        self._tools = selected
        return self.rpc.redact(selected)

    def call_readonly(self, name, arguments):
        if name not in ("maps_textsearch", "taxi_estimate"):
            raise ProbeError("OPERATION_DENIED")
        if not isinstance(arguments, dict):
            raise ProbeError("INVALID_ARGUMENTS")
        if name == "taxi_estimate":
            for prefix in ("from", "to"):
                if not any(
                    arguments.get(prefix + "_lng") == str(p.lng)
                    and arguments.get(prefix + "_lat") == str(p.lat)
                    and arguments.get(prefix + "_name") == p.title
                    for p in self.points.values()
                ):
                    raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")
        tool = next(t for t in self.discover() if t["name"] == name)
        schema = tool["inputSchema"]
        properties = schema.get("properties", {})
        if (
            set(arguments) - set(properties)
            or set(schema.get("required", [])) - set(arguments)
            or any(
                not isinstance(value, str) or not value.strip() or len(value) > 200
                for value in arguments.values()
            )
        ):
            raise ProbeError("INVALID_ARGUMENTS")
        result, _ = self.rpc._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            raise ProbeError("PROVIDER_REJECTED")
        return self.rpc.redact(result)


def decode_didi(result):
    """Didi text payloads may be arrays; do not apply hotel-object unwrapping."""
    value = result
    for _ in range(8):
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            raise ProbeError("UNSTRUCTURED_DATA")
        if value.get("isError") is True or value.get("success") is False:
            raise ProbeError("PROVIDER_REJECTED")
        if isinstance(value.get("structuredContent"), (dict, list)):
            value = value["structuredContent"]
            continue
        if isinstance(value.get("content"), list):
            decoded = None
            for item in value["content"]:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except ValueError:
                    continue
                if isinstance(parsed, (dict, list)):
                    decoded = parsed
                    break
            if decoded is None:
                raise ProbeError("UNSTRUCTURED_DATA")
            value = decoded
            continue
        return value
    raise ProbeError("UNSTRUCTURED_DATA")


def didi_places(client, keyword, city="广州", near=None):
    arguments = {"keywords": keyword, "city": city}
    if near is not None:
        if client.points.get(near.poi_id) != near:
            raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")
        arguments["location"] = f"{near.lng},{near.lat}"
    rows = decode_didi(client.call_readonly("maps_textsearch", arguments))
    if isinstance(rows, dict):
        rows = rows.get("pois", rows.get("data"))
    if not isinstance(rows, list):
        raise ProbeError("INVALID_POIS")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("display_name") or row.get("name")
        location = row.get("location")
        if not isinstance(name, str) or not isinstance(location, dict):
            continue
        if any(type(location.get(k)) not in (float, int) for k in ("lng", "lat")):
            continue
        lng, lat = float(location["lng"]), float(location["lat"])
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            continue
        identity = "didi:" + hashlib.sha256(f"{name}|{lng}|{lat}".encode()).hexdigest()[:24]
        point = Point("didi", identity, name[:200], lng, lat)
        client.points[identity] = point
        client.metadata[identity] = {
            "address": row.get("address") if isinstance(row.get("address"), str) else None,
            "city": row.get("city") if isinstance(row.get("city"), str) else None,
            "open_today": None,
            "open_week": None,
        }
        result.append(point)
    return result


def didi_estimate(client, origin, destination):
    if any(
        not isinstance(p, Point) or p.provider != "didi" or client.points.get(p.poi_id) != p
        for p in (origin, destination)
    ):
        raise ProbeError("FOREIGN_OR_UNSEEN_COORDINATE")
    arguments = {}
    for prefix, point in (("from", origin), ("to", destination)):
        arguments.update(
            {
                prefix + "_lng": str(point.lng),
                prefix + "_lat": str(point.lat),
                prefix + "_name": point.title,
            }
        )
    return decode_didi(client.call_readonly("taxi_estimate", arguments))
