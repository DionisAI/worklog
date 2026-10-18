import dataclasses
import json

import pytest

from worklog.core import Worklog
from worklog.errors import Capacity, Conflict, Invalid, NotFound
from worklog.model import envelope
from conftest import signature


def test_replaces_current_without_history(w, card):
    w.update("replay", card["revision"], {"current": "A wholly new current position"})
    saved = (w.ws.store / "frontier.json").read_text()
    assert "Located duplicate counting" not in saved
    assert w.show("replay")["next"] == "Add regression test"
    assert set(p.name for p in w.ws.store.iterdir()) == {"workspace.json", "frontier.json", "worklog.lock", ".gitignore"}


def test_read_context_status_do_not_write(w, card):
    before = signature(w.ws.store)
    for _ in range(100):
        w.read()
        w.context(session="s_reader")
        w.show("replay")
        w.stats()
    assert signature(w.ws.store) == before


def test_identical_update_is_noop(w, card):
    before = signature(w.ws.store)
    result = w.update("replay", card["revision"], {"current": card["current"]})
    assert not result["changed"]
    assert result["card"]["revision"] == card["revision"]
    assert signature(w.ws.store) == before


def test_finish_removes_entire_card_and_owner(w, card):
    card = w.claim("replay", card["revision"], session="agent_a")["card"]
    w.finish("replay", card["revision"], session="agent_a", confirmed=True)
    assert w.read()["cards"] == {}
    assert "agent_a" not in (w.ws.store / "frontier.json").read_text()
    assert w.stats()["snapshot_backups"] == 0
    with pytest.raises(NotFound):
        w.show("replay")


def test_finish_requires_explicit_confirmation(w, card):
    with pytest.raises(Invalid):
        w.finish("replay", card["revision"])
    assert w.show("replay") == card


def test_stale_update_rejected(w, card):
    w.update("replay", card["revision"], {"current": "New truth"})
    before = signature(w.ws.store)
    with pytest.raises(Conflict):
        w.update("replay", card["revision"], {"current": "Lost old update"})
    assert signature(w.ws.store) == before


def test_card_cas_does_not_conflict_with_unrelated_card(w, card):
    other = w.add("other", "Other", "Still researching", "Inspect docs")["card"]
    w.update("other", other["revision"], {"current": "Found docs"})
    w.update("replay", card["revision"], {"current": "Independent progress"})
    assert w.show("other")["current"] == "Found docs"


def test_aba_delete_recreate_has_new_token(w, card):
    w.finish("replay", card["revision"], confirmed=True)
    new = w.add("replay", card["title"], card["current"], card["next"])["card"]
    assert card["revision"] != new["revision"]
    with pytest.raises(Conflict):
        w.update("replay", card["revision"], {"next": "stale"})


def test_global_landmark_replacement_and_clear(w, card):
    for slot in ("1", "2", "3"):
        w.note(slot, "old lesson " + slot, expected=w.read()["revision"])
    w.add("other", "Other", "Current", "Next")
    w.note("1", "replacement lesson", expected=w.read()["revision"])
    saved = (w.ws.store / "frontier.json").read_text()
    assert "old lesson 1" not in saved
    assert len(w.read()["notes"]) == 3
    w.note("2", None, expected=w.read()["revision"])
    assert len(w.read()["notes"]) == 2
    assert "replacement lesson" in w.context("other")


@pytest.mark.parametrize("slot", ["0", "4", "01", "-1", "any", 1, None])
def test_no_fourth_landmark_slot(w, slot):
    before = signature(w.ws.store)
    with pytest.raises(Invalid):
        w.note(slot, "should never be saved", expected=w.read()["revision"])
    assert signature(w.ws.store) == before


def test_schema_forbids_fourth_slot_even_without_cli(w):
    state = w.read()
    state["notes"] = {str(i): "x" for i in range(1, 5)}
    with pytest.raises(Invalid):
        envelope(state)


def test_note_cas_uses_workspace_revision(w, card):
    old = w.read()["revision"]
    w.update("replay", card["revision"], {"current": "changed"})
    with pytest.raises(Conflict):
        w.note("1", "don't overwrite other state", expected=old)


