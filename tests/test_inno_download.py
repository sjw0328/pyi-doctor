import hashlib
import io
import sys
from pathlib import Path

import pytest

from pyi_doctor import installer as inst
from pyi_doctor.errors import BuildError, ToolNotFoundError


def test_windows_arch_from_env(monkeypatch):
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    assert inst.windows_arch() == "x64"
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "ARM64")
    assert inst.windows_arch() == "x64"
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "x86")
    assert inst.windows_arch() == "x86"
    monkeypatch.setenv("PROCESSOR_ARCHITEW6432", "AMD64")  # 32-bit Python on 64-bit Windows
    assert inst.windows_arch() == "x64"


def test_auto_download_env(monkeypatch):
    monkeypatch.delenv(inst.NO_DOWNLOAD_ENV, raising=False)
    assert inst.auto_download_allowed()
    monkeypatch.setenv(inst.NO_DOWNLOAD_ENV, "0")
    assert inst.auto_download_allowed()
    monkeypatch.setenv(inst.NO_DOWNLOAD_ENV, "1")
    assert not inst.auto_download_allowed()


def test_find_iscc_uses_cache(monkeypatch, tmp_path):
    monkeypatch.delenv(inst.ISCC_ENV, raising=False)
    monkeypatch.setenv("PYI_DOCTOR_CACHE", str(tmp_path))
    monkeypatch.setattr(inst.shutil, "which", lambda name: None)
    for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    assert inst.find_iscc() is None
    d = inst.inno_cache_dir("x64")
    d.mkdir(parents=True)
    (d / "ISCC.exe").write_bytes(b"MZ")
    assert inst.find_iscc() == d / "ISCC.exe"


def _fake_urlopen(payload):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda url, timeout=None: Resp(payload)


def test_download_checks_sha256(monkeypatch, tmp_path):
    payload = b"fake installer"
    monkeypatch.setattr(inst.urllib.request, "urlopen", _fake_urlopen(payload))
    with pytest.raises(BuildError, match="SHA-256"):
        inst.download_inno_setup("x64", cache=tmp_path)
    assert not list((tmp_path / "downloads").glob("*"))  # mismatching file removed

    fname = inst.INNO_DOWNLOADS["x64"][0]
    monkeypatch.setitem(inst.INNO_DOWNLOADS, "x64", (fname, hashlib.sha256(payload).hexdigest()))
    got = inst.download_inno_setup("x64", cache=tmp_path)
    assert got.read_bytes() == payload


def test_ensure_iscc_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(inst.sys, "platform", "linux")
    with pytest.raises(ToolNotFoundError, match="Windows"):
        inst.ensure_iscc(cache=tmp_path)


def test_ensure_iscc_download_off(monkeypatch, tmp_path):
    monkeypatch.setattr(inst.sys, "platform", "win32")
    with pytest.raises(ToolNotFoundError, match="automatic download is off"):
        inst.ensure_iscc(cache=tmp_path, download=False)


def test_ensure_iscc_unpacks(monkeypatch, tmp_path):
    monkeypatch.setattr(inst.sys, "platform", "win32")
    monkeypatch.setattr(inst, "windows_arch", lambda: "x64")
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"MZ")
    monkeypatch.setattr(inst, "download_inno_setup", lambda **kw: setup)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        target = Path(next(a for a in cmd if a.startswith("/DIR="))[5:])
        target.mkdir(parents=True, exist_ok=True)
        (target / "ISCC.exe").write_bytes(b"MZ")

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr(inst.subprocess, "run", fake_run)
    iscc = inst.ensure_iscc(cache=tmp_path)
    assert iscc == inst.inno_cache_dir("x64", cache=tmp_path) / "ISCC.exe"
    for flag in ("/VERYSILENT", "/CURRENTUSER", "/PORTABLE=1", "/SP-"):
        assert flag in seen["cmd"]
    # second call uses the unpacked copy without running setup again
    seen.clear()
    assert inst.ensure_iscc(cache=tmp_path) == iscc
    assert not seen


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Inno Setup runs on Windows")
def test_real_download_and_unpack(tmp_path):
    iscc = inst.ensure_iscc(cache=tmp_path, download=True)
    assert iscc.is_file()
    assert (iscc.parent / "Languages" / "Korean.isl").is_file()
