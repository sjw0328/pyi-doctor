"""Tests for the experimental Linux/macOS -> Windows cross build.

Unit tests run offline on every OS. Tests marked ``cross`` download the
Windows embeddable Python and wheels (network) and build a real exe.
"""

from __future__ import annotations

import marshal
import shutil
import struct
import sys
from pathlib import Path

import pefile
import pytest

from pe_builder import FILE_ALIGN, SECT_ALIGN, build_pe
from pyi_doctor import pe as mype
from pyi_doctor.cli import make_parser
from pyi_doctor.cross import requirement_args, rsrc
from pyi_doctor.cross.hooks import HookRunner, Site, UnsupportedHook
from pyi_doctor.errors import CrossBuildUnsupported


# --------------------------------------------------------------- fixtures
def _image(sections=(".text", ".reloc")) -> bytes:
    """Synthetic PE32+ with alignment fields set and .reloc as the base-relocation directory."""
    data = bytearray(build_pe(sections=sections))
    opt = 0x44 + 20
    struct.pack_into("<II", data, opt + 32, SECT_ALIGN, FILE_ALIGN)
    struct.pack_into("<I", data, opt + 56, SECT_ALIGN * (len(sections) + 1))
    struct.pack_into("<I", data, opt + 60, 0x400)
    if sections[-1] == ".reloc":
        struct.pack_into("<II", data, opt + 112 + 5 * 8, SECT_ALIGN * len(sections), 12)
        reloc_raw = 0x400 + (len(sections) - 1) * FILE_ALIGN
        struct.pack_into("<IIHH", data, reloc_raw, 0x1000, 12, 0xA000, 0)  # one relocation block
    return bytes(data)


def _ico(path: Path, sizes=(16, 32)) -> Path:
    images = [bytes([n]) * (40 + n) for n in sizes]
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for n, img in zip(sizes, images):
        out += struct.pack("<BBBBHHII", n, n, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    for img in images:
        out += img
    path.write_bytes(bytes(out))
    return path


# ------------------------------------------------------------- resources
def test_add_resources_layout_and_parse(tmp_path):
    res = rsrc.icon_resources(_ico(tmp_path / "a.ico"))
    res[(rsrc.RT_VERSION, 1)] = rsrc.version_resource((1, 2, 3, 4), [("CompanyName", "OMEGA 테스트"), ("ProductName", "X")])
    res[(rsrc.RT_MANIFEST, 1)] = rsrc.DEFAULT_MANIFEST
    out = rsrc.add_resources(_image(), res, timestamp=1234567)
    exe = tmp_path / "x.exe"
    exe.write_bytes(out + b"ARCHIVE")
    rsrc.update_checksum(exe)

    p = pefile.PE(str(exe))
    names = [s.Name.rstrip(b"\0") for s in p.sections]
    assert names == [b".text", b".rsrc", b".reloc"]  # .rsrc inserted before .reloc
    reloc = p.sections[-1]
    assert p.OPTIONAL_HEADER.DATA_DIRECTORY[5].VirtualAddress == reloc.VirtualAddress
    assert p.OPTIONAL_HEADER.CheckSum == p.generate_checksum()
    assert p.FILE_HEADER.TimeDateStamp == 1234567
    fixed = p.VS_FIXEDFILEINFO[0]
    assert (fixed.FileVersionMS, fixed.FileVersionLS) == ((1 << 16) | 2, (3 << 16) | 4)
    strings = {k.decode(): v.decode() for fi in p.FileInfo[0] if hasattr(fi, "StringTable")
               for st in fi.StringTable for k, v in st.entries.items()}
    assert strings["CompanyName"] == "OMEGA 테스트"
    types = {e.id: [x.id for x in e.directory.entries] for e in p.DIRECTORY_ENTRY_RESOURCE.entries}
    assert types == {3: [1, 2], 14: [1], 16: [1], 24: [1]}

    info = mype.parse(exe)
    assert info.has_icon and info.has_version_info and info.has_manifest


def test_add_resources_without_reloc_appends(tmp_path):
    out = rsrc.add_resources(_image((".text", ".data")), {(rsrc.RT_MANIFEST, 1): rsrc.DEFAULT_MANIFEST})
    p = pefile.PE(data=out)
    assert [s.Name.rstrip(b"\0") for s in p.sections][-1] == b".rsrc"


@pytest.mark.parametrize("kind", ["pe32", "signed", "overlay", "has_rsrc"])
def test_add_resources_rejects(kind):
    if kind == "pe32":
        data = build_pe(pe32_plus=False)
    elif kind == "signed":
        data = build_pe(signature=b"\0" * 16)
    elif kind == "overlay":
        data = _image() + b"x"
    else:
        data = build_pe(resource_types=[16])
    with pytest.raises(rsrc.RsrcError):
        rsrc.add_resources(data, {(rsrc.RT_MANIFEST, 1): b"<x/>"})


def test_icon_rejects_png(tmp_path):
    f = tmp_path / "a.ico"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    with pytest.raises(rsrc.RsrcError):
        rsrc.icon_resources(f)


def test_checksum_odd_length(tmp_path):
    exe = tmp_path / "o.exe"
    exe.write_bytes(_image() + b"abc")
    assert rsrc.pe_checksum(exe) == pefile.PE(str(exe)).generate_checksum()


# ----------------------------------------------------------------- hooks
@pytest.fixture
def site(tmp_path):
    s = tmp_path / "site"
    pkg = s / "demo"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    (pkg / f"fast.cp{sys.version_info.major}{sys.version_info.minor}-win_amd64.pyd").write_bytes(b"MZ")
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "data.json").write_text("{}")
    (pkg / "native.dll").write_bytes(b"MZ")
    dist = s / "demo-1.2.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Name: demo\nVersion: 1.2.0\nRequires-Dist: other\n")
    (dist / "RECORD").write_text("demo/__init__.py,,\n_9f2c__mypyc.cp313-win_amd64.pyd,,\n")
    return s


