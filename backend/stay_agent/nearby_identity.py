"""Local missing-city exception; never invokes providers or writes SQLite.

All state transitions run under the owning NearbyContext lock. Automatic
source_match and review/ranking evidence are deliberately not modified.
"""
from dataclasses import dataclass, field
import re

from .city_observation import members_snapshot
from .storage import SessionError
from .travel_rules import address_matches, city_name, normalized

_MAINLAND = frozenset("11 12 13 14 15 21 22 23 31 32 33 34 35 36 37 41 42 43 44 45 46 50 51 52 53 54 61 62 63 64 65".split())
_CHINA = {"中国", "中国大陆", "中华人民共和国", "china", "mainland china", "prc"}


class IdentityError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class IdentityState:
    revision: int = 0
    bindings: dict = field(default_factory=dict)
    operations: dict = field(default_factory=dict)
    intents: dict = field(default_factory=dict)
    invalidated: bool = False


def _admin(meta, city):
    code = meta.get("adcode")
    if type(code) not in (str, int):
        return None
    code = str(code).strip()
    if not re.fullmatch(r"[0-9]{6}", code) or code[:2] not in _MAINLAND or code[2:] == "0000":
        return None
    if city_name(meta.get("city")) != city_name(city):
        return None
    province = meta.get("province")
    if isinstance(province, str) and province.strip().casefold().startswith(
        ("香港", "澳门", "澳門", "台湾", "臺灣", "hong kong", "macau", "macao", "taiwan")
    ):
        return None
    return code[:4]


def _source(context, request, sessions, registry, clock):
    if not context.explored or context.selected != request.anchor_id:
        raise IdentityError("POINT_NOT_IN_CONTEXT")
    if not context.source_request_id or request.request_id != context.source_request_id:
        raise IdentityError("IDENTITY_CONTEXT_MISMATCH")
    if context.identity.invalidated:
        raise IdentityError("IDENTITY_SNAPSHOT_CHANGED")
    try:
        query, hotels = sessions.list_hotels(request.request_id)
        expiry = sessions.ensure_live(request.request_id)
    except SessionError:
        raise IdentityError("SOURCE_SESSION_MISSING") from None
    if (query != context.source_query or members_snapshot(hotels) != context.source_members):
        context.identity.invalidated = True
        context.identity.bindings.clear()
        raise IdentityError("IDENTITY_SNAPSHOT_CHANGED")
    remaining = min(context.expires - clock(), context.source_expires - clock(),
                    expiry - sessions.clock())
    if remaining <= 0:
        raise IdentityError("SOURCE_SESSION_MISSING")
    if (query.get("_supplier_source") != "rollinggo" or not query.get("poi_name")
        or query.get("place_type") != "城市"
        or city_name(query.get("place")) != city_name(context.city)
        or not isinstance(query.get("country_region"), (str, type(None)))
        or (query.get("country_region") is not None
            and query["country_region"].casefold() not in _CHINA)):
        return hotels, None, remaining
    observation = registry.lookup(request.request_id, query, hotels, sessions) if registry else None
    return hotels, observation, remaining


def _pairs(context, hotels, observation):
    if observation is None:
        return []
    anchor = context.anchors[context.selected]
    anchor_admin = _admin(context.client.metadata.get(anchor.poi_id, {}), context.city)
    if anchor_admin is None:
        return []
    matches, reverse = {}, {}
    for option, row in context.hotel_rows.items():
        candidates = [h for h in hotels if normalized(row["name"]) == normalized(h.get("name"))
                      and address_matches(row["address"], h.get("address")) is True]
        matches[option] = candidates
        for candidate in candidates:
            reverse[candidate["id"]] = reverse.get(candidate["id"], 0) + 1
    automatic_ids = {r["source_hotel_id"] for r in context.hotel_rows.values()
                     if r["source_match"] == "matched"}
    pairs = []
    for option, candidates in matches.items():
        row = context.hotel_rows[option]
        if len(candidates) != 1 or row["source_match"] != "unverified":
            continue
        hotel = candidates[0]
        observed = observation.observations.get(hotel["id"])
        if (reverse[hotel["id"]] != 1 or hotel["id"] in automatic_ids or observed is None
            or observed.identity_conflict or not observed.country_supported
            or observed.city_status != "missing"):
            continue
        point = context.hotels[option]
        meta = context.client.metadata.get(point.poi_id, {})
        # Never match projected/truncated map text against a different identity.
        if (point.title.strip() != row["name"] or meta.get("address") != row["address"]
            or _admin(meta, context.city) != anchor_admin
            or meta.get("city") != row["city"]):
            continue
        pairs.append({
            "hotel_option_id": option, "source_hotel_id": hotel["id"],
            "map_name": row["name"], "map_address": row["address"], "map_city": row["city"],
            "source_name": hotel["name"], "source_address": hotel["address"],
            "source_city_status": "missing", "source_observed_at": observed.observed_at,
        })
    return pairs


