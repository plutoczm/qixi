"""FlyAI structured hotel-discovery projection (discovery only, never quotes).

The r51k live sample (2026-11-04) shows itemList entries carrying ``shId``,
``name``, ``address``, ``price`` as a display string, ``latitude``,
``longitude``, ``star``, ``interestsPoi`` and ``detailUrl``. There is still no
currency field, no room type, no inventory, no occupancy and no booking
context, so a hint can never become a quote, a jump or a room.

Two extra boundaries from that sample:

* ``price`` is a display string like "¥100" with **no** currency field, so the
  integer amount stays unknown; the string is kept verbatim for display only.
* ``detailUrl`` is a ``router.feizhu.com/multi/webview`` wrapper without date or
  occupancy context. Only its presence is recorded; it is never stored, shown
  or used as a booking link.

Hints must not be merged with Tuniu/RollingGo identities, promoted to rooms, or
turned into distances: coordinates from this source are not mixed with a map
anchor from another source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import os
from typing import Callable, Mapping
from uuid import uuid4

from stay_agent.contracts import SearchInput

from .flyai_client import FlyAIError
from .tuniu import StayScope, TuniuFailure, _text, _yuan_to_fen

SOURCE = "flyai"
SEARCH_COMMAND = "search-hotel"


@dataclass(frozen=True)
class FlyAIHotelHint:
    opaque_id: str
    name: str
    address: str
    source_city: str | None
    search_price_fen: int | None
    #: Supplier display string (e.g. "¥100"); never parsed into money here.
    price_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    star: str | None = None
    #: Supplier "近某地铁站" hint. Descriptive only, never a route or distance.
    nearby_transit: str | None = None
    #: Recorded as a boolean: the URL itself is not kept or used.
    detail_url_present: bool = False
    provider: str = SOURCE
    # An incoming search city is a query, never evidence that the supplier
    # independently confirmed the listing's city or its identity.
    city_verified: bool = False
    booking_url: None = None


@dataclass(frozen=True)
class FlyAIDiscoveryEvidence:
    request_id: str
    scope: StayScope
    observed_at: str
    hints: tuple[FlyAIHotelHint, ...]
    status: str
    excluded_count: int
    limited: bool
    provider: str = SOURCE
    data_mode: str = "test"
    #: Which credential the bundle actually used, so a probe never claims the
    #: project key was exercised when the fallback chain supplied one.
    credential_source: str | None = None


def _coord(value, limit: float) -> float | None:
    """Accept a plausible coordinate only; never guess or repair one."""
    if type(value) not in (int, float):
        return None
    number = float(value)
    if number != number or not -limit <= number <= limit or number == 0:
        return None
    return number


def _search_price(value, currency) -> int | None:
    # The yen/yuan glyph alone is ambiguous for non-China destinations.
    # The published sample omits currency; leave its search price UNKNOWN.
    if currency != "CNY" or type(value) is not str or not value.startswith("¥"):
        return None
    return _yuan_to_fen(value[1:])


#: ``disabled`` is the production default; ``cli`` is an explicit opt-in that
#: runs exactly one bounded ``search-hotel`` subprocess call.
MODES = ("disabled", "offline_test", "cli", "mcp")

#: Client-level errors that mean "not wired up yet", never "the supplier said
#: no". They must not be reported as a source outage.
_CONFIG_ERRORS = frozenset(("INVALID_CONFIGURATION", "INVALID_ARGUMENTS"))


class FlyAIDiscoveryProvider:
    def __init__(self, call_search: Callable[[dict], Mapping] | None = None,
                 *, mode: str | None = None):
        selected = os.environ.get("XUNQI_FLYAI_MODE", "disabled") if mode is None else mode
        if selected not in MODES:
            raise TuniuFailure("PROVIDER_DISABLED")
        self.mode = selected
        self._callback = call_search
        self._live_callback: Callable[[dict], Mapping] | None = None
        #: ``project_env`` when the project supplied the key, ``cli_default_chain``
        #: when the bundle fell back on its own chain (owner unproven), ``None``
        #: in offline/test modes where no credential is involved.
        self.credential_source: str | None = None

    def _callback_for_call(self) -> Callable[[dict], Mapping] | None:
        if self._callback is not None:
            return self._callback
        if self.mode != "cli":
            return None
        if self._live_callback is None:
            from .flyai_client import FlyAICliClient, FlyAIError, load_key, search_callback
            try:
                client = FlyAICliClient(load_key())
            except FlyAIError as error:
                # Only an uninstalled bundle counts as "not connected". A blank
                # key is deliberately *not* fatal: the bundle resolves its own
                # chain, and the resulting credential owner is reported instead
                # of silently blocking a working channel.
                raise TuniuFailure(
                    "NOT_CONNECTED" if error.code == "INVALID_CONFIGURATION"
                    else "SOURCE_ERROR"
                ) from None
            self.credential_source = client.credential_source
            self._live_callback = search_callback(client)
        return self._live_callback

    def search(
        self, query: SearchInput, *, city: str, keyword: str | None = None,
    ) -> FlyAIDiscoveryEvidence:
        if self.mode == "disabled":
            raise TuniuFailure("PROVIDER_DISABLED")
        callback = self._callback_for_call()
        if callback is None:
            raise TuniuFailure("NOT_CONNECTED")
        if not (city := _text(city, 40)):
            raise TuniuFailure("INVALID_ARGUMENTS")
        if keyword is not None and (keyword := _text(keyword, 120)) is None:
            raise TuniuFailure("INVALID_ARGUMENTS")
        scope = StayScope.from_query(query, city)
        # Canonical MCP argument names: the published bundle forwards exactly
        # these names to the `search_hotels` tool, and the CLI transport maps
        # them back to its own flags.
        args = {
            "destName": city,
            "checkInDate": scope.check_in,
            "checkOutDate": scope.check_out,
        }
        if keyword:
            args["keyWords"] = keyword
        if scope.budget_fen is not None:
            yuan = Decimal(scope.budget_fen) / 100
            args["maxPrice"] = int(yuan) if yuan == yuan.to_integral_value() else float(yuan)
        try:
            # One call, no retry. In ``cli`` mode this is a single bounded
            # subprocess; in ``offline_test`` mode it is an injected callback.
            response = callback(args)
        except FlyAIError as error:
            # A missing bundle (or a rejected call shape) is a configuration
            # gap, not a supplier failure; only real transport/tool errors are
            # reported as SOURCE_ERROR.
            raise TuniuFailure(
                "NOT_CONNECTED" if error.code in _CONFIG_ERRORS
                else error.code if error.code in {"TIMEOUT", "RATE_LIMITED", "AUTHENTICATION_FAILED"}
                else "SOURCE_ERROR"
            ) from None
        except Exception:
            raise TuniuFailure("SOURCE_ERROR") from None
        if not isinstance(response, Mapping):
            raise TuniuFailure("INVALID_RESPONSE")
        if type(response.get("status")) is not int or response["status"] != 0:
            raise TuniuFailure("SOURCE_ERROR")
        data = response.get("data")
        if not isinstance(data, Mapping) or not isinstance(data.get("itemList"), list):
            raise TuniuFailure("INVALID_RESPONSE")
        rows = data["itemList"]
        if len(rows) > 200:
            raise TuniuFailure("INVALID_RESPONSE")
        observed: dict[str, FlyAIHotelHint] = {}
        conflicted: set[str] = set()
        excluded = 0
        for row in rows:
            if not isinstance(row, Mapping):
                excluded += 1
                continue
            opaque_id = _text(row.get("shId"), 80)
            name = _text(row.get("name"))
            address = _text(row.get("address"))
            if not (opaque_id and name and address):
                excluded += 1
                continue
            source_city = _text(row.get("cityName"), 40)
            # The published example has no independently sourced cityName.
            if source_city and source_city != city:
                excluded += 1
                continue
            hint = FlyAIHotelHint(
                opaque_id=opaque_id, name=name, address=address,
                source_city=source_city,
                search_price_fen=_search_price(row.get("price"), row.get("currency")),
                price_text=_text(row.get("price"), 20),
                latitude=_coord(row.get("latitude"), 90),
                longitude=_coord(row.get("longitude"), 180),
                star=_text(row.get("star"), 20),
                nearby_transit=_text(row.get("interestsPoi"), 40),
                detail_url_present=bool(_text(row.get("detailUrl"), 400)),
                city_verified=source_city == city,
            )
            if opaque_id in observed:
                if observed[opaque_id] != hint:
                    conflicted.add(opaque_id)
                continue
            observed[opaque_id] = hint
        hints = tuple(value for key, value in observed.items() if key not in conflicted)
        excluded += len(conflicted)
        limited = len(hints) > scope.limit
        hints = hints[:scope.limit]
        return FlyAIDiscoveryEvidence(
            request_id=str(uuid4()), scope=scope,
            observed_at=datetime.now(timezone.utc).isoformat(),
            hints=hints, status="partial" if excluded or limited else "complete",
            excluded_count=excluded, limited=limited,
            data_mode="live" if self.mode in {"cli", "mcp"} else "test",
            credential_source=self.credential_source,
        )
