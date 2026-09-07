# 검증 보고서

검증 환경과 실제 수행 결과를 아래에 기록합니다. 자동 API 테스트는 작은 제한값과 실패 주입을 사용하며 실제 2GiB·브라우저·네트워크 테스트와 구분합니다. 미검증 항목은 수행을 확인하지 못했거나 해당 범위를 실제 환경에서 실행하지 못한 항목입니다.

## 화면 매뉴얼과 템플릿 후속 검증 (2026-09-08)

[24쪽 PPT와 캡처 21개](manual/README.md)를 실제 Windows 관리창과 Edge로 제작했습니다. 가상 계정으로 실제 Caddy·Streamlit·FastAPI·SQLite를 실행했습니다. 화면 문자열이나 API 응답을 성공 상태로 조작하지 않았습니다.

- 배포한 `users_template.xlsx`를 그대로 업로드하는 과정에서 시트 범위 메타데이터 누락을 발견했습니다. Excel에서 다시 저장한 파일로 시험하면 나타나지 않는 문제였습니다. 템플릿의 메타데이터 후처리기를 수정하고, **배포 원본 바이트 그대로** API 미리보기에 넣는 회귀 검사를 추가했습니다. 셀 값과 서식은 변경하지 않았습니다.
- `.venv\Scripts\python.exe -X utf8 -m pytest tests\test_api.py -q`: **20개 통과, 21.26초**. 기존의 테스트 클라이언트 의존성 사용 중단 경고 1건이 있으며 테스트 실패는 없습니다.
- 실제 브라우저에서 템플릿 등록, 임시비밀번호 CSV 다운로드, 과제 생성, 학생 첫 비밀번호 변경, 일회용 코드 연결, 파일 제출, 이어 올리기, 다운로드, 관리자 현황을 확인했습니다.
- 예시 파일은 **11,400,000바이트**입니다. 서버 확정 수신량 **8,388,608바이트** 이후 다음 청크를 Playwright로 끊었습니다. 새로고침·재로그인 후 같은 파일로 전송을 재개해 완료했으며, 실제 다운로드의 SHA-256은 원본과 같았습니다. 이는 물리 네트워크 케이블을 뽑은 시험이 아닙니다. [실행 결과](manual/walkthrough.json)
- 관리창은 테스트 프로세스 안에서 실제 위젯 콜백과 중지 확인 응답을 실행했습니다. 생성·시작·주소 복사·진단·중지 화면을 캡처하고 전용 과정을 중지했습니다. OS 마우스를 직접 움직인 시험과 구분합니다.
- PPT는 편집 가능한 설명 텍스트와 실제 화면 이미지로 구성했습니다. 패키지·슬라이드 크기·글꼴·기하 검사 및 Artifact Tool 재읽기를 통과했고, Microsoft PowerPoint 16.0에서 읽기 전용으로 열어 24쪽 전체를 PNG로 렌더링하여 개별 슬라이드 배치와 한글 줄바꿈을 확인했습니다.

**미검증:** PowerPoint 슬라이드 쇼 조작과 강의실 프로젝터, 다른 물리 PC의 교실 LAN 접속, 실제 TLS 인증서 배포, 새 PC 최초 설치, 실제 백업 복원 훈련. 이번 매뉴얼 후속 작업에서는 기존 2GiB 부하 검사를 다시 실행하지 않았습니다. 이전 대용량 결과는 아래에 별도로 남깁니다.

## 서버 사용성 개선 후속 검증

2026-09-08 한국시간, 같은 Windows11·Python3.12.14·Edge152.0.4191.66 환경에서 수행했습니다. 아래는 더블클릭 서버 관리창과 웹 운영 안내를 추가한 뒤의 검증입니다. 앞서 수행한 대용량 측정과 구분합니다.

