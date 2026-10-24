"""Small subprocess helpers shared by the build steps."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from .errors import BuildError


def tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_logged(
    cmd: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    what: str,
    timeout: Optional[float] = None,
) -> str:
    """Run ``cmd``, write combined output to ``log_path`` and return it.

    Raises :class:`BuildError` with the last lines of output on failure.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BuildError(f"{what}: could not start {cmd[0]!r}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace")
        log_path.write_text(out, encoding="utf-8")
        raise BuildError(f"{what} timed out after {timeout}s", tail(out), str(log_path)) from exc
    out = (proc.stdout or b"").decode("utf-8", "replace")
    log_path.write_text(" ".join(_q(c) for c in cmd) + "\n\n" + out, encoding="utf-8")
    if proc.returncode != 0:
        raise BuildError(f"{what} failed with exit code {proc.returncode}", tail(out), str(log_path))
    return out


def _q(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


def quote_list(cmd: List[str]) -> str:
    return " ".join(_q(c) for c in cmd)
