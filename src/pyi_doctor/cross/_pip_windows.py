"""Run pip with Windows environment markers (internal helper, run as a script).

``pip download --platform win_amd64`` picks Windows wheels but still evaluates
markers such as ``sys_platform == "win32"`` against the build machine, so
Windows-only dependencies (for example ``colorama`` for ``click``) would be
skipped. This wrapper makes pip's marker environment look like 64-bit Windows.
"""

import sys

from pip._vendor.packaging import markers

_original = markers.default_environment


def _windows_environment():
    env = _original()
    env.update(
        os_name="nt",
        sys_platform="win32",
        platform_system="Windows",
        platform_machine="AMD64",
        platform_release="10",
        platform_version="10.0.19045",
        implementation_name="cpython",
        platform_python_implementation="CPython",
    )
    return env


markers.default_environment = _windows_environment

from pip._internal.cli.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
