"""Short end-of-build summary with concrete next steps (shared by the CLI and the window)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .report import Report

_LABEL = {"ok": "OK", "warning": "WARN", "failed": "FAIL", "skipped": "SKIP"}
INNO_SETUP_URL = "https://jrsoftware.org/isdl.php"


def _size(path: Path) -> str:
    try:
        n = float(path.stat().st_size)
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def next_steps(report: Report, project=None) -> List[str]:
    """Things to do after this build, most important first (no duplicates)."""
    tips: List[str] = []

    def add(t: str) -> None:
        if t and t not in tips:
            tips.append(t)

    port = report.step("portable")
    if port and port.status == "failed":
        for r in port.data.get("reasons") or []:
            add(f"cross build: {r}")
        add("run 'pyi-doctor check' to find setting and environment problems")
        if port.data.get("log_tail"):
            add("see the PyInstaller output above / in the build folder for the first error")
    smoke = report.step("smoke")
    if smoke and smoke.status == "failed":
        for r in smoke.data.get("results") or []:
            for t in r.get("suggestions") or []:
                add(t)
        exe = next((a["path"] for a in report.artifacts if a.get("kind") in ("onedir", "onefile")), "<exe>")
        add(f"after fixing, rebuild; to re-run only the start test: pyi-doctor smoke \"{exe}\"")
    inst = report.step("installer")
    if inst and inst.status == "skipped" and "download failed" in inst.message:
        add(f"Inno Setup download failed: check the network and rebuild, or install Inno Setup 6.3+ ({INNO_SETUP_URL})")
    elif inst and inst.status == "skipped" and ("Inno Setup not found" in inst.message
                                                or "compiler (ISCC.exe) not found" in inst.message):
        add(f"install Inno Setup 6.3+ ({INNO_SETUP_URL}) or allow the automatic download to get the setup.exe")
    elif inst and inst.status == "skipped" and "needs Windows" in inst.message:
        add("compile the .iss script on Windows with Inno Setup, or build there to get the setup.exe")
    elif inst and inst.status == "failed":
        add("installer failed: check the .iss script and Inno Setup version (6.3+)")
    diag = report.step("diagnose")
    if diag and diag.data.get("results"):
        seen = set()
        for r in diag.data["results"]:
            for f in r.get("factors") or []:
                if f["key"] in seen or f["key"] in ("stock_bootloader",):
                    continue
                seen.add(f["key"])
                add(f"antivirus risk: {f['label']} -> {f['advice']}")
            if r.get("defender", {}).get("status") == "detected":
                add("Defender flagged a file: submit it with the text in av-submission-notes.txt")
    lic = report.step("licenses")
    if lic and lic.status == "warning":
        add("some bundled packages have no license metadata; check THIRD_PARTY_LICENSES.txt")
    if project is not None and not getattr(project, "app_id", None):
        add("set a fixed app_id before the first release ('pyi-doctor init --write' writes one)")
    if project is not None and getattr(project, "detected", None):
        add("settings were guessed; save them with 'pyi-doctor init --write' to review and keep them")
    return tips


def summary_lines(report: Report, project=None, *, release_path: Optional[Path] = None) -> List[str]:
    """Step table, produced files and next steps, as plain text lines."""
    lines = ["", "=" * 60, f"Result: {'OK' if report.ok else 'FAILED'}"]
    for s in report.steps:
        msg = s.message.splitlines()[0] if s.message else ""
        if len(msg) > 90:
            msg = msg[:87] + "..."
        lines.append(f"  {_LABEL.get(s.status, s.status):4}  {s.name:<10} {msg}")
    rel = release_path or (project.release_path if project is not None else None)
    rstep = report.step("release")
    if rel is not None and rstep and rstep.data.get("files"):
        lines.append("")
        lines.append(f"Files in {rel}:")
        for name in rstep.data["files"]:
            p = Path(rel) / name
            lines.append(f"  {name:<44} {_size(p)}".rstrip())
    tips = next_steps(report, project)
    if tips:
        lines.append("")
        lines.append("Next steps:")
        lines += [f"  - {t}" for t in tips[:8]]
    lines.append("=" * 60)
    return lines


# ------------------------------------------------------------------ brief mode
# Short output for AI coding agents and CI logs: only what needs action, one line each.
AGENT_ENV_VARS = ("AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CURSOR_AGENT", "CODEX_SANDBOX", "GEMINI_CLI", "AIDER_MODEL")


def brief_default() -> bool:
    """``PYI_DOCTOR_BRIEF`` (1/0) if set; otherwise on when a known AI coding agent environment variable is set."""
    import os

    val = os.environ.get("PYI_DOCTOR_BRIEF")
    if val is not None and val.strip():
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return any(os.environ.get(k) for k in AGENT_ENV_VARS)


def guessed_line(notes: List[str]) -> str:
    """``guessed: entry=main.py name=demo console=true`` from Project.detected notes."""
    parts = []
    for n in notes:
        key, _, rest = n.partition(" = ")
        val = rest.split("  (", 1)[0].strip().strip('"')
        parts.append(f"{key}={val}")
    return "guessed: " + " ".join(parts)


def brief_lines(report: Report, project=None) -> List[str]:
    """Build result in a few lines: non-OK steps, release folder, next steps."""
    ok = report.ok
    bad = [s for s in report.steps if s.status in ("failed", "warning")]
    out = [f"{'OK' if ok else 'FAILED'}: {len(report.steps)} steps, {len(bad)} need attention"]
    for s in bad:
        msg = (s.message.splitlines() or [""])[0][:120]
        out.append(f"{_LABEL[s.status]} {s.name}: {msg}")
    port = report.step("portable")
    if port and port.status == "failed" and port.data.get("log_tail"):
        tail = [x for x in str(port.data["log_tail"]).splitlines() if x.strip()][-5:]
        out += [f"  | {x[:160]}" for x in tail]
    rstep = report.step("release")
    if rstep and project is not None:
        out.append(f"release: {project.release_path} ({len(rstep.data.get('files') or [])} files)")
    tips = [t for t in next_steps(report, project) if not t.startswith("antivirus risk:") or not ok]
    out += [f"next: {t}" for t in tips[:5]]
    return out


def diagnosis_brief(r) -> str:
    keys = ",".join(f.key for f in r.factors) or "none"
    line = f"{r.path}: score {r.score}/100 {r.level}; factors {keys}"
    if r.defender.status in ("clean", "detected", "error"):
        line += f"; defender {r.defender.status}"
    return line


def brief_dict(r) -> dict:
    """Diagnosis without PE details, for compact JSON."""
    return {"path": r.path, "score": r.score, "level": r.level, "factors": [f.key for f in r.factors],
            "defender": r.defender.status}
