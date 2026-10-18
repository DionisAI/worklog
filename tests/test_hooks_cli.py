import dataclasses
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import worklog.cli as cli_module
from worklog.core import Worklog
from worklog.hooks import MUTATING, config, handle, install, session_id
from worklog.errors import Invalid, MigrationRequired
from worklog.runtime import Runtime
from worklog.cli import main
from conftest import signature

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def payload(w):
    return {"cwd": str(w.ws.root), "session_id": "native-123", "hook_event_name": "SessionStart"}


@pytest.fixture(autouse=True)
def clean_identity(monkeypatch):
    for key in ("WORKLOG_SESSION_ID", "WORKLOG_ACCOUNT", "WORKLOG_HARNESS"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_start_injects_current_state_without_semantic_write(w, card, payload, harness):
    before = (w.ws.store / "frontier.json").read_bytes()
    result = handle(payload, harness)
    assert card["current"] in result["hookSpecificOutput"]["additionalContext"]
    assert (w.ws.store / "frontier.json").read_bytes() == before
    assert w.read()["cards"] == {"replay": card}


def fill_frontier(w, me):
    """16 cards near the 32 KiB cap: `me` owns one here, other sessions own the rest elsewhere."""
    w.overview("Overview " + "o" * 300, expected=w.read()["revision"])
    for slot in "123":
        w.note(slot, f"Landmark {slot} " + "l" * 500, expected=w.read()["revision"])
    for i in range(16):
        c = w.add(f"card-{i:02d}", f"Card {i}\n" + "t" * 80, f"current-{i:02d} " + "c" * 900, "n" * 300,
                  cautions=["k" * 100], references=["r" * 60])["card"]
        writer = w if i == 7 else Worklog(dataclasses.replace(w.ws, root=w.ws.root / f"wt{i}"))
        writer.claim(c["id"], c["revision"], session=me if i == 7 else f"s_{i:032x}")
    assert w.stats()["frontier_bytes"] > 30000


def test_claude_context_fits_the_hook_cap_with_every_card_indexed_first(w, payload):
    me = session_id(payload, "claude", w.ws, "default")
    fill_frontier(w, me)
    claude = handle(payload, "claude")["hookSpecificOutput"]["additionalContext"]
    codex = handle(payload, "codex")["hookSpecificOutput"]["additionalContext"]
    assert len(claude) < 10_000 < len(codex)
    head = claude[:2000]  # all Claude Code shows of an over-cap context
    assert f"Your Worklog session: {me}" in head and "Before editing, claim" in head
    assert "Read the installed Worklog skill" in head
    rows = [line for line in head.splitlines() if line.startswith("- card-")]
    assert len(rows) == 16 and all(f"- card-{i:02d} | working | " in head for i in range(16))
    assert "- card-07 | working | you | Card 7 t" in head and "worklog context --card ID" in head
    assert "current-07 " in claude and "current-00 " not in claude  # own card in full, others indexed
    assert "Overview" in claude and all(f"Landmark {slot}" in claude for slot in "123")
    assert all(f"current-{i:02d} " in codex for i in range(16))  # Codex keeps the full render
    other = handle({**payload, "session_id": "native-without-card"}, "claude")["hookSpecificOutput"]["additionalContext"]
    assert len(other) < 10_000 and "## Card detail" not in other and "- card-07 | working | s_" in other


def test_prompt_only_injects_when_revision_changes(w, card, payload):
    handle(payload, "claude")
    prompt = {**payload, "hook_event_name": "UserPromptSubmit"}
    assert handle(prompt, "claude") == {}
    w.note("1", "new key lesson", expected=w.read()["revision"])
    assert "new key lesson" in handle(prompt, "claude")["hookSpecificOutput"]["additionalContext"]
    assert handle(prompt, "claude") == {}


def test_stop_reminds_once_and_never_copies_transcript(w, payload, tmp_path):
    transcript = tmp_path / "secret-transcript"
    transcript.write_text("NEVER_STORE_THIS_OLD_CONVERSATION")
    payload["transcript_path"] = str(transcript)
    handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Write"}, "codex")
    stop = {**payload, "hook_event_name": "Stop", "last_assistant_message": "Unverified fake success"}
    assert handle(stop, "codex")["decision"] == "block"
    assert handle({**stop, "stop_hook_active": True}, "codex") == {}
    for p in w.ws.store.rglob("*"):
        if p.is_file():
            assert "NEVER_STORE_THIS" not in p.read_text()
            assert "Unverified fake success" not in p.read_text()


def test_nonmutating_tool_no_stop_reminder(w, payload):
    handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Read"}, "claude")
    assert handle({**payload, "hook_event_name": "Stop"}, "claude") == {}


def test_end_removes_runtime_and_releases_card_as_needs_check(w, card, payload):
    sid = session_id(payload, "claude", w.ws, "default")
    card = w.claim("replay", card["revision"], session=sid)["card"]
    handle(payload, "claude")
    # /clear gives an unpinned session a new native id, so the old identity never writes again.
    handle({**payload, "hook_event_name": "SessionEnd", "reason": "clear"}, "claude")
    assert Runtime(w.ws).get(sid) is None
    released = w.show("replay")
    assert released["owner"] is None and released["status"] == "needs_check"
    assert released["current"] == card["current"]  # released, not finished


@pytest.mark.parametrize("reason,kept", [("clear", True), ("resume", True), ("logout", False),
                                         ("other", False), (None, False)])
def test_pinned_identity_keeps_its_card_across_clear_and_resume(w, card, payload, monkeypatch, reason, kept):
    monkeypatch.setenv("WORKLOG_SESSION_ID", "launched")
    w.claim("replay", card["revision"], session="launched")
    end = {**payload, "hook_event_name": "SessionEnd"}
    if reason:
        end["reason"] = reason
    assert handle(end, "codex") == {}
    assert (w.show("replay")["owner"] is not None) == kept


def test_subagent_does_not_inherit_parent_identity(w, payload, monkeypatch):
    monkeypatch.setenv("WORKLOG_SESSION_ID", "root_session")
    parent = session_id(payload, "claude", w.ws, "default")
    child = session_id({**payload, "agent_id": "child"}, "claude", w.ws, "default")
    assert parent == "root_session" and child != parent


def test_missing_hook_identity_is_not_guessed(w):
    with pytest.raises(Invalid):
        handle({"cwd": str(w.ws.root), "hook_event_name": "SessionStart"}, "codex")


def test_foreign_writer_advisory(w, card, payload):
    w.claim("replay", card["revision"], session="another_session")
    output = handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Bash"}, "claude")
    assert "another cooperating writer" in output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("event", ["PostToolUse", "PreCompact"])
def test_old_observation_callbacks_are_noop(w, payload, event):
    before = signature(w.ws.store)
    assert handle({**payload, "hook_event_name": event}, "claude") == {}
    assert signature(w.ws.store) == before


def test_uninitialized_global_hook_noop(tmp_path):
    assert handle({"cwd": str(tmp_path), "hook_event_name": "SessionStart", "session_id": "id"}, "claude") == {}


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_install_is_idempotent_preserves_other_hooks_removes_old_events(w, harness):
    target = w.ws.root / (".claude/settings.local.json" if harness == "claude" else ".codex/hooks.json")
    target.parent.mkdir()
    legacy = {"permission": "keep", "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": f"/old/python -m worklog hook --harness {harness} --event PostToolUse"},
        {"type": "command", "command": "echo user-command"}]}]}}
    target.write_text(json.dumps(legacy))
    install(w.ws, harness)
    once = target.read_bytes()
    install(w.ws, harness)
    assert target.read_bytes() == once
    parsed = json.loads(once)
    assert parsed["permission"] == "keep"
    assert parsed["hooks"]["PostToolUse"][0]["hooks"] == [{"type": "command", "command": "echo user-command"}]
    assert len(parsed["hooks"]["SessionStart"]) == 1
    assert not list(target.parent.glob("*backup*"))
    install(w.ws, harness, uninstall=True)
    parsed = json.loads(target.read_bytes())
    assert set(parsed["hooks"]) == {"PostToolUse"}


