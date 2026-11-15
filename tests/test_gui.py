import pytest

from pyi_doctor import cli, gui
from pyi_doctor.errors import PyiDoctorError


def test_project_from_form_without_config(tmp_path):
    entry = tmp_path / "app" / "main.py"
    entry.parent.mkdir()
    entry.write_text("print('hi')\n")
    p = gui.project_from_form({"entry": str(entry), "name": "MyApp", "version": "2.0.0", "publisher": " Me ",
                               "icon": "", "console": True})
    assert p.root == entry.parent.resolve()
    assert p.entry_path == entry.resolve()
    assert (p.name, p.version, p.publisher, p.icon, p.console) == ("MyApp", "2.0.0", "Me", None, True)
    assert p.output_path == entry.parent.resolve() / "dist"
    p.check_files()


def test_project_from_form_requires_entry_and_name():
    with pytest.raises(PyiDoctorError):
        gui.project_from_form({"entry": "", "name": "X"})
    with pytest.raises(PyiDoctorError):
        gui.project_from_form({"entry": "main.py", "name": "  "})


def test_project_from_form_with_config_overrides(tmp_path):
    (tmp_path / "main.py").write_text("")
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text('[project]\nname = "fromfile"\nversion = "1.2.3"\n\n[tool.pyi-doctor]\nentry = "main.py"\n'
                   'publisher = "Pub"\n')
    p = gui.project_from_form({"config": str(cfg), "name": "Override", "version": "", "publisher": ""})
    assert p.name == "Override"
    assert p.version == "1.2.3"
    assert p.publisher == "Pub"
    assert p.root == tmp_path.resolve()

    form = gui.form_from_project(p, str(cfg))
    assert form["entry"] == str((tmp_path / "main.py").resolve())
    assert form["name"] == "Override" and form["config"] == str(cfg)


def test_build_kwargs(tmp_path):
    (tmp_path / "main.py").write_text("")
    p = gui.project_from_form({"entry": str(tmp_path / "main.py"), "name": "A"})
    kw = gui.build_kwargs({"licenses": False, "onefile": True}, p)
    assert kw["onefile"] is True
    assert kw["licenses"] is False and kw["sbom_file"] is False
    assert kw["target"] == "native"
    assert "requirements" not in kw
    assert kw["defender"] is gui.IS_WINDOWS

    req = tmp_path / "requirements.txt"
    req.write_text("")
    kw = gui.build_kwargs({"target": "win", "requirements": str(req)}, p)
    assert kw["target"] == "win"
    assert kw["requirements"][-1] == f"-r {req.resolve()}"
    assert gui.build_kwargs({"target": "bogus"}, p)["target"] == "native"


def test_cli_has_gui_command():
    args = cli.make_parser().parse_args(["gui"])
    assert args.func is cli.cmd_gui
