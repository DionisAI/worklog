from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .core import Worklog
from .errors import Invalid, WorklogError
from .hooks import handle, install
from .model import STATUSES
from .runtime import Runtime
from .util import revision
from .workspace import Workspace


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="worklog", description="Current frontier + at most 3 GLOBAL historical landmarks. No event log.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--root", default=".", help="Workspace path (put this before the subcommand)")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize frontier mode; will not overwrite a v0.1 ledger")
    init.add_argument("--name")
    sub.add_parser("status", help="Read the entire current state as JSON; no write")
    sub.add_parser("stats", help="Inspect actual size/file count and bounds")
    sub.add_parser("doctor", help="Validate bounded frontier and report unexpected old files")
    show = sub.add_parser("show", help="Read one current card")
    show.add_argument("id")
    context = sub.add_parser("context", help="Full current context; bounded by admission, never silently truncated")
    context.add_argument("--card")
    context.add_argument("--session", default=os.environ.get("WORKLOG_SESSION_ID"))
    add = sub.add_parser("add", help="Create a current workline, NOT a new card for each session")
    add.add_argument("id")
    add.add_argument("--title", required=True)
    add.add_argument("--current", required=True)
    add.add_argument("--next", required=True)
    add.add_argument("--caution", action="append", default=[])
    add.add_argument("--reference", action="append", default=[])
    add.add_argument("--status", choices=STATUSES, default="ready")
    for name in ("update", "checkpoint", "handoff"):
        c = sub.add_parser(name, help="Replace current fields; handoff also releases the owner")
        c.add_argument("id")
        c.add_argument("--expect", required=True, help="Exact card revision from status/show")
        c.add_argument("--session", default=os.environ.get("WORKLOG_SESSION_ID"))
        for field in ("title", "current", "next"):
            c.add_argument("--" + field)
        cautions = c.add_mutually_exclusive_group()
        cautions.add_argument("--caution", action="append", help="REPLACES the whole caution list")
        cautions.add_argument("--clear-cautions", action="store_true")
        refs = c.add_mutually_exclusive_group()
        refs.add_argument("--reference", action="append", help="REPLACES the whole reference list")
        refs.add_argument("--clear-references", action="store_true")
        c.add_argument("--status", choices=STATUSES)
    for name in ("claim", "start", "resume", "takeover"):
        c = sub.add_parser(name, help="Claim a current card; no persistent session registration")
        c.add_argument("id")
        c.add_argument("--expect", required=True)
        c.add_argument("--session", default=os.environ.get("WORKLOG_SESSION_ID"))
        c.add_argument("--harness", default=os.environ.get("WORKLOG_HARNESS", "manual"))
        c.add_argument("--account", default=os.environ.get("WORKLOG_ACCOUNT", "default"))
        if name == "takeover":
            c.add_argument("--confirm-old-writer-stopped", action="store_true")
    finish = sub.add_parser("finish", help="Remove completed work; no archive and no implied verification")
    finish.add_argument("id")
    finish.add_argument("--expect", required=True)
    finish.add_argument("--session", default=os.environ.get("WORKLOG_SESSION_ID"))
    finish.add_argument("--confirm", action="store_true")
    note = sub.add_parser("note", help="Only 3 GLOBAL historical slots; choose deliberately which one to replace")
    note.add_argument("slot", choices=("1", "2", "3"))
    ng = note.add_mutually_exclusive_group(required=True)
    ng.add_argument("--text")
    ng.add_argument("--clear", action="store_true")
    note.add_argument("--expect", required=True, help="WORKSPACE revision, not a card revision")
    overview = sub.add_parser("overview", help="Replace the current workspace overview")
    overview.add_argument("--text", required=True)
    overview.add_argument("--expect", required=True)
    ack = sub.add_parser("ack", help="Clear a disposable hook reminder when nothing materially changed")
    ack.add_argument("--session", default=os.environ.get("WORKLOG_SESSION_ID"))
    clear = sub.add_parser("clear-runtime", help="Discard bounded hook flags, not frontier or owner claims")
    clear.add_argument("--confirm", action="store_true")
    for name in ("install", "uninstall"):
        c = sub.add_parser(name, help="Merge/remove project-local Worklog hooks and skill")
        c.add_argument("harness", choices=("claude", "codex"))
        c.add_argument("--dry-run", action="store_true")
    hook = sub.add_parser("hook", help="Adapter entrypoint; bounded JSON on stdin")
    hook.add_argument("--harness", required=True, choices=("claude", "codex"))
    hook.add_argument("--event")
    launch = sub.add_parser("launch", help="Launch a harness with an explicit ephemeral identity; credentials unchanged")
    launch.add_argument("--harness", required=True)
    launch.add_argument("--account", default="default")
    launch.add_argument("argv", nargs=argparse.REMAINDER)
    migrate = sub.add_parser("migrate", help="Offline v0.1 conversion; proposal first, explicitly destructive apply")
    mg = migrate.add_mutually_exclusive_group(required=True)
    mg.add_argument("--plan", action="store_true")
    mg.add_argument("--apply", metavar="REVIEWED_JSON")
    migrate.add_argument("--discard-history", action="store_true")
    migrate.add_argument("--confirm-agents-stopped", action="store_true")
    return p