| 명령/작업 | 실제 결과 |
| --- | --- |
| `python -X utf8 -m pytest tests/test_management.py tests/test_server_manager.py tests/test_launcher.py -q` (`.venv` Python) | 21개 통과, 67.05초. 과정 등록·기존 설정 연결·GiB 변환·충돌·설정 보존·실제 Tk 창·실제 서버 프로세스·기존 실행 도구 검사 |
| `python -X utf8 -m pytest tests/test_manager_entrypoint.py -q` | 1개 통과, 5.78초. 실제 `start.bat` → `pythonw` → 해당 프로세스의 관리창 생성 확인. 테스트에서 만든 프로세스만 종료 |
| `python -X utf8 -m pytest tests/test_server_manager.py -q` 최종 추가 실행 | 1개 통과, 27.13초. 관리창을 닫아도 서버가 유지되고, 새 관리창에서 기존 서버를 발견·중지하며 설정을 변경하는 흐름까지 확인 |
| 기존 API·CSV 회귀 검사 | `tests/test_api.py` 20개와 `tests/test_private_csv.py` 1개 통과. 관리 테스트와 함께 실행한 초기 33개 중 관리 테스트의 호출 인자 누락 2개가 실패했고, 해당 테스트를 고친 뒤 관리 테스트 12개 및 위 21개 묶음에서 재통과 |
| 실제 Edge 관리자 화면 | 명단 등록·과제 관리·제출 현황으로 이동하고 안내 주소가 설정과 일치함을 확인. 브라우저 스크립트 오류 0건. 위 Tk 통합 검사에서 `node tests/test_admin_home.cjs`를 실제 서버에 연결해 실행 |
| 실제 창의 시각 검사 | 관리창·과정 생성창·웹 운영 안내를 캡처해 글자와 버튼 배치를 확인. 생성창의 비밀번호·확인·저장 버튼이 창 안에 표시됨을 확인 |

관리창 테스트는 실제 Tk 위젯을 띄우고 버튼의 콜백을 호출합니다. 계정과 DB를 생성하고 공개 Caddy 포트의 실제 서버를 켠 뒤 주소 복사·접속 점검·브라우저 로그인·메뉴 이동·관리창 닫기와 다시 열기·중지·설정 변경을 실행했습니다. 시작 중 Tk 이벤트 처리가 계속되는 것도 확인했습니다. 테스트 서버는 종료했습니다.

초기 BAT 검사에서는 UTF-8 파일의 LF 줄바꿈 때문에 Windows 명령 해석 오류가 발생했습니다. BAT를 CRLF로 저장하고 `.gitattributes`에서도 CRLF를 유지합니다. GUI가 호출 콘솔의 출력 핸들을 물려받는 문제를 방지하도록 출력 연결도 분리했으며, 이후 BAT 진입점 검사가 통과했습니다. 브라우저 테스트의 숨겨진 radio input 선택은 실제 표시되는 메뉴 라벨 클릭으로 수정했습니다.

후속 검증 증거: [결과 JSON](evidence/manager-usability.json), [실제 서버 관리창](evidence/server-manager.png), [웹 운영 안내](evidence/admin-home.png). 과정과 계정은 테스트용이며 비밀번호·세션 토큰은 증거에 포함하지 않습니다.

**미검증:** OS 마우스로 실제 더블클릭·버튼 입력하는 전체 시나리오(이 세션에 Computer Use 런타임 없음), 다른 DPI/화면 크기, Python이 없는 새 PC의 최초 설치, 다른 PC의 LAN·TLS 접속. 이번 후속 변경에서 실제2GiB 전송은 다시 실행하지 않았습니다. 앞선 대용량 결과는 해당 실행 당시의 결과입니다. 전체 67개 Python 테스트를 한 번에 실행했다는 의미도 아닙니다.

## 환경