def test_install_dry_run_does_not_write(w):
    before = signature(w.ws.root)
    result = install(w.ws, "claude", dry_run=True)
    assert result["dry_run"] and signature(w.ws.root) == before


def test_malformed_existing_config_never_overwritten(w):
    target = w.ws.root / ".claude/settings.local.json"
    target.parent.mkdir()
    target.write_text('{"hooks": {"SessionStart": "bad"}}')
    before = target.read_bytes()
    with pytest.raises(Invalid):
        install(w.ws, "claude")
    assert target.read_bytes() == before


def cli(w, args, input=None):
    source = Path(__file__).resolve().parents[1] / "src"
    return subprocess.run([sys.executable, "-m", "worklog", "--root", str(w.ws.root), *args],
                          input=input, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(source)}, timeout=15)


def test_cli_complete_handoff_workflow(w):
    p = cli(w, ["add", "a", "--title", "A", "--current", "Current", "--next", "Next"])
    c = json.loads(p.stdout)["card"]
    assert p.returncode == 0
    c = json.loads(cli(w, ["claim", "a", "--expect", c["revision"], "--session", "claude_a"]).stdout)["card"]
    c = json.loads(cli(w, ["handoff", "a", "--expect", c["revision"], "--session", "claude_a",
                          "--current", "Progress", "--next", "Verify"]).stdout)["card"]
    c = json.loads(cli(w, ["resume", "a", "--expect", c["revision"], "--session", "codex_b"]).stdout)["card"]
    p = cli(w, ["finish", "a", "--expect", c["revision"], "--session", "codex_b", "--confirm"])
    assert p.returncode == 0 and w.read()["cards"] == {}
    assert w.stats()["landmarks"] == 0