def require_session(a) -> str:
    if not a.session:
        raise Invalid("Pass --session or launch with `worklog launch`; identity is never guessed")
    return a.session


def ack_result(ws: Workspace, result: dict, sid: str | None) -> dict:
    if sid:
        try:
            Runtime(ws).update(sid, dirty=False)
        except WorklogError as exc:
            # A disposable flag failure must not misreport a committed semantic write as failed.
            result["runtime_warning"] = str(exc)
    return result


HOOK_PAYLOAD_LIMIT = 1024 * 1024


def hook_invocation(argv: list[str]) -> bool:
    # --root is the only global option that may precede the subcommand.
    if argv[:1] == ["--root"]:
        argv = argv[2:]
    elif argv[:1] and argv[0].startswith("--root="):
        argv = argv[1:]
    return argv[:1] == ["hook"]


def hook_skipped(event: object, code: str, message: str) -> dict:
    print(json.dumps({"error": code, "message": message}, ensure_ascii=False), file=sys.stderr)
    if event in ("SessionStart", "UserPromptSubmit"):
        return {"systemMessage": f"Worklog hook skipped ({code}): {message}"}
    return {}


def run_hook(a) -> dict:
    """Hooks fail open: hosts read exit 2 as "block", so no Worklog problem may stop the session."""
    event = a.event
    try:
        raw = sys.stdin.buffer.read(HOOK_PAYLOAD_LIMIT + 1)
        if len(raw) > HOOK_PAYLOAD_LIMIT:
            # Large Write/prompt payloads are transient host input, not state; drain and skip them.
            while sys.stdin.buffer.read(HOOK_PAYLOAD_LIMIT):
                pass
            print(json.dumps({"skipped": "payload_too_large", "limit_bytes": HOOK_PAYLOAD_LIMIT}), file=sys.stderr)
            return {}
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise Invalid("Hook stdin must contain JSON") from exc
        if event is None and isinstance(payload, dict):
            event = payload.get("hook_event_name")
        return handle(payload, a.harness, event=a.event)
    except WorklogError as exc:
        return hook_skipped(event, exc.code, str(exc))
    except Exception as exc:  # Malformed payloads and I/O errors must not block the host either.
        return hook_skipped(event, "internal_error", f"{type(exc).__name__}: {exc}")


