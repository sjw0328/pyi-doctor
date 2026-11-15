"""Zero-config loading, init, check, build summary and the window helpers (1.6.0)."""

import json
import struct
import sys

import pytest

from pyi_doctor import Project, autodetect, cli, gui, pipeline, preflight
from pyi_doctor.config import _load_toml
from pyi_doctor.errors import ConfigError
from pyi_doctor.report import Report, Step
from pyi_doctor.summary import next_steps, summary_lines

from test_pipeline_cli import _fake_build


def _ico(path, sizes=(16, 32, 256)):
    head = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b"".join(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, 0, 0) for s in sizes)
    path.write_bytes(head + entries)


@pytest.fixture
def gui_app(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"x")
    _ico(tmp_path / "assets" / "app.ico")
    (tmp_path / "LICENSE").write_text("MIT\n")
    (tmp_path / "tools.py").write_text("import customtkinter\n")
    (tmp_path / "notes_app.py").write_text(
        '__version__ = "2.1.0"\nimport tools\n\nif __name__ == "__main__":\n    print(1)\n')
    (tmp_path / "test_notes.py").write_text("")
    return tmp_path


# ------------------------------------------------------------ autodetect
def test_detect_guesses_everything(gui_app):
    det = autodetect.detect(gui_app)
    v = det.values
    assert v["entry"] == "notes_app.py"
    assert v["name"] == "notes_app"
    assert v["version"] == "2.1.0"
    assert v["console"] is False and det.gui_modules == ["customtkinter"]
    assert v["icon"] == "assets/app.ico"
    assert v["license_file"] == "LICENSE"
    assert v["add_data"] == ["assets=assets"]
    assert len(det.notes) == len(v)


