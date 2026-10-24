"""Collect release files, write SHA256 checksums, optionally upload to GitHub."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import Project
from .antivirus import sha256_file
from .errors import BuildError, ToolNotFoundError
from .portable import Artifact


def platform_tag() -> str:
    if sys.platform == "win32":
        return "win64" if sys.maxsize > 2**32 else "win32"
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def zip_folder(folder: Path, dest: Path) -> Path:
    """Zip ``folder`` so that it extracts to a folder with the same name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(folder.rglob("*")):
            arc = Path(folder.name) / path.relative_to(folder)
            if path.is_symlink():
                info = zipfile.ZipInfo(str(arc).replace("\\", "/"))
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                zf.writestr(info, os.readlink(path))
            elif path.is_file():
                zf.write(path, str(arc))
    return dest


def collect(project: Project, artifacts: Iterable[Artifact], extra_files: Iterable[Path] = ()) -> List[Path]:
    """Copy release files to ``<output>/release`` and write ``SHA256SUMS.txt``."""
    rel = project.release_path
    rel.mkdir(parents=True, exist_ok=True)
    tag = platform_tag()
    files: List[Path] = []
    for a in artifacts:
        if a.kind == "onedir" and a.folder:
            files.append(zip_folder(a.folder, rel / f"{project.name}-{project.version}-portable-{tag}.zip"))
        elif a.kind == "onefile":
            suffix = a.path.suffix
            dest = rel / f"{project.name}-{project.version}-{tag}{suffix}"
            shutil.copy2(a.path, dest)
            files.append(dest)
        elif a.kind == "installer":
            dest = rel / a.path.name
            shutil.copy2(a.path, dest)
            files.append(dest)
    for f in extra_files:
        f = Path(f)
        if f.is_file():
            dest = rel / f.name
            if f.resolve() != dest.resolve():
                shutil.copy2(f, dest)
            files.append(dest)
    write_checksums(rel / "SHA256SUMS.txt", files)
    return files


def write_checksums(path: Path, files: Iterable[Path]) -> Path:
    lines = [f"{sha256_file(f)}  {f.name}" for f in files if f.is_file() and f.name != path.name]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def file_entries(files: Iterable[Path]) -> List[Dict[str, object]]:
    return [{"name": f.name, "size": f.stat().st_size, "sha256": sha256_file(f)} for f in files if f.is_file()]


def upload_github(tag: str, files: Iterable[Path], *, repo: Optional[str] = None, notes_file: Optional[Path] = None) -> str:
    """Upload ``files`` to GitHub release ``tag`` with the GitHub CLI (``gh``).

    The release is created as a draft if it does not exist. Authentication is
    handled entirely by ``gh``; pyi-doctor does not read or store tokens.
    """
    gh = shutil.which("gh")
    if gh is None:
        raise ToolNotFoundError("GitHub CLI 'gh' not found; install it and run 'gh auth login'")
    base = [gh, "release"]
    repo_args = ["--repo", repo] if repo else []
    exists = subprocess.run(base + ["view", tag] + repo_args, capture_output=True).returncode == 0
    if not exists:
        create = base + ["create", tag, "--draft", "--title", tag] + repo_args
        create += ["--notes-file", str(notes_file)] if notes_file else ["--notes", ""]
        proc = subprocess.run(create, capture_output=True, text=True)
        if proc.returncode != 0:
            raise BuildError(f"gh release create failed: {proc.stderr.strip()}")
    proc = subprocess.run(base + ["upload", tag, *[str(f) for f in files], "--clobber"] + repo_args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BuildError(f"gh release upload failed: {proc.stderr.strip()}")
    return "created draft and uploaded" if not exists else "uploaded"