def test_cli_note_four_fails_and_three_replaces(w):
    p = cli(w, ["note", "4", "--expect", w.read()["revision"], "--text", "bad"])
    assert p.returncode == 2
    p = cli(w, ["note", "3", "--expect", w.read()["revision"], "--text", "lesson"])
    assert p.returncode == 0 and w.read()["notes"] == {"3": "lesson"}


def test_cli_unknown_old_command_fails_instead_of_creating_history(w):
    p = cli(w, ["events"])
    assert p.returncode == 2 and not (w.ws.store / "events").exists()


def test_cli_clear_cautions_and_references(w, card):
    c = w.update("replay", card["revision"], {"cautions": ["old"], "references": ["old.log"]})["card"]
    p = cli(w, ["update", "replay", "--expect", c["revision"], "--clear-cautions", "--clear-references"])
    assert p.returncode == 0
    assert w.show("replay")["cautions"] == [] and w.show("replay")["references"] == []


def test_cli_checkpoint_clears_stop_reminder(w, card, payload):
    sid = session_id(payload, "claude", w.ws, "default")
    c = w.claim("replay", card["revision"], session=sid)["card"]
    handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Bash"}, "claude")
    p = cli(w, ["checkpoint", "replay", "--expect", c["revision"], "--session", sid, "--current", "New state"])
    assert p.returncode == 0
    assert handle({**payload, "hook_event_name": "Stop"}, "claude") == {}


def test_own_cli_writes_are_not_reinjected(w, card, payload):
    sid = session_id(payload, "claude", w.ws, "default")
    prompt = {**payload, "hook_event_name": "UserPromptSubmit"}
    handle(payload, "claude")
    c = json.loads(cli(w, ["claim", "replay", "--expect", card["revision"], "--session", sid]).stdout)["card"]
    assert handle(prompt, "claude") == {}
    assert cli(w, ["update", "replay", "--expect", c["revision"], "--session", sid, "--current", "Mine"]).returncode == 0
    assert handle(prompt, "claude") == {}
    assert cli(w, ["add", "more", "--title", "M", "--current", "C", "--next", "N", "--session", sid]).returncode == 0
    assert handle(prompt, "claude") == {}
    assert cli(w, ["note", "1", "--expect", w.read()["revision"], "--text", "L", "--session", sid]).returncode == 0
    assert handle(prompt, "claude") == {}