def _runner(site_dir: Path, tmp_path: Path, hook_src: str):
    hooks = tmp_path / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "hook-demo.py").write_text(hook_src)
    return HookRunner(Site([site_dir], site_dir), [hooks], []), hooks / "hook-demo.py"


def test_hook_file_based_helpers(site, tmp_path):
    src = (
        "import sys, os\n"
        "from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata, "
        "collect_dynamic_libs, is_module_satisfies, get_module_attribute\n"
        "from PyInstaller.compat import is_win\n"
        "from _pyinstaller_hooks_contrib.utils.mypy import find_mypyc_module_for_dist\n"
        "assert is_win and sys.platform == 'win32' and os.name == 'nt'\n"
        "hiddenimports = collect_submodules('demo') + find_mypyc_module_for_dist('demo')\n"
        "datas = collect_data_files('demo') + copy_metadata('demo')\n"
        "binaries = collect_dynamic_libs('demo')\n"
        "if is_module_satisfies('demo < 1.0'):\n"
        "    get_module_attribute('demo', 'x')  # imported but not called: allowed\n"
        "excludedimports = ['tkinter']\n"
    )
    runner, hook = _runner(site, tmp_path, src)
    res = runner.run(hook)
    assert set(res.hiddenimports) == {"demo", "demo.a", "demo.fast", "demo.sub", "_9f2c__mypyc"}
    # like PyInstaller, collect_data_files skips only Python/extension suffixes (DLLs are kept)
    assert {(Path(a).name, b) for a, b in res.datas} == {("data.json", "demo"), ("native.dll", "demo"),
                                                        ("METADATA", "demo-1.2.0.dist-info"),
                                                        ("RECORD", "demo-1.2.0.dist-info")}
    assert [(Path(a).name, b) for a, b in res.binaries] == [("native.dll", "demo")]
    assert res.excludedimports == ["tkinter"]


@pytest.mark.parametrize("src, needle", [
    ("import demo\n", "imports 'demo'"),
    ("def hook(hook_api):\n    pass\n", "hook(hook_api)"),
    ("from PyInstaller.utils.hooks import get_module_attribute\nx = get_module_attribute('demo', 'v')\n", "calls"),
    ("data = open('x').read()\n", "open()"),
    ("import sys\nx = sys.getwindowsversion()\n", "sys.getwindowsversion"),
    ("raise RuntimeError('boom')\n", "RuntimeError"),
])
def test_hook_unsupported(site, tmp_path, src, needle):
    runner, hook = _runner(site, tmp_path, src)
    with pytest.raises(UnsupportedHook) as exc:
        runner.run(hook)
    assert needle in str(exc.value)