- 작업 OS: Windows 11 10.0.22631, 논리 CPU 4개, 물리 메모리 17,060,524,032바이트. PowerShell 실행이 OS 오류 5로 거절되어 `cmd.exe`와 Python으로 작업.
- 런타임: 64비트 Python 3.12.14, HTTPX 0.28.1, psutil 7.0.0. 전체 고정 버전은 `requirements.lock` 참조.
- 사용자 엑셀: Artifact Tool로 생성·값 검사·XLSX 내보내기, 전체 ID 열 기본 텍스트 서식 검사 수행. 실제 Excel16.0의 독립된 숨김 인스턴스에서 읽기 전용으로 열어 `001`·`002` 보존과 A10000의 `@` 서식을 확인했습니다. 최초 Artifact Tool 렌더러는 `vkEnumerateInstanceVersion` 오류로 종료했습니다.

## 실제 실행 기록

| 명령/작업 | 결과 |
| --- | --- |
| `node scripts/generate_template.mjs templates --no-render` | 통과: `001`, `002` 문자열 값 검사, 수식 오류 0건, XLSX 내보내기 |
| `node scripts/generate_template.mjs`의 렌더 단계 | 실패: 환경의 Vulkan 함수 해석 오류. 후속 독립 Excel PDF 내보내기도 45초 제한으로 종료되어 시각 렌더는 미검증 |
| 독립된 숨김 Excel16.0으로 XLSX 읽기 전용 검사 | 통과: A2=`001`, A3=`002`, A10000의 텍스트 서식 `@`. 기존 사용자 Excel 인스턴스는 사용하지 않음 |
| `.venv\Scripts\python.exe -m pytest tests\test_api.py -q` | 통과: 17개, 49.59초; Starlette/AnyIO의 외부 의존성 deprecation 경고 1건 |
| `.venv\Scripts\python.exe -m pytest tests\test_api.py -q -k test_a01` | 추가 테스트 통과: 4개, 15개 제외, 26.63초; 비활성 재등록·ID 대소문자·누락 헤더 검증 포함 |
| `.venv\Scripts\python.exe -m pytest tests\test_recovery.py -q` | 통과: 17개, 133.17초; 청크·DB·이동·커밋 장애, 복구, 만료·연결 종료·권한 취소·실제 수신량·48요청 경합 |
| `.venv\Scripts\python.exe -m pytest tests\test_process_recovery.py -q` | 통과: 5개, 182.25초. 실제 TCP 서버에서 `os._exit(73)`로 청크 중·청크 DB 확정 전·파일 이동 후·최종 DB 확정 전·커밋 후 종료, 재시작 후 offset/해시/번호/용량 검증 (8바이트 파일) |
| `.venv\Scripts\python.exe -m pytest tests\test_network_disconnect.py -q` | 통과: 1개, 156.60초. 실제 공개 Caddy TCP 연결에서 8바이트 신고 후 3바이트만 보내 연결 종료(offset 0과8), 임시 파일 절단·재전송·16바이트 완료·다운로드 해시·용량 검증 |
| 두 인스턴스 공유 볼륨 공간 감소 실패 주입 | 통과: 추가 1개, 29.73초. 양쪽 완료 파일과 DB 보존 확인. 운영 볼륨을 실제로 채우지 않음 |
| `.venv\Scripts\python.exe -m pytest tests\test_launcher.py -q` 및 추가 supervisor 종료 테스트 | 통과: 초기 6개 92.73초 + 실제 supervisor 강제 종료/고아 자식 정리 테스트 1개 18.37초. 실제 Windows 2인스턴스·포트·정규화 루트 잠금·독립 중지 |
| 성공 로그인 속도 제한 회귀 테스트 | 통과: 1개, 19개 제외, 21.93초. 같은 프록시 주소의 성공 로그인이 실패 제한을 소진하지 않음 |
| `manage.bat create` 실제 PTY 콘솔 실행 | 통과: `.local-tests/console-create.json`, ID `console_create_test`, 포트 54321. 비밀번호 숨김 입력·확인 2회, 종료 코드 0 |
| 의존성 잠금·설치 검사 | 통과: `requirements.lock`의 `--require-hashes` 설치 dry-run, `pip check` |
| `setup.bat`, `manage.bat --help`, `manage.bat status` | 통과: 기존 `.venv`의 고정 의존성 단계와 공식 Caddy 2.10.2 SHA-512 검사·실행 도구 |
| `node tests/test_streamlit_download.cjs .local-tests/browser-students-retry.json` | 통과: 실제 Edge 152.0.4191.62의 Streamlit iframe 기본 다운로드, 9,437,213바이트와 독립 SHA-256 일치 |
| `.venv\Scripts\python.exe -m build --wheel` | 통과: 소스 패키지 wheel 빌드. 배포 기본은 소스와 BAT |
| `.venv\Scripts\python.exe scripts\run_local_load.py --users 4 --file-bytes 1048576` | 통과: 공개 Caddy 경로에서 별도 사용자 4건 + 같은 사용자 4건 실제 1MiB 업로드·다운로드·반복 완료·재시작 후 해시 일치. 전체 실행 135.39초 |
| `.venv\Scripts\python.exe -m pytest -q` 최종 전체 실행 | 통과: 52개, 경고1개, 448.15초(7분28초), 종료 코드0 |
| 전체 실행 이후 추가된 `tests/test_private_csv.py` | 통과: 1개. 현재 총53개 Python 테스트를 전체52개+추가1개로 검증했으며 단일 명령53개 실행 결과로 표현하지 않음 |
| `node tests/test_browser.cjs .local-tests/browser-students-retry.json` | 통과: 47.874초, Edge152.0.4191.66, 같은 브라우저2포트·WebSocket·최초 비밀번호 변경·9,437,213바이트 업로드/다운로드·브라우저 재개·검증/저장 표시·토큰 격리 |
| `node tests/test_browser.cjs .local-tests/browser-large.json` | 통과: 실제2,147,483,648바이트 파일의 브라우저 업로드·검증/저장 표시·완료번호11·브라우저 기본 다운로드·원본/다운로드 SHA-256 일치. Edge152.0.4191.66, 전체1,055.834초 |
| `node tests/test_browser_multitab.cjs .local-tests/browser-students-retry.json` | 통과: 실제 같은 계정4탭 ×262,157바이트, 서로 다른 업로드ID4개·완료번호4개·모든 저장 해시 일치·JS 오류0, 150.597초 |
| `node tests/test_private_csv_browser.cjs`, `tests/test_private_csv.py` | 통과: 민감 CSV의 인증된 화면 내 Blob 생성, 악의적 스크립트 셀·수식 셀 이스케이프, 외부 요청0. 실제 관리자 명단 전체 UI 흐름을 시험한 것으로 해석하지 않음 |
| `node tests/test_sha256.cjs` | 통과: 브라우저용 SHA-256과 기준 구현 비교84개 |
| 실제 별도 사용자4명·같은 사용자4작업 ×2GiB | 통과: 총8개 파일 전송·저장·다운로드, 정상 재시작 후 전8개 파일 크기·해시 재검증, 마지막 서버 중지. 전체 자동 실행1,803.015초 |

