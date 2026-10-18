import json
import multiprocessing as mp
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worklog.core import Worklog
from worklog.errors import Busy, Conflict, Corruption, Invalid
from worklog.runtime import Runtime
from worklog.util import canonical, file_lock, revision
from worklog.workspace import Workspace
from conftest import signature


def _race_claim(root, expected, n, gate, results):
    gate.wait()
    try:
        w = Worklog(Workspace.open(root))
        w.claim("replay", expected, session=f"worker_{n}")
        results.put("won")
    except Conflict:
        results.put("conflict")


def _race_update(root, card_id, expected, gate, results):
    gate.wait()
    try:
        w = Worklog(Workspace.open(root))
        w.update(card_id, expected, {"current": f"updated {card_id}"})
        results.put("ok")
    except Exception as exc:
        results.put(repr(exc))


def test_eight_real_processes_one_claim_winner(w, card):
    ctx = mp.get_context("spawn")
    gate, results = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_race_claim, args=(str(w.ws.root), card["revision"], n, gate, results)) for n in range(8)]
    for p in processes:
        p.start()
    gate.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    for p in processes:
        p.join(10)
        assert p.exitcode == 0
    assert outcomes.count("won") == 1 and outcomes.count("conflict") == 7


def test_concurrent_different_card_updates_are_not_lost(w):
    ctx = mp.get_context("spawn")
    gate, results = ctx.Event(), ctx.Queue()
    cards = [w.add(f"c{i}", "title", "old", "next")["card"] for i in range(8)]
    processes = [ctx.Process(target=_race_update, args=(str(w.ws.root), c["id"], c["revision"], gate, results)) for c in cards]
    for p in processes:
        p.start()
    gate.set()
    assert [results.get(timeout=10) for _ in processes] == ["ok"] * 8
    for p in processes:
        p.join(10)
        assert p.exitcode == 0
    assert all(c["current"] == f"updated {c['id']}" for c in w.read()["cards"].values())


def test_busy_lock_timeout(w):
    with file_lock(w.ws.lock):
        with pytest.raises(Busy):
            with file_lock(w.ws.lock, timeout=0):
                pass


def test_failed_replace_preserves_old_state(w, card, monkeypatch):
    import worklog.util as util
    before = signature(w.ws.store)
    def fail(*args):
        raise OSError("simulated pre-publication failure")
    monkeypatch.setattr(util.os, "replace", fail)
    with pytest.raises(OSError):
        w.update("replay", card["revision"], {"current": "not committed"})
    assert signature(w.ws.store) == before


def test_post_replace_error_requires_reread_but_state_is_valid(w, card, monkeypatch):
    import worklog.util as util
    def fail(*args):
        raise OSError("simulated directory fsync failure")
    monkeypatch.setattr(util, "fsync_dir", fail)
    with pytest.raises(OSError):
        w.update("replay", card["revision"], {"current": "published before reply loss"})
    assert w.show("replay")["current"] == "published before reply loss"
    with pytest.raises(Conflict):
        w.update("replay", card["revision"], {"current": "retry stale"})


def test_repeated_abrupt_crashes_leave_only_one_fixed_staging_file(w, card):
    code = '''
import os
from worklog.core import Worklog
from worklog.workspace import Workspace
import worklog.util as u
w = Worklog(Workspace.open(os.environ['TEST_ROOT']))
c = w.show('replay')
def crash(source, destination):
    os._exit(23)
u.os.replace = crash
w.update('replay', c['revision'], {'current': 'unpublished-' + 'x'*1200})
'''
    source = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": source, "TEST_ROOT": str(w.ws.root)}
    for _ in range(5):
        p = subprocess.run([sys.executable, "-c", code], env=env, timeout=10)
        assert p.returncode == 23
        assert w.show("replay")["current"] == card["current"]
        assert len(list(w.ws.store.glob(".*.tmp"))) == 1
    w.update("replay", card["revision"], {"current": "next valid write"})
    assert not list(w.ws.store.glob(".*.tmp"))
    assert w.stats()["files"] == 4  # includes the fixed .worklog/.gitignore


