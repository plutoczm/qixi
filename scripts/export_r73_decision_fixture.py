"""Export the P7 offline browser fixture from the real contract models.

Reads the existing r68 quote payload (itself produced by the real Pydantic
contract), runs the *production* decision builder over it, and attaches the
result. Zero supplier HTTP: the only thing this does is compute rules.

The resulting decision is deliberately ``not_comparable``: live sources do
not supply a guest score or a review count, so a browser fixture that showed
a real ranking would be inventing data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from stay_agent.contracts import QuotesResponse  # noqa: E402
from stay_agent.decision import build_quote_decision  # noqa: E402


def main() -> int:
    fixtures = ROOT / "frontend" / "e2e" / "fixtures"
    source = fixtures / "r68-quotes.json"
    target = fixtures / "r73-quotes.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    response = QuotesResponse.model_validate(data)
    decision = build_quote_decision(response)
    data["decision"] = json.loads(decision.model_dump_json())
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {target.name}: status={decision.status}, "
        f"assessments={len(decision.assessments)}, "
        f"excluded={len(decision.excluded_ids)}, reference={len(decision.reference_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
