"""Guess project settings from a folder, so ``pyi-doctor build`` works without a config.

Everything here is a guess based on file names and imports. The guesses are
listed in :attr:`Detection.notes` and can be written to ``pyproject.toml``
with ``pyi-doctor init --write`` to review and fix them.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

ENTRY_CANDIDATES = (
    "main.py", "app.py", "__main__.py", "run.py", "gui.py", "start.py", "launcher.py", "cli.py",
    "main.pyw", "app.pyw",
)
DATA_DIRS = ("assets", "data", "resources", "res", "static", "images", "img", "fonts", "locales", "locale", "i18n")
ICON_DIRS = (".", "assets", "icons", "icon", "resources", "res", "images", "img", "static")
LICENSE_FILES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENCE.txt", "COPYING", "COPYING.txt")
# Top-level modules that mean "this app opens a window".
GUI_MODULES = {
    "tkinter", "customtkinter", "ttkbootstrap", "PySide6", "PySide2", "PyQt6", "PyQt5", "wx", "kivy", "pygame",
    "flet", "dearpygui", "webview", "eel", "toga", "arcade", "pyglet", "PySimpleGUI", "FreeSimpleGUI", "nicegui",
}
_SKIP_SCRIPTS = {"setup.py", "conftest.py", "noxfile.py", "fabfile.py", "manage.py"}
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]\s*:""", re.M)
_VERSION_ASSIGN = re.compile(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", re.M)


@dataclass
class Detection:
    """Guessed settings (keys of :class:`~pyi_doctor.Project`) and a note per guess."""

    root: Path
    values: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    gui_modules: List[str] = field(default_factory=list)

    def note(self, key: str, value: Any, why: str) -> None:
        self.values[key] = value
        self.notes.append(f"{key} = {_fmt(value)}  ({why})")


def _fmt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def safe_name(text: str) -> str:
    """A file-name-safe application name from free text ("my app/v2" -> "my app-v2")."""
    out = _BAD_NAME_CHARS.sub("-", str(text)).strip(" .-")
    return out or "App"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def imported_modules(path: Path) -> Set[str]:
    """Top-level module names imported by a script (empty on syntax errors)."""
    try:
        tree = ast.parse(_read(path), filename=str(path))
    except (SyntaxError, ValueError):
        return set()
    mods: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mods.add(node.module.split(".")[0])
    return mods


def app_imports(entry: Path) -> Set[str]:
    """Modules imported by ``entry`` and by the scripts next to it that it imports (one level)."""
    entry = Path(entry).resolve()
    mods = imported_modules(entry)
    for p in local_scripts(entry.parent):
        if p.resolve() != entry and p.stem in mods:
            mods |= imported_modules(p)
    return mods


def local_scripts(root: Path) -> List[Path]:
    """``.py``/``.pyw`` files directly in ``root`` that look like programs, not tests or setup files."""
    out = []
    for p in sorted(root.glob("*.py*")):
        if p.suffix not in (".py", ".pyw") or not p.is_file():
            continue
        if p.name in _SKIP_SCRIPTS or p.name.startswith("test_") or p.name.endswith("_test.py"):
            continue
        out.append(p)
    return out


def find_entry(root: Path, name_hint: str = "") -> Optional[Path]:
    """Most likely entry script in ``root`` (see :data:`ENTRY_CANDIDATES`)."""
    for cand in ENTRY_CANDIDATES:
        p = root / cand
        if p.is_file():
            return p
    scripts = local_scripts(root)
    hint = name_hint.lower().replace("-", "_")
    for p in scripts:
        if hint and p.stem.lower() == hint:
            return p
    guarded = [p for p in scripts if _MAIN_GUARD.search(_read(p))]
    if guarded:
        return guarded[0]
    return scripts[0] if len(scripts) == 1 else None


def _find_icon(root: Path, name_hint: str) -> Optional[Path]:
    icons: List[Path] = []
    for d in ICON_DIRS:
        folder = root / d
        if folder.is_dir():
            icons.extend(sorted(folder.glob("*.ico")))
    if not icons:
        return None
    hint = name_hint.lower()

    def rank(p: Path) -> int:
        stem = p.stem.lower()
        if hint and stem == hint:
            return 0
        if stem in ("icon", "app", "logo", "app_icon", "appicon"):
            return 1
        return 2

    return sorted(icons, key=rank)[0]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _pyproject(root: Path) -> Dict[str, Any]:
    p = root / "pyproject.toml"
    if not p.is_file():
        return {}
    from .config import _load_toml

    try:
        return _load_toml(p)
    except Exception:  # noqa: BLE001 - a broken file is reported by the real loader later
        return {}


def detect(root: Union[str, Path] = ".", *, entry: Optional[Union[str, Path]] = None) -> Detection:
    """Guess settings for the app in ``root``. ``entry`` fixes the entry script.

    Keys that are already set in ``[tool.pyi-doctor]`` are not guessed.
    """
    root = Path(root).resolve()
    if root.is_file():
        entry = entry or root
        root = root.parent
    det = Detection(root=root)
    data = _pyproject(root)
    tool = data.get("tool", {}).get("pyi-doctor", {}) or {}
    proj = data.get("project", {}) or {}

    # name
    pname = proj.get("name") if isinstance(proj.get("name"), str) else None
    name_hint = tool.get("name") or pname or ""

    # entry
    if entry is not None:
        entry_path = Path(entry).resolve()
        det.values["entry"] = _rel(entry_path, root)
    elif "entry" in tool:
        entry_path = (root / str(tool["entry"])).resolve()
    else:
        found = find_entry(root, name_hint)
        entry_path = found.resolve() if found else None
        if found:
            why = "common entry file name" if found.name in ENTRY_CANDIDATES else "script in the project folder"
            det.note("entry", _rel(found, root), why)

    if "name" not in tool:
        if pname:
            det.note("name", safe_name(pname), "from [project].name")
        elif entry_path is not None and entry_path.stem not in ("main", "app", "__main__", "run", "gui", "start", "cli"):
            det.note("name", safe_name(entry_path.stem), "from the entry script name")
        else:
            det.note("name", safe_name(root.name), "from the folder name")
    name = str(tool.get("name") or det.values.get("name") or "")

    # version
    if "version" not in tool and not isinstance(proj.get("version"), str) and entry_path is not None:
        m = _VERSION_ASSIGN.search(_read(entry_path))
        if m:
            det.note("version", m.group(1), "from __version__ in the entry script")

    # console / GUI
    if entry_path is not None and entry_path.is_file():
        mods = app_imports(entry_path)
        det.gui_modules = sorted(m for m in mods if m in GUI_MODULES)
        if "console" not in tool:
            if det.gui_modules or entry_path.suffix == ".pyw":
                why = f"imports {', '.join(det.gui_modules)}" if det.gui_modules else ".pyw script"
                det.note("console", False, f"window app: {why}")
            else:
                det.note("console", True, "no GUI toolkit import found")

    # icon, license, data folders
    if "icon" not in tool:
        icon = _find_icon(root, name)
        if icon:
            det.note("icon", _rel(icon, root), "found .ico file")
    if "license_file" not in tool:
        for cand in LICENSE_FILES:
            if (root / cand).is_file():
                det.note("license_file", cand, "shown in the installer")
                break
    if "add_data" not in tool:
        dirs = [d for d in DATA_DIRS if (root / d).is_dir() and any((root / d).iterdir())]
        if dirs:
            det.note("add_data", [f"{d}={d}" for d in dirs], "data folders next to the entry script")
    return det


def new_app_id() -> str:
    """A fresh GUID for ``app_id`` (keep it fixed after the first release)."""
    return str(uuid.uuid4()).upper()


_CONFIG_ORDER = (
    "entry", "name", "display_name", "version", "publisher", "description", "icon", "app_id", "console",
    "onefile", "license_file", "add_data", "hidden_imports", "languages",
)
_COMMENTS = {
    "entry": "script PyInstaller starts from",
    "name": "exe name",
    "app_id": "keep fixed after the first release (installer upgrades)",
    "console": "false = window app, true = command-line app",
    "onefile": "true = also build a single-file exe",
    "license_file": "shown in the installer",
}


def config_text(values: Dict[str, Any]) -> str:
    """A ``[tool.pyi-doctor]`` table for ``values`` (TOML), with short comments."""
    lines = ["[tool.pyi-doctor]"]
    keys = [k for k in _CONFIG_ORDER if k in values] + sorted(k for k in values if k not in _CONFIG_ORDER)
    for k in keys:
        v = values[k]
        if v is None:
            continue
        if isinstance(v, bool):
            text = "true" if v else "false"
        elif isinstance(v, (int, float)):
            text = repr(v)
        else:
            text = json.dumps(v, ensure_ascii=False)  # JSON strings/arrays are valid TOML here
        line = f"{k} = {text}"
        if k in _COMMENTS:
            line = f"{line:<40} # {_COMMENTS[k]}"
        lines.append(line)
    hints = [
        ("display_name", '# display_name = "My App"            # shown in the installer and Start menu'),
        ("icon", '# icon = "assets/app.ico"'),
        ("hidden_imports", "# hidden_imports = []                 # modules PyInstaller misses"),
        ("installer_name", '# installer_name = "{name}_Setup_v{version}"'),
    ]
    lines += [h for k, h in hints if k not in values]
    return "\n".join(lines) + "\n"
