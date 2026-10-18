from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

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
# Frontier writes, which touch only the store. Not launch (runs any command), install,
# uninstall, init, migrate, hook or clear-runtime; a `--root` form does not match either.
CODEX_ALLOWED = ("add", "update", "checkpoint", "handoff", "claim", "start", "resume",
                 "takeover", "finish", "note", "overview", "ack")


def codex_rules() -> bytes:
    """Codex's workspace-write sandbox keeps .git read-only, and a Git store lives in
    <git-common-dir>/worklog, outside a linked worktree altogether. Reads work there;
    this rule runs the frontier writes outside the sandbox without a prompt."""
    subcommands = ", ".join(f'"{name}"' for name in CODEX_ALLOWED)
    return f"""# Written by `worklog install codex`; `worklog uninstall codex` removes it.
# Codex keeps .git read-only in its workspace-write sandbox, so it cannot write the
# Worklog store in <git-common-dir>/worklog. These frontier writes touch only that
# store and run outside the sandbox. Other worklog commands keep the normal approval.
prefix_rule(
    pattern = ["worklog", [{subcommands}]],
    decision = "allow",
    match = [["worklog", "update", "CARD", "--expect", "REV"]],
    not_match = [["worklog", "launch", "--", "sh"], ["worklog", "install", "codex"],
                 ["worklog", "--root", ".", "update"]],
)
""".encode()

# Project-local adapter files. They are untracked, so every Git worktree needs its own install.
CONFIG_PATHS = {"claude": ".claude/settings.local.json", "codex": ".codex/hooks.json"}
SKILL_PATHS = {"claude": ".claude/skills/worklog/SKILL.md", "codex": ".agents/skills/worklog/SKILL.md"}
CODEX_RULES_PATH = ".codex/rules/worklog.rules"


def session_id(payload: dict, harness: str, workspace: Workspace, account: str) -> str:
    # Subagents (payload agent_id) are the same writer: same process tree and checkout, and no
    # SessionStart/Stop of their own here. A separate id only produced false foreign-writer
    # advisories and runtime entries that evicted the parent's reminder.
    pinned = os.environ.get("WORKLOG_SESSION_ID")
    if pinned:
        return identifier(pinned, "WORKLOG_SESSION_ID")
    native = payload.get("session_id")
    if not isinstance(native, str) or not native or len(native) > 512:
        raise Invalid("Hook needs its native session_id; refusing to guess a concurrent session")
    # None fills the old subagent slot and the path-based checkout id stays, so existing
    # session ids (and the cards they own) are unchanged by upgrading.
    return "s_" + digest([harness, account, native, None, workspace.legacy_checkout_id])[:32]


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
                   if c["owner"] and ws.is_checkout(c["owner"]["checkout_id"])
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
            "If a worklog write fails with `read_only`, your sandbox cannot write the store: do not "
            "retry, ack or work around it; tell the user the store path, the fix the error names, "
            "and the update you would have saved. "
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
    target = ws.root / CONFIG_PATHS[harness]
    skill_target = ws.root / SKILL_PATHS[harness]
    owned = [(skill_target, files("worklog").joinpath("templates/SKILL.md").read_bytes())]
    rules_target = ws.root / CODEX_RULES_PATH if harness == "codex" else None
    if rules_target:
        owned.append((rules_target, codex_rules()))
    with file_lock(ws.lock):
        if target.is_symlink() or any(path.is_symlink() for path, _ in owned):
            raise Invalid("Refusing to overwrite a symlinked config, skill or rule")
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
            for path, content in owned:
                # A file someone edited is left in place on uninstall.
                if not uninstall and (not path.exists() or path.read_bytes() != content):
                    atomic_write(path, content)
                elif uninstall and path.exists() and path.read_bytes() == content:
                    path.unlink()
    note = ("Only Worklog-owned handlers were replaced. No provider credentials copied. Restart/review hook "
            "trust in your agent. These files are untracked: run install in each Git worktree too.")
    if rules_target:
        note += (" The rule lets frontier writes run outside Codex's sandbox, which keeps .git"
                 " read-only; Codex loads it only in a trusted project.")
    result = {"config_path": str(target), "skill_path": str(skill_target),
              "rules_path": str(rules_target) if rules_target else None, "dry_run": dry_run,
              "uninstall": uninstall, "merged_config": merged, "note": note}
    if not uninstall and not can_import_worklog(sys.executable, ws.root):
        result["warning"] = (f"The hook interpreter {sys.executable} cannot import worklog without PYTHONPATH, so these "
                             "hooks fail unless your agent's environment sets it. Install the package "
                             "(`uv tool install .` or pip), then rerun `worklog install`.")
    return result

