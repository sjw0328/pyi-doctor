"""Setup wizard (installer) generation with Inno Setup.

pyi-doctor writes an Inno Setup script (``.iss``) from the project settings
and compiles it with ``ISCC.exe`` when Inno Setup 6.3 or newer is installed.
Inno Setup itself is not bundled; install it from https://jrsoftware.org/.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from . import __version__
from ._proc import run_logged
from .config import KNOWN_LANGUAGES, Project
from .errors import BuildError, ToolNotFoundError
from .portable import Artifact

ISCC_ENV = "PYI_DOCTOR_ISCC"


def find_iscc() -> Optional[Path]:
    """Locate ``ISCC.exe``: env var, PATH, then the default install folders."""
    env = os.environ.get(ISCC_ENV)
    if env:
        p = Path(env)
        return p if p.is_file() else None
    for name in ("ISCC.exe", "iscc.exe", "ISCC", "iscc"):
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for sub in ("Inno Setup 6", os.path.join("Programs", "Inno Setup 6")):
            cand = Path(root) / sub / "ISCC.exe"
            if cand.is_file():
                return cand
    return None


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
    return f"{project.name}-{project.version}-setup"


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
    v = ".".join(str(x) for x in project.windows_version)
    langs = list(languages) if languages is not None else list(project.languages) or ["english"]
    setup = [
        ("AppId", "{{" + project.effective_app_id + "}"),  # literal brace, already escaped
        ("AppName", iss_value(project.name)),
        ("AppVersion", iss_value(project.version)),
        ("AppVerName", iss_value(f"{project.name} {project.version}")),
        ("AppPublisher", iss_value(project.display_publisher)),
    ]
    if project.website:
        setup += [("AppPublisherURL", iss_value(project.website)), ("AppSupportURL", iss_value(project.website))]
    setup += [
        ("DefaultDirName", "{autopf}\\" + iss_value(project.name)),
        ("DefaultGroupName", iss_value(project.name)),
        ("DisableProgramGroupPage", "yes"),
        ("PrivilegesRequired", project.privileges),
        ("PrivilegesRequiredOverridesAllowed", "dialog"),
        ("OutputDir", iss_value(fix(str(output_dir)))),
        ("OutputBaseFilename", iss_value(output_basename(project))),
        ("UninstallDisplayIcon", "{app}\\" + iss_value(exe)),
        ("UninstallDisplayName", iss_value(project.name)),
        ("Compression", "lzma2"),
        ("SolidCompression", "yes"),
        ("WizardStyle", "modern"),
        ("VersionInfoVersion", v),
        ("VersionInfoCompany", iss_value(project.display_publisher)),
        ("VersionInfoDescription", iss_value(f"{project.name} Setup")),
        ("VersionInfoProductName", iss_value(project.name)),
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
    name_q = iss_value(project.name).replace('"', '""')
    lines += [
        "",
        "[Icons]",
        f'Name: "{{autoprograms}}\\{name_q}"; Filename: "{app_exe}"',
        f'Name: "{{autodesktop}}\\{name_q}"; Filename: "{app_exe}"; Tasks: desktopicon',
        "",
        "[Run]",
        f'Filename: "{app_exe}"; Description: "{{cm:LaunchProgram,{cm_arg(project.name)}}}"; '
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
    iscc = iscc or find_iscc()
    if iscc is None:
        raise ToolNotFoundError(
            "Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6.3+ or set " + ISCC_ENV
        )
    run_logged([str(iscc), "/Q", str(iss)], cwd=iss.parent, log_path=iss.with_suffix(".log"), what="Inno Setup", timeout=timeout)
    exe = project.output_path / "installer" / (output_basename(project) + ".exe")
    if not exe.is_file():
        raise BuildError(f"Inno Setup finished but {exe} was not created", log_path=str(iss.with_suffix(".log")))
    return Artifact(kind="installer", path=exe)


def build(project: Project, onedir: Path, *, extra_files: Iterable[Path] = (), iscc: Optional[Path] = None) -> Artifact:
    """Write the .iss for ``onedir`` and compile it."""
    iscc = iscc or find_iscc()
    iss = write_iss(project, onedir, iscc=iscc, extra_files=extra_files)
    return compile_iss(project, iss, iscc)