## 실제 대용량 측정

`.venv\Scripts\python.exe scripts\run_local_load.py`가 전용 테스트 인스턴스에서 실제 소스 파일을 생성하고 **공개 Caddy → FastAPI → 파일시스템** 경로를 시험했습니다. 각 모드는 서로 다른 내용의 2GiB 파일4개, 합계8GiB입니다. 표의 시간은 전송·서버 검증·반복 완료·다운로드 해시 확인을 포함하고 소스 생성 시간은 제외합니다.

| 모드 | 성공/실패 | 시간(초) | 서버 트리 기준 RSS(바이트) | 표본 최고 RSS(바이트) | 증가(바이트) | 최고 임시 바이트 |
| --- | --- | --- | --- | --- | --- | --- |
| 별도 수강생4명 ×2GiB | 4 /0 | 727.875 | 290,275,328 | 303,296,512 | 13,021,184 | 8,589,934,592 |
| 같은 수강생의 동시4작업 ×2GiB | 4 /0 | 603.860 | 294,899,712 | 304,238,592 | 9,338,880 | 8,589,934,592 |

두 모드 모두 동시 전송 구간4개가 겹쳤고 원본·서버 기록·실제 다운로드의 크기와 SHA-256이 일치했습니다. 제출번호와 파일 ID는 각각4개로 중복되지 않았습니다. 첫 모드는 각 사용자 사용량2GiB·예약0, 둘째 모드는 같은 사용자 사용량8GiB·예약0, 제출자1명·완료 버전4건을 확인했습니다. 표의 메모리 증가는 전송 합계8GiB보다 훨씬 작았습니다. 이 측정은 해당 환경의 표본이며 다른 장비의 속도·최대 인원을 보장하지 않습니다.

