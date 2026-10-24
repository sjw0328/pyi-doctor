"""Real PyInstaller builds. Slow (tens of seconds); run with ``pytest -m integration``."""

import sys
import textwrap

import pytest

from pyi_doctor import Project, build_all, portable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not portable.pyinstaller_available(), reason="PyInstaller not installed"),
]


def _project(tmp_path, body, **kw):
    (tmp_path / "main.py").write_text(textwrap.dedent(body), encoding="utf-8")
    kw.setdefault("name", "ItApp")
    kw.setdefault("version", "1.0.0")
    kw.setdefault("console", True)
    return Project(entry="main.py", root=tmp_path, **kw)


def test_real_build_passes(tmp_path):
    p = _project(tmp_path, "import json\nprint(json.dumps({'ok': True}))\n", publisher="IT")
    report = build_all(p, defender=False)
    st = {s.name: s.status for s in report.steps}
    assert st["portable"] == "ok" and st["smoke"] == "ok", report.to_markdown()
    assert (p.release_path / "SHA256SUMS.txt").is_file()
    if sys.platform == "win32":
        diag = report.step("diagnose").data["results"][0]
        assert diag["pe"]["has_version_info"], "version resource should be embedded on Windows"
        assert not diag["pe"]["upx_packed"]
        if st["installer"] == "ok":
            assert any(a["kind"] == "installer" for a in report.artifacts)


def test_real_build_detects_missing_data_file(tmp_path):
    body = """
    import os, sys
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    open(os.path.join(base, 'data', 'settings.json')).read()
    """
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text("{}")
    p = _project(tmp_path, body)  # add_data intentionally missing
    report = build_all(p, defender=False, installer=False, licenses=False, sbom_file=False)
    smoke = report.step("smoke")
    assert smoke.status == "failed"
    assert smoke.data["results"][0]["missing_files"]
