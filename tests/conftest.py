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