원시 보고서:

- `test-runs/load_1ebc667dbc/separate-users/20260907T160927Z-c776ee02/report.json`
- `test-runs/load_1ebc667dbc/same-user/20260907T162228Z-68414ceb/report.json`

이 HTTP 부하 실행은 실제 브라우저 탭을 사용하지 않았습니다. **같은 브라우저4탭에서 각2GiB를 동시에 전송하는 경로는 미검증**입니다. 개별 브라우저 실행 결과는 별도로 기록합니다. 서버를 정상 중지·재시작한 뒤8개 파일을 다시 다운로드했고 모두 원본 크기·SHA-256과 일치했습니다. 테스트 인스턴스는 마지막에 중지했습니다.

실제 Edge에서 단일2GiB 파일도 별도로 업로드하고 브라우저의 기본 다운로드로 받아 독립 SHA-256을 비교했습니다. 원본과 다운로드 해시는 `93911e9ca3f0a3965982abf690a189ac3002bdae1ab3cf43634aba4332fb7e42`로 일치했습니다. 이 실행의1,055.834초에는 소스 생성·해시·Streamlit 연결·전송·서버 검증·저장·기본 다운로드·해시 확인이 포함됩니다. 다른 시험이 병행되어 단독 속도 측정으로 해석하지 않습니다. 브라우저 실행의 RSS는 별도로 측정하지 않았습니다. 실제 같은 계정4탭 시험은 탭당262,157바이트로 통과했습니다.

저장소에 포함한 비밀값 없는 증거는 [대용량 측정 JSON](evidence/load-results.json), [작은 파일 브라우저 결과](evidence/browser-small.json), [2GiB 브라우저 결과](evidence/browser-2gib.json), [실제4탭 결과](evidence/browser-multitab.json), [Excel 검사 결과](evidence/template-native.json)입니다. [작은 파일 완료 화면](evidence/browser-small-uploader.png)과 [2GiB 완료 화면](evidence/browser-2gib-uploader.png)도 포함합니다. 운영 계정·비밀번호·토큰은 포함하지 않습니다.

## 완료 기준별 상태