def test_note_noop(w):
    w.note("1", "lesson", expected=w.read()["revision"])
    before = signature(w.ws.store)
    w.note("1", "lesson", expected=w.read()["revision"])
    assert signature(w.ws.store) == before


def test_utf8_byte_limit_not_character_limit(w):
    w.note("1", "错" * 341, expected=w.read()["revision"])
    before = signature(w.ws.store)
    with pytest.raises(Capacity):
        w.note("1", "错" * 342, expected=w.read()["revision"])
    assert signature(w.ws.store) == before


@pytest.mark.parametrize("patch", [
    {"current": ""}, {"next": ""}, {"title": "x" * 193}, {"current": "x" * 1537},
    {"next": "x" * 1025}, {"cautions": ["x"] * 9}, {"references": ["x"] * 9},
    {"cautions": ["x" * 385]}, {"references": ["x" * 385]}, {"cautions": ["same", "same"]},
    {"status": "done"}, {"session_log": ["a"]}, {"current": "contains\x00null"},
    {"cautions": "wrong type"}, {"title": None}, {"references": [123]},
])
def test_invalid_update_does_not_partially_save(w, card, patch):
    before = signature(w.ws.store)
    with pytest.raises(Invalid):
        w.update("replay", card["revision"], patch)
    assert signature(w.ws.store) == before


def test_total_card_limit(w, card):
    with pytest.raises(Capacity):
        w.update("replay", card["revision"], {"current": "a"*1536, "next": "b"*1024,
                                              "cautions": ["c"*380+str(i) for i in range(6)]})


def test_total_frontier_limit_is_hard(w):
    failed = False
    for i in range(16):
        before = signature(w.ws.store)
        try:
            w.add(f"c{i}", "a"*190, "b"*1536, "c"*1024,
                  cautions=["d"*380+str(n) for n in range(2)])
        except Capacity:
            assert signature(w.ws.store) == before
            failed = True
            break
    assert failed and w.stats()["frontier_bytes"] <= 32768


def test_sixteen_current_cards_limit(w):
    for i in range(16):
        w.add(f"c{i}", "Current", "Still working", "Next")
    with pytest.raises(Capacity):
        w.add("c17", "Extra", "Current", "Next")
    context = w.context()
    assert "### c0 —" in context and "### c15 —" in context
    assert "TRUNCATED" not in context


def test_replace_lists_does_not_append(w, card):
    c = w.update("replay", card["revision"], {"cautions": ["old"], "references": ["old.log"]})["card"]
    c = w.update("replay", c["revision"], {"cautions": ["new"], "references": []})["card"]
    assert c["cautions"] == ["new"] and c["references"] == []
    c = w.update("replay", c["revision"], {"cautions": []})["card"]
    assert c["cautions"] == []


def test_claim_handoff_and_resume(w, card):
    c = w.claim("replay", card["revision"], session="claude_a", harness="claude", account="A")["card"]
    with pytest.raises(Conflict):
        w.update("replay", c["revision"], {"current": "bad"}, session="codex_b")
    c = w.update("replay", c["revision"], {"current": "Fixed", "next": "Verify"}, session="claude_a", release=True)["card"]
    assert c["owner"] is None and c["status"] == "ready"
    c = w.claim("replay", c["revision"], session="codex_b", harness="codex", account="B")["card"]
    assert c["owner"]["session_id"] == "codex_b"
    assert "claude_a" not in (w.ws.store / "frontier.json").read_text()


def test_checkout_single_writer(w, card):
    w.claim("replay", card["revision"], session="first")
    c = w.add("another", "Other", "Current", "Next")["card"]
    with pytest.raises(Conflict):
        w.claim("another", c["revision"], session="second")


def test_takeover_not_automatic(w, card):
    c = w.claim("replay", card["revision"], session="first")["card"]
    with pytest.raises(Conflict):
        w.claim("replay", c["revision"], session="second")
    with pytest.raises(Invalid):
        w.claim("replay", c["revision"], session="second", takeover=True)
    c = w.claim("replay", c["revision"], session="second", takeover=True, confirmed=True)["card"]
    assert c["status"] == "needs_check"
    with pytest.raises(Conflict):
        w.update("replay", c["revision"], {"next": "steal back"}, session="first")


