import os
import uuid
from pathlib import Path

import pytest

from pyi_doctor import installer
from pyi_doctor.versioninfo import render_version_file, write_version_file


class _Rec:
    def __init__(self, *args, **kw):
        self.args, self.kw = args, kw


def _eval_version(text):
    names = {n: (lambda *a, _n=n, **k: (_n, a, k)) for n in (
        "VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo", "VarStruct")}
    return eval(text, {"__builtins__": {}}, names)  # same evaluation model PyInstaller uses


def _strings(tree):
    out = {}

    def walk(node):
        if isinstance(node, tuple) and node and node[0] == "StringStruct":
            out[node[1][0]] = node[1][1]
        elif isinstance(node, (tuple, list)):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for x in node.values():
                walk(x)

    walk(tree)
    return out


def test_version_file_evaluates(make_project):
    p = make_project(name="Demo App", version="1.2.3rc1", publisher="O'Brien \"Q\"", description="설명", copyright="(c) 2026")
    tree = _eval_version(render_version_file(p))
    assert tree[0] == "VSVersionInfo"
    ffi = tree[2]["ffi"]
    assert ffi[2]["filevers"] == (1, 2, 3, 0)
    s = _strings(tree)
    assert s["CompanyName"] == "O'Brien \"Q\""
    assert s["FileDescription"] == "설명"
    assert s["OriginalFilename"] == "Demo App.exe"
    assert s["ProductVersion"] == "1.2.3rc1"


def test_version_file_written(make_project, tmp_path):
    out = write_version_file(make_project(), tmp_path / "sub" / "v.txt")
    assert out.read_text(encoding="utf-8").startswith("# UTF-8")


def test_escaping_helpers():
    assert installer.iss_value("a{b}") == "a{{b}"
    assert installer.iss_quoted('say "hi" {x}') == '"say ""hi"" {{x}"'
    assert installer.cm_arg("A, B|C}") == "A%2c B%7cC%7d"
    assert installer.cm_arg("100%") == "100%25"


def _render(p, tmp_path, **kw):
    return installer.render_iss(p, source_dir=tmp_path / "dist" / "App", output_dir=tmp_path / "out", **kw)


def test_render_iss_basic(make_project, tmp_path):
    p = make_project(name="App", version="1.0.0", publisher="Pub", website="https://x.example")
    text = _render(p, tmp_path)
    lines = text.split("\r\n")
    assert f"AppId={{{{{p.effective_app_id}}}" in lines
    assert "AppPublisherURL=https://x.example" in lines
    assert "PrivilegesRequired=lowest" in lines
    assert "OutputBaseFilename=App-1.0.0-setup" in lines
    assert "VersionInfoVersion=1.0.0.0" in lines
    assert any(l.startswith('Name: "korean"; MessagesFile: "compiler:Languages\\Korean.isl"') for l in lines)
    assert any("recursesubdirs" in l and str(tmp_path / "dist" / "App") in l for l in lines)
    assert "{cm:LaunchProgram,App}" in text
    assert "LicenseFile" not in text and "SetupIconFile" not in text
    assert "\n" not in text.replace("\r\n", "")  # CRLF only


def test_render_iss_escapes_user_text(make_project, tmp_path):
    p = make_project(name="My {App}, Pro", publisher='A "B"')
    text = _render(p, tmp_path)
    assert "AppName=My {{App}, Pro" in text
    assert "{cm:LaunchProgram,My {{App%7d%2c Pro}" in text
    assert 'AppPublisher=A "B"' in text


def test_render_iss_optional_fields(make_project, tmp_path):
    (tmp_path / "LICENSE").write_text("MIT")
    (tmp_path / "app.ico").write_bytes(b"\0\0\1\0")
    p = make_project(license_file="LICENSE", icon="app.ico", privileges="admin", copyright="(c) Me", languages=["english"])
    extra = tmp_path / "THIRD_PARTY_LICENSES.txt"
    extra.write_text("x")
    text = _render(p, tmp_path, extra_files=[extra])
    assert f"LicenseFile={tmp_path / 'LICENSE'}" in text
    assert f"SetupIconFile={tmp_path / 'app.ico'}" in text
    assert "PrivilegesRequired=admin" in text
    assert "VersionInfoCopyright=(c) Me" in text
    assert "korean" not in text
    assert "THIRD_PARTY_LICENSES.txt" in text


def test_available_languages_filters_by_files(make_project, tmp_path):
    p = make_project(languages=["english", "korean", "japanese"])
    comp = tmp_path / "inno"
    (comp / "Languages").mkdir(parents=True)
    (comp / "Default.isl").write_text("")
    (comp / "Languages" / "Korean.isl").write_text("")
    iscc = comp / "ISCC.exe"
    iscc.write_text("")
    assert installer.available_languages(p, iscc) == ["english", "korean"]
    assert installer.available_languages(p, None) == ["english", "korean", "japanese"]
    empty = tmp_path / "empty" / "ISCC.exe"
    empty.parent.mkdir()
    assert installer.available_languages(p, empty) == ["english"]


def test_write_iss_bom(make_project, tmp_path):
    p = make_project(name="앱")
    iss = installer.write_iss(p, tmp_path / "dist")
    raw = iss.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "AppName=앱" in raw.decode("utf-8-sig")


def test_find_iscc_env(monkeypatch, tmp_path):
    exe = tmp_path / "ISCC.exe"
    exe.write_text("")
    monkeypatch.setenv(installer.ISCC_ENV, str(exe))
    assert installer.find_iscc() == exe
    monkeypatch.setenv(installer.ISCC_ENV, str(tmp_path / "missing.exe"))
    assert installer.find_iscc() is None


def test_find_iscc_program_files(monkeypatch, tmp_path):
    monkeypatch.delenv(installer.ISCC_ENV, raising=False)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    pf = tmp_path / "pf86"
    (pf / "Inno Setup 6").mkdir(parents=True)
    (pf / "Inno Setup 6" / "ISCC.exe").write_text("")
    monkeypatch.setenv("ProgramFiles(x86)", str(pf))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert installer.find_iscc() == pf / "Inno Setup 6" / "ISCC.exe"


def test_compile_without_iscc(make_project, tmp_path, monkeypatch):
    from pyi_doctor import ToolNotFoundError

    monkeypatch.setattr(installer, "find_iscc", lambda: None)
    with pytest.raises(ToolNotFoundError):
        installer.compile_iss(make_project(), tmp_path / "x.iss")
