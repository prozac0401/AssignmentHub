# Python 내장 무설치 배포

## 받는 사람의 실행 방법

1. `AssignmentHub-1.1.0-windows-x64.zip` 전체를 Windows 10/11 x64 PC의 쓰기 가능한 폴더에 압축 해제합니다.
2. 폴더 안의 `start.bat`를 더블클릭합니다. Python·pip·라이브러리 설치, 관리자 권한, 인터넷 다운로드가 필요하지 않습니다.
3. 관리창에서 새 과정을 만들고 서버를 시작합니다. 같은 교육장 네트워크에서 사용할 IP와 방화벽 포트는 기존 운영 안내를 따릅니다.

`manage.bat`를 인자 없이 실행해도 관리창이 열립니다. CLI 명령은 기존과 동일합니다. `start.bat`는 관리창에 필요한 구성만 빠르게 확인합니다. `setup.bat`는 선택적인 오프라인 전체 점검이며 Python, GUI, 네이티브 라이브러리, Caddy와 배포 파일 SHA-256을 확인합니다. 오류가 있으면 배포 ZIP을 새 폴더에 다시 압축 해제하세요. Python 설치를 시도하지 않습니다.

BAT 하나만 다른 폴더로 옮기거나 ZIP 내부에서 직접 실행하지 마세요. 배포 폴더 전체가 필요합니다. 시작 실패의 상세 출력은 콘솔과 `logs/manager-startup.log`에서 확인합니다.

## 포함되는 구성

```text
AssignmentHub-1.1.0-windows-x64/
  start.bat                   서버 관리창
  manage.bat                  관리창 또는 CLI
  setup.bat                   오프라인 구성·해시 점검
  runtime/python/
    python.exe, pythonw.exe   실제 CPython 실행 파일
    python312.dll, *.dll      Python 및 VC 런타임
    python312._pth            배포 폴더 내부 상대 경로만 사용
    Lib/                     표준 라이브러리, tkinter
    Lib/site-packages/       해시 고정 의존성과 각 라이선스
    DLLs/, tcl/              네이티브 모듈 및 Tcl/Tk
    LICENSE*                 Python 라이선스
  tools/                     Caddy 실행 파일·버전·해시·라이선스
  assignmenthub/             프로그램과 웹 정적 파일
  templates/, examples/, docs/
  scripts/portable_env.bat
  distribution.json          버전·의존성·배포 파일 SHA-256 목록
```

Python은 전체 CPython 3.12 x64 배포에서 표준 라이브러리·DLL·Tcl/Tk를 복사하고 `python312._pth`로 검색 경로를 고정해 내장합니다. 가상환경의 리다이렉터를 복사하는 방식이 아니므로 빌드 PC의 Python 경로에 의존하지 않습니다. Python 공식 최소 embeddable ZIP에는 Tcl/Tk가 없으므로 관리창용 Tcl/Tk까지 포함합니다. [Python Windows 배포 문서](https://docs.python.org/3.12/using/windows.html#the-embeddable-package)

운영 시 시스템 Python, 레지스트리의 Python 경로, 사용자 site-packages, `PYTHONPATH`를 사용하지 않습니다. `site`와 외부 `.pth` 자동 실행을 끄고 pywin32의 필요한 DLL과 검색 경로를 명시적으로 포함합니다. 하위 API·Streamlit·관리 프로세스도 동봉된 `sys.executable`로 실행됩니다. `requirements.lock`의 wheel을 빌드 시 해시 검증해 별도 설치하므로 빌드 PC의 다른 라이브러리가 섞이지 않습니다. 빌드 PC의 Python을 가리키는 pip 생성 콘솔 실행 파일은 제외합니다.

`instances/`, `data/`, 운영 로그, 실제 명단, 계정 DB, 비밀번호, `.venv`, 개발 결과물은 배포본에 포함하지 않습니다. 새 과정의 설정·데이터는 첫 실행 이후 생성됩니다. 이미 운영 중인 폴더는 서버를 중지하고 백업한 뒤 업그레이드하세요. 기존 과정 설정의 `storage_root`는 절대 경로이므로 다른 PC로 옮길 때 실제 복원 경로를 확인해야 합니다.

## 빌드 PC에서 다시 만들기

빌드 PC에만 Python 3.12 x64, Tcl/Tk와 pip가 필요합니다. 사용자 PC에는 필요하지 않습니다.

```bat
setup_dev.bat
build_portable.bat
```

기존 `.venv`가 있으면 그 Python으로 빌드하며, 없으면 `py -3.12`를 사용합니다. 기본 Python 원본은 빌더의 `sys.base_prefix`입니다. 필요하면 `build_portable.bat --python-home "C:\Python312"`로 완전한 Python 설치 폴더를 지정합니다. Python 최소 embeddable ZIP만 지정하면 Tcl/Tk가 누락되므로 빌드가 중단됩니다.

결과는 `dist/AssignmentHub-1.1.0-windows-x64/`, 같은 이름의 `.zip`, `.zip.sha256`입니다. 기존 배포본이나 운영 데이터를 덮어쓰지 않으며, 재빌드는 `build_portable.bat --output-dir dist\next`처럼 새 출력 폴더를 지정합니다. Python 패치 버전, 의존성 버전과 실제 파일 해시는 `distribution.json`에 기록됩니다.

빌더는 고정 wheel 다운로드, 기존 Caddy 검증(없으면 공식 버전 다운로드), 라이선스 확보를 수행합니다. `--wheelhouse 폴더`를 지정하면 Python 의존성은 해당 wheel에서만 해시 검증해 설치합니다. 이 옵션만으로 Caddy와 라이선스 다운로드까지 오프라인화하지는 않습니다. 런타임과 배포 ZIP은 Git 제외 대상이므로 소스 ZIP 대신 빌드된 ZIP을 전달하세요.

## 검증

빌드는 시스템 Python이 없는 PATH와 잘못된 `PYTHONHOME`·`PYTHONPATH`를 설정한 상태에서 내장 Python, Tk 생성, SQLite, Argon2, NumPy/Pandas/PyArrow, 시간대, Caddy 실행을 확인한 뒤 ZIP을 만듭니다. 전체 배포 파일 해시는 `setup.bat`로 재확인할 수 있습니다.

배포 ZIP 자체의 이동·실행 검증:

```bat
set AH_PORTABLE_ZIP=D:\releases\AssignmentHub-1.1.0-windows-x64.zip
.venv\Scripts\python.exe -m pytest tests\test_portable.py -q
```

테스트는 ZIP을 한국어·공백이 있는 새 경로에 풀고 시스템 Python 경로와 외부 프록시를 차단한 환경에서 BAT, 실제 GUI, 서버 시작·접속·종료를 확인합니다. 별도 PC/가상머신의 완전한 미설치 Windows 검증 여부는 [검증 보고서](test-report.md)에 구분합니다.
