import uuid

import pytest

from pyi_doctor import ConfigError, Project
from pyi_doctor.config import SAMPLE_CONFIG


def test_defaults(make_project):
    p = make_project()
    assert p.version == "0.1.0"
    assert p.languages == ["english", "korean"]
    assert p.privileges == "lowest"
    assert p.console is False and p.onefile is False and p.upx is False
    assert p.entry_path.name == "main.py"


@pytest.mark.parametrize("version,expected", [
    ("1", (1, 0, 0, 0)),
    ("1.2", (1, 2, 0, 0)),
    ("1.2.3", (1, 2, 3, 0)),
    ("1.2.3.4", (1, 2, 3, 4)),
    ("1.2.3rc1", (1, 2, 3, 0)),
    ("2.0.0-beta.1", (2, 0, 0, 0)),
    ("1.99999.0", (1, 65535, 0, 0)),
])
def test_windows_version(make_project, version, expected):
    assert make_project(version=version).windows_version == expected


@pytest.mark.parametrize("version", ["", "v1.0", "abc", "1..2", ".1"])
def test_bad_version(make_project, version):
    with pytest.raises(ConfigError):
        make_project(version=version)


@pytest.mark.parametrize("name", ["", "a/b", "a\\b", "x:y", "q?", " lead", "trail ", 'qu"ote', "a|b", "a*"])
def test_bad_name(make_project, name):
    with pytest.raises(ConfigError):
        make_project(name=name)


@pytest.mark.parametrize("name", ["App", "My App", "앱이름", "App-2.0", "OMEGA X"])
def test_good_name(make_project, name):
    assert make_project(name=name).name == name


def test_bad_privileges_and_language(make_project):
    with pytest.raises(ConfigError):
        make_project(privileges="root")
    with pytest.raises(ConfigError):
        make_project(languages=["klingon"])
    with pytest.raises(ConfigError):
        make_project(smoke_timeout=0)


def test_languages_normalized(make_project):
    assert make_project(languages=[" English ", "KOREAN", ""]).languages == ["english", "korean"]


def test_app_id_derived_is_stable_and_valid(make_project):
    a = make_project(publisher="Pub").effective_app_id
    b = make_project(publisher="pub ").effective_app_id
    c = make_project(publisher="Other").effective_app_id
    assert a == b != c
    assert uuid.UUID(a) and a == a.upper()


def test_app_id_explicit(make_project):
    gid = str(uuid.uuid4())
    assert make_project(app_id="{" + gid + "}").effective_app_id == gid.upper()
    with pytest.raises(ConfigError):
        make_project(app_id="not-a-guid")


@pytest.mark.parametrize("item,expected", [
    ("assets=assets", ("assets", "assets")),
    ("assets;data", ("assets", "data")),
    ("assets:data", ("assets", "data")),
    ("C:\\x\\y=dest", ("C:\\x\\y", "dest")),
    (["a.txt", "."], ("a.txt", ".")),
    ("file.txt=", ("file.txt", ".")),
])
def test_add_data_normalized(make_project, item, expected):
    assert make_project(add_data=[item]).add_data == [expected]


@pytest.mark.parametrize("item", ["nodest", "C:\\nodest", ["a"], ["a", "b", "c"], "=dest"])
def test_add_data_invalid(make_project, item):
    with pytest.raises(ConfigError):
        make_project(add_data=[item])


def test_check_files(make_project, tmp_path):
    p = make_project(icon="missing.ico")
    with pytest.raises(ConfigError, match="icon"):
        p.check_files()
    p = make_project(entry="nope.py")
    with pytest.raises(ConfigError, match="entry"):
        p.check_files()
    p = make_project(add_data=["nodir=x"])
    with pytest.raises(ConfigError, match="add_data"):
        p.check_files()
    (tmp_path / "LICENSE").write_text("MIT")
    make_project(license_file="LICENSE").check_files()


def test_from_dict_rejects_unknown(tmp_path):
    with pytest.raises(ConfigError, match="unknown"):
        Project.from_dict({"entry": "m.py", "name": "A", "colour": "red"}, root=tmp_path)
    with pytest.raises(ConfigError, match="required"):
        Project.from_dict({"name": "A"}, root=tmp_path)


def test_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fromproject"\nversion = "2.1.0"\ndescription = "d"\n'
        'authors = [{name = "Author Name"}]\n\n'
        '[tool.pyi-doctor]\nentry = "app/main.py"\nhidden-imports = ["x"]\nconsole = true\n',
        encoding="utf-8",
    )
    p = Project.from_pyproject(tmp_path / "pyproject.toml")
    assert (p.name, p.version, p.publisher, p.description) == ("fromproject", "2.1.0", "Author Name", "d")
    assert p.hidden_imports == ["x"] and p.console is True
    assert p.root == tmp_path.resolve()
    assert p.entry_path == (tmp_path / "app" / "main.py").resolve()
    p2 = Project.from_pyproject(tmp_path / "pyproject.toml", name="Override", version=None)
    assert p2.name == "Override" and p2.version == "2.1.0"


def test_from_pyproject_errors(tmp_path):
    with pytest.raises(ConfigError):
        Project.from_pyproject(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[tool.pyi-doctor\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Project.from_pyproject(bad)


def test_sample_config_loads(tmp_path):
    (tmp_path / "pyproject.toml").write_text(SAMPLE_CONFIG, encoding="utf-8")
    p = Project.from_pyproject(tmp_path / "pyproject.toml")
    assert p.name == "MyApp" and p.version == "1.0.0"
