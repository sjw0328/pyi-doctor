"""Antivirus false-positive risk diagnosis.

The score is a sum of commonly cited risk factors for PyInstaller executables
(UPX packing, single-file mode, missing version information, no signature,
prebuilt bootloader, default icon). It is a rough indicator for comparing
builds, not a prediction of what any antivirus product will do.

On Windows the file can also be scanned with the local Microsoft Defender
command-line scanner (``MpCmdRun.exe``) without remediation.
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import pe

WEIGHTS = {
    "upx": 30,
    "onefile": 20,
    "no_version_info": 15,
    "unsigned": 15,
    "stock_bootloader": 10,
    "default_icon": 5,
}

ADVICE = {
    "upx": "Build without UPX (pyi-doctor passes --noupx unless upx=true).",
    "onefile": "Prefer the onedir build or the installer for distribution; onefile self-extraction is a common heuristic trigger.",
    "no_version_info": "Embed a version resource (pyi-doctor does this automatically on Windows builds).",
    "unsigned": "Sign the executable and installer with a code-signing certificate (signtool).",
    "stock_bootloader": "Rebuild the PyInstaller bootloader from source so the stub differs from the widely shared prebuilt one.",
    "default_icon": "Set a custom icon; the stock PyInstaller icon appears in many unrelated executables.",
}

LABELS = {
    "upx": "UPX-packed sections found",
    "onefile": "single-file (onefile) executable",
    "no_version_info": "no version information resource",
    "unsigned": "no Authenticode signature",
    "stock_bootloader": "prebuilt PyInstaller bootloader from the wheel",
    "default_icon": "no custom icon configured",
}


@dataclass
class Factor:
    key: str
    label: str
    weight: int
    advice: str


@dataclass
class DefenderResult:
    status: str  # "clean" | "detected" | "error" | "unavailable" | "skipped"
    detail: str = ""
    exit_code: Optional[int] = None


@dataclass
class Diagnosis:
    path: str
    score: int
    level: str  # LOW | MEDIUM | HIGH
    factors: List[Factor] = field(default_factory=list)
    pe: Optional[dict] = None
    bootloader: str = "unknown"  # stock | unknown | n/a, or the value passed to diagnose()
    defender: DefenderResult = field(default_factory=lambda: DefenderResult("skipped"))
    sha256: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def level_for(score: int) -> str:
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def is_onedir(exe: Path) -> bool:
    return (exe.parent / "_internal").is_dir()


# ------------------------------------------------------------------ bootloader
_BOOT_NAMES = ("run", "run.exe", "runw.exe", "run_d", "run_d.exe", "runw_d.exe")


def _record_digest(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def bootloader_status() -> str:
    """State of the PyInstaller installed in *this* environment.

    'stock' if its bootloader files match the wheel's RECORD hashes, 'custom'
    if one differs (rebuilt from source), 'unknown' otherwise. This describes
    the build environment, not a given exe; see :func:`exe_bootloader_status`.
    """
    try:
        from importlib.metadata import distribution

        dist = distribution("pyinstaller")
    except Exception:
        return "unknown"
    files = dist.files or []
    checked = False
    for f in files:
        posix = str(f).replace("\\", "/")
        if "/bootloader/" not in posix or not f.hash:
            continue
        name = posix.rsplit("/", 1)[-1]
        if name not in _BOOT_NAMES:
            continue
        local = Path(f.locate())
        if not local.is_file() or f.hash.mode != "sha256":
            continue
        checked = True
        if _record_digest(local.read_bytes()) != f.hash.value:
            return "custom"
    return "stock" if checked else "unknown"


def _record_entries(record: Path) -> List[tuple]:
    """(relative path, sha256 RECORD digest) of Windows bootloaders listed in a wheel RECORD."""
    out = []
    try:
        lines = record.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.rsplit(",", 2)
        if len(parts) != 3 or not parts[1].startswith("sha256="):
            continue
        rel = parts[0].strip('"').replace("\\", "/")
        if "/bootloader/Windows-" in rel and rel.rsplit("/", 1)[-1] in _BOOT_NAMES:
            out.append((rel, parts[1][len("sha256="):]))
    return out


def _stock_from_record(record: Path, root: Path, version: str, known: Dict[str, str]) -> None:
    for rel, digest in _record_entries(record):
        f = root / rel
        if not f.is_file():
            continue
        data = f.read_bytes()
        if _record_digest(data) != digest:
            continue  # rebuilt or altered: not a stock reference
        try:
            text = pe.parse(f).text_sha256
        except pe.NotAPEFile:
            continue
        if text:
            known.setdefault(text, version)


def stock_bootloaders(cache: Optional[Path] = None) -> Dict[str, str]:
    """Map ``.text`` SHA-256 -> PyInstaller version for stock Windows bootloaders on this machine.

    Sources: the PyInstaller installed in this environment (Windows wheels
    carry the Windows bootloaders) and the ``win_amd64`` wheels unpacked by the
    cross build in the pyi-doctor cache. A file is used only when it matches
    the hash recorded in its wheel's RECORD.
    """
    known: Dict[str, str] = {}
    try:
        from importlib.metadata import distribution

        dist = distribution("pyinstaller")
        rec = next((f for f in (dist.files or []) if str(f).replace("\\", "/").endswith(".dist-info/RECORD")), None)
        if rec is not None:
            rec_path = Path(rec.locate())
            _stock_from_record(rec_path, rec_path.parent.parent, dist.version, known)
    except Exception:
        pass
    try:
        from .cross.fetch import cache_dir

        base = cache_dir(cache) / "pyinstaller-win"
        if base.is_dir():
            for ver_dir in sorted(d for d in base.iterdir() if d.is_dir()):
                for rec_path in ver_dir.glob("pyinstaller-*.dist-info/RECORD"):
                    _stock_from_record(rec_path, ver_dir, ver_dir.name, known)
    except Exception:
        pass
    return known


def exe_bootloader_status(info: "pe.PEInfo", known: Optional[Dict[str, str]] = None) -> tuple:
    """('stock' | 'unknown', note) for the bootloader inside ``info``.

    Compares the exe's ``.text`` section with stock PyInstaller Windows
    bootloaders found on this machine. Adding icon, version or manifest
    resources does not change that section. No match gives 'unknown': the exe
    may use a rebuilt bootloader or a PyInstaller version not present here.
    """
    if known is None:
        known = stock_bootloaders()
    if info.text_sha256 and info.text_sha256 in known:
        return "stock", f"bootloader matches the stock PyInstaller {known[info.text_sha256]} Windows bootloader"
    if not known:
        return "unknown", ("no stock PyInstaller Windows bootloader to compare with on this machine "
                           "(available after a Windows PyInstaller install or a --target win build)")
    versions = ", ".join(sorted(set(known.values())))
    return "unknown", (f"bootloader does not match the stock Windows bootloaders of PyInstaller {versions}; "
                       "it may be rebuilt from source or from another PyInstaller version")


# --------------------------------------------------------------------- defender
def find_mpcmdrun() -> Optional[Path]:
    if sys.platform != "win32":
        return None
    candidates: List[Path] = []
    pd = os.environ.get("ProgramData")
    if pd:
        plat = Path(pd) / "Microsoft" / "Windows Defender" / "Platform"
        if plat.is_dir():
            versions = sorted((d for d in plat.iterdir() if d.is_dir()), key=lambda d: _version_key(d.name), reverse=True)
            candidates += [d / "MpCmdRun.exe" for d in versions]
    pf = os.environ.get("ProgramFiles")
    if pf:
        candidates.append(Path(pf) / "Windows Defender" / "MpCmdRun.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _version_key(name: str):
    parts = []
    for x in name.split("-")[0].split("."):
        parts.append(int(x) if x.isdigit() else 0)
    return parts


def defender_scan(path: Path, *, mpcmdrun: Optional[Path] = None, timeout: float = 600) -> DefenderResult:
    """Scan one file with Microsoft Defender (no remediation)."""
    exe = mpcmdrun or find_mpcmdrun()
    if exe is None:
        return DefenderResult("unavailable", "MpCmdRun.exe not found (Windows with Microsoft Defender only)")
    cmd = [str(exe), "-Scan", "-ScanType", "3", "-File", str(Path(path).resolve()), "-DisableRemediation"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DefenderResult("error", f"scan could not run: {exc}")
    out = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")
    out = " ".join(out.split())[-500:]
    if proc.returncode == 0:
        return DefenderResult("clean", "no threats reported by this scan", 0)
    if proc.returncode == 2:
        return DefenderResult("detected", out or "Defender reported a threat", 2)
    return DefenderResult("error", out or "scan returned an error", proc.returncode)


# ------------------------------------------------------------------- diagnosis
def diagnose(
    exe: Path,
    *,
    icon_configured: Optional[bool] = None,
    defender: bool = True,
    bootloader: Optional[str] = None,
    expect_pyinstaller: bool = True,
) -> Diagnosis:
    """Score ``exe`` for antivirus false-positive risk factors.

    ``icon_configured``: pass the project setting when known; ``None`` skips
    the icon factor (it cannot be told apart from the binary alone).
    ``expect_pyinstaller``: set to ``False`` for files that are not PyInstaller
    builds (e.g. the Inno Setup installer) to skip the missing-marker note.
    ``bootloader``: ``None`` (default) checks the bootloader inside ``exe``
    against stock PyInstaller Windows bootloaders on this machine; a string
    ('stock', 'custom', 'n/a', ...) is used as given.
    """
    exe = Path(exe)
    notes: List[str] = []
    try:
        info = pe.parse(exe)
    except pe.NotAPEFile as exc:
        info = None
        notes.append(f"not a Windows PE file, PE checks skipped: {exc}")

    keys: List[str] = []
    if info is not None:
        if info.upx_packed:
            keys.append("upx")
        if info.pyinstaller_archive and not is_onedir(exe) and info.overlay_size > 1_000_000:
            keys.append("onefile")
        if not info.has_version_info:
            keys.append("no_version_info")
        if not info.signed:
            keys.append("unsigned")
        if not info.pyinstaller_archive and expect_pyinstaller:
            notes.append("no PyInstaller archive marker found; the file may not be a PyInstaller build")
    if bootloader is not None:
        boot = bootloader
    elif info is not None and info.pyinstaller_archive:
        boot, boot_note = exe_bootloader_status(info)
        notes.append(boot_note)
    else:
        boot = "unknown"
    if info is not None and info.pyinstaller_archive and boot == "stock":
        keys.append("stock_bootloader")
    if icon_configured is False:
        keys.append("default_icon")

    factors = [Factor(k, LABELS[k], WEIGHTS[k], ADVICE[k]) for k in keys]
    score = min(100, sum(f.weight for f in factors))
    result = Diagnosis(
        path=str(exe),
        score=score,
        level=level_for(score),
        factors=factors,
        pe=info.to_dict() if info else None,
        bootloader=boot,
        sha256=sha256_file(exe),
        notes=notes,
    )
    if defender:
        result.defender = defender_scan(exe) if info is not None else DefenderResult("skipped", "not a PE file")
    return result