def identity_action(context, request, sessions, registry, clock, action):
    """Caller owns context.lock; failed transitions never return usable bindings."""
    hotels, observation, remaining = _source(context, request, sessions, registry, clock)
    pairs = _pairs(context, hotels, observation)
    state = context.identity
    valid_pairs = {(p["hotel_option_id"], p["source_hotel_id"]) for p in pairs}
    if any((key, value["source_hotel_id"]) not in valid_pairs for key, value in state.bindings.items()):
        state.bindings.clear()
        state.invalidated = True
        raise IdentityError("IDENTITY_SNAPSHOT_CHANGED")
    if action != "status":
        if request.hotel_option_id not in context.hotels:
            raise IdentityError("POINT_NOT_IN_CONTEXT")
        fingerprint = (action, request.hotel_option_id, request.source_hotel_id,
                       request.expected_revision, request.intent_seq,
                       getattr(request, "target_operation_id", None))
        prior = state.operations.get(request.operation_id)
        if prior is not None:
            if prior != fingerprint:
                raise IdentityError("IDENTITY_OPERATION_CONFLICT")
            # Current state, never a cached pre-revocation binding.
        else:
            if len(state.operations) >= 64:
                raise IdentityError("IDENTITY_OPERATION_LIMIT")
            if (request.expected_revision != state.revision
                or request.intent_seq <= state.intents.get(request.hotel_option_id, 0)):
                raise IdentityError("IDENTITY_REVISION_CONFLICT")
            binding = state.bindings.get(request.hotel_option_id)
            if action == "confirm":
                if (request.hotel_option_id, request.source_hotel_id) not in valid_pairs:
                    raise IdentityError("IDENTITY_NOT_ELIGIBLE")
                if binding is not None or any(b["source_hotel_id"] == request.source_hotel_id
                                               for b in state.bindings.values()):
                    raise IdentityError("IDENTITY_PAIR_OCCUPIED")
                state.bindings[request.hotel_option_id] = {
                    "hotel_option_id": request.hotel_option_id,
                    "source_hotel_id": request.source_hotel_id,
                    "operation_id": request.operation_id,
                    "identity_basis": "user_confirmed",
                }
            else:
                if binding is not None and (
                    binding["operation_id"] != request.target_operation_id
                    or binding["source_hotel_id"] != request.source_hotel_id
                ):
                    raise IdentityError("IDENTITY_OPERATION_CONFLICT")
                state.bindings.pop(request.hotel_option_id, None)
                # A revoke with no binding is a tombstone for an unsent/late confirm.
            state.operations[request.operation_id] = fingerprint
            state.intents[request.hotel_option_id] = request.intent_seq
            state.revision += 1
    reason = ("住宿来源未独立提供城市；这次同店身份由你核对确认。"
              if pairs else "本轮没有可人工确认的唯一同店对；城市观察未知或证据不满足时，请显式重新查询后核对，不会自动查源。")
    return {
        "status": "complete", "revision": state.revision,
        "intent_seq": max(state.intents.values(), default=0), "expires_in_seconds": remaining,
        "bindings": list(state.bindings.values()), "eligible_pairs": pairs,
        "reason": reason, "error": None,
    }
