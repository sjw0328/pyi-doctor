"""pyi-doctor: build PyInstaller apps as a portable exe and an installer, then
check whether they start outside the build machine, which antivirus risk
factors they carry, and which third-party licenses they bundle.

Typical use::

    from pyi_doctor import Project, build_all

    project = Project(entry="main.py", name="MyApp", version="1.0.0", publisher="Me")
    report = build_all(project, log=print)
    print(report.ok)

Without writing any settings (entry script, name, icon, console mode are guessed)::

    project = Project.load(".")        # or "main.py" / "pyproject.toml"
    print(project.detected)            # what was guessed
    for c in check(project):           # quick pre-build checks
        print(c.status, c.message, c.fix)
"""

__version__ = "1.6.0"

from .config import Project  # noqa: E402
from .errors import BuildError, ConfigError, CrossBuildUnsupported, PyiDoctorError, ToolNotFoundError  # noqa: E402

__all__ = [
    "__version__",
    "Project",
    "build_all",
    "build_portable",
    "build_installer",
    "build_windows",
    "diagnose",
    "smoke_test",
    "collect_licenses",
    "check",
    "detect",
    "PyiDoctorError",
    "ConfigError",
    "BuildError",
    "ToolNotFoundError",
    "CrossBuildUnsupported",
]


def build_all(project, **kwargs):
    """Run the full pipeline. See :func:`pyi_doctor.pipeline.build_all`."""
    from .pipeline import build_all as _impl

    return _impl(project, **kwargs)


def build_portable(project, *, onefile: bool = False):
    """Build the onedir (default) or onefile executable with PyInstaller."""
    from .portable import build

    return build(project, onefile=onefile)


def build_windows(project, *, onefile: bool = False, **kwargs):
    """Experimental: build a Windows x64 exe on Linux/macOS. See :func:`pyi_doctor.cross.build_windows`."""
    from .cross import build_windows as _impl

    return _impl(project, onefile=onefile, **kwargs)


def build_installer(project, onedir, **kwargs):
    """Write and compile the Inno Setup installer for an onedir folder."""
    from .installer import build

    return build(project, onedir, **kwargs)


def diagnose(exe, **kwargs):
    """Score an executable for antivirus false-positive risk factors."""
    from .antivirus import diagnose as _impl

    return _impl(exe, **kwargs)


def smoke_test(exe, **kwargs):
    """Start an executable in a temp folder with a reduced environment."""
    from .smoke import run

    return run(exe, **kwargs)


def check(project=None, *, target: str = "native"):
    """Quick pre-build checks of the environment and ``project``; list of ``Check(name, status, message, fix)``."""
    from .preflight import run_checks

    return run_checks(project, target=target)


def detect(folder=".", *, entry=None):
    """Guess settings for the app in ``folder``; ``Detection`` with ``.values`` and ``.notes``."""
    from .autodetect import detect as _impl

    return _impl(folder, entry=entry)


def collect_licenses(work_dir=None):
    """Return (packages, mode) for bundled third-party packages."""
    from .licenses import collect

    return collect(work_dir)
