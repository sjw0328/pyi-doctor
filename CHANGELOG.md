# Changelog

## 1.0.0 - 2026-10-24

First release.

- `build_all()` pipeline: PyInstaller onedir (+ optional onefile) build, Inno Setup installer, smoke test, antivirus risk-factor diagnosis, third-party license notices, CycloneDX 1.5 SBOM, release folder with SHA256 checksums, report (Markdown/JSON) and false-positive submission notes.
- Pure-Python PE reader (sections, resources, signature directory, PyInstaller archive marker).
- Optional Microsoft Defender scan via `MpCmdRun.exe` (Windows).
- Optional upload to GitHub Releases via the GitHub CLI.
- CLI: `build`, `diagnose`, `smoke`, `iss`, `init`.
- Configuration from `[tool.pyi-doctor]` in `pyproject.toml`.
