"""Command-line interface: ``pyi-doctor <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from . import antivirus as diag_mod
from . import installer as inst_mod
from . import smoke as smoke_mod
from .config import SAMPLE_CONFIG, Project
from .errors import PyiDoctorError


def _add_project_args(p: argparse.ArgumentParser, *, settings: bool = False) -> None:
    p.add_argument("path", nargs="?",
                   help="entry script, project folder or pyproject.toml (default: current folder; "
                        "settings are guessed when there is no [tool.pyi-doctor])")
    p.add_argument("--config", default="pyproject.toml", help="pyproject.toml with [tool.pyi-doctor] (default: %(default)s)")
    p.add_argument("--entry", help="entry script (overrides config)")
    p.add_argument("--name", help="application name (overrides config)")
    p.add_argument("--app-version", dest="version", help="application version (overrides config)")
    if settings:
        p.add_argument("--display-name", help="name shown in the installer and Start menu")
        p.add_argument("--publisher", help="publisher / company name")
        p.add_argument("--icon", help=".ico file")
        p.add_argument("--console", action=argparse.BooleanOptionalAction, default=None,
                       help="command-line app (--no-console: window app)")


def _add_brief(p: argparse.ArgumentParser) -> None:
    p.add_argument("--brief", action=argparse.BooleanOptionalAction, default=None,
                   help="short output: only problems and next steps (for AI agents / CI logs). "
                        "Default: on when PYI_DOCTOR_BRIEF=1 or an AI coding agent is detected")


def _brief(args: argparse.Namespace) -> bool:
    from .summary import brief_default

    b = getattr(args, "brief", None)
    return brief_default() if b is None else bool(b)


def _dumps(data, brief: bool) -> str:
    if brief:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(data, indent=2, ensure_ascii=False)


def _overrides(args: argparse.Namespace) -> dict:
    keys = ("entry", "name", "version", "display_name", "publisher", "icon", "console")
    return {k: getattr(args, k, None) for k in keys if getattr(args, k, None) is not None}


def _load_project(args: argparse.Namespace, *, quiet: bool = False, brief: bool = False) -> Project:
    """Project from PATH / --config / the current folder; missing settings are guessed."""
    overrides = _overrides(args)
    path = getattr(args, "path", None)
    if path is None:
        cfg = Path(args.config)
        if cfg.is_file():
            path = cfg
        elif args.config != "pyproject.toml":
            raise PyiDoctorError(f"{cfg} not found")
        else:
            path = Path.cwd()
    project = Project.load(path, **overrides)
    if project.detected and not quiet and brief:
        from .summary import guessed_line

        print(guessed_line(project.detected), file=sys.stderr)
    elif project.detected and not quiet:
        print("Guessed settings (save them with 'pyi-doctor init --write'):", file=sys.stderr)
        for note in project.detected:
            print(f"  {note}", file=sys.stderr)
    return project


def cmd_build(args: argparse.Namespace) -> int:
    from . import preflight
    from .pipeline import build_all
    from .summary import summary_lines

    brief = _brief(args)
    project = _load_project(args, quiet=args.quiet, brief=brief)
    if not args.no_check:
        checks = [c for c in preflight.run_checks(project, target=args.target) if c.status == "failed"]
        if checks:
            print("Stopped before building:", file=sys.stderr)
            for c in checks:
                print(f"  - {c.message}" + (f"\n    -> {c.fix}" if c.fix else ""), file=sys.stderr)
            print("(run 'pyi-doctor check' for details, or --no-check to build anyway)", file=sys.stderr)
            return 1
    reqs = None
    if args.requirements:
        reqs = list(project.requirements) + [f"-r {f}" for f in args.requirements]
    report = build_all(
        project,
        target=args.target,
        requirements=reqs,
        python_version=args.python_version,
        onefile=True if args.onefile else None,
        installer=not args.no_installer,
        smoke_test=not args.no_smoke,
        diagnose=not args.no_diagnose,
        defender=not args.no_defender,
        licenses=not args.no_licenses,
        sbom_file=not args.no_sbom,
        github_tag=args.github_release,
        github_repo=args.github_repo,
        log=None if (args.quiet or brief) else print,
    )
    if brief:
        from .summary import brief_lines

        print("\n".join(brief_lines(report, project)))
    else:
        if not args.quiet:
            print("\n".join(summary_lines(report, project)))
        print(f"\nReport: {project.release_path / 'report.md'}")
    if args.open:
        from .gui import open_folder

        folder = project.release_path if project.release_path.is_dir() else project.output_path
        if folder.is_dir():
            open_folder(folder)
    return 0 if report.ok else 1


def cmd_check(args: argparse.Namespace) -> int:
    from . import preflight

    project, error = None, None
    try:
        project = _load_project(args, quiet=True)
    except PyiDoctorError as exc:
        error = str(exc)
    checks = preflight.run_checks(project, target=args.target, load_error=error)
    brief = _brief(args)
    if args.json:
        shown = [c for c in checks if c.status in ("failed", "warning")] if brief else checks
        print(_dumps([c.to_dict() for c in shown], brief))
    else:
        print("\n".join(preflight.check_lines(checks, brief=brief)))
    return 1 if any(c.status == "failed" for c in checks) else 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    results = []
    for path in args.files:
        p = Path(path)
        if not p.is_file():
            print(f"{p}: not found", file=sys.stderr)
            return 2
        results.append(diag_mod.diagnose(p, defender=not args.no_defender, icon_configured=args.icon))
    brief = _brief(args)
    if brief:
        from .summary import brief_dict, diagnosis_brief

        if args.json:
            print(_dumps([brief_dict(r) for r in results], True))
        else:
            print("\n".join(diagnosis_brief(r) for r in results))
        return 0
    if args.json:
        print(_dumps([r.to_dict() for r in results], False))
        return 0
    from .gui import diagnosis_lines

    for r in results:
        print("\n".join(diagnosis_lines(r)))
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    r = smoke_mod.run(Path(args.exe), args=args.args, timeout=args.timeout, isolate=not args.no_isolate,
                      expect_exit_code=args.expect_exit_code)
    brief = _brief(args)
    if args.json:
        d = r.to_dict()
        if brief:
            d = {k: v for k, v in d.items() if v not in ([], "", None)}
            if "output_tail" in d:
                d["output_tail"] = "\n".join(d["output_tail"].splitlines()[-10:])
        print(_dumps(d, brief))
    else:
        print(f"{'PASSED' if r.ok else 'FAILED'}: {r.status} - {r.message} ({r.duration}s)")
        for tip in r.suggestions:
            print(f"  - {tip}")
        if not r.ok and r.output_tail:
            tail = r.output_tail.splitlines()
            print("--- output (last lines) ---")
            print("\n".join(tail[-10:] if brief else tail))
    return 0 if r.ok else 1


def cmd_iss(args: argparse.Namespace) -> int:
    project = _load_project(args, quiet=True)
    onedir = Path(args.source) if args.source else project.output_path / "portable" / project.name
    iss = inst_mod.write_iss(project, onedir.resolve(), iscc=inst_mod.find_iscc())
    print(iss)
    return 0


def init_text(path: Path, entry: Optional[str] = None) -> str:
    """``[tool.pyi-doctor]`` text for the project next to ``path``, filled with guessed values and a new app_id."""
    from .autodetect import config_text, detect, new_app_id
    from .config import _load_toml, _merge_project_table

    folder = path.parent if path.suffix else path
    det = detect(folder, entry=entry)
    if "entry" not in det.values:
        return SAMPLE_CONFIG
    from_project = _merge_project_table(_load_toml(path)) if path.is_file() else {}
    values = dict(det.values)
    for k in ("name", "version", "description", "publisher"):
        if k in from_project:
            values.pop(k, None)  # already in [project]; pyi-doctor falls back to it
    if "version" not in from_project:
        values.setdefault("version", "1.0.0")
    if "publisher" not in from_project:
        values["publisher"] = "Your Name"
    values["app_id"] = new_app_id()
    values.setdefault("console", False)
    values["onefile"] = False
    values["languages"] = ["english", "korean"]
    return config_text(values)


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if args.write and "[tool.pyi-doctor]" in text:
        print(f"{path} already has [tool.pyi-doctor]", file=sys.stderr)
        return 1
    block = init_text(path.resolve(), entry=args.entry)
    if not args.write:
        print(block, end="")
        return 0
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + block, encoding="utf-8")
    print(f"added [tool.pyi-doctor] to {path}")
    if block != SAMPLE_CONFIG:
        print("review the values (publisher, icon, console); keep app_id unchanged after your first release")
    return 0


QUICKREF = """\
pyi-doctor quick reference (short output: --brief, auto-on for AI agents; PYI_DOCTOR_BRIEF=0 turns it off)
  pyi-doctor check [PATH]           settings + environment problems; exit 1 = would stop the build
  pyi-doctor build [PATH]           build exe + installer, start test, AV risk score, licenses -> dist/release
  pyi-doctor build --target win     Windows x64 exe from Linux/macOS (experimental; no tkinter)
  pyi-doctor init --write           save guessed settings + fixed app_id to pyproject.toml [tool.pyi-doctor]
  pyi-doctor diagnose X.exe --json  AV risk factors (brief JSON: score, level, factor keys)
  pyi-doctor smoke X.exe            start test only
