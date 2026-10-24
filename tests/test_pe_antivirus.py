import subprocess

import pytest

from pe_builder import build_pe
from pyi_doctor import antivirus, pe


def _write(tmp_path, data, name="app.exe"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_parse_basic(tmp_path):
    info = pe.parse(_write(tmp_path, build_pe()))
    assert info.machine == "x64" and info.is_64bit
    assert info.sections == [".text", ".rdata"]
    assert info.resource_types == []
    assert not info.signed and not info.upx_packed and not info.pyinstaller_archive
    assert info.overlay_size == 0


def test_parse_pe32(tmp_path):
    info = pe.parse(_write(tmp_path, build_pe(pe32_plus=False, machine=0x14C)))
    assert info.machine == "x86" and not info.is_64bit


def test_resources(tmp_path):
    info = pe.parse(_write(tmp_path, build_pe(resource_types=[24, 16, 14, 3])))
    assert info.resource_types == [3, 14, 16, 24]
    assert info.has_version_info and info.has_icon and info.has_manifest


def test_upx_cookie_signature(tmp_path):
    data = build_pe(sections=["UPX0", "UPX1", ".rsrc2"], overlay=b"x" * 5000, cookie=True, signature=b"S" * 64)
    info = pe.parse(_write(tmp_path, data))
    assert info.upx_packed
    assert info.pyinstaller_archive
    assert info.signed and info.signature_size == 64
    assert info.overlay_size == 5000 + 32 + 8 + 80 + 64
    d = info.to_dict()
    assert d["upx_packed"] and not d["has_version_info"]


@pytest.mark.parametrize("data", [b"", b"MZ", b"MZ" + b"\0" * 100, b"\x7fELF" + b"\0" * 200, b"MZ" + b"\0" * 58 + b"\xff\xff\xff\x00" + b"\0" * 64])
def test_not_pe(tmp_path, data):
    with pytest.raises(pe.NotAPEFile):
        pe.parse(_write(tmp_path, data))


def test_truncated_pe(tmp_path):
    data = build_pe()[:0x80]
    with pytest.raises(pe.NotAPEFile):
        pe.parse(_write(tmp_path, data))


@pytest.mark.parametrize("score,level", [(0, "LOW"), (24, "LOW"), (25, "MEDIUM"), (49, "MEDIUM"), (50, "HIGH"), (100, "HIGH")])
def test_levels(score, level):
    assert antivirus.level_for(score) == level


def test_diagnose_risky_onefile(tmp_path):
    data = build_pe(sections=["UPX0", "UPX1"], overlay=b"z" * 2_000_000, cookie=True)
    exe = _write(tmp_path, data)
    d = antivirus.diagnose(exe, icon_configured=False, defender=False, bootloader="stock")
    keys = {f.key for f in d.factors}
    assert keys == {"upx", "onefile", "no_version_info", "unsigned", "stock_bootloader", "default_icon"}
    assert d.score == 95 and d.level == "HIGH"
    assert len(d.sha256) == 64
    assert d.defender.status == "skipped"


def test_diagnose_clean_onedir(tmp_path):
    folder = tmp_path / "App"
    (folder / "_internal").mkdir(parents=True)
    exe = folder / "App.exe"
    exe.write_bytes(build_pe(resource_types=[14, 16, 24], overlay=b"q" * 3_000_000, cookie=True, signature=b"S" * 32))
    d = antivirus.diagnose(exe, icon_configured=True, defender=False, bootloader="custom")
    assert d.factors == [] and d.score == 0 and d.level == "LOW"


def test_diagnose_non_pe(tmp_path):
    f = _write(tmp_path, b"#!/bin/sh\necho hi\n", "app")
    d = antivirus.diagnose(f, icon_configured=None, defender=True, bootloader="stock")
    assert d.pe is None and d.score == 0
    assert any("not a Windows PE" in n for n in d.notes)
    assert d.defender.status == "skipped"


def test_diagnose_not_pyinstaller_note(tmp_path):
    d = antivirus.diagnose(_write(tmp_path, build_pe(resource_types=[16])), defender=False, bootloader="stock")
    assert any("PyInstaller archive" in n for n in d.notes)
    assert "stock_bootloader" not in {f.key for f in d.factors}


def test_defender_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(antivirus, "find_mpcmdrun", lambda: None)
    assert antivirus.defender_scan(tmp_path / "x.exe").status == "unavailable"


@pytest.mark.parametrize("code,status", [(0, "clean"), (2, "detected"), (5, "error")])
def test_defender_exit_codes(tmp_path, monkeypatch, code, status):
    seen = {}

    def fake_run(cmd, capture_output, timeout):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, code, stdout=b"Scan output", stderr=b"")

    monkeypatch.setattr(antivirus.subprocess, "run", fake_run)
    r = antivirus.defender_scan(tmp_path / "x.exe", mpcmdrun=tmp_path / "MpCmdRun.exe")
    assert r.status == status and r.exit_code == code
    assert "-DisableRemediation" in seen["cmd"] and "-ScanType" in seen["cmd"]


def test_defender_timeout(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(antivirus.subprocess, "run", boom)
    assert antivirus.defender_scan(tmp_path / "x.exe", mpcmdrun=tmp_path / "m.exe").status == "error"


def test_find_mpcmdrun_picks_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(antivirus.sys, "platform", "win32")
    plat = tmp_path / "Microsoft" / "Windows Defender" / "Platform"
    for v in ("4.18.2001.10-0", "4.18.24090.11-0", "4.18.9999.1-0"):
        (plat / v).mkdir(parents=True)
    (plat / "4.18.24090.11-0" / "MpCmdRun.exe").write_text("")
    (plat / "4.18.2001.10-0" / "MpCmdRun.exe").write_text("")
    monkeypatch.setenv("ProgramData", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    assert antivirus.find_mpcmdrun() == plat / "4.18.24090.11-0" / "MpCmdRun.exe"


def test_find_mpcmdrun_non_windows(monkeypatch):
    monkeypatch.setattr(antivirus.sys, "platform", "linux")
    assert antivirus.find_mpcmdrun() is None


def test_bootloader_status_values():
    assert antivirus.bootloader_status() in ("stock", "custom", "unknown")
