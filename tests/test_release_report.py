import hashlib
import json
import subprocess
import zipfile

import pytest

from pyi_doctor import release
from pyi_doctor.portable import Artifact
from pyi_doctor.report import Report, Step, submission_notes


def _onedir(tmp_path, name="App"):
    folder = tmp_path / "dist" / "portable" / name
    (folder / "_internal").mkdir(parents=True)
    (folder / f"{name}.exe").write_bytes(b"exe")
    (folder / "_internal" / "a.dll").write_bytes(b"dll")
    return folder


def test_zip_folder_layout(tmp_path):
    folder = _onedir(tmp_path)
    z = release.zip_folder(folder, tmp_path / "out" / "a.zip")
    names = zipfile.ZipFile(z).namelist()
    assert "App/App.exe" in names and "App/_internal/a.dll" in names


def test_collect_and_checksums(make_project, tmp_path):
    p = make_project(name="App", version="2.0.0")
    folder = _onedir(tmp_path)
    one = tmp_path / "one.exe"
    one.write_bytes(b"single")
    inst = tmp_path / "App-2.0.0-setup.exe"
    inst.write_bytes(b"setup")
    notes = tmp_path / "THIRD_PARTY_LICENSES.txt"
    notes.write_text("n")
    files = release.collect(
        p,
        [Artifact("onedir", folder / "App.exe", folder), Artifact("onefile", one), Artifact("installer", inst)],
        [notes, tmp_path / "missing.txt"],
    )
    names = [f.name for f in files]
    tag = release.platform_tag()
    assert f"App-2.0.0-portable-{tag}.zip" in names
    assert f"App-2.0.0-{tag}.exe" in names
    assert "App-2.0.0-setup.exe" in names and "THIRD_PARTY_LICENSES.txt" in names
    sums = (p.release_path / "SHA256SUMS.txt").read_text().splitlines()
    assert len(sums) == 4
    expect = hashlib.sha256(b"setup").hexdigest() + "  App-2.0.0-setup.exe"
    assert expect in sums
    entries = release.file_entries(files)
    assert all(len(e["sha256"]) == 64 for e in entries)


def test_upload_github_requires_gh(monkeypatch, tmp_path):
    from pyi_doctor import ToolNotFoundError

    monkeypatch.setattr(release.shutil, "which", lambda n: None)
    with pytest.raises(ToolNotFoundError):
        release.upload_github("v1", [])


def test_upload_github_creates_draft(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, capture_output, text=False):
        calls.append(cmd)
        code = 1 if cmd[2] == "view" else 0
        return subprocess.CompletedProcess(cmd, code, "", "")

    monkeypatch.setattr(release.shutil, "which", lambda n: "/usr/bin/gh")
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    f = tmp_path / "a.zip"
    f.write_bytes(b"z")
    msg = release.upload_github("v1.0.0", [f], repo="me/repo")
    assert msg.startswith("created draft")
    assert calls[1][:4] == ["/usr/bin/gh", "release", "create", "v1.0.0"] and "--draft" in calls[1]
    assert calls[2][2] == "upload" and str(f) in calls[2] and "--clobber" in calls[2]
    assert all("me/repo" in c for c in calls)


def test_report_markdown_and_json():
    r = Report(project={"name": "App", "version": "1.0"})
    r.steps.append(Step("portable", "ok", "done", 1.2))
    r.steps.append(Step("smoke", "failed", "x | y", 0.5, {"results": [
        {"target": "onedir", "status": "failed", "message": "exit 1", "suggestions": ["add 'foo' to hidden_imports"]}]}))
    r.steps.append(Step("diagnose", "ok", "", 0.1, {"results": [{
        "path": "/x/App.exe", "score": 30, "level": "MEDIUM", "sha256": "ab" * 32,
        "defender": {"status": "clean", "detail": ""},
        "factors": [{"weight": 15, "label": "no signature", "advice": "sign it"}], "notes": ["n1"]}]}))
    assert not r.ok
    md = r.to_markdown()
    assert "| smoke | FAIL |" in md and "x \\| y" in md
    assert "App.exe: 30/100 (MEDIUM)" in md and "sign it" in md and "add 'foo'" in md
    data = json.loads(r.to_json())
    assert data["ok"] is False and data["project"]["name"] == "App"
    assert r.step("portable").status == "ok" and r.step("nope") is None


def test_submission_notes():
    text = submission_notes(
        {"name": "App", "version": "1.0", "publisher": "Me", "website": "https://src", "description": "Tool"},
        [{"name": "App.exe", "size": 1234567, "sha256": "f" * 64}],
        {"pyinstaller": "6.x", "python": "3.12", "platform": "Windows"},
    )
    assert "App 1.0" in text and "1,234,567 bytes" in text and "f" * 64 in text
    assert "https://www.microsoft.com/en-us/wdsi/filesubmission" in text
    assert "https://src" in text