PATH = entry .py, project folder or pyproject.toml; without [tool.pyi-doctor] settings are guessed.
Fix loop: run 'check'; on build FAIL read the 'next:' lines (e.g. add a module to hidden_imports), edit
[tool.pyi-doctor] in pyproject.toml, rebuild. Full results: dist/release/report.json (read only if needed).
"""


def cmd_quickref(args: argparse.Namespace) -> int:
    print(QUICKREF, end="")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import main as gui_main

    return gui_main([args.path] if args.path else [])


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyi-doctor",
        description="Build PyInstaller apps as portable exe + installer and check them for common problems.",
    )
    parser.add_argument("--version", action="version", version=f"pyi-doctor {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="run the full pipeline",
                       description="Build, test and check the app. Works without a config: the entry script, "
                                   "name, icon and window/console mode are guessed and printed.")
    _add_project_args(b, settings=True)
    b.add_argument("--onefile", action="store_true", help="also build a single-file exe")
    b.add_argument("--no-installer", action="store_true")
    b.add_argument("--no-smoke", action="store_true")
    b.add_argument("--no-diagnose", action="store_true")
    b.add_argument("--no-defender", action="store_true", help="skip the Microsoft Defender scan")
    b.add_argument("--no-licenses", action="store_true")
    b.add_argument("--no-sbom", action="store_true")
    b.add_argument("--github-release", metavar="TAG", help="upload release files to this GitHub release (uses gh)")
    b.add_argument("--github-repo", metavar="OWNER/REPO")
    b.add_argument("--target", choices=["native", "win"], default="native",
                   help="'win' builds a Windows x64 exe from Linux/macOS (experimental, see README)")
    b.add_argument("-r", "--requirements", action="append", metavar="FILE",
                   help="requirements file for --target win (repeatable; added to 'requirements' from config)")
    b.add_argument("--python-version", metavar="X.Y.Z",
                   help="--target win: embeddable Python release to use (must match this Python's X.Y)")
    b.add_argument("--open", action="store_true", help="open the release folder when done")
    b.add_argument("--no-check", action="store_true", help="skip the quick checks before building")
    b.add_argument("-q", "--quiet", action="store_true")
    _add_brief(b)
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="check the environment and settings before building",
                       description="Quick checks (PyInstaller, venv, Inno Setup, entry script syntax, icon, app_id, "
                                   "...) with what to change. Exit code 1 when a problem would stop the build.")
    _add_project_args(c, settings=True)
    c.add_argument("--target", choices=["native", "win"], default="native")
    c.add_argument("--json", action="store_true")
    _add_brief(c)
    c.set_defaults(func=cmd_check)

    d = sub.add_parser("diagnose", help="score executables for antivirus false-positive risk factors")
    d.add_argument("files", nargs="+")
    d.add_argument("--no-defender", action="store_true")
    d.add_argument("--icon", action=argparse.BooleanOptionalAction, default=None,
                   help="whether a custom icon was set (--no-icon adds the default-icon factor; omit to skip it)")
    d.add_argument("--json", action="store_true")
    _add_brief(d)
    d.set_defaults(func=cmd_diagnose)

    s = sub.add_parser("smoke", help="start an executable in a temp folder with a reduced environment")
    s.add_argument("exe")
    s.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to the executable (after --)")
    s.add_argument("--timeout", type=float, default=15.0)
    s.add_argument("--expect-exit-code", type=int)
    s.add_argument("--no-isolate", action="store_true", help="run in place with the current environment")
    s.add_argument("--json", action="store_true")
    _add_brief(s)
    s.set_defaults(func=cmd_smoke)

    i = sub.add_parser("iss", help="write the Inno Setup script only")
    _add_project_args(i)
    i.add_argument("--source", help="onedir folder to package (default: dist/portable/<name>)")
    i.set_defaults(func=cmd_iss)

    n = sub.add_parser("init", help="print (or --write) a [tool.pyi-doctor] block filled from the project folder",
                       description="Guesses entry script, name, icon, console mode, license and data folders, "
                                   "and adds a new fixed app_id.")
    n.add_argument("--config", default="pyproject.toml")
    n.add_argument("--entry", help="entry script (default: guessed)")
    n.add_argument("--write", action="store_true", help="add the block to the pyproject.toml (created if missing)")
    n.set_defaults(func=cmd_init)

    q = sub.add_parser("quickref", help="print a short usage summary (meant for AI coding agents)")
    q.set_defaults(func=cmd_quickref)

    g = sub.add_parser("gui", help="open the desktop window (tkinter)")
    g.add_argument("path", nargs="?", help="project folder, entry script or pyproject.toml to open")
    g.set_defaults(func=cmd_gui)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if getattr(args, "args", None) and args.args[:1] == ["--"]:
        args.args = args.args[1:]
    try:
        return args.func(args)
    except PyiDoctorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
