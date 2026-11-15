import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def make_project(tmp_path):
    """Create a Project rooted in a temp folder with an entry script."""
    from pyi_doctor import Project

    def _make(**kw):
        entry = tmp_path / "main.py"
        if not entry.exists():
            entry.write_text("print('hi')\n", encoding="utf-8")
        kw.setdefault("entry", "main.py")
        kw.setdefault("name", "DemoApp")
        kw.setdefault("root", tmp_path)
        return Project(**kw)

    return _make


@pytest.fixture(autouse=True)
def _full_output(monkeypatch):
    """Tests expect the normal output even when they run inside an AI agent."""
    from pyi_doctor.summary import AGENT_ENV_VARS

    for k in AGENT_ENV_VARS + ("PYI_DOCTOR_BRIEF",):
        monkeypatch.delenv(k, raising=False)