| ID | 항목 | 상태 / 검증 범위 |
| --- | --- | --- |
| A01 | 명단 등록·재등록 | 통과: 텍스트 ID·전체 A열 텍스트 서식·앞자리0·중복·누락·대소문자·재등록 후 비밀번호/기록/활성 상태 보존 |
| A02 | 최초 비밀번호 변경 | 통과: API 제한 세션·정책과 실제 브라우저 최초 변경·재로그인·제출 |
| A03 | 관리자 초기화 | 통과: 이전 세션·전송 중 청크·완료 권한 즉시 무효화, 재변경 전 차단 |
| A04 | 사용자 권한 분리 | 통과: 타인 업로드 조회·청크·완료·다운로드 및 관리자 경로 접근 거절 |
| U01 | 실제 2GiB·재시작 보존 | 통과: 실제8개 HTTP 전송·저장·다운로드 해시 및 정상 재시작 후 전체8개 크기·해시 보존. 별도 실제 브라우저2GiB 업로드·기본 다운로드 해시도 일치 |
| U02 | 파일 경계·실제 수신 | 통과: 실제2GiB 허용, 작은32/33바이트 경계·초과 청크·신고 위조·없는 Content-Length 검증 |
| U03 | 실제 동시 대용량 | HTTP 경로 통과: 별도4명·같은 사용자4작업의 실제2GiB. 실제 같은 계정4브라우저탭×262,157바이트 통과. 실제 브라우저4탭×2GiB는 미검증 |
| U04 | 파일/청크 중간 연결 종료 | 통과: 실제 공개 Caddy TCP 중간 종료·마지막 확정 offset 재개·해시 일치(작은 파일) |
| U05 | 재시도·응답 유실 | 통과: 시작·청크·완료 반복, 커밋 직후 실제 프로세스 종료 후 재확정, 용량·제출 중복 없음 |
| U06 | 진행률·완료 시점 | 통과: 실제 브라우저 전송100% 후 검증·저장 표시와 최종 응답 전 성공 보류 |
| U07 | 여러 파일 묶음 | 통과: 일부만 수신한 완료 거절·제출자0, 남은 파일 수신 뒤 전체 완료 |
| U08 | 강제 종료·복구 | 통과: 실제 TCP 서버 `os._exit(73)` 5개 지점, 재시작·offset·해시·번호·용량 복구(8바이트 파일) |
| Q01 | 사용자 한도·동시 탭 | 통과: 짧은 DB 예약 경합·초과 거절과 실제 같은 사용자4×2GiB의 합산 사용량8GiB·예약0 |
| Q02 | 디스크 부족 | 통과(실패 주입): 시작·수신·최종 이동·DB 확정 실패에서 거짓 성공·기록 손상 없음 |
| Q03 | 취소·만료 | 통과: 임시 파일·예약 정리, 잠금 중인 활성 업로드 및 완료 파일 보존 |
| I01 | 인스턴스 데이터 분리 | 통과: 같은 ID의 별도 DB·계정·토큰·파일·용량과 실제 독립 인스턴스 실행 |
| I02 | 같은 브라우저 두 포트 | 통과: 같은 Edge 컨텍스트의2포트 쿠키 경로 분리·두 로그인 유지·다른 인스턴스 토큰401 |
| I03 | 실행·중지 분리 | 통과: 실제2인스턴스·포트 충돌·정규화 루트 잠금·무관한 프로세스 보존·고아 자식 정리. 보조 테스트 프로세스의 시간 만료 오류를 수정한 후 전체52개 실행도 통과 |
| I04 | 동일 볼륨 경합 | 통과(실패 주입): 두 인스턴스의 공유 볼륨 공간 감소 중 양쪽 완료 제출·DB 보존 |
| M01 | 관리자 현황 | 통과: 대상·제출·미제출·버전·비활성 이력·CSV 수식 방지·실제 대용량 사용자 집계 |
| S01 | 파일명 | 통과: 한글·공백·같은 이름 버전·경로 조작·예약 이름·ADS의 저장 경로 안전성 |
| E01 | Windows BAT·브라우저 | 통과: Windows11 BAT 설치/생성/시작/상태/중지와 실제 Edge의 단일 포트·WebSocket·인증·업로드·기본 다운로드. 새 외부 PC 설치·다른 PC의 교육장 LAN·Windows10은 미검증 |

## 미검증 범위와 측정 한계

