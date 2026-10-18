from __future__ import annotations

import json
import os
import shlex
import sys
from importlib.resources import files

from .core import Worklog
from .errors import Invalid, NotFound, WorklogError
from .model import identifier
from .runtime import Runtime
from .util import atomic_write, digest, file_lock
from .workspace import Workspace

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "SessionEnd")
MUTATING = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "PowerShell",
            "apply_patch", "exec_command", "shell", "shell_command", "terminal"}
# Anchored so Claude Code (regex path) and Codex (regex) both match whole tool names only;
# other tools then never spawn the hook. handle() still filters on MUTATING.
TOOL_MATCHER = "^(" + "|".join(sorted(MUTATING)) + ")$"
# Recognize old callbacks as no-ops so reinstalling the adapter is not a replay risk.
OLD_NOOP_EVENTS = {"PostToolUse", "PreCompact"}
# Claude Code moves hook context over 10,000 chars to a file, leaving a 2,000-char preview.
# Codex is configured with additionalContextLimit and keeps the full render.
CLAUDE_CONTEXT_CHARS = 9500
# SessionEnd reasons after which the same process (and a pinned identity) keeps working.
RESTARTS = {"clear", "resume"}


def session_id(payload: dict, harness: str, workspace: Workspace, account: str) -> str:
    pinned = os.environ.get("WORKLOG_SESSION_ID")
    child = payload.get("agent_id")
    if pinned and not child:
        return identifier(pinned, "WORKLOG_SESSION_ID")
    native = payload.get("session_id")
    if not isinstance(native, str) or not native or len(native) > 512:
        raise Invalid("Hook needs its native session_id; refusing to guess a concurrent session")
    return "s_" + digest([harness, account, native, child, workspace.checkout_id])[:32]


def handle(payload: dict, harness: str, *, event: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise Invalid("Hook input must be a JSON object")
    if harness not in {"claude", "codex"}:
        raise Invalid("Hook harness must be claude or codex")
    event = event or payload.get("hook_event_name")
    if event in OLD_NOOP_EVENTS:
        return {}
    if event not in EVENTS:
        raise Invalid(f"Unsupported hook event: {event}")
    # Decide no-op events before touching the store, so a broken store cannot extend a
    # continuation the host already granted or tax read-only tools.
    if event == "Stop" and payload.get("stop_hook_active"):
        return {}
    tool = payload.get("tool_name")
    if event == "PreToolUse" and (not isinstance(tool, str) or tool not in MUTATING):
        return {}
    cwd = payload.get("cwd", os.getcwd())
    if not isinstance(cwd, str):
        raise Invalid("Hook cwd must be text")
    try:
        ws = Workspace.open(cwd)
    except NotFound:
        return {}
    worklog, runtime = Worklog(ws), Runtime(ws)
    account = os.environ.get("WORKLOG_ACCOUNT", "default")
    sid = session_id(payload, harness, ws, account)
    if event == "SessionEnd":
        # A pinned launcher identity outlives /clear and resume; any other ended identity never
        # writes again, so its card must not keep blocking the checkout. Release, not finish:
        # working becomes needs_check because external jobs may still run.
        if not (sid == os.environ.get("WORKLOG_SESSION_ID") and payload.get("reason") in RESTARTS):
            worklog.release_session(sid)
        runtime.forget(sid)
        return {}
    if event in {"SessionStart", "UserPromptSubmit"}:
        state = worklog.read()
        known = runtime.get(sid)
        if event == "UserPromptSubmit" and known and known["seen"] == state["revision"]:
            return {}
        context = worklog.context(session=sid, harness=harness, account=account,
                                  limit=CLAUDE_CONTEXT_CHARS if harness == "claude" else None)
        # A concurrent write can only cause an extra later injection, not a lost one:
        # seen is the earlier revision, not a second read after rendering.
        runtime.update(sid, seen=state["revision"])
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}
    if event == "PreToolUse":
        # A boolean only, no commands/transcripts/output retained. CLI update/ack clears it.
        runtime.update(sid, dirty=True)
        foreign = [c["id"] for c in worklog.read()["cards"].values()
                   if c["owner"] and c["owner"]["checkout_id"] == ws.checkout_id
                   and c["owner"]["session_id"] != sid]
        if foreign:
            worktree = "; use a separate Git worktree for parallel edits" if ws.git else ""
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "WORKLOG: another cooperating writer owns this checkout (" + ", ".join(foreign) +
                f"). Read status and coordinate{worktree}. This is an advisory, not an OS sandbox."}}
        return {}
    if event == "Stop":
        flags = runtime.get(sid)
        if not flags or not flags["dirty"]:
            return {}
        worklog.read()  # A broken frontier raises here, so the hook fails open instead of blocking.
        # One reminder per dirty episode: an ignored reminder must not return on later turns.
        # Tools used during the granted continuation set dirty again for the next Stop.
        try:
            runtime.update(sid, dirty=False)
        except (WorklogError, OSError) as exc:  # An unclearable flag repeats the reminder, never loses it.
            print(json.dumps({"warning": "dirty_flag_not_cleared", "message": str(exc)}), file=sys.stderr)
        return {"decision": "block", "reason":
            f"Before stopping, update the CURRENT frontier in place (session {sid}). Read `worklog status`; "
            "save current position, next action and still-valid cautions with `worklog update`/`handoff`. "
            "Do not append a session summary. Historical landmarks have only GLOBAL slots 1..3. "
            f"If no material state changed, run `worklog ack --session {sid}`. "
            "Do not fabricate progress or completion merely to satisfy this reminder."}
    return {}