def test_abrupt_crash_after_replace_has_valid_new_state(w, card):
    code = '''
import os
from worklog.core import Worklog
from worklog.workspace import Workspace
import worklog.util as u
w = Worklog(Workspace.open(os.environ['TEST_ROOT']))
def crash(path):
    os._exit(24)
u.fsync_dir = crash
c = w.show('replay')
w.update('replay', c['revision'], {'current': 'published-state'})
'''
    source = str(Path(__file__).resolve().parents[1] / "src")
    p = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": source,
                       "TEST_ROOT": str(w.ws.root)}, timeout=10)
    assert p.returncode == 24
    assert w.show("replay")["current"] == "published-state"


@pytest.mark.parametrize("broken", [b"{broken", b'[]', b'{"state":{}}', b'x' * 32769])
def test_corruption_not_treated_as_empty(w, broken):
    (w.ws.store / "frontier.json").write_bytes(broken)
    with pytest.raises(Corruption):
        w.read()


def test_checksum_detects_mutation(w, card):
    path = w.ws.store / "frontier.json"
    raw = json.loads(path.read_bytes())
    raw["state"]["cards"]["replay"]["current"] = "tampered"
    path.write_bytes(canonical(raw))
    with pytest.raises(Corruption):
        w.read()


def test_missing_state_never_auto_reinitializes(w):
    (w.ws.store / "frontier.json").unlink()
    with pytest.raises(Corruption):
        Worklog(Workspace.open(w.ws.root, create=True)).read()


def test_temp_symlink_does_not_overwrite_external_file(w, card, tmp_path):
    victim = tmp_path / "do-not-overwrite"
    victim.write_text("unchanged")
    (w.ws.store / ".frontier.json.tmp").symlink_to(victim)
    with pytest.raises(OSError):
        w.update("replay", card["revision"], {"current": "bad"})
    assert victim.read_text() == "unchanged"


def test_runtime_bound_and_frontier_unchanged(w, card):
    frontier_before = (w.ws.store / "frontier.json").read_bytes()
    now = [1000.0]
    r = Runtime(w.ws, clock=lambda: now[0])
    for i in range(200):
        now[0] += 1
        r.update(f"s_{i:032d}", dirty=True, seen=w.read()["revision"])
    raw = json.loads(r.path.read_bytes())
    assert len(raw["sessions"]) == 32 and r.path.stat().st_size <= 16384
    assert (w.ws.store / "frontier.json").read_bytes() == frontier_before
    assert w.show("replay") == card


def test_runtime_noop_and_read_are_side_effect_free(w):
    r = Runtime(w.ws, clock=lambda: 100.0)
    r.update("reader", dirty=True)
    before = signature(w.ws.store)
    for _ in range(100):
        r.update("reader", dirty=True)
        r.get("reader")
    assert signature(w.ws.store) == before


def test_runtime_expiration_prunes_flags_not_frontier(w, card):
    now = [1.0]
    r = Runtime(w.ws, clock=lambda: now[0])
    r.update("old", dirty=True)
    now[0] += 86401
    assert r.get("old") is None
    r.update("new", dirty=False)
    assert set(json.loads(r.path.read_bytes())["sessions"]) == {"new"}
    assert w.show("replay") == card


def test_runtime_seen_advances_only_from_the_expected_revision(w):
    r = Runtime(w.ws, clock=lambda: 100.0)
    a, b, c = revision(), revision(), revision()
    r.update("never_injected", seen=b, if_seen=a)
    assert not r.path.exists()  # no entry, nothing seen: no write and no seen invented
    r.update("s", seen=a)
    r.update("s", seen=c, if_seen=b)  # an unseen write happened in between: keep seen
    assert r.get("s")["seen"] == a
    r.update("s", seen=b, if_seen=a, dirty=True)
    assert r.get("s") == {"seen": b, "dirty": True, "at": 100.0}


def test_runtime_end_deletes_not_archives(w):
    r = Runtime(w.ws)
    r.update("ending", dirty=True)
    r.forget("ending")
    assert "ending" not in r.path.read_text()
    before = signature(w.ws.store)
    r.forget("ending")
    assert signature(w.ws.store) == before


def test_runtime_corruption_can_be_explicitly_cleared(w, card):
    r = Runtime(w.ws)
    r.path.write_bytes(b"broken")
    with pytest.raises(Corruption):
        r.get("s")
    r.clear()
    assert r.get("s") is None
    assert w.show("replay") == card


