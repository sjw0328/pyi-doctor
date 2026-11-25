"""Desktop window for pyi-doctor (tkinter, standard library only).

Start it with ``pyi-doctor gui``, ``pyi-doctor-gui`` or ``python -m pyi_doctor.gui``.

Two tabs:

* **Build**: fill in the entry script and name (or load a ``pyproject.toml``),
  pick the steps, press *Build*. Runs :func:`pyi_doctor.build_all` in a
  background thread and shows its log.
* **Diagnose**: pick any ``.exe`` and show its antivirus risk factors.

Convenience: *Open project...* fills the form from a folder (settings are
guessed when there is no ``[tool.pyi-doctor]``), picking an entry script fills
empty fields, *Check* runs the quick pre-build checks, *Save settings* writes a
``[tool.pyi-doctor]`` block, and the last form is restored on the next start.

The form-to-settings logic lives in plain functions (:func:`project_from_form`,
:func:`build_kwargs`) so it can be used and tested without a display.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import Project
from .errors import PyiDoctorError

IS_WINDOWS = sys.platform == "win32"

# Form keys and their defaults. Strings are text fields, bools are check boxes.
DEFAULTS: Dict[str, Any] = {
    "config": "",
    "entry": "",
    "name": "",
    "display_name": "",
    "version": "0.1.0",
    "publisher": "",
    "icon": "",
    "console": False,
    "onefile": False,
    "installer": True,
    "smoke_test": True,
    "diagnose": True,
    "defender": True,
    "licenses": True,
    "target": "native",
    "requirements": "",
}


def _clean(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def project_from_form(values: Dict[str, Any]) -> Project:
    """Build a :class:`Project` from form values.

    With ``config`` pointing to a ``pyproject.toml``, that file is loaded and
    the form values override it. Without it, ``entry`` and ``name`` are
    required and the project folder is the entry script's folder (outputs go
    to ``<that folder>/dist``).
    """
    v = {**DEFAULTS, **values}
    overrides: Dict[str, Any] = {
        "entry": _clean(v["entry"]),
        "name": _clean(v["name"]),
        "display_name": _clean(v["display_name"]),
        "version": _clean(v["version"]),
        "publisher": _clean(v["publisher"]),
        "icon": _clean(v["icon"]),
        "console": bool(v["console"]),
        "onefile": bool(v["onefile"]),
    }
    cfg = _clean(v["config"])
    if cfg:
        return Project.from_pyproject(cfg, **overrides)
    entry = overrides["entry"]
    if not entry or not overrides["name"]:
        raise PyiDoctorError("Entry script and name are required (or load a pyproject.toml).")
    entry_path = Path(entry).expanduser().resolve()
    data = {k: val for k, val in overrides.items() if val is not None and k != "entry"}
    if not entry_path.is_file():
        raise PyiDoctorError(f"entry script not found: {entry_path}")
    # License file and data folders next to the script are picked up (see Project.detected).
    return Project.from_folder(entry_path.parent, entry=entry_path, **data)


def form_from_project(project: Project, config: str = "") -> Dict[str, Any]:
    """Form values that show an existing :class:`Project`."""
    return {
        **DEFAULTS,
        "config": config,
        "entry": str(project.entry_path),
        "name": project.name,
        "display_name": project.display_name or "",
        "version": project.version,
        "publisher": project.publisher,
        "icon": str(project.icon_path) if project.icon_path else "",
        "console": project.console,
        "onefile": project.onefile,
    }


def build_kwargs(values: Dict[str, Any], project: Project) -> Dict[str, Any]:
    """Keyword arguments for :func:`pyi_doctor.pipeline.build_all` from form values."""
    v = {**DEFAULTS, **values}
    target = v["target"] if v["target"] in ("native", "win") else "native"
    kwargs: Dict[str, Any] = {
        "onefile": bool(v["onefile"]),
        "installer": bool(v["installer"]),
        "smoke_test": bool(v["smoke_test"]),
        "diagnose": bool(v["diagnose"]),
        "defender": bool(v["defender"]) and IS_WINDOWS,
        "licenses": bool(v["licenses"]),
        "sbom_file": bool(v["licenses"]),
        "target": target,
    }
    req = _clean(v["requirements"])
    if target == "win" and req:
        kwargs["requirements"] = list(project.requirements) + [f"-r {Path(req).expanduser().resolve()}"]
    return kwargs


def state_file() -> Path:
    """Where the window remembers the last form (per user)."""
    env = os.environ.get("PYI_DOCTOR_GUI_STATE")
    if env:
        return Path(env)
    if IS_WINDOWS and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "pyi-doctor" / "gui.json"


def load_form_state() -> Dict[str, Any]:
    """Last saved form values (only known keys, right types); empty if none or unreadable."""
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in DEFAULTS and isinstance(v, type(DEFAULTS[k]))}


def save_form_state(values: Dict[str, Any]) -> None:
    """Remember form values for the next start (errors are ignored)."""
    path = state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in values.items() if k in DEFAULTS}
        path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def form_from_path(path: str) -> "tuple[Dict[str, Any], List[str]]":
    """Form values for a folder, entry script or pyproject.toml, plus the guessed-setting notes."""
    project = Project.load(path)
    p = Path(path).expanduser().resolve()
    cfg = p if p.suffix == ".toml" else (p.parent if p.is_file() else p) / "pyproject.toml"
    has_table = False
    if cfg.is_file():
        from .config import _load_toml

        has_table = "pyi-doctor" in _load_toml(cfg).get("tool", {})
    return form_from_project(project, str(cfg) if has_table else ""), list(project.detected)


def fill_from_entry(values: Dict[str, Any], entry: str) -> Dict[str, Any]:
    """Fill empty name/version/icon and the console box from a newly picked entry script."""
    from .autodetect import detect

    out = dict(values)
    out["entry"] = entry
    try:
        det = detect(Path(entry).expanduser().resolve().parent, entry=entry).values
    except Exception:  # noqa: BLE001 - guessing is optional
        return out
    root = Path(entry).expanduser().resolve().parent
    if not str(out.get("name", "")).strip() and det.get("name"):
        out["name"] = det["name"]
    if str(out.get("version", "")).strip() in ("", DEFAULTS["version"]) and det.get("version"):
        out["version"] = det["version"]
    if not str(out.get("icon", "")).strip() and det.get("icon"):
        out["icon"] = str((root / det["icon"]).resolve())
    if "console" in det:
        out["console"] = bool(det["console"])
    return out


def settings_block(values: Dict[str, Any], project: Project) -> str:
    """``[tool.pyi-doctor]`` text for the current form (relative paths, new app_id if none)."""
    from .autodetect import config_text, new_app_id

    root = Path(project.root)

    def rel(p: Optional[Path]) -> Optional[str]:
        if p is None:
            return None
        try:
            return p.resolve().relative_to(root).as_posix()
        except ValueError:
            return str(p)

    data: Dict[str, Any] = {"entry": rel(project.entry_path), "name": project.name}
    if project.display_name:
        data["display_name"] = project.display_name
    data["version"] = project.version
    data["publisher"] = project.publisher or "Your Name"
    if project.icon_path:
        data["icon"] = rel(project.icon_path)
    data["app_id"] = project.app_id or new_app_id()
    data["console"] = project.console
    data["onefile"] = bool(values.get("onefile", project.onefile))
    if project.license_file:
        data["license_file"] = rel(project.license_path)
    if project.add_data:
        data["add_data"] = [f"{s}={d}" for s, d in project.add_data]
    data["languages"] = list(project.languages)
    return config_text(data)


def write_settings(values: Dict[str, Any], project: Project) -> Path:
    """Add the form as ``[tool.pyi-doctor]`` to ``<project root>/pyproject.toml``. Refuses to overwrite one."""
    path = Path(project.root) / "pyproject.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "[tool.pyi-doctor]" in text:
        raise PyiDoctorError(f"{path} already has [tool.pyi-doctor]; edit it there")
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + settings_block(values, project), encoding="utf-8")
    return path


def open_folder(path: Path) -> None:
    """Open a folder (or a file with its default app)."""
    path = Path(path)
    if IS_WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _import_tk():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError as exc:  # pragma: no cover - depends on the Python build
        raise PyiDoctorError(
            "tkinter is not available in this Python. On Debian/Ubuntu install 'python3-tk'; "
            "the python.org installers for Windows and macOS include it."
        ) from exc
    return tk, ttk, filedialog, messagebox, ScrolledText


class App:  # pragma: no cover - needs a display; logic is tested through the functions above
    POLL_MS = 100

    def __init__(self, root) -> None:
        tk, ttk, self.filedialog, self.messagebox, ScrolledText = _import_tk()
        self.tk = tk
        self.root = root
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.last_project: Optional[Project] = None

        root.title(f"pyi-doctor {__version__}")
        root.minsize(720, 600)
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.vars: Dict[str, Any] = {}

        # ---------------------------------------------------------- Build tab
        b = ttk.Frame(nb, padding=10)
        nb.add(b, text="Build")
        b.columnconfigure(1, weight=1)
        row = 0

        def text_row(key: str, label: str, browse: Optional[str] = None, filetypes=None) -> None:
            nonlocal row
            ttk.Label(b, text=label).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=DEFAULTS[key])
            self.vars[key] = var
            ttk.Entry(b, textvariable=var).grid(row=row, column=1, sticky="ew", pady=2)
            if browse == "entry":
                ttk.Button(b, text="Browse...", command=lambda: self._browse_entry(filetypes)).grid(
                    row=row, column=2, padx=(6, 0), pady=2)
            elif browse:
                ttk.Button(b, text="Browse...", command=lambda: self._browse(var, filetypes)).grid(
                    row=row, column=2, padx=(6, 0), pady=2)
            row += 1

        top = ttk.Frame(b)
        top.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        row += 1
        ttk.Button(top, text="Open project folder...", command=self._open_project).pack(side="left")
        ttk.Label(top, text="Fills the form; missing settings are guessed. Or pick the entry script below.",
                  foreground="gray").pack(side="left", padx=8)
        text_row("config", "pyproject.toml (optional)", "file", [("pyproject.toml", "*.toml"), ("All files", "*")])
        ttk.Button(b, text="Load", command=self._load_config).grid(row=row - 1, column=3, padx=(6, 0))
        text_row("entry", "Entry script *", "entry", [("Python", "*.py *.pyw"), ("All files", "*")])
        text_row("name", "App name *")
        text_row("display_name", "Display name")
        text_row("version", "Version")
        text_row("publisher", "Publisher")
        text_row("icon", "Icon (.ico)", "file", [("Icon", "*.ico"), ("All files", "*")])

        opts = ttk.LabelFrame(b, text="Steps", padding=8)
        opts.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 4))
        row += 1
        checks = [
            ("console", "Console app"),
            ("onefile", "Also build a single-file exe"),
            ("installer", "Installer (Windows + Inno Setup)"),
            ("smoke_test", "Smoke test"),
            ("diagnose", "Antivirus risk diagnosis"),
            ("defender", "Microsoft Defender scan (Windows)"),
            ("licenses", "License notices + SBOM"),
        ]
        for i, (key, label) in enumerate(checks):
            var = tk.BooleanVar(value=DEFAULTS[key])
            self.vars[key] = var
            cb = ttk.Checkbutton(opts, text=label, variable=var)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 24), pady=1)
            if key == "defender" and not IS_WINDOWS:
                var.set(False)
                cb.state(["disabled"])

        tgt = ttk.Frame(b)
        tgt.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(4, 4))
        row += 1
        self.vars["target"] = tk.StringVar(value="native")
        ttk.Label(tgt, text="Target:").pack(side="left")
        ttk.Radiobutton(tgt, text="This OS", value="native", variable=self.vars["target"]).pack(side="left", padx=6)
        win = ttk.Radiobutton(tgt, text="Windows x64 from this OS (experimental)", value="win",
                              variable=self.vars["target"])
        win.pack(side="left", padx=6)
        if IS_WINDOWS:
            win.state(["disabled"])
        text_row("requirements", "Requirements file (win)", "file",
                 [("Text", "*.txt"), ("All files", "*")])

        actions = ttk.Frame(b)
        actions.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(6, 4))
        row += 1
        self.build_btn = ttk.Button(actions, text="Build", command=self._start_build)
        self.build_btn.pack(side="left")
        self.check_btn = ttk.Button(actions, text="Check", command=self._run_check)
        self.check_btn.pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Save settings", command=self._save_settings).pack(side="left", padx=(6, 0))
        self.open_btn = ttk.Button(actions, text="Open output folder", command=self._open_output)
        self.open_btn.pack(side="left", padx=6)
        self.open_btn.state(["disabled"])
        self.report_btn = ttk.Button(actions, text="Open report", command=self._open_report)
        self.report_btn.pack(side="left")
        self.report_btn.state(["disabled"])
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=10)

        self.log = ScrolledText(b, height=14, wrap="word", state="disabled")
        self.log.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        b.rowconfigure(row, weight=1)

        # ------------------------------------------------------- Diagnose tab
        d = ttk.Frame(nb, padding=10)
        nb.add(d, text="Diagnose")
        d.columnconfigure(1, weight=1)
        ttk.Label(d, text="Executable").grid(row=0, column=0, sticky="w")
        self.diag_path = tk.StringVar()
        ttk.Entry(d, textvariable=self.diag_path).grid(row=0, column=1, sticky="ew")
        ttk.Button(d, text="Browse...",
                   command=lambda: self._browse(self.diag_path, [("Executable", "*.exe"), ("All files", "*")])).grid(
            row=0, column=2, padx=(6, 0))
        self.diag_defender = tk.BooleanVar(value=IS_WINDOWS)
        dcb = ttk.Checkbutton(d, text="Microsoft Defender scan (Windows)", variable=self.diag_defender)
        dcb.grid(row=1, column=1, sticky="w", pady=4)
        if not IS_WINDOWS:
            dcb.state(["disabled"])
        self.diag_btn = ttk.Button(d, text="Diagnose", command=self._start_diagnose)
        self.diag_btn.grid(row=1, column=2, sticky="e", pady=4)
        self.diag_out = ScrolledText(d, height=20, wrap="word", state="disabled")
        self.diag_out.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        d.rowconfigure(2, weight=1)
        ttk.Label(d, text="The score is a rough indicator for comparing builds, not a prediction of antivirus results.",
                  foreground="gray").grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        saved = load_form_state()
        if saved:
            self._set_values(saved, all_keys=True)
            self.status.set("Restored the last form.")
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(self.POLL_MS, self._poll)

    def open_path(self, path: str) -> None:
        """Fill the form from a folder, entry script or pyproject.toml."""
        try:
            values, notes = form_from_path(path)
        except PyiDoctorError as exc:
            self.messagebox.showerror("pyi-doctor", str(exc))
            return
        self._set_values(values)
        self._write(self.log, f"Opened {path}", clear=True)
        if notes:
            self._write(self.log, "Guessed settings (check them, then 'Save settings' keeps them):")
            for n in notes:
                self._write(self.log, f"  {n}")
        self.status.set("Project loaded.")

    # ------------------------------------------------------------ helpers
    def _browse(self, var, filetypes) -> None:
        path = self.filedialog.askopenfilename(filetypes=filetypes or [("All files", "*")])
        if path:
            var.set(path)

    def _browse_entry(self, filetypes) -> None:
        path = self.filedialog.askopenfilename(filetypes=filetypes or [("All files", "*")])
        if path:
            self._set_values(fill_from_entry(self._values(), path))
            self.status.set("Filled empty fields from the script.")

    def _values(self) -> Dict[str, Any]:
        return {k: v.get() for k, v in self.vars.items()}

    def _set_values(self, values: Dict[str, Any], *, all_keys: bool = False) -> None:
        steps = ("installer", "smoke_test", "diagnose", "defender", "licenses", "target", "requirements")
        for k, val in values.items():
            if k in self.vars and (all_keys or k not in steps):
                if k == "defender" and not IS_WINDOWS:
                    continue
                if k == "target" and IS_WINDOWS:
                    continue
                self.vars[k].set(val)

    def _close(self) -> None:
        save_form_state(self._values())
        self.root.destroy()

    def _write(self, widget, text: str, *, clear: bool = False) -> None:
        widget.configure(state="normal")
        if clear:
            widget.delete("1.0", "end")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _busy(self, busy: bool) -> None:
        for btn in (self.build_btn, self.diag_btn, self.check_btn):
            btn.state(["disabled"] if busy else ["!disabled"])

    # ------------------------------------------------------------ actions
    def _load_config(self) -> None:
        cfg = self.vars["config"].get().strip()
        if not cfg:
            cfg = self.filedialog.askopenfilename(filetypes=[("pyproject.toml", "*.toml"), ("All files", "*")])
            if not cfg:
                return
        try:
            project = Project.from_pyproject(cfg)
        except PyiDoctorError as exc:
            self.messagebox.showerror("pyi-doctor", str(exc))
            return
        self._set_values(form_from_project(project, cfg))
        self.status.set(f"Loaded {Path(cfg).name}")

    def _open_project(self) -> None:
        folder = self.filedialog.askdirectory()
        if folder:
            self.open_path(folder)

    def _project_or_error(self) -> Optional[Project]:
        try:
            return project_from_form(self._values())
        except PyiDoctorError as exc:
            self.messagebox.showerror("pyi-doctor", str(exc))
            return None

    def _run_check(self) -> None:
        from .preflight import check_lines, run_checks

        values = self._values()
        try:
            project, error = project_from_form(values), None
        except PyiDoctorError as exc:
            project, error = None, str(exc)
        target = values.get("target", "native")
        self._write(self.log, "Checking ...", clear=True)
        self._write(self.log, "\n".join(check_lines(run_checks(project, target=target, load_error=error))))

    def _save_settings(self) -> None:
        project = self._project_or_error()
        if project is None:
            return
        try:
            path = write_settings(self._values(), project)
        except PyiDoctorError as exc:
            self.messagebox.showinfo("pyi-doctor", str(exc))
            return
        self.vars["config"].set(str(path))
        self.status.set(f"Saved to {path.name}")
        self._write(self.log, f"Added [tool.pyi-doctor] to {path}. Keep app_id unchanged after the first release.")

    def _open_report(self) -> None:
        if self.last_project is None:
            return
        report = self.last_project.release_path / "report.md"
        if report.is_file():
            open_folder(report)

    def _start_build(self) -> None:
        values = self._values()
        save_form_state(values)
        try:
            project = project_from_form(values)
            project.check_files()
            kwargs = build_kwargs(values, project)
        except PyiDoctorError as exc:
            self.messagebox.showerror("pyi-doctor", str(exc))
            return
        self.last_project = project
        self._busy(True)
        self.status.set("Building ...")
        self._write(self.log, f"pyi-doctor {__version__} - {project.name} {project.version}", clear=True)
        for note in project.detected:
            self._write(self.log, f"  guessed: {note}")

        def work() -> None:
            from .pipeline import build_all

            try:
                report = build_all(project, log=lambda m: self.events.put(("log", m)), **kwargs)
                self.events.put(("built", report))
            except Exception as exc:  # noqa: BLE001 - shown to the user
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _start_diagnose(self) -> None:
        path = Path(self.diag_path.get().strip())
        if not path.is_file():
            self.messagebox.showerror("pyi-doctor", f"File not found: {path}")
            return
        defender = bool(self.diag_defender.get()) and IS_WINDOWS
        self._busy(True)
        self._write(self.diag_out, f"Diagnosing {path} ...", clear=True)

        def work() -> None:
            from .antivirus import diagnose

            try:
                self.events.put(("diagnosed", diagnose(path, defender=defender)))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("diag_error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _open_output(self) -> None:
        if self.last_project is None:
            return
        folder = self.last_project.release_path
        if not folder.is_dir():
            folder = self.last_project.output_path
        if folder.is_dir():
            open_folder(folder)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._write(self.log, str(payload))
                elif kind == "built":
                    from .summary import summary_lines

                    self._busy(False)
                    self.open_btn.state(["!disabled"])
                    self.report_btn.state(["!disabled"])
                    ok = payload.ok
                    self.status.set("Done." if ok else "Finished with failures - see Next steps in the log.")
                    self._write(self.log, "\n".join(summary_lines(payload, self.last_project)))
                    self._write(self.log, f"Report: {self.last_project.release_path / 'report.md'}")
                elif kind == "error":
                    self._busy(False)
                    self.status.set("Failed.")
                    self._write(self.log, f"error: {payload}")
                    self.messagebox.showerror("pyi-doctor", str(payload))
                elif kind == "diagnosed":
                    self._busy(False)
                    self._write(self.diag_out, "\n".join(diagnosis_lines(payload)), clear=True)
                elif kind == "diag_error":
                    self._busy(False)
                    self._write(self.diag_out, f"error: {payload}")
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll)


def diagnosis_lines(r) -> List[str]:
    """Readable lines for a :class:`~pyi_doctor.antivirus.Diagnosis` (shared with the CLI)."""
    lines = [
        f"{r.path}",
        f"  risk score : {r.score}/100 ({r.level})  [indicator only]",
        f"  sha256     : {r.sha256}",
        f"  defender   : {r.defender.status} {r.defender.detail}".rstrip(),
        f"  bootloader : {r.bootloader}",
    ]
    for f in r.factors:
        lines.append(f"  - [{f.weight:2}] {f.label}\n         -> {f.advice}")
    for n in r.notes:
        lines.append(f"  note: {n}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - opens a window
    """Open the window. Returns 0 when it is closed, 1 if tkinter is missing."""
    try:
        tk, *_ = _import_tk()
    except PyiDoctorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if IS_WINDOWS:
        try:  # sharp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    app = App(root)
    if args:
        app.open_path(args[0])
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
