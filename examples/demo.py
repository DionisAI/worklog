"""A runnable local frontier handoff; no model calls, no credentials required."""
from __future__ import annotations

import json
import tempfile

from worklog.core import Worklog
from worklog.workspace import Workspace


def main() -> dict:
    with tempfile.TemporaryDirectory(prefix="worklog-frontier-demo-") as root:
        w = Worklog(Workspace.open(root, create=True, name="Frontier demo"))
        c = w.add("replay", "Fix duplicate counting", "Located duplicate branch", "Add regression test")["card"]
        c = w.claim("replay", c["revision"], session="claude_a", harness="claude", account="A")["card"]
        c = w.update("replay", c["revision"], {"current": "Implementation updated; regression not yet run",
                    "next": "Run the regression test", "cautions": ["Do not drop legitimate out-of-order events"]},
                    session="claude_a", release=True)["card"]
        c = w.claim("replay", c["revision"], session="codex_b", harness="codex", account="B")["card"]
        # This demo does not assert a real test was run. It demonstrates truthful unknowns.
        c = w.update("replay", c["revision"], {"current": "Review complete; test execution remains a user task",
                    "next": "Run actual project tests before declaring completion"}, session="codex_b")["card"]
        for slot, lesson in (("1", "Timestamp alone is not a safe dedup key"),
                             ("2", "Check existing jobs before restarting them"),
                             ("3", "Do not infer success from a process exit")):
            w.note(slot, lesson, expected=w.read()["revision"])
        w.note("3", "Keep verified facts separate from untested claims", expected=w.read()["revision"])
        stats = w.stats()
        assert stats["active_cards"] == 1 and stats["landmarks"] == 3
        assert stats["event_history"] is False
        print(w.context())
        return {"demo_passed": True, "stats": stats, "note": "Agent labels simulated; no external tests or model calls claimed."}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
