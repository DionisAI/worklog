"""Accelerated boundedness test. Uses real atomic writes in a temporary workspace.

Run: PYTHONPATH=src python tools/soak.py --iterations 10000 --output docs/soak-result.json
No models/provider accounts are called. Not a real-time months-long soak.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from worklog.core import Worklog
from worklog.runtime import Runtime
from worklog.workspace import Workspace


def measure(iterations: int) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="worklog-frontier-soak-") as root:
        w = Worklog(Workspace.open(root, create=True))
        card = w.add("current", "Current work", "position 000000", "Continue from current position")["card"]
        for slot in ("1", "2", "3"):
            w.note(slot, "key lesson 000000", expected=w.read()["revision"])
        now = [1_000_000.0]
        rt = Runtime(w.ws, clock=lambda: now[0])
        samples = []
        max_frontier, max_runtime = 0, 0
        for i in range(1, iterations + 1):
            card = w.update("current", card["revision"], {"current": f"position {i:06d}"})["card"]
            w.note(str(i % 3 + 1), f"key lesson {i:06d}", expected=w.read()["revision"])
            now[0] += 1
            rt.update(f"s_{i:032d}", dirty=bool(i % 2), seen=w.read()["revision"])
            max_frontier = max(max_frontier, (w.ws.store / "frontier.json").stat().st_size)
            max_runtime = max(max_runtime, rt.path.stat().st_size)
            if i in {32, 100, 1000, iterations}:
                stat = w.stats()
                samples.append({"iterations": i, "frontier_bytes": stat["frontier_bytes"],
                                "files": stat["files"], "total_file_bytes": stat["total_file_bytes"],
                                "runtime_bytes": rt.path.stat().st_size,
                                "current_cards": stat["active_cards"], "landmarks": stat["landmarks"]})
            assert len(w.read()["notes"]) <= 3
        def sig():
            return {str(p.relative_to(w.ws.store)): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in w.ws.store.rglob("*") if p.is_file()}
        before = sig()
        for _ in range(1000):
            w.context(session="reader")
            w.read()
        readonly_unchanged = before == sig()
        assert readonly_unchanged
        assert max_frontier <= 32768 and max_runtime <= 16384
        assert all(s["files"] == 4 for s in samples)
        assert len({s["frontier_bytes"] for s in samples}) == 1
        assert not (w.ws.store / "events").exists() and not (w.ws.store / "artifacts").exists()
        # Closing hundreds of worklines leaves no task/session history.
        for i in range(200):
            transient = w.add(f"transient_{i}", "Short task", "Working", "Finish")["card"]
            w.finish(transient["id"], transient["revision"], confirmed=True)
        assert len(w.read()["cards"]) == 1 and w.stats()["files"] == 4
        return {"passed": True, "iterations": iterations, "semantic_writes_in_loop": iterations * 2,
                "unique_hook_sessions": iterations, "samples": samples,
                "max_frontier_bytes": max_frontier, "max_runtime_bytes": max_runtime,
                "readonly_context_reads": 1000, "readonly_state_reads": 1000,
                "readonly_changed_files": 0 if readonly_unchanged else -1,
                "completed_cards_created_then_deleted": 200, "remaining_current_cards": 1,
                "max_historical_landmarks": 3, "event_history_files": 0, "artifact_files": 0,
                "snapshot_backup_files": 0, "elapsed_seconds": round(time.monotonic()-started, 3),
                "scope": "Accelerated local API execution with real fsync/replace; no live agents or provider accounts; not real power-loss testing."}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--iterations", type=int, default=10000)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if not 32 <= args.iterations <= 999999:
        p.error("iterations must be 32..999999")
    result = measure(args.iterations)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)
