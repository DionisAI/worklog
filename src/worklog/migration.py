"""Explicit OFFLINE v0.1 -> frontier conversion; legacy history is discarded, not archived."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .errors import Conflict, Corruption, Invalid
from .model import blank, envelope, make_card
from .util import atomic_write, canonical, digest, file_lock, fsync_dir, load_json
from .workspace import Workspace

TABLES = ("tasks", "sessions", "attempts", "checkpoints", "handoffs", "jobs", "evidence", "requests")
TERMINAL = {"reported_succeeded", "reported_failed", "reported_cancelled"}


def read_legacy(ws: Workspace) -> tuple[dict, str]:
    state = {key: {} for key in TABLES}
    head = "0" * 64
    count = 0
    # Only the offline migration walks the old history. Runtime never imports this path.
    for count, path in enumerate(sorted((ws.store / "events").glob("*.json")), 1):
        if path.name != f"{count:016d}.json":
            raise Corruption("Old ledger has an event sequence gap; refuse lossy migration")
        try:
            event = json.loads(path.read_bytes())
            sha = event.pop("hash")
            if (event["schema_version"] != 1 or event["seq"] != count
                    or event["workspace_id"] != ws.workspace_id
                    or event["prev_hash"] != head or digest(event) != sha):
                raise Corruption("Old event integrity check failed")
            for table, values in event["changes"].items():
                if table not in state or not isinstance(values, dict):
                    raise Corruption("Unknown old event table")
                state[table].update(values)
            head = sha
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Corruption("Malformed old ledger event") from exc
    return state, digest({"workspace_id": ws.workspace_id, "count": count, "head": head})


def _live_ids(old: dict) -> set[str]:
    result = {tid for tid, task in old["tasks"].items() if task.get("status") != "verified"}
    for job in old["jobs"].values():
        if job.get("status") not in TERMINAL:
            if job.get("task_id") not in old["tasks"]:
                raise Corruption("An unsettled old job has no task")
            result.add(job["task_id"])
    return result


def plan(ws: Workspace) -> dict:
    with file_lock(ws.lock), file_lock(ws.store / "locks" / "ledger.lock"):
        meta = load_json(ws.store / "workspace.json", 4096)
        if meta.get("schema_version") != 1:
            raise Invalid("Migration planning is only for a v0.1 workspace")
        old, source = read_legacy(ws)
    frontier = blank(ws.workspace_id, ws.name)
    errors = []
    for tid in sorted(_live_ids(old)):
        task = old["tasks"][tid]
        cp = old["checkpoints"].get(task.get("latest_semantic_checkpoint") or task.get("latest_checkpoint"), {})
        handoff = old["handoffs"].get(task.get("latest_handoff"), {})
        # v0.1 checkpoints use observed_at; handoffs use created_at.
        # Absence of a handoff must NEVER replace a real checkpoint with {}.
        checkpoint_time = cp.get("observed_at", cp.get("created_at", 0))
        note = handoff if handoff and (not cp or handoff.get("created_at", 0) >= checkpoint_time) else cp
        current = note.get("summary") or task.get("objective") or task["title"]
        next_action = note.get("next_action") or "Inspect current files and continue the unfinished task."
        cautions = list(dict.fromkeys(note.get("unknowns") or []))
        refs = []
        for job in old["jobs"].values():
            if job["task_id"] != tid or job.get("status") in TERMINAL:
                continue
            process = job.get("process", {})
            cautions.append(f"Existing job {job['id']}: last status {job.get('status')}; PID {process.get('pid')}. Inspect it before restarting anything.")
            if job.get("log"):
                refs.append(job["log"])
            if job.get("tmux"):
                refs.append("tmux:" + job["tmux"])
        try:
            card = make_card(tid, task["title"], current, next_action, cautions=cautions,
                             references=list(dict.fromkeys(refs)), status="needs_check")
        except Invalid as exc:
            # Do not truncate a caution or silently omit active work from the proposal.
            errors.append(f"{tid}: {exc}")
            from .util import revision
            card = {"id": tid, "revision": revision(), "title": task["title"], "current": current,
                    "next": next_action, "cautions": cautions, "references": refs,
                    "status": "needs_check", "owner": None}
        frontier["cards"][tid] = card
    try:
        envelope(frontier)
    except Invalid as exc:
        errors.append(str(exc))
    return {"format": "worklog-frontier-migration-v1", "source_digest": source,
            "frontier": frontier, "acknowledged_omissions": [], "validation_errors": errors,
            "review_required": [
                "Review CURRENT summaries/next/cautions; automatic extraction cannot decide which old claims are still valid.",
                "Choose at most 3 genuine historical landmarks in frontier.notes (keys 1,2,3); none are guessed automatically.",
                "Owners, old sessions, attempts, event history, captured outputs, fingerprints and backups are NOT carried over.",
                "Unsettled jobs are included as current cautions and external references, not restarted or stopped.",
                "Apply deletes legacy history/artifacts. Stop all v0.1 agents first. Do not delete files directly."]}


def _cleanup(ws: Workspace) -> None:
    for name in ("events", "artifacts", "views", "locks"):
        target = ws.store / name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
    for path in ws.store.iterdir():
        if path.name == "init.lock" or path.name.startswith(".tmp-"):
            if path.is_file() or path.is_symlink():
                path.unlink()
    fsync_dir(ws.store)


def apply(ws: Workspace, proposal: dict, *, discard_history: bool,
          agents_stopped: bool) -> dict:
    if not discard_history or not agents_stopped:
        raise Invalid("Offline migration requires --discard-history AND --confirm-agents-stopped. Review the proposal first.")
    if not isinstance(proposal, dict) or proposal.get("format") != "worklog-frontier-migration-v1":
        raise Invalid("Not a Worklog migration proposal")
    state = proposal.get("frontier")
    raw = envelope(state)
    if state["workspace_id"] != ws.workspace_id or state["name"] != ws.name:
        raise Invalid("Proposal belongs to another workspace")
    target_digest = digest(state)
    source_digest = proposal.get("source_digest")
    if not isinstance(source_digest, str) or len(source_digest) != 64:
        raise Invalid("Invalid migration source digest")
    marker_path = ws.store / "migration.json"
    # All old agents must be stopped, even though this also takes their ledger lock.
    # Old processes caching v0.1 workspace metadata are outside this migration boundary.
    with file_lock(ws.lock):
        meta = load_json(ws.store / "workspace.json", 4096)
        if marker_path.exists():
            marker = load_json(marker_path, 4096)
            if marker != {"source_digest": source_digest, "target_digest": target_digest}:
                raise Conflict("An interrupted migration used a different proposal; resume that exact proposal")
            if meta.get("schema_version") == 2:
                saved = load_json(ws.store / "frontier.json", 32768)
                if saved.get("sha256") != target_digest or digest(saved.get("state")) != target_digest:
                    raise Corruption("Pending migration frontier differs from its committed target")
        elif meta.get("schema_version") == 2:
            saved = load_json(ws.store / "frontier.json", 32768)
            if saved.get("sha256") == target_digest and digest(saved.get("state")) == target_digest:
                return {"migrated": True, "already_applied": True, "history_archived": False}
            raise Conflict("This workspace is already frontier mode; do not apply an old proposal over newer work")
        else:
            with file_lock(ws.store / "locks" / "ledger.lock"):
                old, source = read_legacy(ws)
                if source != source_digest:
                    raise Conflict("Old ledger changed after the proposal. Stop agents and regenerate/review the plan.")
                live = _live_ids(old)
                omissions = proposal.get("acknowledged_omissions", [])
                if not isinstance(omissions, list) or any(not isinstance(x, str) for x in omissions):
                    raise Invalid("acknowledged_omissions must be a list of old task IDs explicitly merged/resolved by the reviewer")
                if live - set(state["cards"]) - set(omissions):
                    raise Invalid("Unfinished tasks would be silently dropped. Preserve them or explicitly list reviewed merged/resolved IDs in acknowledged_omissions.")
                atomic_write(marker_path, canonical({"source_digest": source_digest, "target_digest": target_digest}) + b"\n")
        # Repeating after any crash resumes this fixed publication sequence.
        atomic_write(ws.store / "frontier.json", raw)
        atomic_write(ws.store / "workspace.json", canonical({"schema_version": 2,
                     "workspace_id": ws.workspace_id, "name": ws.name, "mode": "frontier"}) + b"\n")
        _cleanup(ws)
        marker_path.unlink()
        fsync_dir(ws.store)
    return {"migrated": True, "active_cards": len(state["cards"]), "landmarks": len(state["notes"]),
            "history_archived": False, "legacy_history_deleted": True,
            "note": "Reinstall BOTH hook adapters. Disable any old native Worklog plugin to avoid duplicate hooks."}
