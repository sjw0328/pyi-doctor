"""Portable builds with PyInstaller.

* onedir (default): ``dist/portable/<name>/<name>.exe`` plus its ``_internal``
  folder. This layout is generally reported to trigger fewer antivirus
  heuristics than onefile, and it is what the installer packages.
* onefile (optional): ``dist/onefile/<name>.exe``.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ._proc import run_logged
from .config import Project
from .errors import BuildError, ToolNotFoundError
from .versioninfo import write_version_file


@dataclass
class Artifact:
    """A file produced by a build step."""

    kind: str  # "onedir", "onefile", "installer", "zip", ...
    path: Path  # the executable (or archive) itself
    folder: Optional[Path] = None  # onedir folder, if any

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": str(self.path), "folder": str(self.folder) if self.folder else None}


def pyinstaller_available() -> bool:
    # Presence check only: PyInstaller is run as a separate process and its
    # code is never imported into pyi-doctor.
    import importlib.util

    return importlib.util.find_spec("PyInstaller") is not None


def pyinstaller_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("pyinstaller")
    except Exception:
        return None


def build_command(project: Project, *, onefile: bool, version_file: Optional[Path] = None) -> List[str]:
    """Return the PyInstaller command line for one build mode."""
    mode = "onefile" if onefile else "onedir"
    dist = project.output_path / ("onefile" if onefile else "portable")
    work = project.work_path / mode
    cmd: List[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(project.entry_path),
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--name",
        project.name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--onefile" if onefile else "--onedir",
        "--console" if project.console else "--windowed",
    ]
    if not project.upx:
        cmd.append("--noupx")
    if project.icon_path:
        cmd += ["--icon", str(project.icon_path)]
    if version_file is not None:
        cmd += ["--version-file", str(version_file)]
    for mod in project.hidden_imports:
        cmd += ["--hidden-import", mod]
    for mod in project.excludes:
        cmd += ["--exclude-module", mod]
    for src, dest in project.add_data:
        cmd += ["--add-data", f"{project.resolve(src)}{os.pathsep}{dest}"]
    cmd += list(project.pyinstaller_args)
    return cmd


def expected_exe(project: Project, *, onefile: bool) -> Path:
    if onefile:
        return project.output_path / "onefile" / project.exe_name
    return project.output_path / "portable" / project.name / project.exe_name


def build(project: Project, *, onefile: bool = False, timeout: Optional[float] = 3600) -> Artifact:
    """Run PyInstaller and return the produced artifact."""
    if not pyinstaller_available():
        raise ToolNotFoundError("PyInstaller is not installed in this Python environment (pip install pyinstaller)")
    project.check_files()
    mode = "onefile" if onefile else "onedir"
    work = project.work_path / mode
    version_file = None
    if sys.platform == "win32":
        version_file = write_version_file(project, work / "version_info.txt")
    exe = expected_exe(project, onefile=onefile)
    target = exe if onefile else exe.parent
    if target.exists():  # PyInstaller --noconfirm also removes it; do it first for clear errors
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    cmd = build_command(project, onefile=onefile, version_file=version_file)
    run_logged(cmd, cwd=Path(project.root), log_path=work / "pyinstaller.log", what=f"PyInstaller ({mode})", timeout=timeout)
    if not exe.is_file():
        raise BuildError(f"PyInstaller finished but {exe} was not created", log_path=str(work / "pyinstaller.log"))
    return Artifact(kind=mode, path=exe, folder=None if onefile else exe.parent)