def test_site_locate_and_submodules(site):
    s = Site([site], site)
    assert s.locate("demo.fast")[0].name.endswith(".pyd")
    assert s.locate("demo.sub")[1] is True
    assert s.locate("demo.nope") is None
    assert s.dist_version("Demo") == "1.2.0"
    assert s.dist_requires("demo") == ["other"]


# ------------------------------------------------------------- analysis
def test_pth_dirs_case_insensitive(tmp_path):
    from pyi_doctor.cross.analysis import _pth_dirs

    site_dir = tmp_path / "site"
    (site_dir / "win32" / "lib").mkdir(parents=True)
    (site_dir / "Pythonwin").mkdir()
    (site_dir / "pywin32.pth").write_text("# c\r\nwin32\r\nwin32\\lib\r\npythonwin\r\nimport x\r\nmissing\r\n")
    assert [p.relative_to(site_dir.resolve()).as_posix() for p in _pth_dirs(site_dir)] == ["win32", "win32/lib", "Pythonwin"]


def test_windows_builtins(tmp_path):
    from pyi_doctor.cross.analysis import windows_builtins

    (tmp_path / "_socket.pyd").write_bytes(b"")
    b = windows_builtins(tmp_path)
    assert {"nt", "winreg", "_winapi"} <= b
    assert "_socket" not in b and "posix" not in b


def test_requirement_args(tmp_path):
    assert requirement_args(["requests>=2", " ", "-r reqs.txt"], tmp_path) == ["requests>=2", "-r", str(tmp_path / "reqs.txt")]


def test_cross_error_lists_reasons():
    e = CrossBuildUnsupported(["a", "b"])
    assert e.reasons == ["a", "b"] and "  - a" in str(e) and "build on Windows" in str(e)


# --------------------------------------------------------------- archives
def test_archives_readable_by_pyinstaller(tmp_path):
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    from pyi_doctor.cross import assemble
    from pyi_doctor.cross.analysis import Collected, PureModule

    def code(src, name):
        return compile(src, name, "exec")

    c = Collected()
    c.pure = {
        "struct": PureModule("struct", code("x = 1", "struct.py"), False),
        "pkg": PureModule("pkg", code("", "pkg/__init__.py"), True),
        "pkg.mod": PureModule("pkg.mod", code("y = 2", "pkg/mod.py"), False),
        "nsp": PureModule("nsp", None, True),
    }
    c.scripts = [("main", code("print(1)", "main.py"))]
    data = tmp_path / "d.txt"
    data.write_text("hi")
    c.datas = {"assets/d.txt": data}
    pyz = assemble.write_pyz(tmp_path / "PYZ.pyz", [m for n, m in c.pure.items() if n != "struct"])
    z = ZlibArchiveReader(str(pyz))
    assert set(z.toc) == {"pkg", "pkg.mod", "nsp"}
    assert z.extract("pkg.mod").co_filename == "pkg\\mod.py"

    pyi_dir = Path(__import__("PyInstaller").__file__).parent  # loader sources are platform independent
    exe = assemble.write_exe(tmp_path / "a.exe", bootloader_image=b"MZ" + b"\0" * 62, pyi_dir=pyi_dir, collected=c,
                             pyz=pyz, onefile=True, base_library=None, options=["pyi-contents-directory _internal"])
    reader = CArchiveReader(str(exe))
    toc = reader.toc
    order = list(toc)
    assert order[:6] == ["struct", "pyimod01_archive", "pyimod02_importers", "pyimod03_ctypes", "pyimod04_pywin32",
                         "pyiboot01_bootstrap"]
    assert toc["assets\\d.txt"][-1] == "x" and toc["PYZ.pyz"][-1] == "z"
    assert reader.options == ["pyi-contents-directory _internal"]
    assert marshal.loads(CArchiveReader(str(exe)).extract("main")).co_filename == "main.py"


