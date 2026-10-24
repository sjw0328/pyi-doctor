"""Third-party license notices for bundled packages.

Bundled top-level modules are read from PyInstaller's TOC files in the work
folder and mapped to installed distributions. License metadata and license
files found in each distribution are written to ``THIRD_PARTY_LICENSES.txt``.
If no TOC file is found, every distribution in the build environment except
known build tools is listed (over-inclusive on purpose).

The output is generated from package metadata, which can be incomplete or
wrong. Review it before relying on it.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import __version__

BUILD_ONLY = {
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "pefile",
    "pywin32-ctypes",
    "macholib",
    "pip",
    "setuptools",
    "wheel",
    "pyi-doctor",
    "build",
    "pyproject-hooks",
    "pytest",
    "pluggy",
    "iniconfig",
    "tomli",
}
_LICENSE_FILE_RX = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)([._-].*)?$", re.I)
_TOC_TYPES = {"PYMODULE", "PYSOURCE", "EXTENSION", "BINARY", "DATA", "PYZ", "OPTION", "DEPENDENCY", "SPLASH", "SYMLINK"}

PYINSTALLER_NOTE = (
    "This program was packaged with PyInstaller (https://pyinstaller.org). The PyInstaller\n"
    "bootloader is distributed under the GNU General Public License version 2 with a\n"
    "special exception that permits distributing it with programs under any license.\n"
    "See https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt\n"
)


@dataclass
class PackageLicense:
    name: str
    version: str
    license: str
    license_files: List[Tuple[str, str]] = field(default_factory=list)  # (filename, text)
    homepage: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["license_files"] = [name for name, _ in self.license_files]
        return d


# ------------------------------------------------------------------ TOC parsing
def _walk(obj, out: Set[str]) -> None:
    if isinstance(obj, (list, tuple)):
        if len(obj) == 3 and all(isinstance(x, str) for x in obj) and obj[2] in _TOC_TYPES:
            name, _, typ = obj
            out.add(_top_level(name, typ))
            return
        for item in obj:
            _walk(item, out)


def _top_level(name: str, typ: str) -> str:
    if typ in ("PYMODULE", "PYSOURCE", "EXTENSION") and "/" not in name and "\\" not in name:
        return name.split(".")[0]
    first = re.split(r"[\\/]", name)[0]
    return first.split(".")[0] if first.endswith((".py", ".pyd", ".so")) else first


def modules_from_toc_text(text: str) -> Set[str]:
    try:
        obj = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        # Fall back to a regex over ('name', 'path', 'TYPE') triples.
        obj = re.findall(r"\(\s*'([^']+)'\s*,\s*(?:'[^']*'|None)\s*,\s*'([A-Z]+)'\s*\)", text)
        obj = [(n, "", t) for n, t in obj]
    found: Set[str] = set()
    _walk(obj, found)
    found.discard("")
    return found


def bundled_modules(work_dir: Path) -> Set[str]:
    """Top-level module/folder names seen in PyInstaller TOC files under ``work_dir``."""
    mods: Set[str] = set()
    for toc in sorted(Path(work_dir).rglob("*.toc")):
        try:
            mods |= modules_from_toc_text(toc.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return mods


# ------------------------------------------------------------ distributions
def _dists():
    from importlib.metadata import distributions

    seen = set()
    for d in distributions():
        name = (d.metadata.get("Name") or "").strip()
        key = _norm(name)
        if not name or key in seen:
            continue
        seen.add(key)
        yield d


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist_top_levels(dist) -> Set[str]:
    tops: Set[str] = set()
    txt = dist.read_text("top_level.txt")
    if txt:
        tops |= {line.strip() for line in txt.splitlines() if line.strip()}
    for f in dist.files or []:
        parts = Path(str(f)).parts
        if not parts:
            continue
        first = parts[0]
        if first.endswith((".dist-info", ".egg-info", ".data")) or first in ("..", "__pycache__", "bin", "Scripts"):
            continue
        tops.add(first.split(".")[0] if len(parts) == 1 else first)
    return tops


def _license_string(meta) -> str:
    expr = (meta.get("License-Expression") or "").strip()
    if expr:
        return expr
    lic = (meta.get("License") or "").strip()
    if lic and lic.upper() != "UNKNOWN" and len(lic) <= 120 and "\n" not in lic:
        return lic
    classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c not in ("OSI Approved",)]
    if classifiers:
        return " / ".join(dict.fromkeys(classifiers))
    if lic:
        return "see license text"
    return "UNKNOWN"


def _license_files(dist) -> List[Tuple[str, str]]:
    wanted = {Path(x).name for x in (dist.metadata.get_all("License-File") or [])}
    out: List[Tuple[str, str]] = []
    for f in dist.files or []:
        name = Path(str(f)).name
        if name in wanted or (_LICENSE_FILE_RX.match(name) and ".dist-info" in str(f).replace("\\", "/")):
            try:
                text = Path(f.locate()).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text.strip() and all(text != t for _, t in out):
                out.append((name, text.strip()))
    return out


def _homepage(meta) -> str:
    hp = (meta.get("Home-page") or "").strip()
    if hp:
        return hp
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository", "source code"):
            return url.strip()
    return ""


def package_license(dist) -> PackageLicense:
    meta = dist.metadata
    return PackageLicense(
        name=meta.get("Name", "?"),
        version=meta.get("Version", "?"),
        license=_license_string(meta),
        license_files=_license_files(dist),
        homepage=_homepage(meta),
    )


def collect(work_dir: Optional[Path] = None, *, exclude: Iterable[str] = ()) -> Tuple[List[PackageLicense], str]:
    """Return (packages, mode). ``mode`` is 'toc' or 'environment'."""
    skip = {_norm(x) for x in BUILD_ONLY} | {_norm(x) for x in exclude}
    mods = bundled_modules(work_dir) if work_dir and Path(work_dir).is_dir() else set()
    result: List[PackageLicense] = []
    if mods:
        mode = "toc"
        for d in _dists():
            key = _norm(d.metadata["Name"])
            if key in skip:
                continue
            if _dist_top_levels(d) & mods:
                result.append(package_license(d))
    else:
        mode = "environment"
        for d in _dists():
            if _norm(d.metadata["Name"]) not in skip:
                result.append(package_license(d))
    result.sort(key=lambda p: p.name.lower())
    return result, mode


# ----------------------------------------------------------------- rendering
def python_license_text() -> str:
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(os.__file__).parent / "LICENSE.txt",
    ]
    for c in candidates:
        if c.is_file():
            try:
                return c.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                pass
    return "Python Software Foundation License Version 2. Full text: https://docs.python.org/3/license.html"


def render(packages: List[PackageLicense], *, app_name: str, mode: str = "toc") -> str:
    v = sys.version_info
    sep = "=" * 78
    parts = [
        f"Third-party software notices for {app_name}",
        f"Generated by pyi-doctor {__version__} from installed package metadata ({mode} mode).",
        "Metadata can be incomplete; review this file before distribution.",
        "",
        sep,
        f"Python {v.major}.{v.minor}.{v.micro}",
        sep,
        python_license_text(),
        "",
        sep,
        "PyInstaller bootloader",
        sep,
        PYINSTALLER_NOTE,
    ]
    for p in packages:
        parts += ["", sep, f"{p.name} {p.version}", f"License: {p.license}"]
        if p.homepage:
            parts.append(f"Homepage: {p.homepage}")
        parts.append(sep)
        if p.license_files:
            for fname, text in p.license_files:
                parts += [f"--- {fname} ---", text, ""]
        else:
            parts.append("(no license file found in the installed package metadata)")
    return "\n".join(parts).rstrip() + "\n"


def write(path: Path, packages: List[PackageLicense], *, app_name: str, mode: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(packages, app_name=app_name, mode=mode), encoding="utf-8")
    return path


def summary(packages: List[PackageLicense]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in packages:
        counts[p.license] = counts.get(p.license, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