def can_import_worklog(python: str, cwd: Path) -> bool:
    """Whether a hook command's interpreter finds worklog on its own: -P, no PYTHONPATH."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # A probe must not leave caches behind.
    try:
        return subprocess.run([python, "-P", "-c", "import worklog"], cwd=cwd, env=env,
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _outside(path: Path, *dirs: Path) -> bool:
    resolved = path.resolve()
    return not any(resolved.is_relative_to(d.resolve()) for d in dirs)


def inspect_adapters(ws: Workspace) -> tuple[dict, list[dict]]:
    """Read-only check of this checkout's project-local hooks: (configs per harness, problems).

    Only an absolute interpreter outside the checkout and store is ever executed, with fixed
    arguments, so a committed config cannot make doctor run project code."""
    found: dict[str, list[str]] = {}
    problems: list[dict] = []
    runnable: dict[str, bool] = {}
    template_skill = files("worklog").joinpath("templates/SKILL.md").read_bytes()
    for harness, rel in (("claude", CONFIG_PATHS["claude"]), ("claude", ".claude/settings.json"),
                         ("codex", CONFIG_PATHS["codex"])):
        path = ws.root / rel
        reinstall = f"`worklog install {harness}` in {ws.root}"
        if rel == ".claude/settings.json":  # Often shared and committed; install never edits it.
            reinstall = ("update its Worklog handlers to match `worklog install claude --dry-run` (merged_config), "
                         "or remove them and run `worklog install claude`")
        try:
            if not path.is_file():
                continue
            hooks = json.loads(path.read_bytes()).get("hooks", {})
            ours = {event: [(g, h) for g in groups for h in g["hooks"] if isinstance(h, dict) and _ours(h, harness)]
                    for event, groups in hooks.items()}
        except (ValueError, OSError, AttributeError, TypeError, KeyError):
            problems.append({"error": "adapter", "message": f"{rel} is not a readable hook config",
                             "fix": f"fix or remove it, then `worklog install {harness}`"})
            continue
        ours = {event: entries for event, entries in ours.items() if entries}
        if not ours:
            continue
        found.setdefault(harness, []).append(rel)
        issues: list[tuple[str, str]] = []
        if missing := [e for e in EVENTS if e not in ours]:
            issues.append(("missing hooks for " + ", ".join(missing), reinstall))
        if twice := [e for e, entries in ours.items() if len(entries) > 1]:
            issues.append(("duplicate hooks for " + ", ".join(twice), reinstall))
        if "PreToolUse" in ours:
            matcher = ours["PreToolUse"][0][0].get("matcher")
            if matcher in (None, "", "*"):
                issues.append(("PreToolUse has no tool matcher, so it runs for every tool", reinstall))
            elif matcher != TOOL_MATCHER:
                issues.append(("PreToolUse matcher differs from the current one", reinstall))
        for entries in ours.values():
            words = shlex.split(entries[0][1]["command"])
            if words[:1] == ["worklog"]:
                if shutil.which("worklog") is None:
                    issues.append(("`worklog` is not on PATH", "install the package (`uv tool install .` or pip)"))
                continue
            flags = words[1:words.index("-m")]
            if not all(f.startswith("-") and not f.startswith("--") for f in flags):
                continue  # A wrapper or prefix (env, uv run, ...): its real interpreter is not knowable here.
            if not any("P" in f or "I" in f for f in flags):
                issues.append(("hook command lacks -P, so project files can shadow Worklog", reinstall))
            python = words[0]
            if not Path(python).is_absolute() or not _outside(Path(python), ws.root, ws.store):
                issues.append((f"hook interpreter {python} is relative or inside this checkout, so doctor did not run it",
                               reinstall))
                continue
            if python not in runnable:
                runnable[python] = can_import_worklog(python, ws.root)
            if not runnable[python]:
                issues.append((f"{python} cannot import worklog with -P and no PYTHONPATH (needs Python 3.11+ "
                               "with worklog installed)",
                               f"install the package (`uv tool install .` or pip), then rerun {reinstall}"))
        skill = ws.root / SKILL_PATHS[harness]
        try:
            current = skill.is_file() and skill.read_bytes() == template_skill
        except OSError:
            current = False
        if not current:
            issues.append((f"skill {SKILL_PATHS[harness]} is missing, unreadable or outdated", reinstall))
        checked = [path, skill]
        if harness == "codex":
            rules = ws.root / CODEX_RULES_PATH
            checked.append(rules)
            try:
                current = rules.is_file() and rules.read_bytes() == codex_rules()
            except OSError:
                current = False
            if not current:
                issues.append((f"rule {CODEX_RULES_PATH} is missing, unreadable or outdated, so Codex runs "
                               "frontier writes inside its sandbox", reinstall))
        links = [p.relative_to(ws.root).as_posix() for p in checked if p.is_symlink()]
        for issue, fix in dict.fromkeys(issues):
            if links:  # install refuses to overwrite symlinks, so it cannot be the fix.
                fix = (f"{' and '.join(links)} is a symlink, which `worklog install` will not overwrite: "
                       f"edit its target, or replace it with a regular file and rerun `worklog install {harness}`")
            problems.append({"error": "adapter", "message": f"{rel}: {issue}", "fix": fix})
    if len(found.get("claude", [])) > 1:
        problems.append({"error": "adapter", "message": "Worklog hooks are in both " + " and ".join(found["claude"]) +
                         ", so they run twice", "fix": "remove them from .claude/settings.json, or run "
                         "`worklog uninstall claude` if your team keeps them there"})
    return found, problems
