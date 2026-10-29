# pyi-doctor

[English](README.md)

**PyInstaller 앱을 한 번의 호출로 포터블 exe와 설치 마법사로 빌드하고, 다른 PC에서 실행되는지·백신 오탐 위험 요인이 있는지·어떤 서드파티 라이선스를 포함하는지 확인하는 파이썬 라이브러리입니다.**

briefcase·pyship 같은 도구는 "배포" 자체에 초점을 둡니다. pyi-doctor는 PyInstaller로 배포한 뒤 자주 생기는 문제, 즉 "내 PC에서만 된다", "Windows Defender가 트로이목마라고 한다", "어떤 라이선스를 재배포하고 있는지 모른다"에 초점을 둡니다.

```python
from pyi_doctor import Project, build_all

project = Project(entry="main.py", name="MyApp", version="1.0.0", publisher="Me")
report = build_all(project, log=print)
print(report.ok)
```

결과물은 `dist/release/`에 모입니다: 포터블 zip, 단일 exe(선택), 설치 프로그램, SBOM, 라이선스 고지, SHA256 체크섬, 오탐 신고용 문안, 리포트.

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

테스트: Linux에서 140개(실제 PyInstaller 빌드 2개 포함) 실행, CI는 Linux·Windows에서 Python 3.9~3.14로 유닛 테스트를 실행합니다. Windows 전용 경로(버전 정보, Inno Setup 컴파일, Defender 검사, 스모크 테스트의 오류 창 감지)는 Windows CI 작업에서 실행됩니다.

## 알려진 한계

- 설치 프로그램·버전 정보·Defender 검사는 Windows에서만 동작합니다. 다른 OS에서는 `.iss` 스크립트만 생성합니다.
- 스모크 테스트는 휴리스틱입니다. 파이썬 환경은 빼지만 깨끗한 PC(설치된 DLL, Visual C++ 런타임, 글꼴 등)를 재현하지는 않습니다. 계속 실행 중인 GUI 앱은 "시작됨"으로 봅니다. 창 모드 앱이 오류로 멈추면 PyInstaller 오류 창이 뜨는데, Windows에서 창 제목으로 이를 찾으며 모든 경우를 잡지 못할 수 있습니다.
- 부트로더 확인은 현재 환경에 설치된 PyInstaller 기준입니다. 다른 곳에서 빌드한 exe에는 맞지 않을 수 있습니다.
- 라이선스 고지는 패키지 메타데이터에서 만들며 메타데이터가 없거나 틀릴 수 있고, 동적으로 불러오는 패키지는 빠질 수 있습니다. 생성된 파일을 검토해야 합니다.
- PyInstaller만 지원합니다(Nuitka·cx_Freeze 미지원). 코드 서명·자동 업데이트 기능은 없습니다.

## 고지

pyi-doctor는 규칙 기반이라 문제를 놓치거나 없는 문제를 보고할 수 있습니다. 리포트는 참고용이며 직접 하는 테스트, 라이선스 검토, 관련 법령상 의무를 대신하지 않습니다. 결과 확인과 라이선스·법령 준수 책임은 사용자에게 있습니다. 이 소프트웨어는 MIT License에 따라 어떠한 보증도 없이 "있는 그대로(AS IS)" 제공됩니다.

pyi-doctor는 독립 프로젝트이며 PyInstaller 프로젝트, Jordan Russell / Inno Setup, Microsoft, GitHub와 관련이 없고 이들의 보증을 받지 않았습니다. 각 제품명은 해당 소유자의 상표입니다.

## 라이선스

MIT © 2026 서주원 (OMEGA SYSTEMS) · 문의: seodes105@gmail.com
