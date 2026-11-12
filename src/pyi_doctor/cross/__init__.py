"""Experimental: build a PyInstaller-format Windows exe on Linux or macOS.

Scope (anything else stops with :class:`~pyi_doctor.errors.CrossBuildUnsupported`):

* 64-bit Windows (x64) only.
* The build machine's Python minor version is used for the app (bytecode is
  compiled by this interpreter); the matching python.org embeddable package
  supplies ``python3XY.dll`` and the stdlib.
* Dependencies must be pure-Python wheels or ``win_amd64`` wheels on PyPI.
* PyInstaller hooks must be answerable from the unpacked files (see
  :mod:`pyi_doctor.cross.hooks`); tkinter is not available.

Run-time behaviour is not checked here; start the exe on Windows (for
example in GitHub Actions ``windows-latest``) before distributing it.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from ..config import Project
from ..errors import CrossBuildUnsupported
from ..portable import Artifact
from . import analysis, assemble, fetch, rsrc

Logger = Callable[[str], None]
TARGET = "win"


def requirement_args(reqs: Sequence[str], root: Path) -> List[str]:
    """Turn requirement strings into pip arguments (``-r file`` entries are split)."""
    args: List[str] = []
    for r in reqs:
        r = str(r).strip()
        if not r:
            continue
        if r.startswith("-r ") or r.startswith("--requirement "):
            path = r.split(None, 1)[1].strip()
            p = Path(path)
            args += ["-r", str(p if p.is_absolute() else (root / p))]
        else:
            args.append(r)
    return args


@dataclass
class WindowsBuild:
    """Prepared cross build: materials downloaded and imports analysed once, then built per mode."""

    project: Project
    embed_dir: Path
    pyi_dir: Path
    site_dir: Path
    collected: analysis.Collected
    work: Path
    log: Logger
    artifacts: List[Artifact] = field(default_factory=list)

    @property
    def python_version(self) -> str:
        return self.embed_dir.name

    @property
    def pyinstaller_version(self) -> str:
        return self.pyi_dir.parent.name

    def _resources(self) -> dict:
        p = self.project
        res = {}
        icon = p.icon_path or (self.pyi_dir / "bootloader" / "images" /
                               ("icon-console.ico" if p.console else "icon-windowed.ico"))
        res.update(rsrc.icon_resources(icon))
        from ..versioninfo import version_strings

        res[(rsrc.RT_VERSION, 1)] = rsrc.version_resource(p.windows_version, version_strings(p))
        res[(rsrc.RT_MANIFEST, 1)] = rsrc.DEFAULT_MANIFEST
        return res

    def build(self, *, onefile: bool = False) -> Artifact:
        p = self.project
        boot_name = "run.exe" if p.console else "runw.exe"
        boot = (self.pyi_dir / "bootloader" / "Windows-64bit-intel" / boot_name).read_bytes()
        image = rsrc.add_resources(boot, self._resources(), timestamp=rsrc.build_timestamp())
        pyz = assemble.write_pyz(
            self.work / "PYZ.pyz",
            [m for name, m in self.collected.pure.items()
             if name not in self.collected.base and name not in assemble.BOOTSTRAP_NAMES],
        )
        base = assemble.write_base_library(
            self.work / "base_library.zip", [self.collected.pure[n] for n in self.collected.base]
        )
        exe_name = p.name + ".exe"
        if onefile:
            exe = p.output_path / "onefile" / exe_name
            if exe.exists():
                exe.unlink()
            assemble.write_exe(exe, bootloader_image=image, pyi_dir=self.pyi_dir, collected=self.collected,
                               pyz=pyz, onefile=True, base_library=base)
            folder = None
            kind = "onefile"
        else:
            folder = p.output_path / "portable" / p.name
            if folder.exists():
                shutil.rmtree(folder)
            exe = folder / exe_name
            assemble.write_exe(exe, bootloader_image=image, pyi_dir=self.pyi_dir, collected=self.collected,
                               pyz=pyz, onefile=False, base_library=None,
                               options=[f"pyi-contents-directory {assemble.CONTENTS_DIR}"])
            assemble.write_onedir_files(folder, self.collected, base)
            kind = "onedir"
        rsrc.update_checksum(exe)
        art = Artifact(kind=kind, path=exe, folder=folder)
        self.artifacts.append(art)
        self.log(f"Windows {kind} exe written: {exe}")
        return art

    def info(self) -> dict:
        c = self.collected
        return {
            "target": "windows-x64",
            "python": self.python_version,
            "pyinstaller": self.pyinstaller_version,
            "modules": len(c.pure),
            "binaries": sorted(c.binaries),
            "data_files": len(c.datas),
            "distributions": [d.name[: -len(".dist-info")] for d in c.dist_infos],
            "warnings": c.warnings,
        }

    def licenses(self):
        """License info of bundled third-party distributions (from the Windows wheels)."""
        from importlib.metadata import PathDistribution

        from ..licenses import BUILD_ONLY, _norm, package_license

        skip = {_norm(x) for x in BUILD_ONLY}
        out = []
        for d in self.collected.dist_infos:
            lic = package_license(PathDistribution(d))
            if _norm(lic.name) not in skip:
                out.append(lic)
        out.sort(key=lambda x: x.name.lower())
        return out


def prepare_windows(
    project: Project,
    *,
    requirements: Optional[Sequence[str]] = None,
    python_version: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    log: Optional[Logger] = None,
) -> WindowsBuild:
    """Download the Windows materials and analyse the app. Raises ``CrossBuildUnsupported`` early."""
    say: Logger = log or (lambda m: None)
    project.check_files()
    problems = []
    if project.pyinstaller_args:
        problems.append("pyinstaller_args are not supported by the cross build")
    if project.icon_path and project.icon_path.suffix.lower() != ".ico":
        problems.append(f"icon must be an .ico file for the cross build: {project.icon_path.name}")
    if problems:
        raise CrossBuildUnsupported(problems)
    if project.upx:
        say("note: upx is ignored by the cross build")

    embed = fetch.embed_python(python_version, cache=cache_dir, log=say)
    pyi = fetch.pyinstaller_windows(cache=cache_dir, log=say)
    work = project.work_path / "cross-win"
    site = work / "site"
    wheels_dir = work / "wheels"
    for d in (site, wheels_dir):
        if d.exists():
            shutil.rmtree(d)
    reqs = list(project.requirements if requirements is None else requirements)
    wheels = fetch.download_wheels(requirement_args(reqs, Path(project.root)), wheels_dir, log=say)
    fetch.install_wheels(wheels, site)
    collected = analysis.analyze(project, embed_dir=embed, pyi_dir=pyi, site_dir=site, log=say)
    for w in collected.warnings:
        say(f"warning: {w}")
    build = WindowsBuild(project, embed, pyi, site, collected, work, say)
    (work / "cross-build.json").write_text(json.dumps(build.info(), indent=2), encoding="utf-8")
    return build


def build_windows(
    project: Project,
    *,
    onefile: bool = False,
    requirements: Optional[Sequence[str]] = None,
    python_version: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    log: Optional[Logger] = None,
) -> Artifact:
    """Build a Windows x64 onedir (default) or onefile exe from Linux/macOS. Experimental, AS IS."""
    b = prepare_windows(project, requirements=requirements, python_version=python_version, cache_dir=cache_dir, log=log)
    return b.build(onefile=onefile)


__all__ = ["build_windows", "prepare_windows", "WindowsBuild", "CrossBuildUnsupported", "TARGET"]
