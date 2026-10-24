"""Smoke test: start the built executable away from the build machine's Python.

The build output is copied to a temporary folder and started with a reduced
environment (no PYTHONPATH/PYTHONHOME/virtualenv, Python folders removed from
PATH). This catches many "works on my PC only" problems such as missing hidden
imports or data files. It is a heuristic and does not reproduce a clean PC.

Outcome rules:

* exits with code 0 (or the expected code)       -> passed
* still running when the timeout elapses           -> passed ("alive"; e.g. GUI apps)
* exits with another code, or (Windows) an error
  dialog from the PyInstaller bootloader is shown  -> failed
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_PATTERNS = [
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"), "module"),
    (re.compile(r"ImportError: No module named '?([\w.]+)'?"), "module"),
    (re.compile(r"ImportError: cannot import name '?([\w.]+)'?"), "symbol"),
    (re.compile(r"(?:DLL load failed[^\n]*|Failed to load Python DLL[^\n]*|Error loading Python lib[^\n]*)"), "dll"),
    (re.compile(r"FileNotFoundError: [^\n]*?'([^']+)'"), "file"),
]
_FAIL_MARKERS = ("Traceback (most recent call last)", "[PYI-", "Failed to execute script", "Unhandled exception in script")
_ERROR_WINDOW_TITLES = ("Unhandled exception in script", "Failed to execute script", "Fatal error detected")


@dataclass
class SmokeResult:
    ok: bool
    status: str  # "passed-exit" | "passed-alive" | "failed" | "error"
    exit_code: Optional[int]
    duration: float
    missing_modules: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    dll_errors: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    output_tail: str = ""
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_output(text: str) -> Dict[str, List[str]]:
    """Extract missing modules, files and DLL errors from program output."""
    found: Dict[str, List[str]] = {"module": [], "symbol": [], "dll": [], "file": []}
    for rx, kind in _PATTERNS:
        for m in rx.finditer(text):
            val = m.group(1) if m.groups() else m.group(0)
            val = val.strip()
            if val and val not in found[kind]:
                found[kind].append(val)
    return found


def suggestions_for(found: Dict[str, List[str]]) -> List[str]:
    tips: List[str] = []
    for mod in found["module"]:
        tips.append(f"add '{mod}' to hidden_imports (or check that the package is installed in the build environment)")
    for f in found["file"]:
        tips.append(f"file '{f}' was not found at runtime; bundle it with add_data and load it relative to sys._MEIPASS")
    if found["dll"]:
        tips.append("a DLL failed to load; check that the Visual C++ runtime and native dependencies are bundled")
    return tips


def clean_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment without the build machine's Python settings."""
    env = dict(os.environ if base is None else base)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONSAFEPATH"):
        env.pop(key, None)
    prefixes = {os.path.normcase(os.path.abspath(p)) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix} if p}
    path_parts = env.get("PATH", "").split(os.pathsep)
    kept = []
    for part in path_parts:
        norm = os.path.normcase(os.path.abspath(part)) if part else ""
        if norm and any(norm == p or norm.startswith(p + os.sep) for p in prefixes):
            continue
        kept.append(part)
    env["PATH"] = os.pathsep.join(kept)
    return env


def _stage(exe: Path, dest_root: Path) -> Path:
    """Copy the executable (and its onedir folder) into ``dest_root``."""
    folder = exe.parent
    if (folder / "_internal").is_dir():
        target = dest_root / folder.name
        shutil.copytree(folder, target)
        return target / exe.name
    target = dest_root / exe.name
    shutil.copy2(exe, target)
    return target


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:  # pragma: no cover
        pass


def run_command(
    cmd: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: Optional[Dict[str, str]] = None,
    expect_exit_code: Optional[int] = None,
) -> SmokeResult:
    """Run ``cmd`` and classify the outcome (used by :func:`run`; testable directly)."""
    start = time.monotonic()
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    out_file = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            list(cmd), cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=out_file, stderr=subprocess.STDOUT, **kwargs
        )
    except OSError as exc:
        out_file.close()
        return SmokeResult(False, "error", None, 0.0, message=f"could not start: {exc}")

    alive = False
    error_window = ""
    deadline = start + timeout
    while True:
        code = proc.poll()
        if code is not None:
            break
        if sys.platform == "win32":
            error_window = _find_error_window(proc.pid)
            if error_window:
                break
        if time.monotonic() >= deadline:
            alive = True
            break
        time.sleep(0.2)
    if alive or error_window:
        _kill_tree(proc)
    duration = round(time.monotonic() - start, 2)
    out_file.seek(0)
    text = out_file.read().decode("utf-8", "replace")
    out_file.close()

    found = analyze_output(text)
    res = SmokeResult(
        ok=False,
        status="failed",
        exit_code=None if (alive or error_window) else proc.returncode,
        duration=duration,
        missing_modules=found["module"],
        missing_files=found["file"],
        dll_errors=found["dll"],
        suggestions=suggestions_for(found),
        output_tail="\n".join(text.splitlines()[-30:]),
    )
    if error_window:
        res.message = f"error dialog shown: {error_window!r}"
    elif alive:
        crashed_text = any(m in text for m in _FAIL_MARKERS)
        res.ok = not crashed_text
        res.status = "passed-alive" if res.ok else "failed"
        res.message = (
            f"still running after {timeout:g}s (treated as started)"
            if res.ok
            else f"still running after {timeout:g}s but printed an error"
        )
    else:
        want = 0 if expect_exit_code is None else expect_exit_code
        res.ok = res.exit_code == want
        res.status = "passed-exit" if res.ok else "failed"
        res.message = f"exited with code {res.exit_code}" + ("" if res.ok else f" (expected {want})")
    return res


def run(
    exe: Path,
    *,
    args: Sequence[str] = (),
    timeout: float = 15.0,
    isolate: bool = True,
    expect_exit_code: Optional[int] = None,
) -> SmokeResult:
    """Copy ``exe`` to a temp folder and start it with a reduced environment."""
    exe = Path(exe)
    if not exe.is_file():
        return SmokeResult(False, "error", None, 0.0, message=f"{exe} not found")
    if not isolate:
        return run_command([str(exe), *args], cwd=exe.parent, timeout=timeout, expect_exit_code=expect_exit_code)
    with tempfile.TemporaryDirectory(prefix="pyi-doctor-smoke-") as tmp:
        staged = _stage(exe, Path(tmp))
        return run_command(
            [str(staged), *args], cwd=Path(tmp), timeout=timeout, env=clean_env(), expect_exit_code=expect_exit_code
        )


# ---------------------------------------------------------------- Windows only
def _find_error_window(root_pid: int) -> str:  # pragma: no cover - Windows only
    """Return the title of a PyInstaller error dialog owned by the process tree."""
    try:
        import ctypes
        from ctypes import wintypes

        pids = _process_tree(root_pid)
        user32 = ctypes.windll.user32
        titles: List[str] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
            return True

        user32.EnumWindows(_enum, 0)
        for t in titles:
            if any(marker.lower() in t.lower() for marker in _ERROR_WINDOW_TITLES):
                return t
    except Exception:
        return ""
    return ""


def _process_tree(root_pid: int) -> set:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    tree, stack = {root_pid}, [root_pid]
    while stack:
        for child in parents.get(stack.pop(), []):
            if child not in tree:
                tree.add(child)
                stack.append(child)
    return tree