def test_detect_prefers_main_and_project_name(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    (tmp_path / "other.py").write_text("if __name__ == '__main__':\n    pass\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "cool/tool"\nversion = "3.0"\n')
    v = autodetect.detect(tmp_path).values
    assert v["entry"] == "main.py" and v["name"] == "cool-tool" and v["console"] is True
    assert "version" not in v  # comes from [project]


def test_detect_skips_keys_in_tool_table(tmp_path):
    (tmp_path / "main.py").write_text("")
    (tmp_path / "pyproject.toml").write_text('[tool.pyi-doctor]\nname = "X"\nconsole = true\n')
    v = autodetect.detect(tmp_path).values
    assert "name" not in v and "console" not in v and v["entry"] == "main.py"


def test_detect_nothing(tmp_path):
    det = autodetect.detect(tmp_path)
    assert "entry" not in det.values and det.values["name"] == autodetect.safe_name(tmp_path.name)


def test_config_text_is_valid_toml(tmp_path):
    text = autodetect.config_text({"entry": "a b.py", "name": 'Q"uote', "console": False, "add_data": ["x=y"],
                                   "smoke_timeout": 5.0, "app_id": autodetect.new_app_id()})
    f = tmp_path / "p.toml"
    f.write_text(text, encoding="utf-8")
    t = _load_toml(f)["tool"]["pyi-doctor"]
    assert t["name"] == 'Q"uote' and t["console"] is False and t["smoke_timeout"] == 5.0


# ------------------------------------------------------------ Project loaders
def test_project_load_folder_without_config(gui_app):
    p = Project.load(gui_app)
    assert p.entry_path == (gui_app / "notes_app.py").resolve()
    assert p.console is False and p.icon == "assets/app.ico" and p.add_data == [("assets", "assets")]
    assert any(n.startswith("entry") for n in p.detected)
    p.check_files()


def test_project_load_script_and_overrides(gui_app):
    p = Project.load(gui_app / "tools.py", name="Tools", console=True)
    assert p.entry_path.name == "tools.py" and p.name == "Tools" and p.console is True


def test_project_load_uses_tool_table(gui_app):
    (gui_app / "pyproject.toml").write_text('[tool.pyi-doctor]\nentry = "tools.py"\nname = "Fixed"\n')
    p = Project.load(gui_app)
    assert (p.name, p.entry_path.name, p.icon, p.detected) == ("Fixed", "tools.py", None, [])


def test_from_pyproject_guesses_missing_entry(gui_app):
    (gui_app / "pyproject.toml").write_text('[tool.pyi-doctor]\npublisher = "Me"\n')
    p = Project.from_pyproject(gui_app / "pyproject.toml")
    assert p.entry_path.name == "notes_app.py" and p.name == "notes_app" and p.publisher == "Me"
    assert {n.split()[0] for n in p.detected} == {"entry", "name"}


def test_load_errors(tmp_path):
    with pytest.raises(ConfigError, match="no entry script found"):
        Project.load(tmp_path)
    with pytest.raises(ConfigError, match="not found"):
        Project.load(tmp_path / "missing.py")


def test_did_you_mean():
    with pytest.raises(ConfigError, match="did you mean 'hidden_imports'"):
        Project.from_dict({"entry": "m.py", "name": "A", "hiden_imports": []})
    with pytest.raises(ConfigError, match="did you mean 'korean'"):
        Project(entry="m.py", name="A", languages=["korea"])


# ------------------------------------------------------------ preflight
def test_ico_sizes(tmp_path):
    _ico(tmp_path / "a.ico", (16, 256))
    assert preflight.ico_sizes(tmp_path / "a.ico") == [16, 256]
    (tmp_path / "b.ico").write_bytes(b"\x89PNG....")
    assert preflight.ico_sizes(tmp_path / "b.ico") == []


def test_project_checks(tmp_path):
    (tmp_path / "main.py").write_text("def broken(:\n")
    _ico(tmp_path / "small.ico", (32,))
    p = Project(entry="main.py", name="A", icon="small.ico", root=tmp_path, upx=True)
    st = {c.name: c.status for c in preflight.project_checks(p)}
    assert st["entry"] == "failed" and st["icon"] == "warning" and st["app_id"] == "warning"
    assert st["publisher"] == "warning" and st["upx"] == "warning"

    (tmp_path / "main.py").write_text("import tkinter\n")
    _ico(tmp_path / "ok.ico")
    p = Project(entry="main.py", name="A", icon="ok.ico", root=tmp_path, publisher="Me",
                app_id="3B1D6F0A-6C2E-4C8B-9E57-2A9D4F1C7E31")
    checks = preflight.project_checks(p)
    assert all(c.status == "ok" for c in checks)
    cross = [c for c in preflight.project_checks(p, target="win") if c.name == "cross"]
    assert cross[0].status == "failed" and "tkinter" in cross[0].message


def test_run_checks_lines():
    checks = preflight.run_checks(None, load_error="boom")
    assert any(c.name == "pyinstaller" for c in checks)
    assert checks[-1].status == "failed"
    lines = preflight.check_lines(checks)
    assert "boom" in "\n".join(lines) and "problem(s)" in lines[-1]


# ------------------------------------------------------------ CLI
@pytest.fixture
def fake_pyinstaller(monkeypatch):
    monkeypatch.setattr(pipeline.portable, "build", _fake_build)


def test_cli_build_without_config(tmp_path, monkeypatch, capsys, fake_pyinstaller):
    (tmp_path / "main.py").write_text("print('hi')\n")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["build", "--no-defender", "--publisher", "Me"])
    out, err = capsys.readouterr()
    assert "Guessed settings" in err and 'entry = "main.py"' in err
    assert "Result:" in out and "Next steps:" in out and "app_id" in out
    if sys.platform != "win32":
        assert code == 0
    data = json.loads((tmp_path / "dist" / "release" / "report.json").read_text())
    assert data["project"]["publisher"] == "Me"


def test_cli_build_path_argument_and_check_stop(tmp_path, capsys, fake_pyinstaller):
    script = tmp_path / "tool.py"
    script.write_text("def x(:\n")
    assert cli.main(["build", str(script), "--no-defender", "-q"]) == 1
    assert "Stopped before building" in capsys.readouterr().err
    assert not (tmp_path / "dist").exists()


def test_cli_check(gui_app, capsys):
    assert cli.main(["check", str(gui_app)]) == 0
    out = capsys.readouterr().out
    assert "guessed" in out and "app_id" in out
    assert cli.main(["check", str(gui_app), "--target", "win", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert any(c["name"] == "cross" and c["status"] == "failed" for c in data)
    assert cli.main(["check", str(gui_app / "nothing_here")]) == 1


def test_cli_init_fills_values(gui_app, capsys):
    cfg = gui_app / "pyproject.toml"
    assert cli.main(["init", "--config", str(cfg), "--write"]) == 0
    t = _load_toml(cfg)["tool"]["pyi-doctor"]
    assert t["entry"] == "notes_app.py" and t["console"] is False and t["icon"] == "assets/app.ico"
    assert Project._parse_app_id(t["app_id"])  # a GUID
    p = Project.load(gui_app)
    assert p.detected == [] and p.app_id == t["app_id"]


def test_cli_init_skips_project_keys(tmp_path, capsys):
    (tmp_path / "main.py").write_text("")
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text('[project]\nname = "pkg"\nversion = "1.2.3"\nauthors = [{name = "Ann"}]\n')
    assert cli.main(["init", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    keys = {line.split(" =")[0] for line in out.splitlines() if " = " in line and not line.startswith("#")}
    assert "entry" in keys and not keys & {"name", "version", "publisher"}


# ------------------------------------------------------------ summary
def test_summary_next_steps():
    r = Report(project={"name": "A", "version": "1"})
    r.steps = [
        Step("portable", "ok", "x"),
        Step("smoke", "failed", "onedir: failed",
             data={"results": [{"suggestions": ["add 'foo' to hidden_imports"]}]}),
        Step("installer", "skipped", "Inno Setup not found; script written to a.iss"),
        Step("diagnose", "ok", "", data={"results": [{"factors": [
            {"key": "unsigned", "label": "no signature", "advice": "sign it"},
            {"key": "stock_bootloader", "label": "b", "advice": "c"}], "defender": {"status": "detected"}}]}),
    ]
    tips = next_steps(r)
    assert tips[0] == "add 'foo' to hidden_imports"
    assert any("Inno Setup 6.3+" in t for t in tips)
    assert any("no signature" in t for t in tips) and not any(t.endswith("-> c") for t in tips)
    assert any("av-submission-notes" in t for t in tips)
    lines = summary_lines(r)
    assert "Result: FAILED" in lines and any(line.strip().startswith("FAIL") for line in lines)


# ------------------------------------------------------------ window helpers
def test_form_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PYI_DOCTOR_GUI_STATE", str(tmp_path / "s" / "gui.json"))
    assert gui.load_form_state() == {}
    gui.save_form_state({"name": "A", "console": True, "bogus": 1})
    assert gui.load_form_state() == {"name": "A", "console": True}
    (tmp_path / "s" / "gui.json").write_text('{"name": 5, "onefile": true}')
    assert gui.load_form_state() == {"onefile": True}
    (tmp_path / "s" / "gui.json").write_text("not json")
    assert gui.load_form_state() == {}


def test_fill_from_entry_and_form_from_path(gui_app):
    v = gui.fill_from_entry(dict(gui.DEFAULTS), str(gui_app / "notes_app.py"))
    assert v["name"] == "notes_app" and v["version"] == "2.1.0" and v["console"] is False
    assert v["icon"].endswith("app.ico")
    kept = gui.fill_from_entry({**gui.DEFAULTS, "name": "Mine"}, str(gui_app / "notes_app.py"))
    assert kept["name"] == "Mine"

    values, notes = gui.form_from_path(str(gui_app))
    assert values["name"] == "notes_app" and values["config"] == "" and notes


def test_project_from_form_picks_up_license_and_data(gui_app):
    p = gui.project_from_form({"entry": str(gui_app / "notes_app.py"), "name": "N", "console": True})
    assert p.license_file == "LICENSE" and p.add_data == [("assets", "assets")] and p.console is True


def test_write_settings(gui_app):
    values = {**gui.DEFAULTS, "entry": str(gui_app / "notes_app.py"), "name": "Notes", "publisher": "Me",
              "icon": str(gui_app / "assets" / "app.ico")}
    p = gui.project_from_form(values)
    path = gui.write_settings(values, p)
    t = _load_toml(path)["tool"]["pyi-doctor"]
    assert t["entry"] == "notes_app.py" and t["icon"] == "assets/app.ico" and t["name"] == "Notes"
    assert t["app_id"] and t["license_file"] == "LICENSE"
    with pytest.raises(Exception, match="already has"):
        gui.write_settings(values, p)
    loaded = Project.load(gui_app)
    assert loaded.name == "Notes" and loaded.publisher == "Me"


# ------------------------------------------------------------ brief mode (AI agents)
def test_brief_default(monkeypatch):
    from pyi_doctor.summary import brief_default

    assert brief_default() is False
    monkeypatch.setenv("CLAUDECODE", "1")
    assert brief_default() is True
    monkeypatch.setenv("PYI_DOCTOR_BRIEF", "0")
    assert brief_default() is False


def test_cli_brief_build_is_short(tmp_path, monkeypatch, capsys, fake_pyinstaller):
    (tmp_path / "main.py").write_text("print('hi')\n")
    monkeypatch.chdir(tmp_path)
    cli.main(["build", "--no-defender"])
    full = capsys.readouterr()
    monkeypatch.setenv("AI_AGENT", "test")
    code = cli.main(["build", "--no-defender"])
    out, err = capsys.readouterr()
    assert err.startswith("guessed: entry=main.py") and err.count("\n") == 1
    assert out.splitlines()[0].startswith(("OK:", "FAILED:"))
    assert "release:" in out and "[OK" not in out
    assert len(out) + len(err) < (len(full.out) + len(full.err)) / 2
    if sys.platform != "win32":
        assert code == 0
    assert cli.main(["build", "--no-defender", "--no-brief", "-q"]) in (0, 1)


def test_cli_brief_check_and_diagnose(gui_app, tmp_path, capsys):
    from pe_builder import build_pe

    assert cli.main(["check", str(gui_app), "--brief"]) == 0
    out = capsys.readouterr().out
    assert "OK " not in out and "WARN app_id" in out and "guessed" not in out
    assert cli.main(["check", str(gui_app), "--brief", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data and all(c["status"] in ("warning", "failed") for c in data)

    exe = tmp_path / "a.exe"
    exe.write_bytes(build_pe(sections=["UPX0"], cookie=True))
    assert cli.main(["diagnose", str(exe), "--no-defender", "--brief"]) == 0
    line = capsys.readouterr().out.strip()
    assert "\n" not in line and "upx" in line
    assert cli.main(["diagnose", str(exe), "--no-defender", "--brief", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)[0]
    assert set(d) == {"path", "score", "level", "factors", "defender"} and "upx" in d["factors"]


def test_cli_quickref(capsys):
    assert cli.main(["quickref"]) == 0
    out = capsys.readouterr().out
    assert "pyi-doctor check" in out and len(out.splitlines()) < 15
