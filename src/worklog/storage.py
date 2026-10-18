from __future__ import annotations

import copy
from typing import Callable, Any

from .errors import Conflict, Corruption, Invalid
from .model import MAX_FRONTIER_BYTES, blank, envelope, validate
from .util import atomic_write, digest, file_lock, load_json, revision
from .workspace import Workspace


class Store:
    """One bounded authoritative snapshot. No replay, history, archive or backups."""
    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self.path = workspace.store / "frontier.json"

    def _load(self) -> dict:
        try:
            wrapped = load_json(self.path, MAX_FRONTIER_BYTES)
        except FileNotFoundError as exc:
            raise Corruption(f"{self.path} is missing; Worklog never recreates it on its own (see `worklog doctor`)") from exc
        if not isinstance(wrapped, dict) or set(wrapped) != {"sha256", "state"}:
            raise Corruption(f"Invalid frontier envelope: {self.path}")
        state = wrapped["state"]
        try:
            validate(state)
            if state["workspace_id"] != self.ws.workspace_id or digest(state) != wrapped["sha256"]:
                raise Corruption(f"Frontier checksum or workspace identity mismatch: {self.path}")
        except Invalid as exc:
            raise Corruption(f"Invalid saved frontier {self.path}: {exc}") from exc
        return state

    def reset_unreadable(self) -> dict:
        """Operator-confirmed recovery: replace an UNREADABLE frontier with an empty one.

        Never automatic and never for readable state; nothing is archived."""
        with file_lock(self.ws.lock):
            try:
                self._load()
            except Corruption:
                state = blank(self.ws.workspace_id, self.ws.name)
                atomic_write(self.path, envelope(state))
                return state
        raise Conflict(f"{self.path} is readable; update or finish its cards instead of resetting")

    def read(self) -> dict:
        with file_lock(self.ws.lock, shared=True):
            return self._load()

    def transaction(self, mutate: Callable[[dict], Any]) -> tuple[Any, dict, bool, str]:
        """Returns (result, state, changed, pre-write revision); the last lets a caller
        tell its own write apart from writes it has not seen."""
        with file_lock(self.ws.lock):
            before = self._load()
            after = copy.deepcopy(before)
            result = mutate(after)
            if before == after:
                return copy.deepcopy(result), before, False, before["revision"]
            after["revision"] = revision()
            raw = envelope(after)  # Validate before touching the current snapshot.
            atomic_write(self.path, raw)
            return copy.deepcopy(result), after, True, before["revision"]
