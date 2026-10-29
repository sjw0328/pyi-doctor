# Changelog

## 1.1.0 - 2026-10-29

- CLI: `pyi-doctor diagnose --icon / --no-icon` to include the default-icon factor (previously always skipped on the command line).
- `diagnose(..., expect_pyinstaller=False)`: skips the "no PyInstaller archive marker" note; `build_all` uses it for the Inno Setup installer, so the report no longer shows that note for every installer.
- Python 3.14 added to classifiers and the CI matrix.
- CI/release workflows: GitHub Actions updated to Node 24 versions (checkout v7, setup-python v7, upload-artifact v7, download-artifact v8).
- Removed unused imports in tests.

## 1.0.0 - 2026-10-24

First release.

- `build_all()` pipeline: PyInstaller onedir (+ optional onefile) build, Inno Setup installer, smoke test, antivirus risk-factor diagnosis, third-party license notices, CycloneDX 1.5 SBOM, release folder with SHA256 checksums, report (Markdown/JSON) and false-positive submission notes.
- Pure-Python PE reader (sections, resources, signature directory, PyInstaller archive marker).
- Optional Microsoft Defender scan via `MpCmdRun.exe` (Windows).
- Optional upload to GitHub Releases via the GitHub CLI.
- CLI: `build`, `diagnose`, `smoke`, `iss`, `init`.
- Configuration from `[tool.pyi-doctor]` in `pyproject.toml`.