# -------------------------------------------------------------- CLI / iss
def test_cli_target_flags():
    args = make_parser().parse_args(["build", "--target", "win", "-r", "a.txt", "-r", "b.txt", "--python-version", "3.13.2"])
    assert args.target == "win" and args.requirements == ["a.txt", "b.txt"] and args.python_version == "3.13.2"
    assert make_parser().parse_args(["build"]).target == "native"


def test_relative_iss(make_project, tmp_path):
    from pyi_doctor.installer import write_iss_relative

    ico = _ico(tmp_path / "app.ico")
    project = make_project(icon=str(ico), output_dir="dist")
    onedir = project.output_path / "portable" / project.name
    onedir.mkdir(parents=True)
    iss = write_iss_relative(project, onedir)
    text = iss.read_text(encoding="utf-8-sig")
    assert iss == project.output_path / "DemoApp.iss"
    assert 'Source: "portable\\DemoApp\\*"' in text
    assert "OutputDir=installer" in text and "SetupIconFile=iss-files\\app.ico" in text
    assert (project.output_path / "iss-files" / "app.ico").is_file()
    assert "/" not in text.split("[Files]")[1].split("[Icons]")[0]


def test_build_all_unknown_target(make_project):
    from pyi_doctor.errors import PyiDoctorError
    from pyi_doctor.pipeline import build_all

    with pytest.raises(PyiDoctorError):
        build_all(make_project(), target="mac")


def test_requirements_default_from_project_dependencies(tmp_path):
    from pyi_doctor import Project

    (tmp_path / "main.py").write_text("")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "1.0"\ndependencies = ["rich>=13"]\n[tool.pyi-doctor]\nentry = "main.py"\n'
    )
    assert Project.from_pyproject(tmp_path / "pyproject.toml").requirements == ["rich>=13"]


# ------------------------------------------------------- network / end to end
@pytest.mark.cross
@pytest.mark.skipif(sys.platform == "win32", reason="cross build is for Linux/macOS hosts")
def test_cross_build_end_to_end(tmp_path):
    from PyInstaller.archive.readers import CArchiveReader

    from pyi_doctor import Project
    from pyi_doctor.pipeline import build_all

    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text(
        "import json, sqlite3, ssl, colorama\nprint(json.dumps({'ok': True, 'sqlite': sqlite3.sqlite_version}))\n"
    )
    project = Project(entry="main.py", name="CrossDemo", version="1.0.0", console=True, onefile=True, root=app,
                      requirements=["colorama"])
    report = build_all(project, target="win", defender=False, log=print)
    assert report.ok, report.to_markdown()
    exe = app / "dist" / "portable" / "CrossDemo" / "CrossDemo.exe"
    internal = exe.parent / "_internal"
    assert (internal / f"python{sys.version_info.major}{sys.version_info.minor}.dll").is_file()
    assert (internal / "base_library.zip").is_file() and (internal / "_ssl.pyd").is_file()
    assert (internal / "colorama-0.4.6.dist-info").is_dir() or any(internal.glob("colorama-*.dist-info"))
    info = mype.parse(exe)
    assert info.machine == "x64" and info.has_version_info and info.pyinstaller_archive
    assert "pyimod04_pywin32" in CArchiveReader(str(exe)).toc
    assert (app / "dist" / "CrossDemo.iss").is_file()
    rel = app / "dist" / "release"
    assert (rel / "CrossDemo-1.0.0-portable-win64.zip").is_file() and (rel / "CrossDemo-1.0.0-win64.exe").is_file()

    wine = shutil.which("wine") or shutil.which("wine64")
    if wine:
        import subprocess

        for target in (exe, app / "dist" / "onefile" / "CrossDemo.exe"):
            out = subprocess.run([wine, str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=300, env={**__import__("os").environ, "WINEDEBUG": "-all"})
            assert b'"ok": true' in out.stdout


@pytest.mark.cross
@pytest.mark.skipif(sys.platform == "win32", reason="cross build is for Linux/macOS hosts")
def test_cross_build_stops_on_tkinter(tmp_path):
    from pyi_doctor import Project
    from pyi_doctor.cross import prepare_windows

    (tmp_path / "main.py").write_text("import tkinter\n")
    with pytest.raises(CrossBuildUnsupported) as exc:
        prepare_windows(Project(entry="main.py", name="Tk", root=tmp_path))
    assert any("tkinter" in r for r in exc.value.reasons)