- **서로 다른 실제 PC 사이의 교육장 LAN 접속**: 실제 네트워크 어댑터·방화벽·기관 네트워크 정책을 포함한 접속은 미검증입니다. 실행한 공개 게이트웨이 검증은 같은 Windows11 호스트의 loopback 주소를 사용했습니다.
- **HTTPS/TLS 전체 경로**: 인증서 설정·Caddy 구성 기능을 제공하지만 실제 기관 인증서·브라우저 신뢰·WebSocket·대용량 전송을 합한 TLS 운영 검증은 미검증입니다.
- **Windows10 및 비Windows 실행**: 이번 실행 OS는 Windows11입니다. 해당 다른 OS의 실행·브라우저·파일시스템 내구성은 미검증입니다.
- **새 외부 PC에서 처음부터 설치**: 이번 `.venv`는 번들 Python3.12.14로 먼저 만들고 의존성을 설치했습니다. 그 뒤 기존 `.venv`에 대해 `setup.bat`·해시 잠금·Caddy 검사를 실행했습니다. Python도 없는 새 PC에 대한 설치는 미검증입니다.
- **갑작스러운 전원 차단·하드웨어 장애**: 프로세스 강제 종료 복구를 검증했습니다. 전원 차단, 디스크 컨트롤러 캐시, 실제 저장장치 고장과 백업 복원 훈련은 미검증입니다.
- **같은 브라우저4탭 ×2GiB 동시 전송**: HTTP 클라이언트의 동일 사용자4작업 ×2GiB와 실제 브라우저 검증을 구분합니다. 이 대용량4탭 조합은 미검증입니다.
- **Excel 템플릿 시각 렌더**: 실제 Excel16.0 값·서식 검사는 통과했습니다. Artifact Tool의 Vulkan 오류와 후속 독립 Excel PDF 내보내기 제한시간 초과로 이미지/PDF 시각 검증은 미검증입니다.
- RSS는 서버 프로세스 트리의 0.25초 간격 표본입니다. 샘플 사이 피크·OS 파일 캐시·별도 브라우저 메모리는 포함하지 않습니다. 실제 최대 수용 인원이나 다른 환경의 속도를 보장하지 않습니다.

초기 통합 실행에서 보조 테스트 프로세스가 자체90초 타이머로 끝나 정리 단계에 `NoSuchProcess`가 발생했습니다(48통과/1실패). 해당 보조 프로세스를 표준입력으로 수명 제어하도록 수정하고 실제 독립 중지 테스트 및 최종 전체52개 실행을 통과했습니다. 브라우저 기본 다운로드에서는 `no-referrer`에 따른 `Origin:null` 거절을 발견하여 같은 origin referrer 정책으로 수정했으며 origin 검사 유지 상태에서 업로더·Streamlit 기본 다운로드를 각각 재검증했습니다.

## 브라우저 검증 재현

브라우저 시험은 Node20 이상, Playwright1.62.1, 설치된 Microsoft Edge를 사용했습니다. 운영 실행에는 Node와 Playwright가 필요하지 않습니다. 시험 환경에서 `npm install --no-save playwright@1.62.1`로 준비한 후, Git에서 제외되는 `.local-tests/`에 전용 시험 계정 설정을 만듭니다.

```json
[
  {
    "url": "http://127.0.0.1:18501",
    "instance_id": "browser_1",
    "user_id": "001",
    "password": "실제 시험 계정 비밀번호",
    "resume_test": true
  }
]
```

서로 다른 인스턴스 설정 두 개를 배열에 넣으면 같은 브라우저의 두 포트 시험도 실행합니다. 최초 변경 대상 계정은 `new_password`에 정책에 맞는 새 비밀번호를 지정합니다. 변경 후 재실행할 때는 `password`를 갱신하고 `new_password`를 제거합니다. 실제2GiB 시험은 첫 설정에 `file_bytes: 2147483648`을 추가하고 `resume_test`를 제거했습니다. 기본 작은 파일은9,437,213바이트이며 그 실행에서 새로고침·재개를 시험합니다.

```bat
node tests\test_browser.cjs .local-tests\browser.json
node tests\test_browser_multitab.cjs .local-tests\browser.json
node tests\test_streamlit_download.cjs .local-tests\browser.json
node tests\test_private_csv_browser.cjs
node tests\test_sha256.cjs
```

시험 계정의 비밀번호와 설정은 저장소에 커밋하지 마세요. 브라우저 시험은 완료 파일과 임시 원본·다운로드 파일을 남깁니다. 전용 시험 인스턴스를 중지하고 결과 보존 여부를 결정한 뒤 해당 시험 파일만 정리하세요.

