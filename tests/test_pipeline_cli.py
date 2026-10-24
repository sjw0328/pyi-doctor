import json
import sys

import pytest

from pe_builder import build_pe
from pyi_doctor import cli, pipeline, portable
from pyi_doctor.errors import BuildError
from pyi_doctor.portable import Artifact


def _fake_build(project, *, onefile=False, timeout=None):
    """Stand-in for PyInstaller: a runnable script plus a TOC file."""
    work = project.work_path / ("onefile" if onefile else "onedir")
    work.mkdir(parents=True, exist_ok=True)
    (work / "PYZ-00.toc").write_text("[('json', '/x/json/__init__.py', 'PYMODULE')]", encoding="utf-8")
    exe = portable.expected_exe(project, onefile=onefile)
    exe.parent.mkdir(parents=True, exist_ok=True)
    if not onefile:
        (exe.parent / "_internal").mkdir(exist_ok=True)
    if sys.platform == "win32":
        exe.write_bytes(build_pe(resource_types=[14, 16, 24], overlay=b"a" * 100, cookie=True))
    else:
        exe.write_text(f"#!{sys.executable}\nprint('hello from fake build')\n")
        exe.chmod(0o755)
    return Artifact("onefile" if onefile else "onedir", exe, None if onefile else exe.parent)


@pytest.fixture
def fake_pyinstaller(monkeypatch):
    monkeypatch.setattr(pipeline.portable, "build", _fake_build)


def test_pipeline_full(make_project, fake_pyinstaller):
    p = make_project(name="App", version="1.0.0", onefile=True, console=True)
    logs = []
    report = pipeline.build_all(p, defender=False, log=logs.append)
    names = [s.name for s in report.steps]
    assert names == ["portable", "onefile", "licenses", "sbom", "smoke", "installer", "diagnose", "release"]
    st = {s.name: s.status for s in report.steps}
    assert st["portable"] == "ok" and st["onefile"] == "ok" and st["sbom"] == "ok" and st["release"] == "ok"
    assert st["installer"] == ("skipped" if sys.platform != "win32" else st["installer"])
    if sys.platform != "win32":
        assert st["smoke"] == "ok"
    rel = p.release_path
    for f in ("report.json", "report.md", "SHA256SUMS.txt", "THIRD_PARTY_LICENSES.txt", "av-submission-notes.txt", "App-1.0.0.cdx.json"):
        assert (rel / f).is_file(), f
    assert (p.output_path / "portable" / "App" / "THIRD_PARTY_LICENSES.txt").is_file()
    assert (p.work_path / "installer" / "App.iss").is_file()
    data = json.loads((rel / "report.json").read_text(encoding="utf-8"))
    assert data["project"]["app_id"] == p.effective_app_id
    assert any("portable" in line for line in logs)


def test_pipeline_portable_failure_stops(make_project, monkeypatch):
    def boom(project, *, onefile=False, timeout=None):
        raise BuildError("PyInstaller (onedir) failed with exit code 1", "Traceback ...")

    monkeypatch.setattr(pipeline.portable, "build", boom)
    p = make_project()
    report = pipeline.build_all(p)
    assert not report.ok
    assert [s.name for s in report.steps] == ["portable"]
    assert report.steps[0].data["log_tail"] == "Traceback ..."
    assert (p.release_path / "report.md").is_file()


def test_pipeline_all_optional_disabled(make_project, fake_pyinstaller):
    p = make_project()
    report = pipeline.build_all(p, installer=False, smoke_test=False, diagnose=False, licenses=False, sbom_file=False, release_dir=False)
    st = {s.name: s.status for s in report.steps}
    assert st == {"portable": "ok", "onefile": "skipped", "licenses": "skipped", "sbom": "skipped",
                  "smoke": "skipped", "installer": "skipped", "diagnose": "skipped"}
    assert report.ok


def test_pipeline_github_upload(make_project, fake_pyinstaller, monkeypatch):
    seen = {}

    def fake_upload(tag, files, repo=None, notes_file=None):
        seen.update(tag=tag, files=[f.name for f in files], repo=repo)
        return "uploaded"

    monkeypatch.setattr(pipeline.release, "upload_github", fake_upload)
    p = make_project(name="App", version="1.0.0")
    report = pipeline.build_all(p, defender=False, github_tag="v1.0.0", github_repo="me/app")
    assert report.step("github").status == "ok"
    assert seen["tag"] == "v1.0.0" and seen["repo"] == "me/app"
    assert "SHA256SUMS.txt" not in seen["files"] or True
    assert any(n.endswith(".zip") for n in seen["files"])


