"""``build_all``: the full pipeline in one call.

Order: portable (onedir) -> onefile (optional) -> licenses -> SBOM -> smoke
test -> installer -> antivirus diagnosis -> release folder + report.

Only the onedir build is fatal; every later step records its own status so
one missing tool (for example Inno Setup) does not stop the rest.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

from . import antivirus as diag_mod
from . import installer as inst_mod
from . import licenses as lic_mod
from . import portable, release, sbom, smoke
from .config import Project
from .errors import PyiDoctorError, ToolNotFoundError
from .portable import Artifact
from .report import Report, Step, submission_notes

Logger = Callable[[str], None]


def _project_dict(p: Project) -> dict:
    return {
        "name": p.name,
        "version": p.version,
        "publisher": p.publisher,
        "description": p.description,
        "website": p.website,
        "app_id": p.effective_app_id,
        "console": p.console,
        "onefile": p.onefile,
        "upx": p.upx,
    }


class _Timer:
    def __enter__(self):
        self.t = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.seconds = round(time.monotonic() - self.t, 2)
        return False


def build_all(
    project: Project,
    *,
    onefile: Optional[bool] = None,
    installer: bool = True,
    smoke_test: bool = True,
    diagnose: bool = True,
    defender: bool = True,
    licenses: bool = True,
    sbom_file: bool = True,
    release_dir: bool = True,
    github_tag: Optional[str] = None,
    github_repo: Optional[str] = None,
    log: Optional[Logger] = None,
) -> Report:
    """Run every step and return a :class:`Report` (also written to ``<output>/release``)."""
    say: Logger = log or (lambda msg: None)
    report = Report(project=_project_dict(project))
    artifacts: List[Artifact] = []
    want_onefile = project.onefile if onefile is None else onefile

    def record(name: str, status: str, message: str, seconds: float, **data) -> Step:
        st = Step(name, status, message, seconds, data)
        report.steps.append(st)
        say(f"[{status.upper():7}] {name}: {message}")
        return st

    # 1. portable onedir (fatal)
    say(f"Building {project.name} {project.version} (onedir) ...")
    onedir: Optional[Artifact] = None
    error: Optional[PyiDoctorError] = None
    with _Timer() as t:
        try:
            onedir = portable.build(project, onefile=False)
        except PyiDoctorError as exc:
            error = exc
    if onedir is None:
        record("portable", "failed", str(error).splitlines()[0], t.seconds, log_tail=getattr(error, "log_tail", ""))
        _finish(project, report, artifacts, [], release_dir, say)
        return report
    artifacts.append(onedir)
    record("portable", "ok", f"{onedir.folder}", t.seconds)

    # 2. onefile
    if want_onefile:
        say("Building onefile ...")
        with _Timer() as t:
            try:
                one = portable.build(project, onefile=True)
                artifacts.append(one)
                status, msg = "ok", str(one.path)
            except PyiDoctorError as exc:
                status, msg = "failed", str(exc).splitlines()[0]
        record("onefile", status, msg, t.seconds)
    else:
        record("onefile", "skipped", "onefile disabled", 0.0)

    # 3. licenses + 4. SBOM
    extra_release: List[Path] = []
    notices: Optional[Path] = None
    packages = []
    if licenses or sbom_file:
        with _Timer() as t:
            packages, mode = lic_mod.collect(project.work_path / "onedir")
        if licenses:
            notices = lic_mod.write(project.work_path / "THIRD_PARTY_LICENSES.txt", packages, app_name=project.name, mode=mode)
            unknown = [p.name for p in packages if p.license == "UNKNOWN"]
            status = "warning" if unknown or mode != "toc" else "ok"
            msg = f"{len(packages)} packages ({mode} mode)"
            if unknown:
                msg += f"; license unknown: {', '.join(unknown[:5])}" + (" ..." if len(unknown) > 5 else "")
            record("licenses", status, msg, t.seconds, mode=mode, summary=lic_mod.summary(packages),
                   packages=[p.to_dict() for p in packages])
            # Place the notice next to the portable exe so the zip and installer carry it.
            if onedir.folder:
                (onedir.folder / notices.name).write_bytes(notices.read_bytes())
            for a in artifacts:
                if a.kind == "onefile":
                    (a.path.parent / notices.name).write_bytes(notices.read_bytes())
            extra_release.append(notices)
        else:
            record("licenses", "skipped", "disabled", 0.0)
        if sbom_file:
            sb = sbom.write(project.work_path / f"{project.name}-{project.version}.cdx.json", project, packages)
            extra_release.append(sb)
            record("sbom", "ok", f"CycloneDX 1.5, {len(packages)} components", 0.0)
        else:
            record("sbom", "skipped", "disabled", 0.0)
    else:
        record("licenses", "skipped", "disabled", 0.0)
        record("sbom", "skipped", "disabled", 0.0)

    # 5. smoke test
    if smoke_test:
        results = []
        with _Timer() as t:
            for a in artifacts:
                if a.kind not in ("onedir", "onefile"):
                    continue
                say(f"Smoke testing {a.kind} ...")
                r = smoke.run(a.path, args=project.smoke_args, timeout=project.smoke_timeout)
                d = r.to_dict()
                d["target"] = a.kind
                results.append(d)
        failed = [r for r in results if not r["ok"]]
        msg = "; ".join(f"{r['target']}: {r['status']}" for r in results)
        record("smoke", "failed" if failed else "ok", msg, t.seconds, results=results)
    else:
        record("smoke", "skipped", "disabled", 0.0)

    # 6. installer
    if installer:
        with _Timer() as t:
            iscc = inst_mod.find_iscc()
            iss = inst_mod.write_iss(project, onedir.folder, iscc=iscc)
            if sys.platform != "win32":
                status, msg = "skipped", f"installer needs Windows; script written to {iss}"
            elif iscc is None:
                status, msg = "skipped", f"Inno Setup not found; script written to {iss}"
            else:
                try:
                    inst = inst_mod.compile_iss(project, iss, iscc)
                    artifacts.append(inst)
                    status, msg = "ok", str(inst.path)
                except (PyiDoctorError, ToolNotFoundError) as exc:
                    status, msg = "failed", str(exc).splitlines()[0]
        record("installer", status, msg, t.seconds, iss=str(iss))
    else:
        record("installer", "skipped", "disabled", 0.0)

    # 7. diagnosis
    if diagnose:
        results = []
        with _Timer() as t:
            boot = diag_mod.bootloader_status()
            for a in artifacts:
                say(f"Diagnosing {a.path.name} ...")
                icon = (project.icon is not None) if a.kind != "installer" else None
                d = diag_mod.diagnose(a.path, icon_configured=icon, defender=defender, bootloader=boot if a.kind != "installer" else "n/a")
                results.append(d.to_dict())
        detected = [r for r in results if r["defender"]["status"] == "detected"]
        worst = max((r["score"] for r in results), default=0)
        msg = f"highest risk score {worst}/100 ({diag_mod.level_for(worst)})"
        if detected:
            msg += f"; Defender detection on {len(detected)} file(s)"
        record("diagnose", "warning" if detected or worst >= 50 else "ok", msg, t.seconds, results=results)
    else:
        record("diagnose", "skipped", "disabled", 0.0)

    files = _finish(project, report, artifacts, extra_release, release_dir, say)

    # 8. GitHub upload (opt-in)
    if github_tag:
        with _Timer() as t:
            try:
                msg = release.upload_github(github_tag, files, repo=github_repo,
                                            notes_file=project.release_path / "report.md")
                status = "ok"
            except PyiDoctorError as exc:
                status, msg = "failed", str(exc)
        record("github", status, msg, t.seconds)
        _write_reports(project, report)
    return report


def _finish(project: Project, report: Report, artifacts: List[Artifact], extra: List[Path], release_dir: bool, say: Logger) -> List[Path]:
    report.artifacts = [a.to_dict() for a in artifacts]
    files: List[Path] = []
    if release_dir and artifacts:
        with _Timer() as t:
            files = release.collect(project, artifacts, extra)
            notes = project.release_path / "av-submission-notes.txt"
            exe_files = [f for f in files if f.suffix.lower() in (".exe", ".zip", "")]
            notes.write_text(submission_notes(report.project, release.file_entries(exe_files), report.environment), encoding="utf-8")
            files.append(notes)
        report.steps.append(Step("release", "ok", str(project.release_path), t.seconds, {"files": [f.name for f in files]}))
        say(f"[OK     ] release: {project.release_path}")
    _write_reports(project, report)
    return files


def _write_reports(project: Project, report: Report) -> None:
    out = project.release_path
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(report.to_json(), encoding="utf-8")
    (out / "report.md").write_text(report.to_markdown(), encoding="utf-8")