def run(a) -> tuple[object, int]:
    if a.command == "hook":
        return run_hook(a), 0
    ws = Workspace.open(a.root, create=a.command == "init", name=getattr(a, "name", None),
                        allow_legacy=a.command == "migrate")
    if a.command == "migrate":
        from .migration import apply, plan
        from .util import load_json
        if a.plan:
            return plan(ws), 0
        proposal = load_json(Path(a.apply).expanduser(), 256 * 1024)
        return apply(ws, proposal, discard_history=a.discard_history,
                     agents_stopped=a.confirm_agents_stopped), 0
    w = Worklog(ws)
    if a.command == "init":
        return {"version": __version__, "workspace_id": ws.workspace_id, "store": str(ws.store),
                "mode": "frontier", "historical_slots": 3, "frontier_limit_bytes": 32768}, 0
    if a.command == "status":
        return w.read(), 0
    if a.command in {"stats", "doctor"}:
        result = w.stats()
        if a.command == "doctor":
            Runtime(ws).get("doctor")  # Validate the bounded optional cache without modifying it.
            result["ok"] = not result["unexpected_files"]
            return result, 0 if result["ok"] else 2
        return result, 0
    if a.command == "show":
        return w.show(a.id), 0
    if a.command == "context":
        return w.context(a.card, session=a.session), 0
    if a.command == "add":
        return w.add(a.id, a.title, a.current, a.next, cautions=a.caution,
                     references=a.reference, status=a.status), 0
    if a.command in {"update", "checkpoint", "handoff"}:
        patch = {k: getattr(a, k) for k in ("title", "current", "next", "status") if getattr(a, k) is not None}
        if a.clear_cautions or a.caution is not None:
            patch["cautions"] = [] if a.clear_cautions else a.caution
        if a.clear_references or a.reference is not None:
            patch["references"] = [] if a.clear_references else a.reference
        result = w.update(a.id, a.expect, patch, session=a.session, release=a.command == "handoff")
        return ack_result(ws, result, a.session), 0
    if a.command in {"claim", "start", "resume", "takeover"}:
        return w.claim(a.id, a.expect, session=require_session(a), harness=a.harness,
                       account=a.account, takeover=a.command == "takeover",
                       confirmed=getattr(a, "confirm_old_writer_stopped", False)), 0
    if a.command == "finish":
        result = w.finish(a.id, a.expect, session=a.session, confirmed=a.confirm)
        return ack_result(ws, result, a.session), 0
    if a.command == "note":
        return w.note(a.slot, None if a.clear else a.text, expected=a.expect), 0
    if a.command == "overview":
        return w.overview(a.text, expected=a.expect), 0
    if a.command == "ack":
        Runtime(ws).update(require_session(a), dirty=False)
        return {"acknowledged": True, "frontier_changed": False}, 0
    if a.command == "clear-runtime":
        if not a.confirm:
            raise Invalid("Pass --confirm to discard disposable hook flags")
        Runtime(ws).clear()
        return {"cleared": True, "frontier_changed": False, "ownership_changed": False}, 0
    if a.command in {"install", "uninstall"}:
        return install(ws, a.harness, dry_run=a.dry_run, uninstall=a.command == "uninstall"), 0
    if a.command == "launch":
        argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
        if not argv:
            raise Invalid("Pass the harness command after --")
        from .model import text
        text(a.harness, "harness", 48)
        text(a.account, "account", 96)
        sid = "s_" + revision()
        env = {**os.environ, "WORKLOG_SESSION_ID": sid, "WORKLOG_ACCOUNT": a.account,
               "WORKLOG_HARNESS": a.harness}
        print(w.context(session=sid), file=sys.stderr)
        try:
            code = subprocess.call(argv, cwd=ws.root, env=env)
            return None, code if code >= 0 else 128 - code
        finally:
            Runtime(ws).forget(sid)
    raise Invalid("Unknown command")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # Exit 2 is a structured CLI error, but a hook exiting 2 blocks the host's prompt/tool/stop.
    failure = 0 if hook_invocation(argv) else 2
    try:
        a = parser().parse_args(argv)
        result, code = run(a)
        if isinstance(result, str):
            print(result, end="" if result.endswith("\n") else "\n")
        elif result is not None:
            # ASCII hook output cannot fail on a non-UTF-8 locale or a payload's lone surrogate.
            print(json.dumps(result, ensure_ascii=a.command == "hook", indent=2, allow_nan=False))
        return code
    except SystemExit as exc:
        if failure == 0 and exc.code not in (0, None):
            print("{}")  # Codex Stop requires JSON on stdout even when the hook does nothing.
            return 0
        raise
    except WorklogError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return failure
    except OSError as exc:
        print(json.dumps({"error": "io_error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return failure
    except KeyboardInterrupt:
        return 130