def test_other_sessions_writes_are_still_injected(w, card, payload):
    sid = session_id(payload, "claude", w.ws, "default")
    prompt = {**payload, "hook_event_name": "UserPromptSubmit"}
    handle(payload, "claude")
    w.add("theirs", "Theirs", "Their unseen work", "Next")  # another session writes first...
    cli(w, ["claim", "replay", "--expect", card["revision"], "--session", sid])  # ...then we do
    assert "Their unseen work" in handle(prompt, "claude")["hookSpecificOutput"]["additionalContext"]
    cli(w, ["update", "replay", "--expect", w.show("replay")["revision"], "--session", sid, "--current", "Mine"])
    w.note("1", "Their later lesson", expected=w.read()["revision"])  # another session writes after us
    assert "Their later lesson" in handle(prompt, "claude")["hookSpecificOutput"]["additionalContext"]


def test_cli_ack_no_frontier_write(w):
    before = (w.ws.store / "frontier.json").read_bytes()
    p = cli(w, ["ack", "--session", "reader"])
    assert p.returncode == 0
    assert (w.ws.store / "frontier.json").read_bytes() == before


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_generated_hook_command_executes(w, payload, harness):
    command = config(harness)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    source = Path(__file__).resolve().parents[1] / "src"
    p = subprocess.run(command, shell=True, input=json.dumps(payload), capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(source)}, timeout=15)
    assert p.returncode == 0
    assert "WORKLOG FRONTIER" in json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_launch_has_explicit_identity_and_passes_exit_code(w):
    code = "import os,sys; print(os.environ['WORKLOG_SESSION_ID']); print(os.environ['WORKLOG_ACCOUNT']); sys.exit(7)"
    p = cli(w, ["launch", "--harness", "test", "--account", "separate-alias", "--", sys.executable, "-c", code])
    assert p.returncode == 7
    assert "s_" in p.stdout and "separate-alias" in p.stdout
    assert w.read()["cards"] == {}


def test_launch_releases_its_card_when_the_harness_exits(w, card):
    code = ("import os; from worklog.core import Worklog; from worklog.workspace import Workspace; "
            f"Worklog(Workspace.open('.')).claim('replay', {card['revision']!r}, session=os.environ['WORKLOG_SESSION_ID'])")
    p = cli(w, ["launch", "--harness", "test", "--", sys.executable, "-c", code])
    assert p.returncode == 0, p.stderr
    released = w.show("replay")
    assert released["owner"] is None and released["status"] == "needs_check"
    assert '"released": ["replay"]' in p.stderr


def test_cli_structured_errors(w, capsys):
    code = main(["--root", str(w.ws.root), "show", "missing"])
    error = json.loads(capsys.readouterr().err)
    assert code == 2 and error["error"] == "not_found"


def test_doctor_rejects_corrupt_runtime_cache(w):
    (w.ws.store / "runtime.json").write_bytes(b"not-json")
    p = cli(w, ["doctor"])
    assert p.returncode == 2 and "corruption" in p.stderr
    p = cli(w, ["clear-runtime", "--confirm"])
    assert p.returncode == 0
    assert cli(w, ["doctor"]).returncode == 0


# Hooks fail open: Claude Code and Codex read exit 2 as "block the prompt/tool" or "do not stop".

def break_store(w, kind):
    store = w.ws.store
    if kind == "legacy":
        (store / "workspace.json").write_text(json.dumps(
            {"schema_version": 1, "workspace_id": w.ws.workspace_id, "name": "old"}))
    elif kind == "pending_migration":
        (store / "migration.json").write_text("{}")
    elif kind == "corrupt_runtime":
        (store / "runtime.json").write_bytes(b"not-json")
    elif kind == "corrupt_frontier":
        (store / "frontier.json").write_bytes(b"<<<<<<< HEAD\n")


def run_hook_cli(monkeypatch, capsys, body, *args):
    stdin = io.TextIOWrapper(io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode()))
    monkeypatch.setattr(sys, "stdin", stdin)
    code = main(["hook", *args])
    out, err = capsys.readouterr()
    return code, json.loads(out), err, stdin


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "SessionEnd"])
@pytest.mark.parametrize("kind", ["legacy", "pending_migration", "corrupt_runtime", "corrupt_frontier"])
def test_broken_store_never_blocks_the_host(w, payload, monkeypatch, capsys, kind, event):
    handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Edit"}, "claude")  # pending reminder
    break_store(w, kind)
    body = {**payload, "hook_event_name": event, "tool_name": "Edit"}
    code, out, err, _ = run_hook_cli(monkeypatch, capsys, body, "--harness", "claude", "--event", event)
    assert code == 0 and "decision" not in out
    if event in ("SessionStart", "UserPromptSubmit"):
        assert out["systemMessage"].startswith("Worklog hook skipped (")
        assert json.loads(err.splitlines()[-1])["error"] in out["systemMessage"]
    else:
        assert out == {}


