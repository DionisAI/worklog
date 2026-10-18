import json
from pathlib import Path

import pytest

from worklog.core import Worklog
from worklog.errors import Conflict, Corruption, Invalid, MigrationRequired
from worklog.migration import apply, plan
from worklog.util import canonical, digest
from worklog.workspace import Workspace


def legacy_fixture(root: Path):
    store = root / ".worklog"
    (store / "events").mkdir(parents=True)
    (store / "artifacts").mkdir()
    (store / "views").mkdir()
    meta = {"schema_version": 1, "workspace_id": "ws_legacy", "name": "Legacy"}
    (store / "workspace.json").write_bytes(canonical(meta))
    (store / "artifacts" / ("f" * 64)).write_text("OLD_TRANSCRIPT_ARTIFACT_TO_DISCARD")
    (store / "views" / "state.json").write_text("OLD_SESSION_HISTORY_TO_DISCARD")
    event = {"schema_version": 1, "workspace_id": "ws_legacy", "seq": 1, "prev_hash": "0"*64,
             "operation": "fixture", "timestamp": 1.0, "changes": {
                 "tasks": {
                     "active": {"id": "active", "title": "Live task", "objective": "Original goal", "status": "in_progress",
                                "latest_semantic_checkpoint": "cp1", "latest_checkpoint": "cp2", "latest_handoff": None},
                     "completed": {"id": "completed", "title": "Finished", "status": "verified"}},
                 "checkpoints": {
                     "cp1": {"id": "cp1", "summary": "Actual latest finding", "next_action": "Inspect running job",
                             "unknowns": ["Not checked on full data"], "observed_at": 2.0},
                     "cp2": {"id": "cp2", "summary": "Automatic observation, not semantic", "observed_at": 3.0}},
                 "jobs": {"j1": {"id": "j1", "task_id": "active", "status": "running",
                                 "process": {"pid": 1234}, "log": "/external/replay.log", "tmux": "replay:0"}},
                 "sessions": {"retired": {"id": "retired", "status": "ended"}}}}
    event["hash"] = digest(event)
    (store / "events" / "0000000000000001.json").write_bytes(canonical(event))
    return Workspace.open(root, allow_legacy=True)


@pytest.fixture
def legacy(tmp_path):
    return legacy_fixture(tmp_path)


def test_legacy_requires_explicit_migration(legacy):
    with pytest.raises(MigrationRequired):
        Workspace.open(legacy.root)
    with pytest.raises(MigrationRequired):
        Workspace.open(legacy.root, create=True)


def test_plan_only_current_work_and_no_invented_lessons(legacy):
    p = plan(legacy)
    assert set(p["frontier"]["cards"]) == {"active"}
    assert p["frontier"]["cards"]["active"]["current"] == "Actual latest finding"
    assert "tmux:replay:0" in p["frontier"]["cards"]["active"]["references"]
    assert "1234" in str(p["frontier"]["cards"]["active"]["cautions"])
    assert p["frontier"]["notes"] == {}
    assert p["validation_errors"] == []
    assert (legacy.store / "events").exists() and not (legacy.store / "frontier.json").exists()


def test_apply_removes_old_history_instead_of_archiving(legacy):
    p = plan(legacy)
    p["frontier"]["notes"] = {"1": "Reviewed important mistake"}
    result = apply(legacy, p, discard_history=True, agents_stopped=True)
    w = Worklog(Workspace.open(legacy.root))
    assert result["legacy_history_deleted"]
    assert not result["history_archived"]
    assert w.read()["notes"] == {"1": "Reviewed important mistake"}
    assert set(x.name for x in legacy.store.iterdir()) == {"workspace.json", "frontier.json", "worklog.lock"}
    assert w.stats()["unexpected_files"] == []
    assert "retired" not in (legacy.store / "frontier.json").read_text()
    assert "OLD_TRANSCRIPT" not in (legacy.store / "frontier.json").read_text()


@pytest.mark.parametrize("discard,stopped", [(False,False), (True,False), (False,True)])
def test_requires_both_destructive_migration_flags(legacy, discard, stopped):
    p = plan(legacy)
    with pytest.raises(Invalid):
        apply(legacy, p, discard_history=discard, agents_stopped=stopped)
    assert (legacy.store / "events").exists()


def test_rejects_silent_omission_of_active_work(legacy):
    p = plan(legacy)
    p["frontier"]["cards"] = {}
    with pytest.raises(Invalid):
        apply(legacy, p, discard_history=True, agents_stopped=True)
    p["acknowledged_omissions"] = ["active"]
    apply(legacy, p, discard_history=True, agents_stopped=True)
    assert Worklog(Workspace.open(legacy.root)).read()["cards"] == {}


