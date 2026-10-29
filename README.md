# pyi-doctor

[한국어 문서 (Korean)](README.ko.md)

**Build a PyInstaller app as a portable exe and a setup wizard in one call, then check whether it starts outside your machine, which antivirus false-positive risk factors it carries, and which third-party licenses it bundles.**

Tools such as briefcase and pyship focus on shipping an app. pyi-doctor focuses on the problems that tend to show up after shipping a PyInstaller build: "it works on my PC only", "Windows Defender calls it a trojan", and "which licenses am I redistributing?".

```python
from pyi_doctor import Project, build_all

project = Project(entry="main.py", name="MyApp", version="1.0.0", publisher="Me")
report = build_all(project, log=print)
print(report.ok)
```

```
dist/release/
├── MyApp-1.0.0-portable-win64.zip   # onedir build (+ THIRD_PARTY_LICENSES.txt)
├── MyApp-1.0.0-win64.exe            # single-file build (if onefile = true)
├── MyApp-1.0.0-setup.exe            # Inno Setup wizard (Windows + Inno Setup)
├── MyApp-1.0.0.cdx.json             # CycloneDX 1.5 SBOM
├── THIRD_PARTY_LICENSES.txt
├── SHA256SUMS.txt
├── av-submission-notes.txt          # text for false-positive submission forms
├── report.md / report.json
```

## What it does

