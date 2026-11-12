"""Download and cache the Windows pieces used by the cross build.

* python.org Windows embeddable package (``python3XY.dll``, stdlib ``.pyc``
  and ``.pyd`` files) for the same Python minor version as the build machine.
* The ``win_amd64`` PyInstaller wheel of the installed PyInstaller version
  (Windows bootloaders and loader scripts).
* ``win_amd64`` / pure-Python wheels of the app's requirements.

Downloads go to ``$PYI_DOCTOR_CACHE`` or ``~/.cache/pyi-doctor``. Files come
from python.org and PyPI over HTTPS; no further signature check is done.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .._proc import tail
from ..errors import BuildError, CrossBuildUnsupported, ToolNotFoundError

Logger = Callable[[str], None]
PY_FTP = "https://www.python.org/ftp/python/"
PLATFORM = "win_amd64"


def cache_dir(override: Optional[Path] = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("PYI_DOCTOR_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pyi-doctor"


def py_tag() -> str:
    return f"{sys.version_info.major}{sys.version_info.minor}"


def _http_ok(url: str, timeout: float = 30) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _download(url: str, dest: Path, timeout: float = 300) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"download failed: {url}: {exc}") from exc
    tmp.replace(dest)
    return dest


def embed_versions(minor: str) -> List[str]:
    """Patch releases of ``minor`` (e.g. ``"3.13"``) listed on python.org, newest first."""
    try:
        with urllib.request.urlopen(PY_FTP, timeout=60) as resp:
            html = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise BuildError(f"cannot list {PY_FTP}: {exc}") from exc
    found = set(re.findall(r'href="(' + re.escape(minor) + r'\.\d+)/"', html))
    return sorted(found, key=lambda v: [int(x) for x in v.split(".")], reverse=True)


def embed_python(version: Optional[str] = None, *, cache: Optional[Path] = None, log: Optional[Logger] = None) -> Path:
    """Return a folder with the extracted embeddable package and ``stdlib/`` (its zip, extracted).

    ``version`` must match the build machine's Python minor version, because
    the app's bytecode is compiled by the build machine's interpreter.
    """
    say = log or (lambda m: None)
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if version and not (version == minor or version.startswith(minor + ".")):
        raise CrossBuildUnsupported(
            [f"python_version {version} differs from the build Python {minor}; run pyi-doctor with that Python version"]
        )
    root = cache_dir(cache) / "embed"
    if version and version.count(".") == 2:
        candidates = [version]
    else:
        local = sorted((p.name for p in root.glob(minor + ".*") if (p / "stdlib").is_dir()),
                       key=lambda v: [int(x) for x in v.split(".")], reverse=True)
        if local:
            return root / local[0]
        candidates = embed_versions(minor)[:8]
    for ver in candidates:
        folder = root / ver
        if (folder / "stdlib").is_dir():
            return folder
        url = f"{PY_FTP}{ver}/python-{ver}-embed-amd64.zip"
        if not _http_ok(url):
            continue  # security-only releases ship no Windows binaries
        say(f"Downloading Windows embeddable Python {ver} ...")
        archive = _download(url, root / f"python-{ver}-embed-amd64.zip")
        tmp = root / (ver + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        stdzip = tmp / f"python{py_tag()}.zip"
        if not stdzip.is_file():
            raise BuildError(f"{archive.name}: python{py_tag()}.zip not found inside")
        with zipfile.ZipFile(stdzip) as zf:
            zf.extractall(tmp / "stdlib")
        shutil.rmtree(folder, ignore_errors=True)
        tmp.replace(folder)
        archive.unlink(missing_ok=True)
        return folder
    raise BuildError(f"no Windows embeddable package found on python.org for Python {minor}")


def _pip(args: Sequence[str], *, cwd: Path, windows_markers: bool = True, timeout: float = 1800) -> subprocess.CompletedProcess:
    import importlib.util

    if importlib.util.find_spec("pip") is None:
        raise ToolNotFoundError(
            "the cross build downloads Windows wheels with pip, which is not installed in this environment "
            "(python -m ensurepip, or uv pip install pip)"
        )
    script = Path(__file__).with_name("_pip_windows.py")
    cmd = [sys.executable, str(script), *args] if windows_markers else [sys.executable, "-m", "pip", *args]
    try:
        return subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                              text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:  # pragma: no cover
        raise ToolNotFoundError(f"cannot run pip: {exc}") from exc


def pyinstaller_windows(*, cache: Optional[Path] = None, log: Optional[Logger] = None) -> Path:
    """Extract the ``win_amd64`` wheel of the installed PyInstaller version; return its ``PyInstaller`` folder."""
    from ..portable import pyinstaller_version

    say = log or (lambda m: None)
    ver = pyinstaller_version()
    if not ver:
        raise ToolNotFoundError("PyInstaller is not installed in this Python environment (pip install pyinstaller)")
    folder = cache_dir(cache) / "pyinstaller-win" / ver
    if (folder / "PyInstaller" / "bootloader" / "Windows-64bit-intel" / "run.exe").is_file():
        return folder / "PyInstaller"
    say(f"Downloading PyInstaller {ver} Windows wheel ...")
    dl = folder.parent / (ver + ".download")
    shutil.rmtree(dl, ignore_errors=True)
    dl.mkdir(parents=True)
    proc = _pip(["download", f"pyinstaller=={ver}", "--no-deps", "--only-binary=:all:", "--platform", PLATFORM,
                 "--dest", str(dl), "--quiet", "--disable-pip-version-check"], cwd=dl, windows_markers=False)
    wheels = list(dl.glob("*.whl"))
    if proc.returncode != 0 or not wheels:
        raise BuildError(f"could not download the PyInstaller {ver} win_amd64 wheel", tail(proc.stdout or ""))
    tmp = folder.parent / (ver + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(wheels[0]) as zf:
        zf.extractall(tmp)
    shutil.rmtree(folder, ignore_errors=True)
    tmp.replace(folder)
    shutil.rmtree(dl, ignore_errors=True)
    return folder / "PyInstaller"


_NO_DIST_RX = re.compile(r"No matching distribution found for ([^\s]+)")
_NO_VER_RX = re.compile(r"Could not find a version that satisfies the requirement ([^\s]+)")


def download_wheels(requirements: Sequence[str], dest: Path, *, log: Optional[Logger] = None) -> List[Path]:
    """Download Windows wheels for ``requirements`` (with dependencies) into ``dest``."""
    say = log or (lambda m: None)
    dest.mkdir(parents=True, exist_ok=True)
    if not requirements:
        return []
    say(f"Downloading win_amd64 wheels for {len(requirements)} requirement(s) ...")
    tag = py_tag()
    args = ["download", "--only-binary=:all:", "--platform", PLATFORM, "--python-version", tag,
            "--implementation", "cp", "--abi", f"cp{tag}", "--abi", "abi3", "--abi", "none",
            "--dest", str(dest), "--disable-pip-version-check", "--progress-bar", "off", *requirements]
    proc = _pip(args, cwd=dest)
    out = proc.stdout or ""
    if proc.returncode != 0:
        missing = _NO_DIST_RX.findall(out) or _NO_VER_RX.findall(out)
        if missing:
            raise CrossBuildUnsupported(
                [f"no Windows (win_amd64) wheel for Python {sys.version_info.major}.{sys.version_info.minor} on PyPI: {m}"
                 for m in dict.fromkeys(missing)]
            )
        raise BuildError("pip download failed", tail(out))
    return sorted(dest.glob("*.whl"))


def install_wheels(wheels: Sequence[Path], site: Path) -> None:
    """Unpack wheels into ``site`` like an installer would (purelib/platlib only)."""
    site.mkdir(parents=True, exist_ok=True)
    for whl in wheels:
        with zipfile.ZipFile(whl) as zf:
            for info in zf.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                parts = name.split("/")
                if len(parts) > 2 and parts[0].endswith(".data"):
                    if parts[1] in ("purelib", "platlib"):
                        rel = "/".join(parts[2:])
                    else:
                        continue  # scripts/headers/data: not part of an importable package
                else:
                    rel = name
                target = (site / rel).resolve()
                if site.resolve() not in target.parents:
                    raise BuildError(f"{whl.name}: unsafe path in wheel: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