## HTTP 부하 검증 재현

전용 인스턴스 생성부터 두 부하 모드·정상 중지·재시작 후 해시 검증·최종 중지까지 자동으로 실행하려면 다음 명령을 사용합니다. 관리자 비밀번호는 무작위로 생성해 프로세스 메모리에만 전달합니다. 기본 실행은 테스트 완료 파일16GiB를 `test-runs/`의 해당 인스턴스에 보존합니다.

```bat
.venv\Scripts\python.exe scripts\run_local_load.py
.venv\Scripts\python.exe scripts\summarize_load.py
```

기존 전용 테스트 인스턴스를 직접 지정하려면:

```bat
.venv\Scripts\python.exe -m pytest -q
manage.bat status --config instances\load_test.json --json
.venv\Scripts\python.exe scripts\load_test.py --url http://127.0.0.1:8510 --server-pid SUPERVISOR_PID --storage-root D:\Assignments\load_test
.venv\Scripts\python.exe scripts\load_test.py --url http://127.0.0.1:8510 --server-pid SUPERVISOR_PID --storage-root D:\Assignments\load_test --same-user
```

`SUPERVISOR_PID`는 `status --json`에서 확인한 실제 supervisor PID로 바꿉니다. 관리자 ID/비밀번호는 실행 시 콘솔로 입력합니다. 자동화 시 `AH_TEST_ADMIN_ID`, `AH_TEST_ADMIN_PASSWORD` 환경변수를 설정할 수 있으나 저장소나 일반 로그에 기록하지 마세요. 전용 테스트 인스턴스와 최소 소스·저장 파일 공간을 확보해야 합니다. 기본으로 원본 8GiB + 서버 완료 8GiB와 설정한 여유 공간이 필요합니다. 작은 시험은 `--users 2 --file-bytes 1048576`처럼 조정합니다.

도구는 실제 파일을 1MiB 버퍼로 생성·해시하고 공개 URL의 청크 API에 전송하며 다운로드 해시도 확인합니다. 원본은 작업별 서로 다른 내용으로 생성합니다. 완료 재요청 결과, 사용자 예약·사용량, 제출 집계, 동시 전송 구간, 전체 서버 프로세스 트리의 기준/최고 RSS, 임시 디스크 바이트, 환경·시간·성공/실패·해시를 `test-runs/<시각>/report.json`에 기록합니다. 프로세스 RSS는 0.25초 샘플 간의 순간 피크를 놓칠 수 있고 OS 파일 캐시나 별도 브라우저 프로세스 메모리를 포함하지 않습니다. 측정값으로 최대 수용 인원을 보장하지 않습니다.

HTTP 부하 도구의 동일 계정 동시 요청은 API에서 여러 탭에 해당하는 경합을 시험합니다. **실제 브라우저 탭·파일 선택·XHR 진행률·WebSocket 검증은 별도로 수행해야 합니다.** Chrome·Edge에서 단일 URL에 로그인하고 브라우저 개발자 도구의 WebSocket 연결, File.slice 청크 요청, 전송 100% 후 검증/저장, 두 포트 세션 격리를 확인하세요.

완료 파일은 재시작 검증을 위해 서버에 보존합니다. 스크립트는 자신이 생성한 원본 파일만 지우고 운영 DB나 다른 제출을 삭제하지 않습니다. 재시작 이후:

```bat
manage.bat stop --config instances\load_test.json
manage.bat start --config instances\load_test.json
.venv\Scripts\python.exe scripts\load_test.py --url http://127.0.0.1:8510 --verify-report test-runs\RUN_DIRECTORY\report.json --confirm-restarted
```

관리자 권한으로 완료 파일을 다시 다운로드하여 크기·해시를 기존 보고서와 비교하고 후속 결과를 기록합니다. 시험 계정·서버 완료 파일은 유지되므로 전용 테스트 인스턴스를 정상 종료한 뒤 해당 테스트 루트만 운영자가 정리합니다.
