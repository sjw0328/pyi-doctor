# Changelog

## 1.3.0 - 2026-11-11

- Experimental: build a PyInstaller-format Windows x64 exe on Linux/macOS without Wine: `pyi-doctor build --target win`, `build_windows()`, `build_all(..., target="win")`. Uses the python.org Windows embeddable package, the `win_amd64` PyInstaller wheel (bootloader) and `win_amd64` / pure-Python wheels; modules via PyInstaller's modulegraph, DLLs via `pefile`, hooks answered from the unpacked files; icon, version information and manifest written as PE resources in pure Python. Stops with `CrossBuildUnsupported` (list of reasons) when the app is outside the supported scope.
- Writes an Inno Setup script with relative paths (`dist/<name>.iss`) for compiling on Windows.
- New setting `requirements` (defaults to `[project].dependencies`); CLI `-r/--requirements`, `--python-version`.
- Example workflow `examples/github-actions/cross-build-windows.yml` (build on Linux, start test on Windows); CI runs the same.
- New dependency: `pefile`.

## 1.2.0 - 2026-11-08

- Version bump to publish the 1.1.0 changes to PyPI (the 1.1.0 release did not reach PyPI because its tag did not match the package version). No code changes.

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
