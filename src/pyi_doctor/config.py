"""Project configuration.

A :class:`Project` describes one application to build. It can be created in
code or loaded from the ``[tool.pyi-doctor]`` table of ``pyproject.toml``.
All relative paths are resolved against :attr:`Project.root`.
"""

from __future__ import annotations

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
    smoke_timeout: float = 15.0
    output_dir: str = "dist"
    work_dir: str = "build"
    root: Union[str, Path] = "."

    def __post_init__(self) -> None:
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
            raise ConfigError(f"unknown installer languages: {unknown}; known: {sorted(KNOWN_LANGUAGES)}")
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
    def _parse_app_id(value: str) -> uuid.UUID:
        try:
            return uuid.UUID(value.strip().strip("{}"))
        except ValueError as exc:
            raise ConfigError(f"app_id {value!r} is not a GUID") from exc

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
            return str(self._parse_app_id(self.app_id)).upper()
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
    def display_publisher(self) -> str:
        return self.publisher or self.name

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_dict(cls, data: Dict[str, Any], root: Union[str, Path] = ".") -> "Project":
        allowed = {f.name for f in fields(cls)}
        clean: Dict[str, Any] = {}
        for key, value in data.items():
            k = key.replace("-", "_")
            if k not in allowed:
                raise ConfigError(f"unknown setting {key!r}")
            clean[k] = value
        clean.setdefault("root", root)
        if "entry" not in clean or "name" not in clean:
            raise ConfigError("settings 'entry' and 'name' are required")
        try:
            return cls(**clean)
        except TypeError as exc:  # wrong value types
            raise ConfigError(str(exc)) from exc

    @classmethod
    def from_pyproject(cls, path: Union[str, Path] = "pyproject.toml", **overrides: Any) -> "Project":
        """Load ``[tool.pyi-doctor]``; ``name``/``version``/``description`` fall back to ``[project]``."""
        path = Path(path).resolve()
        if not path.is_file():
            raise ConfigError(f"{path} not found")
        data = _load_toml(path)
        tool = dict(data.get("tool", {}).get("pyi-doctor", {}))
        proj = data.get("project", {})
        for key in ("name", "version", "description"):
            if key not in tool and isinstance(proj.get(key), str):
                tool[key] = proj[key]
        if "publisher" not in tool:
            authors = proj.get("authors") or []
            if authors and isinstance(authors[0], dict) and authors[0].get("name"):
                tool["publisher"] = authors[0]["name"]
        tool.update({k: v for k, v in overrides.items() if v is not None})
        return cls.from_dict(tool, root=path.parent)


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
version = "1.0.0"
publisher = "Your Name"
description = "What the app does"
# icon = "assets/app.ico"
# app_id = "PUT-A-FIXED-GUID-HERE"   # keep it fixed after the first release
console = false              # true for command-line apps
onefile = false              # true = also build a single-file exe
# license_file = "LICENSE"   # shown in the installer
# add_data = ["assets=assets"]
# hidden_imports = []
languages = ["english", "korean"]
"""


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
