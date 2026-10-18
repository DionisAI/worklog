from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import Conflict, Corruption, Invalid, MigrationRequired, NotFound
from .model import blank, envelope, identifier, text
from .util import atomic_write, canonical, digest, file_lock, load_json, revision


def git_output(path: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                           text=True, timeout=5)
        return p.stdout.rstrip("\n") if p.returncode == 0 else None  # Keep spaces inside paths.
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


@dataclass(frozen=True)
class Workspace:
    root: Path
    store: Path
    workspace_id: str
    name: str
    git: bool = False  # Inside a Git repository, so linked worktrees share this store.
    # "main" (also a plain directory) or "worktree:<admin name>"; empty falls back to the path.
    checkout: str = ""

    @property
    def legacy_checkout_id(self) -> str:
        # Older owners hashed the absolute path, which changed whenever a checkout moved.
        return "co_" + digest(str(self.root))[:24]

    @property
    def checkout_id(self) -> str:
        if not self.checkout:
            return self.legacy_checkout_id
        return "co_" + digest([self.workspace_id, self.checkout])[:24]

    def is_checkout(self, checkout_id: str) -> bool:
        """True for this checkout's id, including an owner recorded under the old path-based id."""
        return checkout_id in (self.checkout_id, self.legacy_checkout_id)

    @property
    def lock(self) -> Path:
        return self.store / "worklog.lock"

    @classmethod
    def open(cls, path: Path | str = ".", *, create: bool = False,
             name: str | None = None, allow_legacy: bool = False) -> Workspace:
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            raise Invalid(f"Not a directory: {path}")
        info = git_output(path, "rev-parse", "--path-format=absolute",
                          "--show-toplevel", "--git-common-dir", "--git-dir")
        top = None
        if info:
            parts = info.split("\n")  # Not splitlines(): paths may hold other line-break characters.
            if len(parts) != 3:
                raise Invalid("Cannot resolve the Git common directory")
            top, common, git_dir = parts
            root = Path(top).resolve()
            common_path = Path(common)
            # Which checkout this is, not where it lives: survives moving or renaming the repo.
            linked = Path(git_dir).resolve() != common_path.resolve()
            checkout = "worktree:" + Path(git_dir).name if linked else "main"
            store = common_path / "worklog"
            # A workspace initialized before `git init` remains shared by later
            # worktrees of a conventional .git directory, without copying state.
            main_plain = common_path.parent / ".worklog"
            if (common_path.name == ".git" and not (store / "workspace.json").exists()
                    and (main_plain / "workspace.json").exists()
                    and ((main_plain / "frontier.json").exists() or (main_plain / "events").is_dir())):
                store = main_plain
        else:
            root = path
            for ancestor in [path, *path.parents]:
                if (ancestor / ".worklog" / "workspace.json").exists():
                    root = ancestor
                    break
            store = root / ".worklog"
            checkout = "main"  # The same label it gets after a later `git init`, so owners survive it.
        pointer = root / ".worklog" / "workspace.json"
        if pointer.exists():
            binding = load_json(pointer, 4096)
            if not isinstance(binding, dict):
                raise Corruption(f"Invalid workspace pointer: {pointer}")
            if "store" in binding:
                selected = Path(binding["store"]).expanduser()
                store = (selected if selected.is_absolute() else root / selected).resolve()
                checkout = ""  # A redirected store may serve several directories or repos: keep them apart.
            elif (pointer.parent / "frontier.json").exists() or (pointer.parent / "events").is_dir():
                # A plain workspace may later gain a Git repository. Do not lose its
                # explicitly present local store by silently switching to .git/worklog.
                candidate = pointer.parent
                existing_meta = store / "workspace.json"
                if candidate != store and existing_meta.exists():
                    other = load_json(existing_meta, 4096)
                    if not isinstance(other, dict):
                        raise Corruption(f"Invalid workspace metadata: {existing_meta}")
                    if other.get("workspace_id") != binding.get("workspace_id"):
                        raise Conflict(f"Two different Worklog stores are present ({candidate} and {store}); "
                                       "reconcile their workspace bindings explicitly")
                store = candidate
        meta = store / "workspace.json"
        if not meta.exists():
            if not create:
                raise NotFound("No Worklog workspace here. Run `worklog init` first.")
            store.mkdir(parents=True, exist_ok=True, mode=0o700)
            with file_lock(store / "worklog.lock"):
                if not meta.exists():
                    target = store / "frontier.json"
                    if target.exists():
                        # Recover only an interrupted initialization, never invent empty state.
                        prior = load_json(target, 32768)
                        state = prior.get("state") if isinstance(prior, dict) else None
                        envelope(state)
                        if prior.get("sha256") != digest(state):
                            raise Corruption(f"Interrupted initialization has a corrupt frontier: {target}")
                    else:
                        state = blank("ws_" + revision(), name or root.name or "workspace")
                        atomic_write(target, envelope(state))
                    atomic_write(meta, canonical({"schema_version": 2, "workspace_id": state["workspace_id"],
                                                  "name": state["name"], "mode": "frontier"}) + b"\n")
        config = load_json(meta, 4096)
        if not isinstance(config, dict) or config.get("schema_version") not in (1, 2):
            raise Corruption(f"Unsupported workspace metadata: {meta}")
        identifier(config.get("workspace_id"), "workspace_id")
        text(config.get("name"), "workspace name", 192)
        if config["schema_version"] == 1 and not allow_legacy:
            raise MigrationRequired("v0.1 event ledger detected. Stop all old agents, then use `worklog migrate --plan`.")
        if (store / "migration.json").exists() and not allow_legacy:
            raise MigrationRequired("An offline migration needs completion; rerun the same migration apply command.")
        ignore = store / ".gitignore"
        if create and store.name == ".worklog" and (ignore.is_symlink() or not ignore.exists()):
            # A working-tree store must never be committed: clones and worktrees would fork it.
            # Git ignores a symlinked .gitignore, so atomic_write refuses one with a clear error.
            with file_lock(store / "worklog.lock"):
                if ignore.is_symlink() or not ignore.exists():
                    atomic_write(ignore, b"*\n")
        return cls(root, store, config["workspace_id"], config["name"], git=bool(top), checkout=checkout)