@pytest.mark.parametrize("event", ["UserPromptSubmit", "PreToolUse", "Stop"])
@pytest.mark.parametrize("body", ["not json", "list", "bad_cwd", "deleted_cwd", "no_session"])
def test_malformed_hook_input_never_blocks_the_host(w, monkeypatch, capsys, tmp_path, event, body):
    raw = {"not json": b"{not json", "list": b"[1, 2]",
           "bad_cwd": {"cwd": 7, "session_id": "native", "tool_name": "Edit"},
           "deleted_cwd": {"cwd": str(tmp_path / "gone"), "session_id": "native", "tool_name": "Edit"},
           "no_session": {"cwd": str(w.ws.root), "tool_name": "Edit"}}[body]
    code, out, _, _ = run_hook_cli(monkeypatch, capsys, raw, "--harness", "codex", "--event", event)
    assert code == 0 and "decision" not in out
    assert ("systemMessage" in out) if event == "UserPromptSubmit" else out == {}


def test_oversized_hook_payload_is_drained_and_ignored(w, payload, monkeypatch, capsys):
    body = json.dumps({**payload, "hook_event_name": "PreToolUse", "tool_name": "Write",
                       "tool_input": {"content": "x" * (3 * 1024 * 1024)}}).encode()
    before = signature(w.ws.store)
    code, out, err, stdin = run_hook_cli(monkeypatch, capsys, body, "--harness", "claude", "--event", "PreToolUse")
    assert code == 0 and out == {} and "payload_too_large" in err
    assert stdin.buffer.read() == b""  # fully drained, so the host never sees a broken pipe
    assert signature(w.ws.store) == before


