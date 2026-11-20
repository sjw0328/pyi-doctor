"""Setup wizard (installer) generation with Inno Setup.

pyi-doctor writes an Inno Setup script (``.iss``) from the project settings
and compiles it with ``ISCC.exe``. Inno Setup is not bundled. When no
installed copy is found on Windows, :func:`ensure_iscc` downloads the official
Inno Setup installer from the jrsoftware/issrc GitHub release, checks its
SHA-256 against the value pinned below and runs it in portable, current-user
mode into the pyi-doctor cache (no administrator rights, no uninstall entry).
Inno Setup is third-party software under its own license (https://jrsoftware.org/).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from . import __version__
from ._proc import run_logged
from .config import KNOWN_LANGUAGES, Project
from .errors import BuildError, ToolNotFoundError
from .portable import Artifact

ISCC_ENV = "PYI_DOCTOR_ISCC"
NO_DOWNLOAD_ENV = "PYI_DOCTOR_NO_INNO_DOWNLOAD"

# Pinned Inno Setup release used when none is installed (official GitHub release assets).
INNO_VERSION = "7.1.1"
INNO_RELEASE_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_1/"
INNO_DOWNLOADS = {
    "x64": ("innosetup-7.1.1-x64.exe", "8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0"),
    "x86": ("innosetup-7.1.1-x86.exe", "03446e46f5da932e408881168c01afc59cc667cb5bc927d839c5526ace5cc0d3"),
}

Logger = Callable[[str], None]


def _cache_root() -> Path:
    env = os.environ.get("PYI_DOCTOR_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pyi-doctor"


def windows_arch() -> str:
    """``"x64"`` on 64-bit Windows (also ARM64, which runs x64 programs), else ``"x86"``."""
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    if arch in ("AMD64", "ARM64", "X64"):
        return "x64"
    if arch:
        return "x86"
    return "x64" if _is_64bit() else "x86"


def inno_cache_dir(arch: Optional[str] = None, cache: Optional[Path] = None) -> Path:
    """Folder that :func:`ensure_iscc` installs the pinned Inno Setup into."""
    return Path(cache or _cache_root()) / "inno-setup" / f"{INNO_VERSION}-{arch or windows_arch()}"


def find_iscc() -> Optional[Path]:
    """Locate ``ISCC.exe``: env var, PATH, the default install folders, then the pyi-doctor cache."""
    env = os.environ.get(ISCC_ENV)
    if env:
        p = Path(env)
        return p if p.is_file() else None
    for name in ("ISCC.exe", "iscc.exe", "ISCC", "iscc"):
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    for sub_name in ("Inno Setup 7", "Inno Setup 6"):
        for root in filter(None, roots):
            for sub in (sub_name, os.path.join("Programs", sub_name)):
                cand = Path(root) / sub / "ISCC.exe"
                if cand.is_file():
                    return cand
    for arch in ("x64", "x86"):
        cand = inno_cache_dir(arch) / "ISCC.exe"
        if cand.is_file():
            return cand
    return None


def auto_download_allowed() -> bool:
    """False when ``PYI_DOCTOR_NO_INNO_DOWNLOAD`` is set to a non-empty value other than ``0``."""
    return os.environ.get(NO_DOWNLOAD_ENV, "").strip() in ("", "0")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_inno_setup(arch: Optional[str] = None, *, cache: Optional[Path] = None,
                        log: Optional[Logger] = None, timeout: float = 300) -> Path:
    """Download the pinned Inno Setup installer and check its SHA-256; returns the file path."""
    say = log or (lambda m: None)
    arch = arch or windows_arch()
    fname, digest = INNO_DOWNLOADS[arch]
    dest = Path(cache or _cache_root()) / "downloads" / fname
    if dest.is_file() and _sha256(dest) == digest:
        return dest
    url = INNO_RELEASE_URL + fname
    say(f"downloading Inno Setup {INNO_VERSION} ({arch}) from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise ToolNotFoundError(f"Inno Setup download failed ({url}): {exc}") from exc
    got = _sha256(tmp)
    if got != digest:
        tmp.unlink(missing_ok=True)
        raise BuildError(f"Inno Setup download {fname}: SHA-256 {got} does not match the pinned {digest}; file removed")
    tmp.replace(dest)
    return dest


def ensure_iscc(*, download: Optional[bool] = None, cache: Optional[Path] = None,
                log: Optional[Logger] = None, timeout: float = 600) -> Path:
    """Return ``ISCC.exe``; on Windows, download and unpack Inno Setup first when none is found.

    The pinned installer (:data:`INNO_VERSION`) runs with ``/VERYSILENT
    /CURRENTUSER /PORTABLE=1`` into :func:`inno_cache_dir`: no administrator
    rights, no uninstall entry, no file associations or shortcuts. Set
    ``PYI_DOCTOR_NO_INNO_DOWNLOAD=1`` or pass ``download=False`` to turn this off.
    """
    found = find_iscc() if cache is None else None
    if found:
        return found
    say = log or (lambda m: None)
    if download is None:
        download = auto_download_allowed()
    target = inno_cache_dir(cache=cache)
    if (target / "ISCC.exe").is_file():
        return target / "ISCC.exe"
    if sys.platform != "win32":
        raise ToolNotFoundError("Inno Setup runs on Windows only; compile the .iss on Windows")
    if not download:
        raise ToolNotFoundError(
            "Inno Setup compiler (ISCC.exe) not found and automatic download is off. Install Inno Setup 6.3+ or set "
            + ISCC_ENV
        )
    setup = download_inno_setup(cache=cache, log=say)
    say(f"unpacking Inno Setup into {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    log_file = target.parent / f"install-{target.name}.log"
    cmd = [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER",
           "/PORTABLE=1", "/NOICONS", f"/DIR={target}", f"/LOG={log_file}"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"Inno Setup unpack failed: {exc}", log_path=str(log_file)) from exc
    iscc = target / "ISCC.exe"
    if proc.returncode != 0 or not iscc.is_file():
        raise BuildError(f"Inno Setup unpack failed (exit code {proc.returncode}); see {log_file}",
                         log_path=str(log_file))
    say(f"Inno Setup ready: {iscc}")
    return iscc


def available_languages(project: Project, iscc: Optional[Path]) -> List[str]:
    """Languages whose message files exist next to ``iscc`` (all requested if unknown)."""
    langs = list(project.languages) or ["english"]
    if iscc is None:
        return langs
    base = iscc.parent
    ok = [lang for lang in langs if (base / KNOWN_LANGUAGES[lang].replace("\\", os.sep)).is_file()]
    return ok or ["english"]


# --------------------------------------------------------------------- escaping
def iss_value(text: str) -> str:
    """Escape a value used in [Setup] or a quoted parameter: braces are constants."""
    return str(text).replace("{", "{{")


def iss_quoted(text: str) -> str:
    return '"' + iss_value(text).replace('"', '""') + '"'


def cm_arg(text: str) -> str:
    """Escape a parameter inside ``{cm:Name,...}``."""
    return str(text).replace("%", "%25").replace(",", "%2c").replace("|", "%7c").replace("}", "%7d").replace("{", "{{")


def output_basename(project: Project) -> str:
    return project.installer_basename


def render_iss(
    project: Project,
    *,
    source_dir: Path,
    output_dir: Path,
    languages: Optional[Iterable[str]] = None,
    extra_files: Iterable[Path] = (),
    windows_paths: bool = False,
    icon_file: Optional[str] = None,
    license_file: Optional[str] = None,
) -> str:
    """Return the Inno Setup script text.

    ``windows_paths``: paths are written as given with ``\\`` separators (for a
    script compiled on another machine); ``icon_file``/``license_file``
    override the project paths in that case.
    """
    sep = "\\" if windows_paths else os.sep

    def fix(path: str) -> str:
        return path.replace("/", "\\") if windows_paths else path

    icon = icon_file if icon_file is not None else (str(project.icon_path) if project.icon_path else None)
    lic = license_file if license_file is not None else (str(project.license_path) if project.license_path else None)
    exe = project.name + ".exe"
    title = project.app_display_name
    app_id = ("{{" + project.effective_app_id + "}") if project.app_id_is_guid else iss_value(project.effective_app_id)
    v = ".".join(str(x) for x in project.windows_version)
    langs = list(languages) if languages is not None else list(project.languages) or ["english"]
    setup = [
        ("AppId", app_id),  # a GUID is written as {{GUID} (literal brace, already escaped)
        ("AppName", iss_value(title)),
        ("AppVersion", iss_value(project.version)),
        ("AppVerName", iss_value(f"{title} {project.version}")),
        ("AppPublisher", iss_value(project.display_publisher)),
    ]
    if project.website:
        setup += [("AppPublisherURL", iss_value(project.website)), ("AppSupportURL", iss_value(project.website))]
    setup += [
        ("DefaultDirName", "{autopf}\\" + iss_value(title)),
        ("DefaultGroupName", iss_value(title)),
        ("DisableProgramGroupPage", "yes"),
        ("PrivilegesRequired", project.privileges),
        ("PrivilegesRequiredOverridesAllowed", "dialog"),
        ("OutputDir", iss_value(fix(str(output_dir)))),
        ("OutputBaseFilename", iss_value(output_basename(project))),
        ("UninstallDisplayIcon", "{app}\\" + iss_value(exe)),
        ("UninstallDisplayName", iss_value(title)),
        ("Compression", "lzma2"),
        ("SolidCompression", "yes"),
        ("WizardStyle", "modern"),
        ("VersionInfoVersion", v),
        ("VersionInfoCompany", iss_value(project.display_publisher)),
        ("VersionInfoDescription", iss_value(f"{title} Setup")),
        ("VersionInfoProductName", iss_value(title)),
        ("VersionInfoProductVersion", v),
        ("ArchitecturesAllowed", "x64compatible" if _is_64bit() else "x86compatible"),
    ]
    if _is_64bit():
        setup.append(("ArchitecturesInstallIn64BitMode", "x64compatible"))
    if project.copyright:
        setup.append(("VersionInfoCopyright", iss_value(project.copyright)))
    if icon:
        setup.append(("SetupIconFile", iss_value(fix(icon))))
    if lic:
        setup.append(("LicenseFile", iss_value(fix(lic))))

    lines = [f"; Generated by pyi-doctor {__version__}. Edit freely or regenerate.", "", "[Setup]"]
    lines += [f"{k}={val}" for k, val in setup]
    lines += ["", "[Languages]"]
    for lang in langs:
        lines.append(f'Name: "{lang}"; MessagesFile: "compiler:{KNOWN_LANGUAGES[lang]}"')
    lines += [
        "",
        "[Tasks]",
        'Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; '
        'GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked',
        "",
        "[Files]",
        f"Source: {iss_quoted(fix(str(source_dir)).rstrip(sep) + sep + '*')}; DestDir: \"{{app}}\"; "
        "Flags: ignoreversion recursesubdirs createallsubdirs",
    ]
    for f in extra_files:
        lines.append(f'Source: {iss_quoted(fix(str(f)))}; DestDir: "{{app}}"; Flags: ignoreversion')
    app_exe = "{app}\\" + iss_value(exe)
    name_q = iss_value(title).replace('"', '""')
    lines += [
        "",
        "[Icons]",
        f'Name: "{{autoprograms}}\\{name_q}"; Filename: "{app_exe}"',
        f'Name: "{{autodesktop}}\\{name_q}"; Filename: "{app_exe}"; Tasks: desktopicon',
        "",
        "[Run]",
        f'Filename: "{app_exe}"; Description: "{{cm:LaunchProgram,{cm_arg(title)}}}"; '
        "Flags: nowait postinstall skipifsilent",
        "",
    ]
    return "\r\n".join(lines)


def _is_64bit() -> bool:
    return sys.maxsize > 2**32


def write_iss(project: Project, onedir: Path, *, iscc: Optional[Path] = None, extra_files: Iterable[Path] = ()) -> Path:
    out_dir = project.output_path / "installer"
    iss = project.work_path / "installer" / f"{project.name}.iss"
    iss.parent.mkdir(parents=True, exist_ok=True)
    text = render_iss(
        project,
        source_dir=onedir,
        output_dir=out_dir,
        languages=available_languages(project, iscc),
        extra_files=[Path(f) for f in extra_files if Path(f).is_file()],
    )
    # UTF-8 with BOM so Inno Setup reads non-ASCII names correctly.
    iss.write_text(text, encoding="utf-8-sig")
    return iss


def write_iss_relative(project: Project, onedir: Path) -> Path:
    """Write ``<output>/<name>.iss`` with paths relative to the script, for compiling on Windows later.

    Used by the cross build: copy the whole output folder to Windows and run
    ``ISCC.exe <name>.iss`` there. The icon and license file are copied next
    to the script.
    """
    out = project.output_path
    out.mkdir(parents=True, exist_ok=True)
    side = out / "iss-files"
    icon = lic = None
    for src, kind in ((project.icon_path, "icon"), (project.license_path, "license")):
        if src and Path(src).is_file():
            side.mkdir(exist_ok=True)
            shutil.copyfile(src, side / Path(src).name)
            rel = f"iss-files\\{Path(src).name}"
            if kind == "icon":
                icon = rel
            else:
                lic = rel
    iss = out / f"{project.name}.iss"
    text = render_iss(
        project,
        source_dir=Path(os.path.relpath(onedir, out)).as_posix(),
        output_dir="installer",
        windows_paths=True,
        icon_file=icon or "",
        license_file=lic or "",
    )
    iss.write_text(text, encoding="utf-8-sig")
    return iss


def compile_iss(project: Project, iss: Path, iscc: Optional[Path] = None, timeout: Optional[float] = 1800) -> Artifact:
    iscc = iscc or ensure_iscc()
    run_logged([str(iscc), "/Q", str(iss)], cwd=iss.parent, log_path=iss.with_suffix(".log"), what="Inno Setup", timeout=timeout)
    exe = project.output_path / "installer" / (output_basename(project) + ".exe")
    if not exe.is_file():
        raise BuildError(f"Inno Setup finished but {exe} was not created", log_path=str(iss.with_suffix(".log")))
    return Artifact(kind="installer", path=exe)


def build(project: Project, onedir: Path, *, extra_files: Iterable[Path] = (), iscc: Optional[Path] = None,
          download: Optional[bool] = None, log: Optional[Logger] = None) -> Artifact:
    """Write the .iss for ``onedir`` and compile it (Inno Setup is downloaded first if missing, see :func:`ensure_iscc`)."""
    iscc = iscc or ensure_iscc(download=download, log=log)
    iss = write_iss(project, onedir, iscc=iscc, extra_files=extra_files)
    return compile_iss(project, iss, iscc)
