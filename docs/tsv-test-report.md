# TSV 명단 입력 검증

2026-09-08, Windows x64 / Python 3.12.14 / Edge 152.0.4191.66에서 임시 과정과 예시 계정을 사용했습니다.

| 확인 항목 | 결과 |
| --- | --- |
| API와 TSV 검사 | `test_api.py`, `test_roster_tsv.py`: 48개 통과, 22.89초 |
| 텍스트 보존 | `001`과 `1`, 대소문자가 다른 ID를 각각 등록하고 앞자리 0 보존 확인 |
| 입력 형식 | UTF-8, UTF-8 BOM, UTF-16 LE/BE BOM, 선택 group, 빈 행, 열 순서 변경, 따옴표 안의 탭·줄바꿈 확인 |
| 오류 처리 | 중복 ID·필수 열·빈 이름·관리자 ID·잘못된 인코딩·따옴표·크기·행/열 한도 검사 |
| 실제 브라우저 | 붙여넣기, UTF-8 파일 등록, UTF-16 파일 미리보기, TSV 템플릿 다운로드 통과 |
| 미리보기와 반영 | 입력 변경·입력 방식 전환 후 이전 미리보기 해제, 중복 명단 반영 차단, 정상 명단 반영 후 계정 조회 확인 |

![TSV 명단 붙여넣기와 미리보기](manual/screenshots/25-tsv-roster.png)

재실행:

```bat
.venv\Scripts\python.exe -m pytest tests\test_api.py tests\test_roster_tsv.py -q
.venv\Scripts\python.exe scripts\check_roster_browser.py
```

브라우저 검사는 Node.js, Playwright와 Edge가 설치된 개발 PC에서 수행합니다. `.local-tests/` 아래 별도 과정에만 데이터를 쓰고 완료 후 해당 서버를 종료합니다. 배포 사용자에게는 이 도구나 개발 의존성을 설치할 필요가 없습니다.

API 검사에는 기존 Starlette/AnyIO 사용 중단 경고가 1건 있었습니다. 실제 브라우저에서 JavaScript 오류나 Streamlit 예외는 발생하지 않았습니다. 이번 변경에서 실제 2GiB 전송을 반복하지 않았으며, 기존 대용량 및 내장 Python 검증 기록은 [전체 보고서](test-report.md)와 [무설치 배포 보고서](portable-test-report.md)를 참고하세요.
