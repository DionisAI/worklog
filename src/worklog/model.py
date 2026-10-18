from __future__ import annotations

import re
from typing import Any

from .errors import Capacity, Invalid
from .util import canonical, digest, revision

MAX_FRONTIER_BYTES = 32 * 1024
MAX_CARD_BYTES = 4 * 1024
MAX_CARDS = 16
MAX_NOTE_BYTES = 1024
MAX_RUNTIME_BYTES = 16 * 1024
MAX_SESSIONS = 32
RUNTIME_TTL_SECONDS = 24 * 60 * 60
SLOTS = ("1", "2", "3")
STATUSES = ("ready", "working", "blocked", "waiting", "needs_check")
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def text(value: Any, field: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise Invalid(f"{field} must be text")
    if not empty and not value.strip():
        raise Invalid(f"{field} must not be empty")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise Invalid(f"{field} contains control characters")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise Invalid(f"{field} must be valid UTF-8 text") from exc
    if size > limit:
        raise Capacity(f"{field} is {size} bytes; limit {limit}. Replace/condense current state; do not append a log.")
    return value


def identifier(value: Any, field: str = "id") -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Invalid(f"{field} must be 1..64 ASCII letters/digits/._-, starting with a letter/digit")
    return value


def token(value: Any, field: str = "revision") -> str:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise Invalid(f"{field} must be the full 32-character token returned by Worklog")
    return value


def exact_keys(obj: Any, keys: set[str], label: str) -> None:
    if not isinstance(obj, dict) or set(obj) != keys:
        raise Invalid(f"{label} must have exactly these fields: {', '.join(sorted(keys))}")


def text_list(value: Any, field: str, count: int = 8, item_bytes: int = 384) -> None:
    if not isinstance(value, list):
        raise Invalid(f"{field} must be a list")
    if len(value) > count:
        raise Capacity(f"{field} has more than {count} current items; consolidate resolved items")
    for item in value:
        text(item, field, item_bytes)
    if len(value) != len(set(value)):
        raise Invalid(f"{field} contains duplicates; keep current facts once")


def blank(workspace_id: str, name: str) -> dict:
    return {"schema_version": 2, "workspace_id": workspace_id, "name": name,
            "revision": revision(), "overview": "", "cards": {}, "notes": {}}


def make_card(card_id: str, title: str, current: str, next_action: str,
              *, cautions: list[str] | None = None, references: list[str] | None = None,
              status: str = "ready") -> dict:
    result = {"id": card_id, "revision": revision(), "title": title, "current": current,
              "next": next_action, "cautions": cautions or [], "references": references or [],
              "status": status, "owner": None}
    validate_card(result)
    return result


def validate_card(card: dict) -> None:
    exact_keys(card, {"id", "revision", "title", "current", "next", "cautions",
                      "references", "status", "owner"}, "card")
    identifier(card["id"])
    token(card["revision"])
    text(card["title"], "title", 192)
    text(card["current"], "current", 1536)
    text(card["next"], "next", 1024)
    text_list(card["cautions"], "cautions")
    text_list(card["references"], "references")
    if card["status"] not in STATUSES:
        raise Invalid(f"status must be one of {STATUSES}; completed cards must be removed")
    owner = card["owner"]
    if owner is not None:
        exact_keys(owner, {"session_id", "harness", "account", "checkout_id"}, "owner")
        identifier(owner["session_id"], "session_id")
        identifier(owner["checkout_id"], "checkout_id")
        text(owner["harness"], "harness", 48)
        text(owner["account"], "account alias", 96)
    if len(canonical(card)) > MAX_CARD_BYTES:
        raise Capacity(f"Card {card['id']} exceeds {MAX_CARD_BYTES} bytes; condense it")


def validate(state: dict) -> None:
    exact_keys(state, {"schema_version", "workspace_id", "name", "revision", "overview", "cards", "notes"}, "frontier")
    if type(state["schema_version"]) is not int or state["schema_version"] != 2:
        raise Invalid("Unsupported frontier schema (expected 2)")
    identifier(state["workspace_id"], "workspace_id")
    text(state["name"], "workspace name", 192)
    token(state["revision"])
    text(state["overview"], "overview", 2048, empty=True)
    if not isinstance(state["cards"], dict):
        raise Invalid("cards must be an object")
    if len(state["cards"]) > MAX_CARDS:
        raise Capacity(f"At most {MAX_CARDS} current cards; close/merge existing cards before adding more")
    sessions, checkouts = set(), set()
    for cid, card in state["cards"].items():
        validate_card(card)
        if cid != card["id"]:
            raise Invalid("Card key and id differ")
        if card["owner"]:
            owner = card["owner"]
            if owner["session_id"] in sessions or owner["checkout_id"] in checkouts:
                raise Invalid("One cooperating writer per checkout; one current card per session")
            sessions.add(owner["session_id"])
            checkouts.add(owner["checkout_id"])
    if not isinstance(state["notes"], dict) or not set(state["notes"]).issubset(SLOTS):
        raise Invalid("Historical landmarks occupy GLOBAL slots 1, 2, 3 only")
    for value in state["notes"].values():
        text(value, "landmark", MAX_NOTE_BYTES)


def envelope(state: dict) -> bytes:
    validate(state)
    raw = canonical({"sha256": digest(state), "state": state}) + b"\n"
    if len(raw) > MAX_FRONTIER_BYTES:
        raise Capacity(f"Current frontier exceeds {MAX_FRONTIER_BYTES} bytes; nothing was saved. Consolidate active state.")
    return raw
