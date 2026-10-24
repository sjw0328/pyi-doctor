import os
import sys
import textwrap

import pytest

from pyi_doctor import smoke


def _script(tmp_path, body, name="app.py"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _run(tmp_path, body, timeout=10.0, **kw):
    script = _script(tmp_path, body)
    return smoke.run_command([sys.executable, str(script)], cwd=tmp_path, timeout=timeout, **kw)


def test_exit_zero(tmp_path):
    r = _run(tmp_path, "print('ok')")
    assert r.ok and r.status == "passed-exit" and r.exit_code == 0
    assert "ok" in r.output_tail


def test_missing_module(tmp_path):
    r = _run(tmp_path, "import definitely_missing_module_xyz")
    assert not r.ok and r.status == "failed" and r.exit_code == 1
    assert r.missing_modules == ["definitely_missing_module_xyz"]
    assert any("hidden_imports" in s for s in r.suggestions)


def test_missing_file(tmp_path):
    r = _run(tmp_path, "open('assets/logo.png')")
    assert not r.ok and r.missing_files == ["assets/logo.png"]
    assert any("add_data" in s for s in r.suggestions)


def test_alive_counts_as_pass(tmp_path):
    r = _run(tmp_path, "import time\nprint('started', flush=True)\ntime.sleep(60)", timeout=1.5)
    assert r.ok and r.status == "passed-alive" and r.exit_code is None
    assert r.duration < 30


def test_alive_but_traceback_fails(tmp_path):
    body = """
    import sys, time, traceback
    try:
        raise RuntimeError('boom')
    except RuntimeError:
        traceback.print_exc()
        sys.stderr.flush()
    time.sleep(60)
    """
    r = _run(tmp_path, body, timeout=1.5)
    assert not r.ok and r.status == "failed"


def test_expected_exit_code(tmp_path):
    assert _run(tmp_path, "raise SystemExit(3)", expect_exit_code=3).ok
    r = _run(tmp_path, "raise SystemExit(0)", expect_exit_code=3)
    assert not r.ok and "expected 3" in r.message


def test_start_failure(tmp_path):
    r = smoke.run_command([str(tmp_path / "nope.exe")], cwd=tmp_path, timeout=1)
    assert not r.ok and r.status == "error"


def test_run_missing_exe(tmp_path):
    r = smoke.run(tmp_path / "missing.exe")
    assert not r.ok and r.status == "error"


@pytest.mark.parametrize("text,kind,value", [
    ("ModuleNotFoundError: No module named 'pkg.sub'", "module", "pkg.sub"),
    ("ImportError: No module named foo", "module", "foo"),
    ("ImportError: cannot import name 'X' from 'y'", "symbol", "X"),
    ("ImportError: DLL load failed while importing _ssl: The specified module could not be found.", "dll", None),
    ("[PYI-1234:ERROR] Failed to load Python DLL 'C:\\x\\python313.dll'.", "dll", None),
    ("FileNotFoundError: [Errno 2] No such file or directory: 'data/config.json'", "file", "data/config.json"),
])
def test_analyze_output(text, kind, value):
    found = smoke.analyze_output(text)
    assert found[kind]
    if value:
        assert found[kind][0] == value


def test_analyze_output_clean():
    found = smoke.analyze_output("all good\nnothing to see")
    assert not any(found.values())


def test_clean_env_strips_python(monkeypatch):
    base = {
        "PYTHONPATH": "x",
        "PYTHONHOME": "y",
        "VIRTUAL_ENV": "z",
        "PATH": os.pathsep.join([os.path.join(sys.prefix, "bin"), sys.prefix, os.path.abspath(os.sep + "usr_other_bin"), ""]),
        "KEEP": "1",
    }
    env = smoke.clean_env(base)
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env and "VIRTUAL_ENV" not in env
    assert env["KEEP"] == "1"
    parts = env["PATH"].split(os.pathsep)
    assert os.path.abspath(os.sep + "usr_other_bin") in parts
    assert sys.prefix not in parts


def test_stage_onedir_and_onefile(tmp_path):
    onedir = tmp_path / "App"
    (onedir / "_internal").mkdir(parents=True)
    (onedir / "_internal" / "lib.bin").write_bytes(b"x")
    (onedir / "App.exe").write_bytes(b"exe")
    dest = tmp_path / "stage1"
    dest.mkdir()
    staged = smoke._stage(onedir / "App.exe", dest)
    assert staged == dest / "App" / "App.exe" and (dest / "App" / "_internal" / "lib.bin").is_file()
    single = tmp_path / "one.exe"
    single.write_bytes(b"x")
    dest2 = tmp_path / "stage2"
    dest2.mkdir()
    assert smoke._stage(single, dest2) == dest2 / "one.exe"


@pytest.mark.skipif(sys.platform == "win32", reason="shebang script")
def test_run_isolated_shebang(tmp_path):
    exe = tmp_path / "tool"
    exe.write_text(f"#!{sys.executable}\nimport os,sys\nprint(os.environ.get('PYTHONPATH','none'))\n")
    exe.chmod(0o755)
    os.environ["PYTHONPATH"] = "should-be-removed"
    try:
        r = smoke.run(exe, timeout=10)
    finally:
        del os.environ["PYTHONPATH"]
    assert r.ok and "none" in r.output_tail
