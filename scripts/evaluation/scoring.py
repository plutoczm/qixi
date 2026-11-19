"""Deterministic assertions stay separate from qualitative LLM judgments."""


def score_turn(row, expected, prior=None):
    checks = []
    review = []
    draft = row.get("draft", {})
    success = row.get("status") == "succeeded" and row.get("error") is None
    factual = {
        "place",
        "place_type",
        "check_in",
        "check_out",
        "nights",
        "adults",
        "rooms",
        "children_count",
        "children_ages",
        "budget_fen",
        "budget_basis",
        "notes",
        "visits",
    }
    for key, value in expected.items():
        if key in factual or key == "query_ready":
            actual = row.get("query_ready") if key == "query_ready" else draft.get(key)
            checks.append(
                dict(
                    field=key,
                    expected=value,
                    actual=actual,
                    passed=success
                    and (key in row if key == "query_ready" else key in draft)
                    and actual == value,
                )
            )
        elif key == "preserve":
            for field in value:
                before = (prior or {}).get("draft", {}).get(field)
                actual = draft.get(field)
                present = field in draft and field in (prior or {}).get("draft", {})
                checks.append(
                    dict(
                        field="preserve." + field,
                        expected=before,
                        actual=actual,
                        passed=success and present and actual == before,
                    )
                )
        elif key in {
            "visit_contains",
            "visit_excludes",
            "visits_include",
            "stay_near",
            "area_hint_contains",
        }:
            visits = draft.get("visits", [])
            names = [v["name"] for v in visits]
            if key == "visit_contains":
                ok = any(value in name for name in names)
            elif key == "visit_excludes":
                ok = all(value not in name for name in names)
            elif key == "visits_include":
                ok = all(any(name in found for found in names) for name in value)
            elif key == "stay_near":
                ok = bool(visits) and any(v["stay_near"] is value for v in visits)
            else:
                ok = any(value in (v.get("area_hint") or "") for v in visits)
            checks.append(dict(field=key, expected=value, actual=visits, passed=success and ok))
        else:
            review.append(key)
    return dict(
        passed=sum(c["passed"] for c in checks),
        failed=sum(not c["passed"] for c in checks),
        checks=checks,
        requires_review=review,
    )
