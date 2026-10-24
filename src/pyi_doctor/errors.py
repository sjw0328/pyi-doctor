"""Exception types used by pyi-doctor."""


class PyiDoctorError(Exception):
    """Base class for all pyi-doctor errors."""


class ConfigError(PyiDoctorError):
    """The project configuration is missing or invalid."""


class BuildError(PyiDoctorError):
    """An external build step (PyInstaller, Inno Setup) failed."""

    def __init__(self, message: str, log_tail: str = "", log_path: str = ""):
        super().__init__(message)
        self.log_tail = log_tail
        self.log_path = log_path

    def __str__(self) -> str:  # pragma: no cover - formatting only
        base = super().__str__()
        if self.log_path:
            base += f" (log: {self.log_path})"
        if self.log_tail:
            base += "\n--- last lines ---\n" + self.log_tail
        return base


class ToolNotFoundError(PyiDoctorError):
    """A required external tool could not be located."""
