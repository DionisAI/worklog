import contextlib
import os
from pathlib import Path

import pytest

from worklog.core import Worklog
from worklog.workspace import Workspace


@pytest.fixture
def w(tmp_path):
    return Worklog(Workspace.open(tmp_path, create=True))


@pytest.fixture
def card(w):
    return w.add("replay", "Fix replay", "Located duplicate counting", "Add regression test")["card"]


def signature(path: Path):
    return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in path.rglob("*") if p.is_file()}


needs_file_modes = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")


@contextlib.contextmanager
def read_only(store: Path):
    """Like .git/worklog inside Codex's workspace-write sandbox: nothing in the store can be
    opened for writing and nothing can be created there; everything stays readable."""
    paths = [store, *store.rglob("*")]
    modes = {p: p.stat().st_mode for p in paths}
    for p in paths:
        p.chmod(0o500 if p.is_dir() else 0o400)
    try:
        yield
    finally:
        for p in paths:
            p.chmod(modes[p])
