"""Project configuration.

A :class:`Project` describes one application to build. It can be created in
code or loaded from the ``[tool.pyi-doctor]`` table of ``pyproject.toml``.
All relative paths are resolved against :attr:`Project.root`.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .errors import ConfigError

_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([-+.]?[A-Za-z][A-Za-z0-9.+-]*)?$")
_NAME_RE = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]+$')
_APP_ID_NAMESPACE = uuid.UUID("6f1c3e2a-9b7d-4c1e-8a55-3d2f0b9e7a10")

PRIVILEGES = ("lowest", "admin")
KNOWN_LANGUAGES = {
    # name -> Inno Setup messages file (relative to the compiler directory)
    "english": "Default.isl",
    "korean": "Languages\\Korean.isl",
    "japanese": "Languages\\Japanese.isl",
    "chinesesimplified": "Languages\\ChineseSimplified.isl",
    "german": "Languages\\German.isl",
    "french": "Languages\\French.isl",
    "spanish": "Languages\\Spanish.isl",
    "portuguese": "Languages\\Portuguese.isl",
    "brazilianportuguese": "Languages\\BrazilianPortuguese.isl",
    "russian": "Languages\\Russian.isl",
    "italian": "Languages\\Italian.isl",
    "turkish": "Languages\\Turkish.isl",
    "polish": "Languages\\Polish.isl",
    "dutch": "Languages\\Dutch.isl",
    "ukrainian": "Languages\\Ukrainian.isl",
}

DataPair = Tuple[str, str]


def _normalize_data(items: Sequence[Union[str, Sequence[str]]]) -> List[DataPair]:
    pairs: List[DataPair] = []
    for item in items:
        if isinstance(item, str):
            # Accept "src;dest", "src:dest" (but not a Windows drive colon) or "src=dest".
            for sep in ("=", ";"):
                if sep in item:
                    src, dest = item.rsplit(sep, 1)
                    break
            else:
                idx = item.rfind(":")
                if idx <= 1:  # no separator, or only a drive letter like C:
                    raise ConfigError(
                        f"add_data entry {item!r} needs a destination, e.g. 'assets=assets'"
                    )
                src, dest = item[:idx], item[idx + 1 :]
        else:
            seq = list(item)
            if len(seq) != 2:
                raise ConfigError(f"add_data entry {item!r} must be [source, destination]")
            src, dest = seq
        src, dest = str(src).strip(), str(dest).strip() or "."
        if not src:
            raise ConfigError(f"add_data entry {item!r} has an empty source")
        pairs.append((src, dest))
    return pairs


@dataclass
class Project:
    """Settings for one application.

    Only ``entry`` and ``name`` are required.
    """

    entry: str
    name: str
    version: str = "0.1.0"
    display_name: Optional[str] = None  # shown in the installer, Start menu and Apps list
    installer_name: Optional[str] = None  # installer file name template, e.g. "{name}_Setup_v{version}"
    publisher: str = ""
    description: str = ""
    copyright: str = ""
    website: str = ""
    icon: Optional[str] = None
    app_id: Optional[str] = None
    console: bool = False
    onefile: bool = False
    upx: bool = False
    hidden_imports: List[str] = field(default_factory=list)
    excludes: List[str] = field(default_factory=list)
    add_data: List[Any] = field(default_factory=list)
    pyinstaller_args: List[str] = field(default_factory=list)
    license_file: Optional[str] = None
    privileges: str = "lowest"
    languages: List[str] = field(default_factory=lambda: ["english", "korean"])
    smoke_args: List[str] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)  # used by the Windows cross build
    smoke_timeout: float = 15.0
    output_dir: str = "dist"
    work_dir: str = "build"
    root: Union[str, Path] = "."

    def __post_init__(self) -> None:
        self.detected: List[str] = []  # settings guessed by from_folder()/from_pyproject(), for display
        self.root = Path(self.root).resolve()
        self.add_data = _normalize_data(self.add_data)
        self.languages = [str(x).strip().lower() for x in self.languages if str(x).strip()]
        self.validate()

    # ------------------------------------------------------------------ checks
    def validate(self) -> None:
        if not self.name or not _NAME_RE.match(self.name) or self.name != self.name.strip():
            raise ConfigError(f"name {self.name!r} is empty or contains characters not allowed in file names")
        if not _VERSION_RE.match(self.version):
            raise ConfigError(f"version {self.version!r} must start with numbers like 1.0.0")
        if self.privileges not in PRIVILEGES:
            raise ConfigError(f"privileges must be one of {PRIVILEGES}, got {self.privileges!r}")
        if self.smoke_timeout <= 0:
            raise ConfigError("smoke_timeout must be positive")
        unknown = [lang for lang in self.languages if lang not in KNOWN_LANGUAGES]
        if unknown:
            hints = "".join(_did_you_mean(lang, KNOWN_LANGUAGES) for lang in unknown)
            raise ConfigError(f"unknown installer languages: {unknown}{hints}; known: {sorted(KNOWN_LANGUAGES)}")
        if self.display_name is not None:
            dn = self.display_name
            if not dn.strip() or not _NAME_RE.match(dn) or dn != dn.strip():
                raise ConfigError(
                    f"display_name {dn!r} is empty or contains characters not allowed in folder names"
                )
        if self.installer_name is not None:
            self.installer_basename  # raises ConfigError when the template is invalid
        if self.app_id is not None:
            self._parse_app_id(self.app_id)

    def check_files(self) -> None:
        """Verify that referenced files exist. Called right before building."""
        if not self.entry_path.is_file():
            raise ConfigError(f"entry script not found: {self.entry_path}")
        if self.icon and not self.icon_path.is_file():
            raise ConfigError(f"icon not found: {self.icon_path}")
        if self.license_file and not self.license_path.is_file():
            raise ConfigError(f"license_file not found: {self.license_path}")
        for src, _ in self.add_data:
            if not self.resolve(src).exists():
                raise ConfigError(f"add_data source not found: {self.resolve(src)}")

    @staticmethod
    def _parse_app_id(value: str) -> Union[uuid.UUID, str]:
        """GUID (with or without braces) or, as Inno Setup allows, any other fixed text."""
        text = str(value).strip()
        try:
            return uuid.UUID(text.strip("{}"))
        except ValueError:
            pass
        if not text or any(ord(ch) < 32 for ch in text) or "{" in text or "}" in text:
            raise ConfigError(f"app_id {value!r} must be a GUID or text without braces/control characters")
        return text

    # ------------------------------------------------------------------ paths
    def resolve(self, path: Union[str, Path]) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (Path(self.root) / p).resolve()

    @property
    def entry_path(self) -> Path:
        return self.resolve(self.entry)

    @property
    def icon_path(self) -> Optional[Path]:
        return self.resolve(self.icon) if self.icon else None

    @property
    def license_path(self) -> Optional[Path]:
        return self.resolve(self.license_file) if self.license_file else None

    @property
    def output_path(self) -> Path:
        return self.resolve(self.output_dir)

    @property
    def work_path(self) -> Path:
        return self.resolve(self.work_dir)

    @property
    def release_path(self) -> Path:
        return self.output_path / "release"

    @property
    def exe_name(self) -> str:
        return self.name + (".exe" if sys.platform == "win32" else "")

    # ------------------------------------------------------------------ derived
    @property
    def effective_app_id(self) -> str:
        """GUID used by the installer to recognise upgrades.

        If ``app_id`` is not set, a stable GUID is derived from publisher and
        name. Changing either value then changes the GUID, so set ``app_id``
        explicitly once the application has been released.
        """
        if self.app_id:
            parsed = self._parse_app_id(self.app_id)
            return str(parsed).upper() if isinstance(parsed, uuid.UUID) else parsed
        seed = f"{self.publisher.strip().lower()}|{self.name.strip().lower()}"
        return str(uuid.uuid5(_APP_ID_NAMESPACE, seed)).upper()

    @property
    def windows_version(self) -> Tuple[int, int, int, int]:
        """Four numeric parts for Windows version resources (1.2.3rc1 -> 1,2,3,0)."""
        nums = re.match(r"^(\d+(?:\.\d+){0,3})", self.version)
        parts = [int(x) for x in nums.group(1).split(".")] if nums else [0]
        parts = [min(p, 65535) for p in parts]
        while len(parts) < 4:
            parts.append(0)
        return tuple(parts[:4])  # type: ignore[return-value]

    @property
    def app_id_is_guid(self) -> bool:
        if not self.app_id:
            return True
        return isinstance(self._parse_app_id(self.app_id), uuid.UUID)

    @property
    def display_publisher(self) -> str:
        return self.publisher or self.name

    @property
    def app_display_name(self) -> str:
        """Name shown to users (installer, shortcuts, Apps list); defaults to ``name``."""
        return self.display_name or self.name

    @property
    def installer_basename(self) -> str:
        """Installer file name without ``.exe``; ``installer_name`` may use {name}, {version}, {display_name}."""
        if self.installer_name is None:
            return f"{self.name}-{self.version}-setup"
        try:
            out = self.installer_name.format(
                name=self.name, version=self.version, display_name=self.app_display_name
            )
        except (KeyError, IndexError, ValueError) as exc:
            raise ConfigError(
                f"installer_name {self.installer_name!r}: use only {{name}}, {{version}}, {{display_name}}"
            ) from exc
        if out.lower().endswith(".exe"):
            out = out[:-4]
        if not out or not _NAME_RE.match(out) or out != out.strip():
            raise ConfigError(f"installer_name gives {out!r}, which is not a valid file name")
        return out

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_dict(cls, data: Dict[str, Any], root: Union[str, Path] = ".") -> "Project":
        allowed = {f.name for f in fields(cls)}
        clean: Dict[str, Any] = {}
        for key, value in data.items():
            k = key.replace("-", "_")
            if k not in allowed:
                raise ConfigError(f"unknown setting {key!r}{_did_you_mean(k, allowed - {'root'})}")
            clean[k] = value
        clean.setdefault("root", root)
        if "entry" not in clean or "name" not in clean:
            missing = " and ".join(repr(k) for k in ("entry", "name") if k not in clean)
            raise ConfigError(f"setting {missing} is required (or run 'pyi-doctor init --write' to fill them in)")
        try:
            return cls(**clean)
        except TypeError as exc:  # wrong value types
            raise ConfigError(str(exc)) from exc

    @classmethod
    def from_pyproject(cls, path: Union[str, Path] = "pyproject.toml", **overrides: Any) -> "Project":
        """Load ``[tool.pyi-doctor]``; ``name``/``version``/``description`` fall back to ``[project]``.

        A missing ``entry`` (and ``name``) is guessed from the project folder
        (see :mod:`pyi_doctor.autodetect`).
        """
        path = Path(path).resolve()
        if not path.is_file():
            raise ConfigError(f"{path} not found")
        tool = _merge_project_table(_load_toml(path))
        tool.update({k: v for k, v in overrides.items() if v is not None})
        detected: List[str] = []
        if "entry" not in tool or "name" not in tool:
            from .autodetect import detect

            det = detect(path.parent)
            for key in ("entry", "name"):
                if key not in tool and key in det.values:
                    tool[key] = det.values[key]
                    detected += [n for n in det.notes if n.startswith(key + " ")]
            if "entry" not in tool:
                raise ConfigError(_no_entry_message(path.parent))
        project = cls.from_dict(tool, root=path.parent)
        project.detected = detected
        return project

    @classmethod
    def from_folder(cls, folder: Union[str, Path] = ".", *, entry: Optional[Union[str, Path]] = None,
                    **overrides: Any) -> "Project":
        """Guess every setting from ``folder`` (entry script, name, icon, console, data folders, ...).

        ``[project]`` values in a ``pyproject.toml`` are used when present. The
        guesses are listed in :attr:`Project.detected`.
        """
        from .autodetect import detect

        folder = Path(folder).resolve()
        det = detect(folder, entry=entry)
        cfg = folder / "pyproject.toml"
        tool = _merge_project_table(_load_toml(cfg)) if cfg.is_file() else {}
        for k, v in det.values.items():
            tool.setdefault(k, v)
        tool.update({k: v for k, v in overrides.items() if v is not None})
        if "entry" not in tool:
            raise ConfigError(_no_entry_message(folder))
        project = cls.from_dict(tool, root=folder)
        project.detected = list(det.notes)
        return project

    @classmethod
    def load(cls, path: Union[str, Path] = ".", **overrides: Any) -> "Project":
        """Load whatever ``path`` is: a ``pyproject.toml``, a project folder or an entry script.

        With ``[tool.pyi-doctor]`` present it is used (:meth:`from_pyproject`);
        otherwise settings are guessed (:meth:`from_folder`).
        """
        p = Path(path).expanduser().resolve()
        overrides = {k: v for k, v in overrides.items() if v is not None}
        if p.suffix.lower() in (".py", ".pyw"):
            if not p.is_file():
                raise ConfigError(f"entry script not found: {p}")
            overrides["entry"] = str(p)
            p = p.parent
        if p.is_file():
            cfg, folder = p, p.parent
        elif p.is_dir():
            cfg, folder = p / "pyproject.toml", p
        else:
            raise ConfigError(f"{p} not found")
        if cfg.is_file() and "pyi-doctor" in _load_toml(cfg).get("tool", {}):
            return cls.from_pyproject(cfg, **overrides)
        entry = overrides.pop("entry", None)
        return cls.from_folder(folder, entry=entry, **overrides)


def _did_you_mean(word: str, options) -> str:
    match = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return f" (did you mean {match[0]!r}?)" if match else ""


def _no_entry_message(folder: Path) -> str:
    return (f"no entry script found in {folder}; pass it (e.g. 'pyi-doctor build main.py') "
            f"or set entry in [tool.pyi-doctor]")


def _merge_project_table(data: Dict[str, Any]) -> Dict[str, Any]:
    """``[tool.pyi-doctor]`` with ``[project]`` fallbacks for name, version, description, requirements, publisher."""
    tool = dict(data.get("tool", {}).get("pyi-doctor", {}))
    proj = data.get("project", {})
    for key in ("name", "version", "description"):
        if key not in tool and isinstance(proj.get(key), str):
            tool[key] = proj[key]
            if key == "name" and not _NAME_RE.match(proj[key]):
                from .autodetect import safe_name

                tool[key] = safe_name(proj[key])
    if "requirements" not in tool and isinstance(proj.get("dependencies"), list):
        tool["requirements"] = list(proj["dependencies"])
    if "publisher" not in tool:
        authors = proj.get("authors") or []
        if authors and isinstance(authors[0], dict) and authors[0].get("name"):
            tool["publisher"] = authors[0]["name"]
    return tool


def _load_toml(path: Path) -> Dict[str, Any]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # Python < 3.11
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ConfigError("reading pyproject.toml on Python < 3.11 needs 'tomli'") from exc
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


SAMPLE_CONFIG = """\
[tool.pyi-doctor]
entry = "main.py"            # script PyInstaller starts from
name = "MyApp"               # exe and installer name
# display_name = "My App"    # shown in the installer and Start menu (default: name)
# installer_name = "{name}_Setup_v{version}"   # default: {name}-{version}-setup
version = "1.0.0"
publisher = "Your Name"
description = "What the app does"
# icon = "assets/app.ico"
# app_id = "PUT-A-FIXED-GUID-HERE"   # keep it fixed after the first release (an existing non-GUID AppId also works)
console = false              # true for command-line apps
onefile = false              # true = also build a single-file exe
# license_file = "LICENSE"   # shown in the installer
# add_data = ["assets=assets"]
# hidden_imports = []
# requirements = ["requests"]  # cross build only; defaults to [project] dependencies
languages = ["english", "korean"]
"""


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
