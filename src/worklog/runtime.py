from __future__ import annotations

import math
import time
from typing import Callable

from .errors import Corruption, Invalid
from .model import MAX_RUNTIME_BYTES, MAX_SESSIONS, RUNTIME_TTL_SECONDS, identifier, token
from .util import atomic_write, canonical, file_lock, load_json
from .workspace import Workspace


class Runtime:
    """Disposable bounded hook flags, NOT task state or session history.

    At most 32 current session entries; flags expire after 24h. Completed sessions
    are deleted. Eviction never removes a frontier card or historical landmark.
    """
    def __init__(self, workspace: Workspace, *, clock: Callable[[], float] = time.time):
        self.ws, self.clock = workspace, clock
        self.path = workspace.store / "runtime.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "sessions": {}}
        raw = load_json(self.path, MAX_RUNTIME_BYTES)
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "sessions"} or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise Corruption(f"Invalid runtime cache {self.path}; `worklog clear-runtime --confirm` can discard it")
        sessions = raw["sessions"]
        if not isinstance(sessions, dict) or len(sessions) > MAX_SESSIONS:
            raise Corruption(f"Runtime session bound exceeded: {self.path}")
        try:
            for sid, entry in sessions.items():
                identifier(sid)
                if not isinstance(entry, dict) or set(entry) != {"seen", "dirty", "at"}:
                    raise Invalid("Invalid runtime flags")
                if entry["seen"] is not None:
                    token(entry["seen"])
                if type(entry["dirty"]) is not bool:
                    raise Invalid("Invalid dirty flag")
                if type(entry["at"]) not in (int, float) or not math.isfinite(entry["at"]):
                    raise Invalid("Invalid runtime timestamp")
        except Invalid as exc:
            raise Corruption(f"Invalid runtime cache {self.path}: {exc}") from exc
        return raw

    def get(self, sid: str) -> dict | None:
        identifier(sid, "session_id")
        with file_lock(self.ws.lock, shared=True):
            entry = self._load()["sessions"].get(sid)
            if entry is not None and self.clock() - entry["at"] < RUNTIME_TTL_SECONDS:
                return entry
            return None

    def update(self, sid: str, *, if_seen: str | None = None, **patch) -> None:
        """With if_seen, seen is set only if it currently equals if_seen."""
        identifier(sid, "session_id")
        if set(patch) - {"seen", "dirty"}:
            raise Invalid("Runtime accepts only seen/dirty flags")
        if "seen" in patch and patch["seen"] is not None:
            token(patch["seen"])
        if if_seen is not None:
            token(if_seen)
        if "dirty" in patch and type(patch["dirty"]) is not bool:
            raise Invalid("dirty must be boolean")
        with file_lock(self.ws.lock):
            state = self._load()
            before = canonical(state)
            now = self.clock()
            # Bound size even with thousands of abrupt/never-ended sessions.
            state["sessions"] = {key: val for key, val in state["sessions"].items()
                                 if now - val["at"] < RUNTIME_TTL_SECONDS}
            sessions = state["sessions"]
            old = sessions.get(sid)
            if if_seen is not None and (not old or old["seen"] != if_seen):
                patch.pop("seen", None)  # A write this session never saw must still be injected.
            if not patch:
                return
            values = {"seen": None, "dirty": False, **(old or {}), **patch}
            # Same flags within a minute are a no-op, including mtime.
            if old and {k: values[k] for k in ("seen", "dirty")} == {k: old[k] for k in ("seen", "dirty")} and now - old["at"] < 60:
                values["at"] = old["at"]
            else:
                values["at"] = now
            if sid not in sessions and len(sessions) >= MAX_SESSIONS:
                del sessions[min(sessions, key=lambda key: (sessions[key]["at"], key))]
            sessions[sid] = values
            raw = canonical(state) + b"\n"
            if len(raw) > MAX_RUNTIME_BYTES:
                raise Corruption(f"Runtime cache exceeded its fixed budget: {self.path}")
            if before != canonical(state):
                atomic_write(self.path, raw)

    def forget(self, sid: str) -> None:
        identifier(sid, "session_id")
        with file_lock(self.ws.lock):
            state = self._load()
            if sid in state["sessions"]:
                del state["sessions"][sid]
                atomic_write(self.path, canonical(state) + b"\n")

    def clear(self) -> None:
        with file_lock(self.ws.lock):
            atomic_write(self.path, canonical({"schema_version": 1, "sessions": {}}) + b"\n")
