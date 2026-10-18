import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worklog.hooks import config, handle, install, session_id
from worklog.errors import Invalid
from worklog.runtime import Runtime
from worklog.cli import main
from conftest import signature


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


def test_end_removes_runtime_not_ownership(w, card, payload):
    sid = session_id(payload, "claude", w.ws, "default")
    card = w.claim("replay", card["revision"], session=sid)["card"]
    handle(payload, "claude")
    handle({**payload, "hook_event_name": "SessionEnd"}, "claude")
    assert Runtime(w.ws).get(sid) is None
    assert w.show("replay")["owner"] == card["owner"]


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
