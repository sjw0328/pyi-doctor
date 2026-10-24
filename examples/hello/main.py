"""Minimal console app used by CI to exercise the whole pipeline."""

import json
import platform
import sys


def main() -> int:
    print(json.dumps({"app": "hello", "python": platform.python_version(), "frozen": getattr(sys, "frozen", False)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