def test_fourth_historical_slot_prevents_apply(legacy):
    p = plan(legacy)
    p["frontier"]["notes"] = {str(i): "lesson" for i in range(1,5)}
    with pytest.raises(Invalid):
        apply(legacy, p, discard_history=True, agents_stopped=True)
    assert not (legacy.store / "frontier.json").exists()


def test_changed_old_history_rejects_stale_proposal(legacy):
    p = plan(legacy)
    first = json.loads((legacy.store / "events/0000000000000001.json").read_bytes())
    second = {"schema_version": 1, "workspace_id": "ws_legacy", "seq": 2,
              "prev_hash": first["hash"], "operation": "later", "timestamp": 4.0, "changes": {"sessions": {"another": {"id": "another"}}}}
    second["hash"] = digest(second)
    (legacy.store / "events/0000000000000002.json").write_bytes(canonical(second))
    with pytest.raises(Conflict):
        apply(legacy, p, discard_history=True, agents_stopped=True)
    assert not (legacy.store / "frontier.json").exists()


def test_corrupt_old_event_refuses_to_guess(legacy):
    path = legacy.store / "events/0000000000000001.json"
    data = json.loads(path.read_bytes())
    data["hash"] = "0"*64
    path.write_bytes(canonical(data))
    with pytest.raises(Corruption):
        plan(legacy)


@pytest.mark.parametrize("fail_at", ["frontier.json", "workspace.json"])
def test_interrupted_publication_can_resume_exact_proposal(legacy, monkeypatch, fail_at):
    import worklog.migration as m
    p = plan(legacy)
    original = m.atomic_write
    def fail(path, data):
        if path.name == fail_at:
            raise OSError("simulated interruption")
        return original(path, data)
    monkeypatch.setattr(m, "atomic_write", fail)
    with pytest.raises(OSError):
        apply(legacy, p, discard_history=True, agents_stopped=True)
    assert (legacy.store / "migration.json").exists()
    with pytest.raises(MigrationRequired):
        Workspace.open(legacy.root)
    monkeypatch.setattr(m, "atomic_write", original)
    apply(legacy, p, discard_history=True, agents_stopped=True)
    assert Worklog(Workspace.open(legacy.root)).read()["cards"]["active"]["current"] == "Actual latest finding"
    assert not (legacy.store / "migration.json").exists()


def test_interrupted_cleanup_resumes_and_blocks_normal_writes(legacy, monkeypatch):
    import worklog.migration as m
    p = plan(legacy)
    original = m._cleanup
    monkeypatch.setattr(m, "_cleanup", lambda ws: (_ for _ in ()).throw(OSError("interrupted cleanup")))
    with pytest.raises(OSError):
        apply(legacy, p, discard_history=True, agents_stopped=True)
    with pytest.raises(MigrationRequired):
        Workspace.open(legacy.root)
    monkeypatch.setattr(m, "_cleanup", original)
    apply(Workspace.open(legacy.root, allow_legacy=True), p, discard_history=True, agents_stopped=True)
    assert not (legacy.store / "events").exists()


def test_completed_migration_retry_does_not_clobber_new_work(legacy):
    p = plan(legacy)
    apply(legacy, p, discard_history=True, agents_stopped=True)
    assert apply(legacy, p, discard_history=True, agents_stopped=True)["already_applied"]
    w = Worklog(Workspace.open(legacy.root))
    w.update("active", w.show("active")["revision"], {"current": "New work after migration"})
    with pytest.raises(Conflict):
        apply(legacy, p, discard_history=True, agents_stopped=True)
    assert w.show("active")["current"] == "New work after migration"


@pytest.mark.parametrize("handoff_time,expected", [(1.0, "Actual latest finding"), (4.0, "A newer handoff")])
def test_actual_v01_observed_at_compared_with_handoff_created_at(legacy, handoff_time, expected):
    path = legacy.store / "events/0000000000000001.json"
    event = json.loads(path.read_bytes())
    event.pop("hash")
    event["changes"]["tasks"]["active"]["latest_handoff"] = "h1"
    event["changes"]["handoffs"] = {"h1": {"id": "h1", "summary": "A newer handoff",
                                            "next_action": "Continue here", "created_at": handoff_time}}
    event["hash"] = digest(event)
    path.write_bytes(canonical(event))
    assert plan(legacy)["frontier"]["cards"]["active"]["current"] == expected


def test_missing_handoff_never_erases_checkpoint_without_timestamp(legacy):
    path = legacy.store / "events/0000000000000001.json"
    event = json.loads(path.read_bytes())
    event.pop("hash")
    event["changes"]["checkpoints"]["cp1"].pop("observed_at")
    event["hash"] = digest(event)
    path.write_bytes(canonical(event))
    assert plan(legacy)["frontier"]["cards"]["active"]["current"] == "Actual latest finding"
