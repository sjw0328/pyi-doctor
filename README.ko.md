# pyi-doctor

[![PyPI](https://img.shields.io/pypi/v/pyi-doctor)](https://pypi.org/project/pyi-doctor/)
[![Python](https://img.shields.io/pypi/pyversions/pyi-doctor)](https://pypi.org/project/pyi-doctor/)
[![Downloads](https://static.pepy.tech/badge/pyi-doctor)](https://pepy.tech/project/pyi-doctor)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[English](README.md)

**내 PC에서는 잘 되던 PyInstaller exe가 다른 PC에서는 트로이목마로 잡힐 때, 왜 그런지 확인하는 도구입니다.**

```bash
pip install pyi-doctor
pyi-doctor diagnose MyApp.exe
```

**PyInstaller 앱을 한 번의 호출로 포터블 exe와 설치 마법사로 빌드하고, 다른 PC에서 실행되는지·백신 오탐 위험 요인이 있는지·어떤 서드파티 라이선스를 포함하는지 확인하는 파이썬 라이브러리입니다.**

briefcase·pyship 같은 도구는 "배포" 자체에 초점을 둡니다. pyi-doctor는 PyInstaller로 배포한 뒤 자주 생기는 문제, 즉 "내 PC에서만 된다", "Windows Defender가 트로이목마라고 한다", "어떤 라이선스를 재배포하고 있는지 모른다"에 초점을 둡니다.

```python
from pyi_doctor import Project, build_all

project = Project(entry="main.py", name="MyApp", version="1.0.0", publisher="Me")
report = build_all(project, log=print)
print(report.ok)
```

결과물은 `dist/release/`에 모입니다: 포터블 zip, 단일 exe(선택), 설치 프로그램, SBOM, 라이선스 고지, SHA256 체크섬, 오탐 신고용 문안, 리포트.

## 만든 이유

한국의 초등학생 개발자가 직접 만든 PyInstaller 앱이 계속 백신에 오탐되는 것이 불편해서, AI 코딩 도구의 도움을 받아 만들었습니다.

## 하는 일

| 단계 | 내용 |
|---|---|
| 포터블 빌드 | PyInstaller **onedir** 빌드(기본). `--noupx`, Windows 버전 정보, 아이콘 적용. **onefile** 빌드 선택 가능 |
| 설치 프로그램 | Inno Setup 스크립트 생성(업그레이드용 고정 AppId, 기본 사용자별 설치, 시작 메뉴·선택형 바탕화면 바로가기, 제거 프로그램, 한/영 등 다국어) 후 Inno Setup 6.3 이상이 설치돼 있으면 컴파일 |
| 스모크 테스트 | 빌드 결과를 임시 폴더로 복사하고 파이썬 관련 환경 변수·경로를 뺀 상태로 실행. 누락 모듈·데이터 파일·DLL 오류를 찾아 힌트 제시 |
| 백신 오탐 진단 | PE 파일을 순수 파이썬으로 읽어 흔히 거론되는 위험 요인(UPX, onefile, 버전 정보 없음, 서명 없음, 기본 부트로더, 기본 아이콘)을 점수화. Windows에서는 로컬 Microsoft Defender 명령줄 검사(치료 없이) 가능 |
| 라이선스·SBOM | PyInstaller TOC 파일에서 포함된 모듈을 찾아 설치된 패키지와 연결하고 `THIRD_PARTY_LICENSES.txt`(exe 옆과 설치 프로그램에도 포함)와 CycloneDX SBOM 작성 |
| 릴리스 정리 | 파일 수집, `SHA256SUMS.txt`, 리포트, 오탐 신고 문안 작성. 선택 시 GitHub CLI(`gh`)로 GitHub Release에 업로드 |

onedir 빌드만 필수이고, 나머지 단계는 각자 상태를 기록하므로 도구 하나가 없어도(예: Inno Setup) 나머지는 계속 진행됩니다.

## 설치

```bash
pip install pyi-doctor
```

요구 사항: Python 3.9+, PyInstaller 6+(의존성으로 설치). 설치 프로그램 단계는 Windows와 [Inno Setup](https://jrsoftware.org/isinfo.php) 6.3 이상이 필요합니다(동봉하지 않음, 기본 위치가 아니면 `PYI_DOCTOR_ISCC` 환경 변수로 지정). GitHub 업로드는 [GitHub CLI](https://cli.github.com/)가 필요합니다.

## 리눅스·맥에서 윈도우 exe 빌드 (실험 기능)

PyInstaller는 크로스 컴파일을 하지 않아 윈도우 exe를 만들려면 보통 윈도우(또는 Wine)가 필요합니다. `pyi-doctor build --target win`은 Wine 없이 리눅스·맥에서 PyInstaller 형식의 윈도우 exe를 만듭니다.

```bash
pyi-doctor build --target win -r requirements.txt
```

```python
from pyi_doctor import Project, build_windows

project = Project(entry="main.py", name="MyApp", version="1.0.0", requirements=["requests"])
onedir = build_windows(project)                  # dist/portable/MyApp/MyApp.exe (+ _internal)
single = build_windows(project, onefile=True)    # dist/onefile/MyApp.exe
```

결과물은 onedir 폴더, 단일 exe(선택), 윈도우에서 컴파일하는 상대 경로 Inno Setup 스크립트(`dist/MyApp.iss`)입니다. 참고로 [pynsist](https://pynsist.readthedocs.io/en/latest/faq.html)도 리눅스·맥에서 빌드되지만 결과물은 설치 프로그램뿐입니다.

동작 방식: python.org 윈도우 임베디드 배포판(빌드 PC와 같은 파이썬 minor 버전), 설치된 PyInstaller와 같은 버전의 `win_amd64` wheel(윈도우 부트로더), 의존성의 `win_amd64`·순수 파이썬 wheel을 받아(`~/.cache/pyi-doctor`에 캐시) PyInstaller의 modulegraph로 모듈을, pefile로 `.pyd`가 쓰는 DLL을 찾습니다. PyInstaller hook은 패키지를 import하지 않고 풀어둔 파일을 읽는 방식으로 실행합니다. 그다음 PYZ·`base_library.zip`·CArchive를 PyInstaller 형식으로 쓰고, 아이콘·버전 정보·매니페스트를 순수 파이썬으로 PE 리소스에 넣습니다. exe를 실행해 보지는 않으므로 [예제 워크플로](examples/github-actions/cross-build-windows.yml)처럼 `windows-latest`에서 실행 테스트를 한 뒤 배포하는 것을 권장합니다.

아래 경우에는 파일을 쓰기 전에 이유 목록과 함께 중단합니다: 64비트 윈도우(x64) 외 대상(32비트·ARM64), 해당 파이썬 버전용 `win_amd64`·순수 파이썬 wheel이 PyPI에 없는 패키지, 패키지 import가 필요하거나 `hook(hook_api)`를 쓰는 등 파일로 대체할 수 없는 hook, tkinter(customtkinter 포함; 임베디드 배포판에 Tcl/Tk 없음), wheel에도 없고 알려진 윈도우 시스템 DLL도 아닌 DLL.

리눅스, 파이썬 3.13에서(종단 테스트는 3.12에서도 통과) 표준 라이브러리(ssl·sqlite3·ctypes), requests, PyYAML, click, rich, tqdm, colorama, charset-normalizer, Pillow, pydantic 2, psutil, orjson, pywin32를 빌드해 Wine 9에서 onedir·onefile로 실행을 확인했습니다. numpy는 빌드는 되었으나 Wine 9에서는 실행할 수 없었습니다. 그 밖의 패키지는 확인하지 않았고, 실제 윈도우에서의 결과는 다를 수 있습니다.

## 설정

`pyproject.toml`에 아래 표를 추가합니다(`pyi-doctor init --write`로 템플릿 추가 가능).

```toml
[tool.pyi-doctor]
entry = "main.py"
name = "MyApp"
version = "1.0.0"
publisher = "Your Name"
icon = "assets/app.ico"
app_id = "고정-GUID"          # 첫 배포 후에는 바꾸지 말 것
console = false
onefile = false
license_file = "LICENSE"
add_data = ["assets=assets"]
languages = ["english", "korean"]
```

`app_id`를 비워 두면 publisher와 name으로 GUID를 만듭니다. 둘 중 하나를 바꾸면 GUID도 바뀌어 기존 설치본을 업그레이드로 인식하지 못하므로, 첫 배포 전에 고정값을 넣는 것을 권장합니다. 전체 설정 목록은 [README.md](README.md#configuration)에 있습니다.

## 명령줄

```bash
pyi-doctor build                        # pyproject.toml 기준 전체 실행
pyi-doctor build --onefile --no-defender
pyi-doctor build --target win -r requirements.txt   # 리눅스·맥에서 윈도우 exe (실험 기능)
pyi-doctor diagnose MyApp.exe           # 아무 exe나 점수화
pyi-doctor diagnose MyApp.exe --no-icon # 기본 아이콘 항목까지 포함
pyi-doctor smoke MyApp.exe -- --version
pyi-doctor iss                          # Inno Setup 스크립트만 생성
pyi-doctor init --write
```

## 백신 점수에 대해

점수는 PyInstaller 실행 파일의 휴리스틱 탐지에 영향을 준다고 흔히 알려진 요인에 고정 가중치(UPX 30, onefile 20, 버전 정보 없음 15, 서명 없음 15, 기본 부트로더 10, 기본 아이콘 5)를 더한 값입니다. 자신의 빌드끼리 비교하는 용도이며, Microsoft Defender나 다른 제품의 판정을 예측하지 않습니다. 점수가 낮아도 탐지될 수 있습니다.

pyi-doctor는 코드를 난독화·암호화하거나 검사를 피하도록 숨기지 않습니다. 버전 정보, 패커 미사용, onedir 구성, 서명 안내 같은 일반적인 배포 관행을 적용하고 오탐 신고 준비를 돕습니다. 신뢰할 수 있는 인증 기관의 코드 서명이 일반적으로 가장 효과적인 방법이며, 이는 이 도구의 범위 밖입니다.

## 측정값 (동봉 예제)

`examples/hello`, Linux x86_64, Python 3.13, PyInstaller 6.22.3 기준(설치 프로그램 단계는 Windows 전용이라 제외): onedir 4.5초, onefile 12.2초, 라이선스·SBOM 0.8초, 스모크 테스트 0.6초, 진단 0.1초 미만, 릴리스 정리 1.5초, **합계 19.7초**.

테스트: Linux에서 167개(실제 PyInstaller 빌드 2개, 크로스 빌드 2개 포함) 실행, CI는 Linux·Windows에서 Python 3.9~3.14로 유닛 테스트를, Linux에서 크로스 빌드와 Windows 실행 테스트를 합니다. Windows 전용 경로(버전 정보, Inno Setup 컴파일, Defender 검사, 스모크 테스트의 오류 창 감지)는 Windows CI 작업에서 실행됩니다.

## 알려진 한계

- 설치 프로그램·버전 정보·Defender 검사는 Windows에서만 동작합니다. 다른 OS에서는 `.iss` 스크립트만 생성합니다.
- 스모크 테스트는 휴리스틱입니다. 파이썬 환경은 빼지만 깨끗한 PC(설치된 DLL, Visual C++ 런타임, 글꼴 등)를 재현하지는 않습니다. 계속 실행 중인 GUI 앱은 "시작됨"으로 봅니다. 창 모드 앱이 오류로 멈추면 PyInstaller 오류 창이 뜨는데, Windows에서 창 제목으로 이를 찾으며 모든 경우를 잡지 못할 수 있습니다.
- 부트로더 확인은 현재 환경에 설치된 PyInstaller 기준입니다. 다른 곳에서 빌드한 exe에는 맞지 않을 수 있습니다.
- 라이선스 고지는 패키지 메타데이터에서 만들며 메타데이터가 없거나 틀릴 수 있고, 동적으로 불러오는 패키지는 빠질 수 있습니다. 생성된 파일을 검토해야 합니다.
- PyInstaller만 지원합니다(Nuitka·cx_Freeze 미지원). 코드 서명·자동 업데이트 기능은 없습니다.
- 윈도우 크로스 빌드(`--target win`)는 실험 기능입니다. 위 범위만 다루고, 모든 PyInstaller hook을 확인한 것이 아니며, exe를 실행해 보지 않습니다. python.org·PyPI 다운로드는 HTTPS 외 서명 검증을 하지 않습니다.

## 고지

pyi-doctor는 규칙 기반이라 문제를 놓치거나 없는 문제를 보고할 수 있습니다. 리포트는 참고용이며 직접 하는 테스트, 라이선스 검토, 관련 법령상 의무를 대신하지 않습니다. 결과 확인과 라이선스·법령 준수 책임은 사용자에게 있습니다. 이 소프트웨어는 MIT License에 따라 어떠한 보증도 없이 "있는 그대로(AS IS)" 제공됩니다.

pyi-doctor는 독립 프로젝트이며 PyInstaller 프로젝트, Jordan Russell / Inno Setup, Microsoft, GitHub와 관련이 없고 이들의 보증을 받지 않았습니다. 각 제품명은 해당 소유자의 상표입니다.

## 라이선스

MIT © 2026 [서주원](https://github.com/sjw0328) (Juwon Seo, OMEGA SYSTEMS) · 문의: seodes105@gmail.com
