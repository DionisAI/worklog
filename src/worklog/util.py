from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from .errors import Busy, Corruption, Invalid


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def revision() -> str:
    # Fixed-width tokens prevent ABA on delete/recreate and do not grow with time.
    return uuid.uuid4().hex


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Caller holds a stable lock. A single fixed staging file bounds crash residue.

    No old snapshots, timestamped backups, or randomly named orphan temp files.
    Publication uses same-directory replace followed by fsync of the directory.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise Invalid(f"Refusing to replace a symlink: {path}")
    tmp = path.with_name("." + path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 10.0) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:
        raise Invalid("Worklog requires POSIX flock (macOS/Linux)") from exc
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("Worklog is busy; retry after the current write finishes") from None
                time.sleep(0.005)
        yield
    finally:
        os.close(fd)


def load_json(path: Path, max_bytes: int) -> Any:
    if path.is_symlink():
        raise Corruption(f"Unexpected symlink: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise Corruption(f"{path.name} exceeds its {max_bytes}-byte limit")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise Corruption(f"Invalid JSON: {path.name}") from exc