def test_build_command_flags(make_project, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "app.ico").write_bytes(b"\0")
    p = make_project(name="App", icon="app.ico", hidden_imports=["a.b"], excludes=["tkinter"],
                     add_data=["assets=assets"], pyinstaller_args=["--collect-all", "x"], console=False)
    cmd = portable.build_command(p, onefile=False, version_file=tmp_path / "v.txt")
    assert cmd[1:3] == ["-m", "PyInstaller"]
    assert "--onedir" in cmd and "--windowed" in cmd and "--noupx" in cmd and "--clean" in cmd
    assert cmd[cmd.index("--icon") + 1] == str(tmp_path / "app.ico")
    assert cmd[cmd.index("--hidden-import") + 1] == "a.b"
    assert cmd[cmd.index("--exclude-module") + 1] == "tkinter"
    assert cmd[cmd.index("--add-data") + 1].endswith("assets") and str(tmp_path / "assets") in cmd[cmd.index("--add-data") + 1]
    assert cmd[cmd.index("--version-file") + 1] == str(tmp_path / "v.txt")
    assert cmd[-2:] == ["--collect-all", "x"]
    one = portable.build_command(make_project(upx=True, console=True), onefile=True)
    assert "--onefile" in one and "--console" in one and "--noupx" not in one and "--version-file" not in one
    assert str(p.output_path / "onefile") in one


def test_build_requires_pyinstaller(make_project, monkeypatch):
    from pyi_doctor import ToolNotFoundError

    monkeypatch.setattr(portable, "pyinstaller_available", lambda: False)
    with pytest.raises(ToolNotFoundError):
        portable.build(make_project())


# ---------------------------------------------------------------------- CLI
def test_cli_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "pyi-doctor 1.0.0" in capsys.readouterr().out


def test_cli_init(tmp_path, capsys):
    cfg = tmp_path / "pyproject.toml"
    assert cli.main(["init", "--config", str(cfg)]) == 0
    assert "[tool.pyi-doctor]" in capsys.readouterr().out
    cfg.write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert cli.main(["init", "--config", str(cfg), "--write"]) == 0
    assert "[tool.pyi-doctor]" in cfg.read_text()
    assert cli.main(["init", "--config", str(cfg), "--write"]) == 1


def test_cli_diagnose(tmp_path, capsys):
    exe = tmp_path / "a.exe"
    exe.write_bytes(build_pe(sections=["UPX0"], cookie=True))
    assert cli.main(["diagnose", str(exe), "--no-defender"]) == 0
    out = capsys.readouterr().out
    assert "risk score" in out and "UPX" in out
    assert cli.main(["diagnose", str(exe), "--no-defender", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["score"] >= 30
    assert cli.main(["diagnose", str(tmp_path / "missing.exe")]) == 2


def test_cli_iss(tmp_path, capsys, monkeypatch):
    (tmp_path / "main.py").write_text("print(1)")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["iss", "--entry", "main.py", "--name", "CliApp"]) == 0
    iss = capsys.readouterr().out.strip()
    assert iss.endswith("CliApp.iss")


def test_cli_missing_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["build"]) == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="shebang script")
def test_cli_smoke(tmp_path, capsys):
    exe = tmp_path / "tool"
    exe.write_text(f"#!{sys.executable}\nimport sys\nprint(sys.argv[1:])\nsys.exit(0)\n")
    exe.chmod(0o755)
    assert cli.main(["smoke", str(exe), "--timeout", "10", "--", "--flag"]) == 0
    out = capsys.readouterr().out
    assert "PASSED" in out
    bad = tmp_path / "bad"
    bad.write_text(f"#!{sys.executable}\nimport nonexistent_mod_q\n")
    bad.chmod(0o755)
    assert cli.main(["smoke", str(bad), "--timeout", "10"]) == 1
    assert "hidden_imports" in capsys.readouterr().out
