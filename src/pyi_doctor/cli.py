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


def _add_project_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", default="pyproject.toml", help="pyproject.toml with [tool.pyi-doctor] (default: %(default)s)")
    p.add_argument("--entry", help="entry script (overrides config)")
    p.add_argument("--name", help="application name (overrides config)")
    p.add_argument("--app-version", dest="version", help="application version (overrides config)")


def _load_project(args: argparse.Namespace) -> Project:
    overrides = {"entry": args.entry, "name": args.name, "version": args.version}
    cfg = Path(args.config)
    if cfg.is_file():
        return Project.from_pyproject(cfg, **overrides)
    if args.entry and args.name:
        return Project.from_dict({k: v for k, v in overrides.items() if v is not None}, root=Path.cwd())
    raise PyiDoctorError(f"{cfg} not found; pass --entry and --name or run 'pyi-doctor init'")


def cmd_build(args: argparse.Namespace) -> int:
    from .pipeline import build_all

    project = _load_project(args)
    report = build_all(
        project,
        onefile=True if args.onefile else None,
        installer=not args.no_installer,
        smoke_test=not args.no_smoke,
        diagnose=not args.no_diagnose,
        defender=not args.no_defender,
        licenses=not args.no_licenses,
        sbom_file=not args.no_sbom,
        github_tag=args.github_release,
        github_repo=args.github_repo,
        log=None if args.quiet else print,
    )
    print(f"\nReport: {project.release_path / 'report.md'}")
    return 0 if report.ok else 1


def cmd_diagnose(args: argparse.Namespace) -> int:
    results = []
    for path in args.files:
        p = Path(path)
        if not p.is_file():
            print(f"{p}: not found", file=sys.stderr)
            return 2
        results.append(diag_mod.diagnose(p, defender=not args.no_defender, icon_configured=args.icon))
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        return 0
    for r in results:
        print(f"{r.path}")
        print(f"  risk score : {r.score}/100 ({r.level})  [indicator only]")
        print(f"  sha256     : {r.sha256}")
        print(f"  defender   : {r.defender.status} {r.defender.detail}".rstrip())
        print(f"  bootloader : {r.bootloader} (PyInstaller in this environment)")
        for f in r.factors:
            print(f"  - [{f.weight:2}] {f.label}\n         -> {f.advice}")
        for n in r.notes:
            print(f"  note: {n}")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    r = smoke_mod.run(Path(args.exe), args=args.args, timeout=args.timeout, isolate=not args.no_isolate,
                      expect_exit_code=args.expect_exit_code)
    if args.json:
        print(json.dumps(r.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"{'PASSED' if r.ok else 'FAILED'}: {r.status} - {r.message} ({r.duration}s)")
        for tip in r.suggestions:
            print(f"  - {tip}")
        if not r.ok and r.output_tail:
            print("--- output (last lines) ---")
            print(r.output_tail)
    return 0 if r.ok else 1


def cmd_iss(args: argparse.Namespace) -> int:
    project = _load_project(args)
    onedir = Path(args.source) if args.source else project.output_path / "portable" / project.name
    iss = inst_mod.write_iss(project, onedir.resolve(), iscc=inst_mod.find_iscc())
    print(iss)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if not args.write:
        print(SAMPLE_CONFIG, end="")
        return 0
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "[tool.pyi-doctor]" in text:
        print(f"{path} already has [tool.pyi-doctor]", file=sys.stderr)
        return 1
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + SAMPLE_CONFIG, encoding="utf-8")
    print(f"added [tool.pyi-doctor] to {path}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyi-doctor",
        description="Build PyInstaller apps as portable exe + installer and check them for common problems.",
    )
    parser.add_argument("--version", action="version", version=f"pyi-doctor {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="run the full pipeline")
    _add_project_args(b)
    b.add_argument("--onefile", action="store_true", help="also build a single-file exe")
    b.add_argument("--no-installer", action="store_true")
    b.add_argument("--no-smoke", action="store_true")
    b.add_argument("--no-diagnose", action="store_true")
    b.add_argument("--no-defender", action="store_true", help="skip the Microsoft Defender scan")
    b.add_argument("--no-licenses", action="store_true")
    b.add_argument("--no-sbom", action="store_true")
    b.add_argument("--github-release", metavar="TAG", help="upload release files to this GitHub release (uses gh)")
    b.add_argument("--github-repo", metavar="OWNER/REPO")
    b.add_argument("-q", "--quiet", action="store_true")
    b.set_defaults(func=cmd_build)

    d = sub.add_parser("diagnose", help="score executables for antivirus false-positive risk factors")
    d.add_argument("files", nargs="+")
    d.add_argument("--no-defender", action="store_true")
    d.add_argument("--icon", action=argparse.BooleanOptionalAction, default=None,
                   help="whether a custom icon was set (--no-icon adds the default-icon factor; omit to skip it)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_diagnose)

    s = sub.add_parser("smoke", help="start an executable in a temp folder with a reduced environment")
    s.add_argument("exe")
    s.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to the executable (after --)")
    s.add_argument("--timeout", type=float, default=15.0)
    s.add_argument("--expect-exit-code", type=int)
    s.add_argument("--no-isolate", action="store_true", help="run in place with the current environment")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_smoke)

    i = sub.add_parser("iss", help="write the Inno Setup script only")
    _add_project_args(i)
    i.add_argument("--source", help="onedir folder to package (default: dist/portable/<name>)")
    i.set_defaults(func=cmd_iss)

    n = sub.add_parser("init", help="print (or --write) a [tool.pyi-doctor] config block")
    n.add_argument("--config", default="pyproject.toml")
    n.add_argument("--write", action="store_true")
    n.set_defaults(func=cmd_init)
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