def config(harness: str, python: str | None = None) -> dict:
    if harness not in {"claude", "codex"}:
        raise Invalid("Supported adapters: claude, codex")
    # -P drops the implicit cwd entry from sys.path, so a project's worklog.py/json.py is not
    # imported as hook code. Entries the user adds to PYTHONPATH still apply.
    command_base = f"{shlex.quote(python or sys.executable)} -P -m worklog hook --harness {harness}"
    result: dict = {"hooks": {}}
    for event in EVENTS:
        handler = {"type": "command", "command": f"{command_base} --event {event}",
                   "timeout": 3 if event == "SessionEnd" else 15}
        if harness == "codex" and event in {"SessionStart", "UserPromptSubmit"}:
            handler["additionalContextLimit"] = 60000
        group = {"matcher": TOOL_MATCHER, "hooks": [handler]} if event == "PreToolUse" else {"hooks": [handler]}
        result["hooks"][event] = [group]
    return result


def _ours(handler: dict, harness: str) -> bool:
    command = handler.get("command", "")
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return any(words[i:i+5] == ["-m", "worklog", "hook", "--harness", harness]
               for i in range(len(words))) or words[:4] == ["worklog", "hook", "--harness", harness]


def install(ws: Workspace, harness: str, *, dry_run: bool = False, uninstall: bool = False) -> dict:
    template = config(harness)
    target = ws.root / (".claude/settings.local.json" if harness == "claude" else ".codex/hooks.json")
    skill_target = ws.root / (".claude/skills/worklog/SKILL.md" if harness == "claude" else ".agents/skills/worklog/SKILL.md")
    skill = files("worklog").joinpath("templates/SKILL.md").read_bytes()
    with file_lock(ws.lock):
        if target.is_symlink() or skill_target.is_symlink():
            raise Invalid("Refusing to overwrite a symlinked config or skill")
        raw = target.read_bytes() if target.exists() else b"{}"
        try:
            merged = json.loads(raw)
            if not isinstance(merged, dict) or not isinstance(merged.get("hooks", {}), dict):
                raise Invalid("Existing config must be an object with a hooks object")
            hooks = merged.setdefault("hooks", {})
            # Visit ALL old event names; remove obsolete v0.1 callbacks as well.
            for event, groups in list(hooks.items()):
                if not isinstance(groups, list):
                    raise Invalid(f"Malformed existing hook list: {event}")
                preserved_groups = []
                for group in groups:
                    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                        raise Invalid("Malformed existing hook group")
                    if any(not isinstance(h, dict) for h in group["hooks"]):
                        raise Invalid("Malformed hook handler")
                    kept = [h for h in group["hooks"] if not _ours(h, harness)]
                    if kept:
                        preserved_groups.append({**group, "hooks": kept})
                if preserved_groups:
                    hooks[event] = preserved_groups
                else:
                    hooks.pop(event, None)
            if not uninstall:
                for event, groups in template["hooks"].items():
                    hooks.setdefault(event, []).extend(groups)
        except (ValueError, TypeError) as exc:
            raise Invalid(f"Cannot safely merge existing hook config: {target}") from exc
        updated = json.dumps(merged, ensure_ascii=False, indent=2).encode() + b"\n"
        if not dry_run:
            if raw != updated:
                atomic_write(target, updated)
            if not uninstall and (not skill_target.exists() or skill_target.read_bytes() != skill):
                atomic_write(skill_target, skill)
            elif uninstall and skill_target.exists() and skill_target.read_bytes() == skill:
                skill_target.unlink()
    return {"config_path": str(target), "skill_path": str(skill_target), "dry_run": dry_run,
            "uninstall": uninstall, "merged_config": merged,
            "note": "Only Worklog-owned handlers were replaced. No provider credentials copied. Restart/review hook trust in your agent."}
