"""Checks to run before a build: ``pyi-doctor check``.

Looks at the build environment (PyInstaller, virtual environment, Inno Setup,
...) and the project settings (entry script syntax, icon, ``app_id``, ...) and
says what to change, so problems show up in seconds instead of after a long
PyInstaller run. Rule-based; a clean result does not mean the build will work.
"""

from __future__ import annotations

import shutil
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from .config import Project

INNO_SETUP_URL = "https://jrsoftware.org/isdl.php"
_LABEL = {"ok": "OK", "info": "INFO", "warning": "WARN", "failed": "FAIL"}


@dataclass
class Check:
    name: str
    status: str  # "ok" | "info" | "warning" | "failed"
    message: str
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def ico_sizes(path: Path) -> List[int]:
    """Image widths stored in an ``.ico`` file (256 is stored as 0). Empty if not an ICO."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(6)
            if len(head) < 6:
                return []
            reserved, kind, count = struct.unpack("<HHH", head)
            if reserved != 0 or kind != 1 or not 0 < count < 256:
                return []
            sizes = []
            for _ in range(count):
                entry = fh.read(16)
                if len(entry) < 16:
                    return []
                sizes.append(entry[0] or 256)
            return sizes
    except OSError:
        return []


def environment_checks(*, target: str = "native") -> List[Check]:
    from .antivirus import find_mpcmdrun
    from .installer import find_iscc
    from .portable import pyinstaller_available, pyinstaller_version

    out: List[Check] = []
    bits = 64 if sys.maxsize > 2**32 else 32
    py = f"Python {sys.version.split()[0]} ({bits}-bit)"
    if bits == 32:
        out.append(Check("python", "warning", py, "a 32-bit Python builds a 32-bit exe; use 64-bit Python for x64"))
    else:
        out.append(Check("python", "ok", py))

    if pyinstaller_available():
        out.append(Check("pyinstaller", "ok", f"PyInstaller {pyinstaller_version() or '?'}"))
    else:
        out.append(Check("pyinstaller", "failed", "PyInstaller is not installed in this Python",
                         f"{Path(sys.executable).name} -m pip install pyinstaller"))

    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        out.append(Check("venv", "ok", f"virtual environment: {sys.prefix}"))
    else:
        out.append(Check("venv", "warning", "not running in a virtual environment",
                         "build from a venv that has only your app's packages; the exe and license list get smaller"))

    if target == "win" and sys.platform != "win32":
        out.append(Check("installer", "info", "cross build: the Inno Setup script is written for compiling on Windows"))
    elif sys.platform == "win32":
        iscc = find_iscc()
        if iscc:
            out.append(Check("installer", "ok", f"Inno Setup: {iscc}"))
        else:
            out.append(Check("installer", "warning", "Inno Setup not found; the installer step will be skipped",
                             f"install Inno Setup 6.3+ from {INNO_SETUP_URL} (or set PYI_DOCTOR_ISCC)"))
        if find_mpcmdrun():
            out.append(Check("defender", "ok", "Microsoft Defender command-line scanner found"))
        else:
            out.append(Check("defender", "info", "Microsoft Defender scanner not found; the scan will be skipped"))
    else:
        out.append(Check("installer", "info",
                         "installer and version resource need Windows; the .iss script is still written"))

    if shutil.which("gh"):
        out.append(Check("gh", "ok", "GitHub CLI found (for --github-release)"))
    return out


def project_checks(project: Project, *, target: str = "native") -> List[Check]:
    out: List[Check] = []
    notes = getattr(project, "detected", []) or []
    for i, note in enumerate(notes):
        fix = "save these guesses to pyproject.toml with 'pyi-doctor init --write'" if i == len(notes) - 1 else ""
        out.append(Check("guessed", "info", note, fix))

    entry = project.entry_path
    if not entry.is_file():
        out.append(Check("entry", "failed", f"entry script not found: {entry}", "set entry to your main script"))
    else:
        try:
            compile(entry.read_bytes(), str(entry), "exec", dont_inherit=True)
            out.append(Check("entry", "ok", str(entry)))
        except SyntaxError as exc:
            out.append(Check("entry", "failed", f"syntax error in {entry.name} line {exc.lineno}: {exc.msg}",
                             "fix the script; PyInstaller would fail on it"))

    if not project.icon:
        out.append(Check("icon", "warning", "no icon set; the exe gets the default PyInstaller icon (risk factor +5)",
                         'set icon = "app.ico" (an .ico with a 256x256 image)'))
    elif not project.icon_path.is_file():
        out.append(Check("icon", "failed", f"icon not found: {project.icon_path}"))
    elif project.icon_path.suffix.lower() != ".ico":
        st = "failed" if target == "win" else "warning"
        out.append(Check("icon", st, f"{project.icon_path.name} is not an .ico file",
                         "convert it to .ico (the cross build needs .ico; PyInstaller converts others only with Pillow)"))
    else:
        sizes = ico_sizes(project.icon_path)
        if not sizes:
            out.append(Check("icon", "failed", f"{project.icon_path.name} is not a valid .ico file",
                             "re-export it as a Windows .ico"))
        elif 256 not in sizes:
            out.append(Check("icon", "warning", f"icon sizes {sorted(set(sizes))}; no 256x256 image",
                             "add a 256x256 image so the icon is sharp on high-DPI screens"))
        else:
            out.append(Check("icon", "ok", f"{project.icon_path.name} ({len(sizes)} sizes)"))

    if not project.app_id:
        from .autodetect import new_app_id

        out.append(Check("app_id", "warning",
                         "app_id not set; it is derived from publisher + name and changes if either changes",
                         f'set app_id = "{new_app_id()}" before the first release (pyi-doctor init --write does this)'))
    else:
        out.append(Check("app_id", "ok", project.effective_app_id))

    if not project.publisher:
        out.append(Check("publisher", "warning", "publisher is empty; the installer and file properties show no company",
                         'set publisher = "Your Name"'))

    if project.license_file and not project.license_path.is_file():
        out.append(Check("license_file", "failed", f"license_file not found: {project.license_path}"))
    for src, _ in project.add_data:
        if not project.resolve(src).exists():
            out.append(Check("add_data", "failed", f"add_data source not found: {project.resolve(src)}"))

    if project.upx:
        out.append(Check("upx", "warning", "upx = true adds the highest-weight risk factor (+30)", "set upx = false"))
    if project.onefile:
        out.append(Check("onefile", "info", "onefile builds add a risk factor (+20); the onedir build is kept too"))

    if target == "win":
        from .autodetect import GUI_MODULES, app_imports

        mods = app_imports(entry) if entry.is_file() else set()
        tk = sorted(m for m in mods if m in ("tkinter", "customtkinter", "ttkbootstrap"))
        if tk:
            out.append(Check("cross", "failed", f"{', '.join(tk)} cannot be cross-built (no Tcl/Tk in the embeddable package)",
                             "build on Windows instead"))
        elif not (mods & GUI_MODULES):
            out.append(Check("cross", "ok", "no known unsupported import in the entry script"))
        if project.pyinstaller_args:
            out.append(Check("cross", "warning", "pyinstaller_args are ignored by the cross build"))
    return out


def run_checks(project: Optional[Project] = None, *, target: str = "native",
               load_error: Optional[str] = None) -> List[Check]:
    """Environment checks plus project checks (or a failed ``config`` check with ``load_error``)."""
    checks = environment_checks(target=target)
    if project is not None:
        checks.append(Check("config", "ok", f"{project.name} {project.version} in {project.root}"))
        checks += project_checks(project, target=target)
    elif load_error:
        checks.append(Check("config", "failed", load_error, "run 'pyi-doctor init --write' in the project folder"))
    return checks


def check_lines(checks: List[Check], *, brief: bool = False) -> List[str]:
    """Readable lines; ``brief`` keeps only problems and warnings, one line each."""
    if brief:
        bad = [c for c in checks if c.status in ("failed", "warning")]
        out = [f"{_LABEL[c.status]} {c.name}: {c.message}" + (f" -> {c.fix}" if c.fix else "") for c in bad]
        return out or ["OK: no problems"]
    lines = []
    for c in checks:
        lines.append(f"[{_LABEL.get(c.status, c.status):4}] {c.name:<12} {c.message}")
        if c.fix and c.status in ("warning", "failed", "info"):
            lines.append(f"{'':19}-> {c.fix}")
    failed = sum(c.status == "failed" for c in checks)
    warned = sum(c.status == "warning" for c in checks)
    lines.append("")
    lines.append(f"{failed} problem(s), {warned} warning(s)" if failed or warned else "No problems found.")
    return lines
