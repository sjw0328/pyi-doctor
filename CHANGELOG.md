# Changelog

## 1.6.0 - 2026-11-15

- Zero-config: without `[tool.pyi-doctor]`, entry script, name, version, console/window mode, icon, license file and data folders are guessed from the project folder and printed. `pyi-doctor build [PATH]` accepts an entry script, project folder or `pyproject.toml`. Library: `Project.load()`, `Project.from_folder()`, `Project.detected`, `detect()`. `from_pyproject()` guesses a missing `entry`.
- New command `pyi-doctor check` (library: `check()`): quick checks of PyInstaller, virtual environment, Inno Setup, entry script syntax, icon (valid `.ico`, 256x256), `app_id`, publisher, missing files and cross-build limits, each with what to change. `build` runs them first and stops on problems that would make PyInstaller fail (`--no-check` skips).
- `pyi-doctor init` fills the block from the project folder and adds a new fixed `app_id`; keys already in `[project]` are left out. `--entry` picks the script.
- Build summary with release files and next steps (CLI and window).
- Brief output for AI coding agents and CI logs: `--brief` on `build`, `check`, `diagnose`, `smoke`; compact `--json`. On automatically when an agent environment variable is set (`AI_AGENT`, `CLAUDECODE`, ...); `PYI_DOCTOR_BRIEF=0/1` overrides. New `pyi-doctor quickref` and `llms.txt`.
- `build` options `--icon`, `--publisher`, `--display-name`, `--console/--no-console`, `--open`.
- "did you mean" suggestions for unknown settings and installer languages.
- Window: *Open project folder...*, entry script fills empty fields, *Check*, *Save settings*, *Open report*, summary after the build, last form restored; `pyi-doctor gui [PATH]`.

## 1.5.0 - 2026-11-15

- New setting `display_name`: the name shown in the installer, Start menu, default install folder, Apps list and the exe's ProductName. Previously these always used `name` (the exe file name), so an exe named `MY_APP` showed as `MY_APP` everywhere. Defaults to `name`; also in the desktop window.
- New setting `installer_name`: installer file name template with `{name}`, `{version}`, `{display_name}` (default unchanged: `{name}-{version}-setup`). Lets an existing update channel that expects a fixed file name keep working.
- `app_id` now also accepts a non-GUID AppId, as Inno Setup does, so an app first released with a hand-written script can be upgraded in place. Braces and control characters are rejected.

## 1.4.0 - 2026-11-15

- Desktop window (tkinter, no new dependency): `pyi-doctor gui`, `pyi-doctor-gui` or `python -m pyi_doctor.gui`. Build tab runs `build_all()` with a live log (form fields or a loaded `pyproject.toml`, step check boxes, Windows cross-build target); Diagnose tab scores any `.exe`.
- Form helpers usable without a display: `pyi_doctor.gui.project_from_form()`, `build_kwargs()`, `form_from_project()`.
- CLI `diagnose` output now shares its formatting with the window (`gui.diagnosis_lines`); text unchanged.

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
