"""Recover actual validated final UI state from this run's isolated conversation store."""

# ruff: noqa: E402
from pathlib import Path
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from stay_agent.conversation import ConversationService
from stay_agent.conversation_store import ConversationStore


if __name__ == "__main__":
    directory = Path(sys.argv[1]).resolve()
    if not directory.is_relative_to(ROOT / "runtime"):
        raise ValueError("ISOLATED_RUN_REQUIRED")
    database = directory / "browser-conversations.sqlite"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        ids = db.execute("SELECT id,draft FROM conversations").fetchall()
    service = ConversationService(ConversationStore(database))
    try:
        states = {}
        for row in json.loads((directory / "browser.json").read_text(encoding="utf-8")):
            matches = [
                ident for ident, draft in ids if json.loads(draft) == row["turns"][-1]["draft"]
            ]
            if len(matches) != 1:
                raise ValueError("AMBIGUOUS_RECORDED_CONVERSATION")
            state = service.view(matches[0])
            if state.status != "succeeded" or state.query is None:
                raise ValueError("RECORDED_STATE_NOT_SUBMITTABLE")
            states[row["name"]] = state.model_dump(mode="json")
        (directory / "browser-validated-states.json").write_text(
            json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Recovered validated final states:", len(states), "network calls: 0")
    finally:
        service.close()
