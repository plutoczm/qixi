"""Derive the r70 bed-place fixture from the r68 one, through the real models.

The r68 fixture already contains a dormitory row ("青旅四人床位房") that the old
engine excluded by default. This script re-runs each row through ``assess`` and
``reference_gate`` with ``accepts_dormitory=True`` and writes a new quotes
payload, so the browser test renders payloads produced by the contract models
instead of hand-written JSON that could drift from them.

Run: ``python scripts/export_r70_bed_fixture.py``
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.contracts import ReferenceGate, Qualification  # noqa: E402
from stay_agent.pricing import QuoteInput, assess, lowest_qualified  # noqa: E402
from stay_agent.qualification import reference_gate  # noqa: E402

SOURCE = ROOT / "frontend/e2e/fixtures/r68-quotes.json"
SEARCH_SOURCE = ROOT / "frontend/e2e/fixtures/r68-search.json"
TARGET = ROOT / "frontend/e2e/fixtures/r70-bed-quotes.json"
SEARCH_TARGET = ROOT / "frontend/e2e/fixtures/r70-bed-search.json"


def main() -> int:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    context = data["context"]
    nights = (
        date.fromisoformat(context["check_out"])
        - date.fromisoformat(context["check_in"])
    ).days

    verdicts = []
    for quote in data["quotes"]:
        item = QuoteInput(
            source_id="tuniu",
            room_name=quote["room_name"],
            amount_fen=quote["amount_fen"],
            price_unit="unstated",
            remaining=quote.get("source_inventory_observed"),
            nights=nights,
            rooms=context["rooms"],
            adults=context["adults"],
            children=len(context["children_ages"]),
            max_occupancy=quote.get("max_occupancy"),
            cancellation_text=quote["cancellation"]["text"],
            budget_fen=context["budget_fen"],
            accepts_dormitory=True,
        )
        assessment = assess(item)
        quote["qualification"] = Qualification.from_assessment(
            assessment).model_dump(mode="json")
        quote["reference"] = ReferenceGate.from_gate(reference_gate(
            "tuniu",
            nights=nights,
            rooms=context["rooms"],
            adults=context["adults"],
            remaining=quote.get("source_inventory_observed"),
            snapshot_at=data["queried_at"],
            room_name=quote["room_name"],
            accepts_dormitory=True,
        )).model_dump(mode="json")
        verdicts.append(assessment)

    best = lowest_qualified(verdicts)
    data["qualified_lowest_id"] = data["quotes"][best]["id"] if best is not None else None
    data["context"]["accepts_dormitory"] = True
    TARGET.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )

    search = json.loads(SEARCH_SOURCE.read_text(encoding="utf-8"))
    search["context"]["accepts_dormitory"] = True
    SEARCH_TARGET.write_text(
        json.dumps(search, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"wrote {TARGET.relative_to(ROOT)}: "
          f"{[q['qualification']['tier'] for q in data['quotes']]} "
          f"lowest={data['qualified_lowest_id']}")
    print(f"wrote {SEARCH_TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
