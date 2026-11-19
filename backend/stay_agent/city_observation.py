"""Bounded, ephemeral observations made BEFORE supplier DTO projection.

Absence in SQLite/DTOs cannot establish absence in a supplier response. Only
this process's same-search observation may open the missing-city exception.
No raw response, geographic code, price, link or credential is retained.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import threading

from .storage import SessionError

_CITY_FIELDS = ("city", "cityName", "cityname", "cityCode", "citycode", "cityId", "city_id")
_REGION_FIELDS = ("province", "provinceName", "provinceCode")
_COUNTRY_FIELDS = ("country", "countryName", "countryCode")
_CHINA = {"cn", "中国", "中国大陆", "中华人民共和国", "china", "mainland china"}


def _empty(value):
    return value is None or isinstance(value, str) and not value.strip()


def members_snapshot(hotels):
    return tuple(sorted((h["id"], h.get("name"), h.get("address")) for h in hotels))


@dataclass(frozen=True)
class CityObservation:
    city_status: str
    country_supported: bool
    identity_conflict: bool
    observed_at: str


@dataclass(frozen=True)
class CitySnapshot:
    query: dict
    members: tuple
    observations: dict[int, CityObservation]
    expires_epoch: float
    store: object


class CityObservationRegistry:
    def __init__(self):
        self._rows: dict[str, CitySnapshot] = {}
        self._lock = threading.Lock()

    def observe(self, request_id, query, raw, hotels, store, observed_at):
        """Observe all raw duplicates, including those outside projected limit."""
        context, members = store.list_hotels(request_id)
        if context != {**query, "_supplier_source": "rollinggo"}:
            return
        if members_snapshot(members) != members_snapshot(hotels):
            return
        observations = {}
        for member in members:
            candidates = [r for r in raw if isinstance(r, dict)
                          and type(r.get("hotelId")) is int and r["hotelId"] == member["id"]]
            identity = (member["name"], member.get("address"))
            # Reject projection truncation/placeholders as well as conflicting IDs.
            conflict = not candidates or any(
                tuple(v.strip() if isinstance(v, str) and v.strip() else None
                      for v in (r.get("name"), r.get("address"))) != identity
                for r in candidates
            )
            missing = bool(candidates) and all(
                _empty(r.get(key)) for r in candidates for key in (*_CITY_FIELDS, *_REGION_FIELDS)
            )
            country = bool(candidates) and all(
                r.get("areaCode") == "CN" and all(
                    _empty(r.get(key)) or isinstance(r.get(key), str)
                    and r[key].strip().casefold() in _CHINA
                    for key in _COUNTRY_FIELDS
                ) for r in candidates
            )
            observations[member["id"]] = CityObservation(
                "missing" if missing else "present_unverified", country, conflict, observed_at,
            )
        snapshot = CitySnapshot(deepcopy(context), members_snapshot(members), observations,
                                store.ensure_live(request_id), store)
        with self._lock:
            for key, row in list(self._rows.items()):
                if row.expires_epoch <= row.store.clock():
                    self._rows.pop(key, None)
            self._rows.pop(request_id, None)
            while len(self._rows) >= 64:
                self._rows.pop(next(iter(self._rows)))
            self._rows[request_id] = snapshot

    def lookup(self, request_id, query, hotels, store):
        with self._lock:
            row = self._rows.get(request_id)
        if row is None or row.store is not store:
            return None
        try:
            current_query, current_hotels = store.list_hotels(request_id)
            expiry = store.ensure_live(request_id)
        except SessionError:
            return None
        if (expiry != row.expires_epoch or row.query != query or current_query != row.query
            or members_snapshot(hotels) != row.members
            or members_snapshot(current_hotels) != row.members):
            return None
        return row

    def publish(self, request_id, staged, store):
        """Bind a completed staged observation to its now persisted members."""
        query, hotels = store.list_hotels(request_id)
        expiry = store.ensure_live(request_id)
        with self._lock:
            row = self._rows.get(request_id)
            if (row is not None and row.store is staged and row.query == query
                    and row.members == members_snapshot(hotels)):
                self._rows[request_id] = replace(row, expires_epoch=expiry, store=store)