def test_git_worktrees_share_frontier_but_separate_owners(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (root / "code.py").write_text("x = 1\n")
    git("add", "code.py")
    git("commit", "-m", "initial")
    other = tmp_path / "other"
    git("worktree", "add", "-b", "worker", str(other))
    a = Worklog(Workspace.open(root, create=True))
    b = Worklog(Workspace.open(other))
    assert a.ws.store == b.ws.store and a.ws.checkout_id != b.ws.checkout_id
    ca = a.add("a", "A", "current", "next")["card"]
    cb = b.add("b", "B", "current", "next")["card"]
    a.claim("a", ca["revision"], session="a_session")
    b.claim("b", cb["revision"], session="b_session")
    assert a.read() == b.read()
    with pytest.raises(Conflict):
        b.update("a", a.show("a")["revision"], {"next": "wrong checkout"}, session="a_session")


def test_checkout_conflict_suggests_a_worktree_only_inside_git(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, env=git_env())
    w = Worklog(Workspace.open(tmp_path, create=True))
    a, b = (w.add(cid, "T", "C", "N")["card"] for cid in ("a", "b"))
    w.claim("a", a["revision"], session="first")
    with pytest.raises(Conflict, match="separate Git worktree") as exc:
        w.claim("b", b["revision"], session="second")
    assert "worklog takeover a --expect" in str(exc.value)


def test_open_nested_plain_workspace(w):
    sub = w.ws.root / "nested" / "folder"
    sub.mkdir(parents=True)
    assert Workspace.open(sub) == w.ws


def test_plain_workspace_stays_discoverable_after_git_init(w, card):
    subprocess.run(["git", "-C", str(w.ws.root), "init"], check=True, capture_output=True)
    again = Workspace.open(w.ws.root)
    assert again.store == w.ws.store
    assert Worklog(again).show("replay") == card


def test_plain_then_git_then_worktree_still_shares_frontier(w, card, tmp_path):
    root = w.ws.root
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (root / "code.py").write_text("x=1\n")
    git("add", "code.py")
    git("commit", "-m", "initial")
    other = root.parent / (root.name + "-linked")
    git("worktree", "add", "-b", "linked", str(other))
    b = Worklog(Workspace.open(other))
    assert b.ws.store == w.ws.store
    assert b.show("replay") == card


def git_env():
    # No global/system config and no default XDG excludes file: only .worklog/.gitignore may ignore.
    return {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "XDG_CONFIG_HOME": os.devnull}


def test_plain_store_is_git_ignored_from_init(w, card):
    assert (w.ws.store / ".gitignore").read_bytes() == b"*\n"
    assert Worklog(w.ws).stats()["unexpected_files"] == []
    root = w.ws.root
    (root / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=git_env())
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=git_env())
    staged = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only"],
                            check=True, capture_output=True, text=True, env=git_env()).stdout.split()
    assert staged == ["code.py"]


def test_init_adds_missing_ignore_file_but_reads_never_do(w, card):
    ignore = w.ws.store / ".gitignore"
    ignore.unlink()
    frontier = (w.ws.store / "frontier.json").read_bytes()
    Worklog(Workspace.open(w.ws.root)).context()
    assert not ignore.exists()
    Workspace.open(w.ws.root, create=True)
    assert ignore.read_bytes() == b"*\n"
    assert (w.ws.store / "frontier.json").read_bytes() == frontier


def test_git_common_dir_store_gets_no_ignore_file(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, env=git_env())
    ws = Workspace.open(tmp_path, create=True)
    assert ws.store.name == "worklog" and not (ws.store / ".gitignore").exists()
    assert not (tmp_path / ".worklog").exists()


def test_init_refuses_a_symlinked_ignore_file(w, tmp_path):
    ignore = w.ws.store / ".gitignore"
    target = tmp_path / "elsewhere"
    target.write_text("*\n")
    ignore.unlink()
    ignore.symlink_to(target)  # git does not follow an in-tree .gitignore symlink
    with pytest.raises(Invalid):
        Workspace.open(w.ws.root, create=True)
