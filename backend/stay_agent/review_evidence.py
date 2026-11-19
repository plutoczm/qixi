"""Review aggregates are opt-in, license-attested, and never fetched or scraped."""

from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import os
from .travel_rules import address_matches, city_name, normalized


def stamp(raw):
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (ValueError, OverflowError):
        return None


def safe_label(raw, limit=150):
    if (
        not isinstance(raw, str)
        or not 1 <= len(raw.strip()) <= limit
        or any(ord(c) < 32 for c in raw)
    ):
        return None
    return raw.strip()


def safe_score(raw):
    if type(raw) not in (str, int, float):
        return None
    try:
        n = Decimal(str(raw))
        return float(n) if n.is_finite() and 0 < n <= 5 else None
    except (InvalidOperation, ValueError):
        return None


class ReviewCatalog:
    """A recorded grant is an operator attestation, NOT automatic legal verification."""

    def __init__(self, source, records, expires_at, supplier_source):
        self.source = source
        self.expires_at = expires_at
        self.supplier_source = supplier_source
        self.records = {}
        for row in records:
            key = row["supplier_hotel_id"]
            if key in self.records:
                self.records[key] = None
            else:
                self.records[key] = row

    @classmethod
    def parse(cls, grant, feed, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or not isinstance(grant, dict) or not isinstance(feed, dict):
            return None
        now = now.astimezone(timezone.utc)
        uses = grant.get("allowed_uses")
        supplier_source = grant.get("supplier_source")
        if (
            grant.get("schema") != 1
            or feed.get("schema") != 1
            or supplier_source not in ("rollinggo", "tuniu")
            or not safe_label(grant.get("source_name"), 64)
            or not safe_label(grant.get("agreement_ref"))
            or not safe_label(grant.get("confirmed_by"))
            or not safe_label(grant.get("grant_id"))
            or grant["grant_id"] != feed.get("grant_id")
            or grant.get("score_semantics") != "verified_guest_mean_5"
            or grant.get("count_semantics") != "verified_stay_reviews"
            or type(uses) is not list
            or not all(isinstance(use, str) for use in uses)
            or not {"display_aggregates", "rank_hotels", "store_aggregates"}.issubset(uses)
            or not stamp(grant.get("expires_at"))
            or stamp(grant["expires_at"]) <= now
        ):
            return None
        rows = feed.get("hotels")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
            return None
        valid = []
        review_ids = Counter(
            row.get("review_hotel_id")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("review_hotel_id"), str)
        )
        supplier_ids = Counter(
            row.get("supplier_hotel_id")
            for row in rows
            if isinstance(row, dict) and type(row.get("supplier_hotel_id")) is int
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            id_ = row.get("supplier_hotel_id")
            count = row.get("review_count")
            date = stamp(row.get("observed_at"))
            score = safe_score(row.get("guest_score"))
            if (
                type(id_) is not int
                or not 0 < id_ <= 2**53 - 1
                or type(count) is not int
                or not 1 <= count <= 100000000
                or score is None
                or not date
                or not now - timedelta(days=30) <= date <= now
                or not safe_label(row.get("hotel_name"))
                or not safe_label(row.get("hotel_city"), 60)
                or not safe_label(row.get("hotel_address"), 240)
                or not safe_label(row.get("review_hotel_id"))
                or not safe_label(row.get("identity_evidence_ref"))
            ):
                continue
            # A review-provider hotel ID must not be mapped to two supplier hotels.
            if review_ids[row["review_hotel_id"]] != 1 or supplier_ids[id_] != 1:
                continue
            valid.append({**row, "guest_score": score, "observed_at": date.isoformat()})
        return (
            cls(grant["source_name"], valid, stamp(grant["expires_at"]), supplier_source)
            if valid
            else None
        )

    def apply(self, map_rows, source_hotels, city, *, supplier_source, now=None):
        now = now or datetime.now(timezone.utc)
        if now >= self.expires_at or supplier_source != self.supplier_source:
            return 0
        members = {
            s["id"]: s for s in source_hotels if isinstance(s, dict) and type(s.get("id")) is int
        }
        count = 0
        for row in map_rows:
            if row.get("source_match") != "matched":
                continue
            rid = row.get("source_hotel_id")
            data = self.records.get(rid)
            member = members.get(rid)
            if (
                not data
                or not member
                or not now - timedelta(days=30) <= stamp(data["observed_at"]) <= now
            ):
                continue
            if (
                city_name(data["hotel_city"]) != city_name(city)
                or normalized(data["hotel_name"]) != normalized(row.get("name"))
                or normalized(data["hotel_name"]) != normalized(member.get("name"))
                or address_matches(data["hotel_address"], row.get("address")) is not True
                or address_matches(data["hotel_address"], member.get("address")) is not True
            ):
                continue
            row["guest_review"] = {
                "score": data["guest_score"],
                "scale": 5,
                "review_count": data["review_count"],
                "source": self.source,
                "observed_at": data["observed_at"],
                "authorization_status": "operator_attested",
                "valid_until": min(
                    self.expires_at, stamp(data["observed_at"]) + timedelta(days=30)
                ).isoformat(),
            }
            row["review_count"] = data["review_count"]
            count += 1
        return count


def load_local_review_catalog():
    grant = os.environ.get("XUNQI_REVIEW_GRANT_FILE")
    data = os.environ.get("XUNQI_REVIEW_AGGREGATES_FILE")
    if not grant or not data:
        return None
    base = (Path(__file__).resolve().parents[2] / "runtime" / "reviews").resolve()

    def read(path):
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_relative_to(base) or resolved.stat().st_size > 256000:
            return {}

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("DUPLICATE_JSON_KEY")
                result[key] = value
            return result

        value = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        return value if isinstance(value, dict) else {}

    try:
        return ReviewCatalog.parse(read(grant), read(data))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return None


def elong_document_example(review):
    """Official published example only. ReviewScore ratio is NOT a 5-star average."""
    count = review.get("ReviewCount")
    return {
        "review_count": count if type(count) is int and count >= 0 else None,
        "guest_score": None,
    }
