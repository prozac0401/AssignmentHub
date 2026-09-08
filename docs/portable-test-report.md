# Python 내장 배포 검증

2026-09-08, Windows x64 / CPython 3.12.14 / Caddy 2.10.2에서 수행했습니다. 기존 기능·대용량 검증 기록은 [전체 검증 보고서](test-report.md)를 참고하세요.

| 확인 항목 | 결과 |
| --- | --- |
| 해시 고정 wheel 설치 후 독립 런타임 빌드 | 통과. 표준 라이브러리·Tcl/Tk·VC 런타임·네이티브 패키지·Caddy 포함 |
| `tests/test_portable.py` | 4개 통과, 145.03초. 새 경로에 실제 ZIP 압축 해제 후 검증 |
| 시스템 Python과 외부 패키지 경로 분리 | PATH를 Windows System32로 제한하고 PYTHONHOME·PYTHONPATH·PYTHONUSERBASE·Tcl/Tk 경로를 잘못된 값으로 설정한 상태에서 BAT 실행 통과 |
| 오프라인 점검 | 외부 HTTP/HTTPS/ALL 프록시를 응답하지 않는 로컬 주소로 지정한 환경에서 `setup.bat`·전체 파일 해시·네이티브 구성요소 검증 통과 |
| 경로 이동과 BAT | 한국어·공백·`&`·`!`가 포함된 폴더에서 `setup.bat`, `manage.bat --help`, 잘못된 CLI 인자의 종료 코드, `start.bat` 통과 |
| 실제 관리창 | Win32 창 제목과 실행한 pythonw 프로세스를 확인. 테스트가 연 관리창만 정리 |
| 실제 서버 | 내장 Python으로 supervisor·API·Streamlit을 실행하고 Caddy를 통해 상태·웹 페이지·업로더 접속 확인. 서버 종료 후 중지 상태 확인 |
| 파일 제출 | 관리자 로그인 → 수강생 생성 → 최초 비밀번호 변경 → 한국어 내용의 파일을 8바이트 청크로 전송 → 완료 → 다운로드한 내용이 원본과 같은지 확인 |
| 기존 기능 회귀 검사 | `test_portable.py`, `test_management.py`, `test_server_manager.py`, `test_launcher.py`, `test_api.py`: 42개 통과, ZIP 경로 미지정 시 배포 통합 검사 3개 제외, 87.89초 |
| 현재 저장소의 BAT | 런타임을 저장소에도 준비한 뒤 전체 점검 통과. 빠른 시작 점검 적용 후 `test_manager_entrypoint.py` 1개 통과, 8.13초 |

검증 중 대량 압축 해제와 전체 무결성 검사를 동시에 수행하면 관리창 시작 검사가 기존 30초 테스트 제한을 넘었습니다. `start.bat`는 관리창 구성만 빠르게 검사하도록 변경하고 전체 검사를 `setup.bat`에 두었습니다. 변경 후 실제 저장소 BAT 관리창 검사를 다시 통과했습니다.

회귀 검사에는 기존 Starlette/AnyIO 사용 중단 경고와 Tk 변수 정리 시 `main thread is not in main loop` 경고가 각 1건 있었습니다. ZIP 통합 검사에는 기존 Starlette/AnyIO 경고 1건이 있었습니다. 테스트 실패와는 구분합니다.

별도 미설치 Windows PC/VM에서의 실행, Windows 10 실기기, 다른 PC의 LAN·방화벽 통과, 이번 런타임으로 실제 2GiB 재전송은 이번 검증 범위에 포함하지 않습니다. 프록시 제한과 시스템 Python 경로 분리 검증은 OS에서 네트워크 어댑터를 끄거나 다른 Python을 제거한 것과 구분합니다.

현재 배포 방식에는 Python 사전 설치가 필요하지 않습니다. 기존 PPT 매뉴얼의 사전 설치 설명은 [무설치 배포 안내](portable-distribution.md)를 따릅니다.