| Step | What happens |
|---|---|
| Portable build | PyInstaller **onedir** build (default) with `--noupx`, a Windows version resource and your icon. Optional **onefile** build. |
| Installer | Writes an Inno Setup script (fixed AppId for upgrades, per-user install by default, modern wizard, Start menu / optional desktop shortcut, uninstaller, English + Korean and other languages) and compiles it when Inno Setup 6.3+ is installed. |
| Smoke test | Copies each build to a temp folder and starts it with Python's environment variables and folders removed from the environment. Reports missing modules, data files and DLL errors with hints (e.g. "add 'x' to hidden_imports"). |
| Antivirus diagnosis | Reads the PE file (pure Python) and scores commonly cited risk factors: UPX sections, onefile, missing version resource, no signature, prebuilt bootloader, default icon. On Windows it can also scan with the local Microsoft Defender CLI (no remediation). |
| Licenses + SBOM | Maps bundled modules (from PyInstaller's TOC files) to installed distributions and writes `THIRD_PARTY_LICENSES.txt` (also placed next to the exe and inside the installer) and a CycloneDX SBOM. |
| Release folder | Collects the files, writes `SHA256SUMS.txt`, a report and false-positive submission notes. Optional upload to a GitHub release through the GitHub CLI (`gh`). |

Only the onedir build is required to succeed; every other step records its own status, so a missing tool (for example Inno Setup) does not stop the rest.

## Install

```bash
pip install pyi-doctor
```

Requirements: Python 3.9+, PyInstaller 6+ (installed as a dependency). The installer step needs Windows and [Inno Setup](https://jrsoftware.org/isinfo.php) 6.3 or newer (not bundled; set `PYI_DOCTOR_ISCC` if it is not in the default location). The GitHub upload needs the [GitHub CLI](https://cli.github.com/).

## Configuration

Add a table to your `pyproject.toml` (`pyi-doctor init --write` adds a template):

```toml
[tool.pyi-doctor]
entry = "main.py"
name = "MyApp"
version = "1.0.0"
publisher = "Your Name"
description = "What the app does"
icon = "assets/app.ico"
app_id = "PUT-A-FIXED-GUID-HERE"   # keep it fixed after the first release
console = false
onefile = false
license_file = "LICENSE"            # shown in the installer
add_data = ["assets=assets"]
hidden_imports = []
languages = ["english", "korean"]
```

`name`, `version` and `description` fall back to `[project]`. If `app_id` is not set, a GUID is derived from publisher and name; changing either then changes the GUID, so set it explicitly before the first release.

<details>
<summary>All settings</summary>

| Key | Default | Meaning |
|---|---|---|
| `entry` | required | Script PyInstaller starts from |
| `name` | required | Exe / installer name |
| `version` | `0.1.0` | Version string; the first four numbers go into Windows version resources |
| `publisher`, `description`, `copyright`, `website` | `""` | Metadata for version resources, installer and reports |
| `icon` | none | `.ico` file |
| `app_id` | derived | Installer GUID |
| `console` | `false` | `true` for command-line apps |
| `onefile` | `false` | Also build a single-file exe |
| `upx` | `false` | Allow UPX (adds a high-weight risk factor) |
| `hidden_imports`, `excludes` | `[]` | Passed to PyInstaller |
| `add_data` | `[]` | `"src=dest"`, `"src;dest"` or `["src", "dest"]` |
| `pyinstaller_args` | `[]` | Extra PyInstaller arguments |
| `license_file` | none | License page in the installer |
| `privileges` | `lowest` | `lowest` (per-user, can be raised in the wizard) or `admin` |
| `languages` | `["english", "korean"]` | Installer languages available in your Inno Setup |
| `smoke_args`, `smoke_timeout` | `[]`, `15` | Arguments and seconds for the smoke test |
| `output_dir`, `work_dir` | `dist`, `build` | Output folders |

</details>

## Command line

```bash
pyi-doctor build                        # full pipeline from pyproject.toml
pyi-doctor build --onefile --no-defender
pyi-doctor build --github-release v1.0.0 --github-repo me/myapp
pyi-doctor diagnose dist/portable/MyApp/MyApp.exe   # score any exe
pyi-doctor diagnose MyApp.exe --no-icon             # also count the default-icon factor
pyi-doctor smoke dist/portable/MyApp/MyApp.exe -- --version
pyi-doctor iss                          # write the Inno Setup script only
pyi-doctor init --write                 # add [tool.pyi-doctor] to pyproject.toml
```

## Library API

```python
from pyi_doctor import Project, build_portable, build_installer, smoke_test, diagnose, collect_licenses

project = Project.from_pyproject("pyproject.toml")
onedir = build_portable(project)                     # Artifact(kind="onedir", path=..., folder=...)
result = smoke_test(onedir.path, timeout=10)         # SmokeResult(ok, status, missing_modules, ...)
risk = diagnose(onedir.path, icon_configured=True)   # Diagnosis(score, level, factors, defender, ...)
setup = build_installer(project, onedir.folder)      # Windows + Inno Setup
packages, mode = collect_licenses(project.work_path / "onedir")
```

`build_all(project, onefile=..., installer=..., smoke_test=..., diagnose=..., defender=..., licenses=..., sbom_file=..., github_tag=...)` runs everything and returns a `Report` with `.ok`, `.steps`, `.to_markdown()` and `.to_json()`.

## About the antivirus score

The score adds fixed weights for factors that are commonly reported to affect heuristic detection of PyInstaller executables (UPX 30, onefile 20, no version resource 15, unsigned 15, prebuilt bootloader 10, default icon 5). It is meant for comparing your own builds. It does not predict the verdict of Microsoft Defender or any other product, and a low score does not mean a file will not be flagged.

pyi-doctor does not obfuscate, encrypt or otherwise hide code from scanners. It applies ordinary distribution practices (version information, no packer, onedir layout, signing advice) and helps you prepare a false-positive report. Code signing with a certificate from a trusted authority is generally the most effective measure and is outside the scope of this tool.

## Measured on the bundled example

`examples/hello`, Linux x86_64, Python 3.13, PyInstaller 6.22.3 (installer step skipped because it needs Windows):

| Step | Time |
|---|---|
| onedir build | 4.5 s |
| onefile build | 12.2 s |
| licenses + SBOM | 0.8 s |
| smoke test (both builds) | 0.6 s |
| diagnosis | < 0.1 s |
| release folder (zip + checksums) | 1.5 s |
| **total** | **19.7 s** |

Test suite: 140 tests (including 2 real PyInstaller builds) on Linux; CI runs the unit tests on Python 3.9–3.14 on Linux and Windows. Windows-specific paths (version resource, Inno Setup compilation, Defender scan, error-dialog detection in the smoke test) run in the Windows CI jobs.

## Known limitations

- Windows features (installer, version resource, Defender scan) need Windows. On other systems the `.iss` script is still written.
- The smoke test is a heuristic. It removes Python from the environment but does not reproduce a clean PC (installed DLLs, Visual C++ runtime, fonts, etc.). GUI apps that keep running are counted as started. Windowed apps that crash show a PyInstaller error dialog; on Windows pyi-doctor looks for that dialog by window title, which may not catch every case.
- The bootloader check compares the installed PyInstaller bootloader with the hashes recorded in its wheel. It describes the PyInstaller in the current environment, not an exe built elsewhere.
- License notices come from package metadata, which can be missing or wrong; packages loaded dynamically may be missed. Review the generated file.
- Only PyInstaller is supported (no Nuitka / cx_Freeze). No code signing and no auto-update.

## Disclaimer

pyi-doctor is rule-based and may miss problems or report problems that do not exist. Its reports are informational and do not replace your own testing, review of licenses, or obligations under applicable law; checking the results and complying with license terms and laws remain the user's responsibility. The software is provided "AS IS", without warranty of any kind, under the MIT License.

pyi-doctor is an independent project and is not affiliated with or endorsed by the PyInstaller project, Jordan Russell / Inno Setup, Microsoft, or GitHub. Product names are trademarks of their respective owners.

## License

MIT © 2026 서주원 (OMEGA SYSTEMS) · Contact: seodes105@gmail.com
