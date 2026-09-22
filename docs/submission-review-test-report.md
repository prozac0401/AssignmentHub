# 제출물 저장·운영 화면 변경 검증

검증일: 2026-09-10. 수정된 개발 소스와 별도의 테스트 저장 루트를 사용했습니다. 운영 중인 데이터와 기존 배포 ZIP은 변경하지 않았습니다.

## 변경 사항

- 로그인 슬로건을 계정별 기능 설명으로 교체하고 영어 장식 문구를 정리했습니다.
- 공통 임시비밀번호를 지원하는 실제 동작에 맞게 운영 안내를 수정했습니다.
- 영상 결과는 첫 5초의 화면 데이터 읽기·음성 확인 범위만 설명합니다.
- 저장 경로에 과제명·수강생 이름·ID·접수 순번·원본 파일명을 사용합니다. 파일 내용 비교용 SHA-256과 인증 해시는 유지합니다.
- 기존 UUID 경로의 완료·미완료 파일을 서버 시작 시 전환합니다. 중단 후 재시작과 기존 파일 다운로드를 검증했습니다.
- 운영 화면에 이름·파일명·최신 버전, 이전 버전 선택, 검색·정렬, 파일별 CSV, 이름·제출 버전이 포함된 다운로드 이름을 적용했습니다.

## 자동 검사

전체 실행: `.venv/Scripts/python.exe -m pytest -q --disable-warnings --maxfail=4`

최초 전체 결과: **175 통과, 2 실패, 3 생략**. 실패 항목은 다음과 같이 해결 후 개별 재검증했습니다.

- `tests/test_network_disconnect.py`: 테스트가 과거 UUID 임시 경로를 직접 참조했습니다. 실명 경로와 파일명 확인으로 갱신 후 실제 Caddy/TCP 연결 중단·재전송 검사를 통과했습니다.
- `tests/test_server_manager.py`: 실행 환경에 Windows Program Files 변수가 없어 Edge를 찾지 못했습니다. 테스트 프로세스에 실제 설치 경로 변수를 제공한 뒤 **1 통과**했습니다. 제품 코드는 바꾸지 않았습니다.

후속 검사:

- `tests/test_network_disconnect.py tests/test_readable_storage.py tests/test_operator_dashboard.py`: **7 통과**.
- 긴 유니코드 이름 검사 4개를 추가한 최종 `tests/test_readable_storage.py tests/test_operator_dashboard.py`: **10 통과**.
- 전체 실행과 후속 검사를 합쳐 서로 다른 테스트 **181개 통과**, 배포 ZIP 검사용 환경 변수 `AH_PORTABLE_ZIP`이 필요한 **3개 생략**입니다.
- `git diff --check`: 통과.

저장 검사는 한글·공백·동명이인·대소문자 차이·유니코드 정규화·긴 파일명·동일 파일명·경로 조작 문자열·Windows 예약 이름·재제출·수강생 간 권한·파일 내용 일치·기존 경로 전환 중단/재시작·미완료 이어 올리기를 포함합니다.

## 실제 브라우저

`.venv/Scripts/python.exe scripts/check_operator_browser.py`: **통과**.

Edge에서 로그인 문구, 수강생 이름·파일명 표시, 최신/이전 버전, 파일명 검색, 운영자 다운로드 파일명과 실제 내용, 검색 조건이 반영된 CSV, 미제출자 필터, 모바일 메뉴 접힘과 가로 넘침 여부를 확인했습니다. JavaScript 오류와 Streamlit 예외는 없었습니다.

[로그인 화면](evidence/operator-login.png) · [제출 현황 화면](evidence/operator-dashboard.png) · [모바일 조회 화면](evidence/operator-mobile.png)

이번 변경에서는 대용량 부하 시험과 배포 ZIP 재빌드를 수행하지 않았습니다. [저장 경로·전환·운영 안내](submission-storage.md)를 함께 참고하세요.