def test_unexpected_hook_exception_fails_open(payload, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("bug")
    monkeypatch.setattr(cli_module, "handle", boom)
    code, out, _, _ = run_hook_cli(monkeypatch, capsys, {**payload, "hook_event_name": "UserPromptSubmit"},
                                   "--harness", "claude")
    assert code == 0 and "internal_error" in out["systemMessage"]


def test_hook_usage_errors_fail_open_but_cli_errors_still_exit_2(capsys, tmp_path):
    for argv in (["hook", "--harness", "claude", "--event", "Stop", "--future-flag"],
                 ["--root", str(tmp_path), "hook", "--harness", "unknown"],
                 ["--root=" + str(tmp_path), "hook", "--harness", "unknown"]):
        assert main(argv) == 0
        assert json.loads(capsys.readouterr().out) == {}
    with pytest.raises(SystemExit) as exc:
        main(["show"])
    assert exc.value.code == 2


def test_noop_events_are_decided_before_store_access(w, payload):
    break_store(w, "legacy")
    assert handle({**payload, "hook_event_name": "Stop", "stop_hook_active": True}, "claude") == {}
    assert handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Read"}, "claude") == {}
    assert handle({**payload, "hook_event_name": "PreToolUse", "tool_name": ["Edit"]}, "claude") == {}
    with pytest.raises(MigrationRequired):  # the CLI layer is what turns this into a no-op
        handle({**payload, "hook_event_name": "Stop"}, "claude")


def test_stop_reminder_is_one_shot_per_dirty_episode(w, payload):
    def tool(name):
        handle({**payload, "hook_event_name": "PreToolUse", "tool_name": name}, "claude")
    def stop(**extra):
        return handle({**payload, "hook_event_name": "Stop", **extra}, "claude")
    tool("Bash")
    assert stop()["decision"] == "block"
    assert stop(stop_hook_active=True) == {}
    assert stop() == {}  # An ignored reminder does not return on a later tool-free turn.
    tool("Edit")
    assert stop()["decision"] == "block"
    tool("Write")  # Edits made during the granted continuation...
    assert stop(stop_hook_active=True) == {}
    assert stop()["decision"] == "block"  # ...are flagged at the next turn's Stop.


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_pretooluse_matcher_selects_only_mutating_tools(harness):
    hooks = config(harness)["hooks"]
    matcher = hooks["PreToolUse"][0]["matcher"]
    assert all(re.search(matcher, name) for name in MUTATING)
    assert not any(re.search(matcher, name) for name in
                   ("Read", "Grep", "Glob", "WebFetch", "TodoWrite", "BashOutput", "mcp__fs__Edit"))
    assert all("matcher" not in groups[0] for event, groups in hooks.items() if event != "PreToolUse")


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_generated_hook_ignores_project_modules_and_fails_open(w, payload, harness):
    marker = w.ws.root / "PWNED"
    for name in ("worklog.py", "json.py"):
        (w.ws.root / name).write_text(f"open({str(marker)!r}, 'w').write({name!r})\n")
    hooks = config(harness)["hooks"]
    assert all(" -P -m worklog hook " in groups[0]["hooks"][0]["command"] for groups in hooks.values())
    def run(event, body):
        return subprocess.run(hooks[event][0]["hooks"][0]["command"], shell=True, cwd=w.ws.root,
                              input=json.dumps(body), capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=15)
    p = run("SessionStart", payload)
    assert p.returncode == 0
    assert "WORKLOG FRONTIER" in json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    break_store(w, "corrupt_runtime")
    p = run("Stop", {**payload, "hook_event_name": "Stop"})
    assert p.returncode == 0 and json.loads(p.stdout) == {}
    assert not marker.exists()


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_install_replaces_unfiltered_handler_and_keeps_user_groups(w, harness):
    target = w.ws.root / (".claude/settings.local.json" if harness == "claude" else ".codex/hooks.json")
    target.parent.mkdir()
    user = {"matcher": "Read", "hooks": [{"type": "command", "command": "echo user-read"}]}
    target.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": f"/old/python -m worklog hook --harness {harness} --event PreToolUse"}]},
        user]}}))
    install(w.ws, harness)
    groups = json.loads(target.read_text())["hooks"]["PreToolUse"]
    assert user in groups
    ours = [g for g in groups if g != user]
    assert ours == config(harness)["hooks"]["PreToolUse"]
    assert "Bash" in ours[0]["matcher"] and " -P -m worklog " in ours[0]["hooks"][0]["command"]


def test_plugin_shells_match_generated_hooks():
    spec = importlib.util.spec_from_file_location("build_plugins", ROOT / "tools/build_plugins.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    skill = (ROOT / "src/worklog/templates/SKILL.md").read_bytes()
    for harness in ("claude", "codex"):
        shell = ROOT / "plugins" / harness
        assert json.loads((shell / "hooks/hooks.json").read_text()) == build.plugin_hooks(harness)
        assert (shell / "skills/worklog/SKILL.md").read_bytes() == skill


def test_unclearable_dirty_flag_repeats_the_reminder_instead_of_losing_it(w, payload, monkeypatch):
    handle({**payload, "hook_event_name": "PreToolUse", "tool_name": "Edit"}, "claude")
    original = Runtime.update
    def failing(self, sid, **patch):
        if patch == {"dirty": False}:
            raise OSError(28, "No space left on device")
        return original(self, sid, **patch)
    monkeypatch.setattr(Runtime, "update", failing)
    stop = {**payload, "hook_event_name": "Stop"}
    assert handle(stop, "claude")["decision"] == "block"
    monkeypatch.setattr(Runtime, "update", original)
    assert handle(stop, "claude")["decision"] == "block"  # flag kept, so the reminder repeats once more
    assert handle(stop, "claude") == {}


def test_hook_output_is_ascii_json_under_a_non_utf8_locale(w, payload):
    w.add("cjk", "中文卡片", "当前位置 — 已定位", "下一步")
    command = config("claude")["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    p = subprocess.run(command, shell=True, input=json.dumps(payload), capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "latin-1"},
                       timeout=15, encoding="latin-1")
    assert p.returncode == 0 and p.stdout.isascii()
    assert "中文卡片" in json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
