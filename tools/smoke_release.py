"""Offline wheel install + CLI/hooks demo + optional actual v0.1 wheel migration."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def execute(argv, *, cwd, env, input=None, expected=0):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, input=input,
                       capture_output=True, text=True, timeout=30)
    if p.returncode != expected:
        raise RuntimeError(f"Command failed ({p.returncode}): {argv}\n{p.stdout}\n{p.stderr}")
    return p


def source_version(source_root: Path) -> str:
    """The single-sourced worklog.__version__, read without importing the source tree."""
    text = (source_root / "src/worklog/__init__.py").read_text()
    return re.search(r'^__version__ = "([^"]+)"$', text, re.M).group(1)


def run(wheel: Path, legacy_wheel: Path | None = None) -> dict:
    source_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="worklog-release-smoke-") as tmp:
        tmp = Path(tmp)
        env = {k: v for k, v in os.environ.items()
               if k not in {"PYTHONPATH", "WORKLOG_SESSION_ID", "WORKLOG_ACCOUNT", "WORKLOG_HARNESS"}}
        venv = tmp / "venv"
        execute([sys.executable, "-m", "venv", venv], cwd=tmp, env=env)
        python, cli = venv / "bin/python", venv / "bin/worklog"
        execute([python, "-m", "pip", "install", "--no-index", "--no-deps", wheel], cwd=tmp, env=env)
        version = execute([cli, "--version"], cwd=tmp, env=env).stdout.strip()
        assert version == source_version(source_root), version
        origin = execute([python, "-c", "import worklog; print(worklog.__file__)"], cwd=tmp, env=env).stdout.strip()
        assert str(venv) in origin and "site-packages" in origin
        root = tmp / "project"
        root.mkdir()
        def call(*args):
            p = execute([cli, "--root", root, *args], cwd=tmp, env=env)
            return json.loads(p.stdout)
        call("init")
        c = call("add", "test", "--title", "Installed test", "--current", "Unverified", "--next", "Inspect current code")["card"]
        c = call("claim", "test", "--expect", c["revision"], "--session", "claude_a")["card"]
        c = call("handoff", "test", "--expect", c["revision"], "--session", "claude_a",
                 "--current", "Reviewed current code", "--next", "Run actual tests")["card"]
        c = call("resume", "test", "--expect", c["revision"], "--session", "codex_b")["card"]
        for slot in ("1", "2", "3"):
            call("note", slot, "--expect", call("status")["revision"], "--text", "Reviewed lesson " + slot)
        call("note", "1", "--expect", call("status")["revision"], "--text", "Replacement, not a fourth lesson")
        hooks = {}
        for harness in ("claude", "codex"):
            installation = call("install", harness)
            command = installation["merged_config"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            payload = {"cwd": str(root), "session_id": f"native_{harness}", "hook_event_name": "SessionStart"}
            p = execute(["sh", "-c", command], cwd=tmp, env=env, input=json.dumps(payload))
            context = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
            assert "Reviewed current code" in context and "Replacement, not a fourth lesson" in context
            hooks[harness] = {"generated_command_ran": True, "context_included_frontier": True,
                              "live_provider_session": False}
        call("finish", "test", "--expect", c["revision"], "--session", "codex_b", "--confirm")
        state = call("status")
        assert len(state["notes"]) == 3 and not state["cards"]
        assert call("doctor")["ok"]
        demo = execute([python, source_root / "examples/demo.py"], cwd=tmp, env=env)
        assert '"demo_passed": true' in demo.stdout
        result = {"passed": True, "wheel_version": version, "loaded_from_fresh_site_packages": True,
                  "PYTHONPATH_removed": True, "installed_cli_handoff_flow": True,
                  "installed_demo": True, "installed_project_hooks": hooks,
                  "final_current_cards": 0, "final_global_landmarks": 3}
        if legacy_wheel:
            oldenv = tmp / "oldenv"
            execute([sys.executable, "-m", "venv", oldenv], cwd=tmp, env=env)
            oldpython = oldenv / "bin/python"
            execute([oldpython, "-m", "pip", "install", "--no-index", "--no-deps", legacy_wheel], cwd=tmp, env=env)
            oldroot = tmp / "old-project"
            oldroot.mkdir()
            (oldroot / "main.py").write_text("value = 1\n")
            seed = '''
import json,sys
from worklog.core import Worklog
from worklog.workspace import Workspace
from worklog.hooks import install
w = Worklog(Workspace.open(sys.argv[1], create=True))
t = w.task_add('Old active task', 'Keep useful current progress', 'Real check later', {'smoke':[sys.executable,'-c','print("ok")']})
s = w.register('claude', 'old-account')
w.claim(s['id'],t['id'])
w.checkpoint(s['id'],'Current semantic finding from v0.1','Check actual project result',['Full test not run'])
for _ in range(10):
    w.context(s['id'])
install(w.ws, 'claude')
install(w.ws, 'codex')
print(json.dumps({'task_id': t['id'], 'event_files': len(list((w.ws.store/'events').glob('*.json')))}))
'''
            old_result = json.loads(execute([oldpython, "-c", seed, oldroot], cwd=tmp, env=env).stdout)
            proposal = json.loads(execute([cli, "--root", oldroot, "migrate", "--plan"], cwd=tmp, env=env).stdout)
            proposal["frontier"]["notes"] = {"1": "Keep only current state, not accumulated session history"}
            review = tmp / "review.json"
            review.write_text(json.dumps(proposal))
            migrated = json.loads(execute([cli, "--root", oldroot, "migrate", "--apply", review,
                                          "--discard-history", "--confirm-agents-stopped"], cwd=tmp, env=env).stdout)
            stat = json.loads(execute([cli, "--root", oldroot, "stats"], cwd=tmp, env=env).stdout)
            assert migrated["legacy_history_deleted"] and stat["files"] == 3
            state = json.loads(execute([cli, "--root", oldroot, "status"], cwd=tmp, env=env).stdout)
            assert state["cards"][old_result["task_id"]]["current"] == 'Current semantic finding from v0.1'
            for harness in ("claude", "codex"):
                installed = json.loads(execute([cli, "--root", oldroot, "install", harness], cwd=tmp, env=env).stdout)
                assert "PostToolUse" not in installed["merged_config"]["hooks"]
                assert "PreCompact" not in installed["merged_config"]["hooks"]
            reject = subprocess.run([str(oldpython), "-c", "import sys; from worklog.workspace import Workspace; Workspace.open(sys.argv[1])", str(oldroot)],
                                    cwd=tmp, env=env, capture_output=True, text=True, timeout=15)
            assert reject.returncode != 0 and "Unsupported workspace schema" in reject.stderr
            result["actual_v01_wheel_migration"] = {"passed": True,
                "legacy_event_files_before": old_result["event_files"], "core_files_after": stat["files"],
                "current_semantics_retained": True, "history_archived": False,
                "old_adapters_replaced": True, "old_version_rejects_new_schema": True}
        return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--wheel", type=Path, required=True)
    p.add_argument("--legacy-wheel", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    result = run(a.wheel.resolve(), a.legacy_wheel.resolve() if a.legacy_wheel else None)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if a.output:
        a.output.write_text(raw)
    print(raw)