def test_claim_conflicts_name_the_blocker_and_the_exact_takeover(w, card):
    c = w.claim("replay", card["revision"], session="crashed", harness="claude")["card"]
    takeover = f"worklog takeover replay --expect {c['revision']} --session newer --confirm-old-writer-stopped"
    with pytest.raises(Conflict) as exc:
        w.claim("replay", c["revision"], session="newer")
    assert "session crashed (claude, this checkout)" in str(exc.value) and takeover in str(exc.value)
    other = w.add("other", "Other", "Current", "Next")["card"]
    with pytest.raises(Conflict) as exc:
        w.claim("other", other["revision"], session="newer")  # the checkout, not the card, is taken
    assert "Card replay is owned by session crashed" in str(exc.value) and takeover in str(exc.value)
    assert "worktree" not in str(exc.value)  # a plain directory has no worktrees to suggest
    with pytest.raises(Conflict) as exc:
        w.claim("other", other["revision"], session="crashed")
    assert f"worklog handoff replay --expect {c['revision']} --session crashed" in str(exc.value)
    taken = w.claim("replay", c["revision"], session="newer", takeover=True, confirmed=True)["card"]
    assert taken["owner"]["session_id"] == "newer" and taken["status"] == "needs_check"


def test_release_session_frees_only_its_cards_in_this_checkout(w, card):
    linked = Worklog(dataclasses.replace(w.ws, root=w.ws.root / "linked", checkout="worktree:linked"))
    c = linked.claim("replay", card["revision"], session="ended")["card"]
    alive = w.add("alive", "Alive", "Current", "Next")["card"]
    w.claim("alive", alive["revision"], session="alive")
    w.update("alive", w.show("alive")["revision"], {"status": "waiting"}, session="alive")
    before = signature(w.ws.store)
    assert w.release_session("ended")["result"] == [] and signature(w.ws.store) == before
    assert linked.release_session("ended")["result"] == ["replay"]
    released = w.show("replay")
    assert released["owner"] is None and released["status"] == "needs_check"
    assert released["current"] == c["current"] and released["revision"] != c["revision"]
    assert w.show("alive")["owner"]["session_id"] == "alive"
    assert w.release_session("alive")["result"] == ["alive"]
    assert w.show("alive")["owner"] is None and w.show("alive")["status"] == "waiting"


def test_focus_context_keeps_all_owner_summaries_and_notes(w, card):
    w.note("3", "Critical old lesson", expected=w.read()["revision"])
    w.add("another", "Other module", "Blocked", "Ask for input")
    ctx = w.context("replay")
    assert "Critical old lesson" in ctx and "\n- another | ready | free | Other module\n" in ctx
    assert "Located duplicate counting" in ctx and "### another" not in ctx


def test_overview_replaces_and_clears(w):
    w.overview("Old focus", expected=w.read()["revision"])
    w.overview("New focus", expected=w.read()["revision"])
    assert "Old focus" not in w.context()
    w.overview("", expected=w.read()["revision"])
    assert w.read()["overview"] == ""


@pytest.mark.parametrize("bad_id", ["../outside", "", "a/b", "a"*65, "has space", "-prefix"])
def test_safe_card_ids(w, bad_id):
    with pytest.raises(Invalid):
        w.add(bad_id, "x", "y", "z")


def test_noncanonical_extra_state_is_rejected(w):
    state = w.read()
    state["archive"] = []
    with pytest.raises(Invalid):
        envelope(state)


@pytest.mark.parametrize("bad", [None, "", "123", 0])
@pytest.mark.parametrize("operation", ["update", "claim", "finish"])
def test_python_api_cannot_bypass_cas_with_missing_token(w, card, bad, operation):
    before = signature(w.ws.store)
    with pytest.raises(Invalid):
        if operation == "update":
            w.update("replay", bad, {"current": "bad"})
        elif operation == "claim":
            w.claim("replay", bad, session="bad_writer")
        else:
            w.finish("replay", bad, confirmed=True)
    assert signature(w.ws.store) == before
