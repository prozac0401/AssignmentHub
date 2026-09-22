# API 참조

외부 클라이언트는 인스턴스의 **단일 공개 주소**를 사용합니다. 예시는 `http://192.168.0.10:8501`이며 실제 안내 주소로 바꿉니다. API 기본 경로는 `/api`입니다. HTML 업로더는 `/upload`, 로컬 정적 자산은 `/upload-static/`, Streamlit 화면은 `/ui/{instance_id}/`입니다. 외부 `/`는 해당 Streamlit 경로로 이동합니다. 내부 loopback 포트는 클라이언트에 안내하지 않습니다.

## 인증과 오류

API는 문서에 기재한 정확한 경로를 사용합니다. 말미 `/`가 다르면 자동 리다이렉트 없이 404를 반환합니다.

공개 `/api/health`, `/api/info`, 로그인·일회용 코드 교환 외의 JSON 요청에는 `Authorization: Bearer <token>` 헤더를 보냅니다. 토큰·비밀번호·일회용 코드는 URL이나 일반 로그에 넣지 않습니다. 브라우저 origin은 설정된 외부 URL과 일치해야 합니다. API bearer 인증은 브라우저 포트 공유 쿠키에 의존하지 않습니다.

로그인 응답은 `{token, must_change_password, user}`입니다. `user`는 내부 `id`, 표시용 `user_id`, `name`, `group`, `role`, `active`, `must_change_password`를 포함합니다. 임시비밀번호 세션은 비밀번호 변경·자기 계정 확인·로그아웃만 허용하며 과제·업로드·다운로드·관리 작업은 거절합니다. 비밀번호 변경은 모든 기존 세션을 무효화하므로 새 비밀번호로 다시 로그인합니다.

오류는 보통 `{detail: "한국어 설명"}`이며 입력 검증 오류는 해당 `fields`도 제공합니다. 비밀번호·토큰 입력값은 검증 오류에 되돌려 주지 않습니다.

| HTTP | 의미와 클라이언트 조치 |
| --- | --- |
| 401 / 403 | 인증 만료·취소, 최초 변경 필요, 권한 또는 origin 오류. 안내에 따라 재로그인/변경 |
| 404 | 대상 없음 또는 접근 불가 |
| 409 | offset·작업 상태·멱등 요청 충돌. 상태 조회 후 같은 파일 확인 |
| 410 | 미완료 보관 시간 만료 |
| 413 / 422 | 요청·청크·파일 크기, 형식 또는 해시 검증 오류 |
| 415 | 과제에서 허용하지 않은 분류 또는 변경된 영상 소리 정책. 허용 파일로 새 제출 시작 |
| 429 | 동시 전송 슬롯 또는 로그인 시도 제한. 제한된 대기·재시도 |
| 503 / 507 | DB/저장 장치 실패. 성공으로 표시하지 않고 공간·권한 점검 후 상태 확인 |

## 계정·화면 연결

| 메서드·경로 | 본문 / 결과 |
| --- | --- |
| `GET /health` | `{status, instance_id}` |
| `GET /info` | 과정명, 인스턴스 ID, 시간대, 공개 용량·청크·동시성 설정, `ui_path`, `file_categories: [{id,label,extensions}]`, `video_sample_seconds: 5` |
| `POST /auth/login` | `{user_id,password}` → 로그인 결과 |
| `GET /auth/me` | 현재 사용자. 제한 세션에서도 허용 |
| `POST /auth/password` | `{current_password,new_password,confirm_password}` → 변경 안내; 재로그인 필요 |
| `POST /auth/logout` | 해당 로그인 및 연결 세션 무효화 |
| `POST /upload` (API 접두사 없음) | 수업 화면의 폼 본문 `{token,assignment_id}`로 현재 로그인과 선택한 과제를 전달하여 제출 HTML 반환 |

v1.3.0에서는 `/auth/bridge`와 `/auth/exchange`를 제거했습니다. `POST /upload`는 기존 세션을 검증하고 새 세션을 발급하지 않습니다. 정상 로그인이 있어야 열 수 있으며, 첫 비밀번호 변경 전·로그아웃·계정 초기화·비활성화·다른 과정의 토큰은 거절합니다. 응답은 `no-store`이고 토큰을 URL·쿠키·브라우저 저장소에 보관하지 않습니다. `GET /upload`는 직접 로그인 화면입니다.

일반 비밀번호는 8~128자입니다. `/admin/roster/apply`의 선택 필드 `common_temporary_password`에 영문·숫자 8자리를 지정하면 이번 명단의 신규 계정에만 같은 임시비밀번호를 적용합니다. 생략 또는 `null`이면 개인별 8자리 자동 발급입니다. 빈 문자열·공백·한글·기호 또는 다른 길이는 전체 요청을 거절합니다. 초기화는 해당 계정에 새로운 개인별 8자리를 발급합니다.

## 과제·업로드·이력

| 메서드·경로 | 의미 |
| --- | --- |
| `GET /assignments` | 과제 목록 `{id,title,description,is_open,allowed_file_categories,video_audio_required}` |
| `GET /quota` | `used_bytes,reserved_bytes,quota_bytes,max_file_bytes,chunk_bytes,max_files,min_free_bytes` |
| `GET /uploads` | 본인 미완료·취소·만료·실패 작업 목록 |
| `POST /uploads` | 고정 파일 묶음 시작 및 용량 예약 |
| `GET /uploads/{upload_id}` | 소유권 검사 후 상태와 파일별 확정 offset |
| `PATCH /uploads/{upload_id}/files/{file_id}?offset=N` | 인증된 원시 청크 수신 |
| `POST /uploads/{upload_id}/verify` | 전 파일 크기·SHA-256·현재 허용 분류 및 영상 첫 5초 검사. 성공 시 `finalizing`; 아직 제출 완료 아님 |
| `POST /uploads/{upload_id}/complete` | 필요 시 검증하고 파일 이동·DB 확정 후 `completed` |
| `POST /uploads/{upload_id}/cancel` | 미완료 작업의 파일·예약 정리. 완료 제출은 취소/삭제 불가 |
| `GET /submissions?assignment_id=...&all_versions=true` | 본인 완료 이력. `all_versions=false`는 과제별 최신 완료만 반환 |

시작 본문:

```json
{
  "assignment_id": "서버가 발급한 과제 ID",
  "request_id": "클라이언트가 생성한 UUID",
  "files": [
    {"name": "과제.txt", "size": 3, "sha256": "전체 파일 SHA-256의 64자리 16진수"}
  ]
}
```

`request_id`를 같은 사용자·같은 파일 목록으로 재전송하면 같은 업로드를 반환합니다. 파일 목록을 바꾸면서 같은 키를 사용하면 거절합니다. 파일 크기·해시는 브라우저에서 제한된 청크로 계산합니다. 업로드 ID와 파일 ID는 시작 응답의 서버 발급 값을 사용합니다.

청크 본문은 원시 바이트이며 `X-Chunk-SHA256: <현재 청크의 64자리 SHA-256>` 헤더가 필수입니다. `offset`은 바이트 기준입니다. 파일별 순차 청크를 보내고 응답 `{offset: 서버가 확정한 다음 위치}`를 다음 요청에 사용합니다. 예를 들어 8MiB 청크가 성공하면 다음 offset은 8,388,608입니다. 실제 수신 바이트도 한도를 검사하므로 `Content-Length`나 선언 크기만 줄여 우회할 수 없습니다.

연결 종료·응답 유실 시 상태를 다시 조회하고 확정 offset에서 이어 갑니다. 같은 확정 청크를 동일 내용으로 재전송하는 것은 허용하며 다른 내용은 거절합니다. 전체 파일이 수신된 뒤에도 검증·저장 단계를 거쳐야 합니다. 동일 완료 요청은 이미 확정된 제출번호와 결과를 반환합니다. 파일 재선택 시 원본 전체 SHA-256을 비교해 잘못된 내용 연결을 막습니다.

업로드/완료 결과는 `id,assignment_id,assignment_title,user_id,status,total_bytes,submission_number,version,completed_at,files`를 포함합니다. 파일마다 `id,name,size,offset,sha256,stored_sha256`가 있습니다. 완료 전 번호·시각은 비어 있을 수 있습니다. 시각은 UTC ISO 8601입니다. 관리자 조회에는 저장 경로가 포함됩니다. 상태·내구성·예약 계산은 [구조 문서](architecture.md)를 참조합니다.

파일 결과의 `video_validation`은 미검사·일반 파일·기존 완료 파일이면 `null`, 통과한 영상이면 `{version,status:"passed",sample_seconds,video_frames,width,height,audio_present,audio_frames,audio_detected,audio_peak_dbfs,audio_rms_dbfs,warnings,sha256,checked_at}`입니다. `audio_detected`는 검사 구간에서 RMS -60 dBFS 이상의 소리가 확인되었다는 뜻입니다. 무음 수치는 `null`일 수 있습니다. 영상 오류는 422와 `failed` 상태로 반환하고 묶음의 파일·예약을 정리합니다. 검사기 불가(503)는 재시도 가능 상태를 유지합니다. 완료 요청을 직접 호출하거나 서버가 재시작되어도 영상 검사를 생략하지 않습니다.

## 다운로드

| 메서드·경로 | 의미 |
| --- | --- |
| `GET /files/{file_id}/download` | bearer 인증 후 완료 파일 스트리밍; 소유자 또는 관리자만 허용 |
| `POST /files/{file_id}/ticket` | bearer 인증 후 120초 일회용 다운로드 `{ticket,expires_in}` |
| `POST /downloads` | `application/x-www-form-urlencoded` 본문 `ticket=...`으로 브라우저 기본 다운로드 |

다운로드는 `Content-Disposition: attachment`와 실제 크기를 사용합니다. 티켓은 URL query에 넣지 않습니다. 본래 세션의 로그아웃·초기화·만료도 적용되며 전송 중 세션 유효성을 재확인합니다. 서버는 파일 전체를 메모리에 올리지 않습니다.

## 관리자

모든 `/admin/*` 경로는 정상 관리자 세션을 요구합니다. 명단으로 관리자 권한을 부여하지 않습니다.

`allowed_file_categories`는 `documents, spreadsheets, presentations, images, video, audio, archives, code, other` 중 중복 없는 배열입니다. `[]`는 모든 파일 차단입니다. 생성 시 생략하면 기타를 제외한 모든 분류가 허용되고, `video_audio_required`는 기본 `true`입니다. PATCH는 생략한 필드를 유지하며 `false`와 `[]`를 실제 변경으로 처리합니다. 허용 분류와 소리 필수는 제출 확정 트랜잭션에서도 현재 값을 재확인하며, 완료된 제출은 이후 설정 변경과 무관하게 보존합니다.

| 메서드·경로 | 본문 / 결과 |
| --- | --- |
| `GET /admin/users?search=...` | ID·이름·그룹 검색, 활성·변경 필요 상태 |
| `POST /admin/roster/preview` | 원시 TSV 본문 (`text/tab-separated-values`), 최대 5MiB → `{rows,errors,valid}` |
| `POST /admin/roster/apply` | `{rows:[{user_id,name,group}],update_existing:false}` → 새 계정의 일회성 임시비밀번호와 수정 건수 |
| `POST /admin/roster/jobs` | 위 등록 본문 + `request_id` → 202, 복구 가능한 명단 작업 상태 |
| `GET /admin/roster/jobs` | 현재 관리자·계정 버전의 보관 중 작업과 최근 실패 상태 |
| `GET /admin/roster/jobs/{job_id}` | 상태, 준비 인원, 완료·만료 시각, 신규·변경 건수 |
| `GET /admin/roster/jobs/{job_id}/result` | 완료 결과 `{created:[{user_id,name,temporary_password}],updated}` 재조회 |
| `DELETE /admin/roster/jobs/{job_id}/result` | 결과 수신 확인: 암호화 결과·작업별 복구 키 삭제 |
| `POST /admin/users/{internal_user_id}/reset` | 새 임시비밀번호 발급, 기존 세션 무효화 |
| `PATCH /admin/users/{internal_user_id}` | `{active:true 또는 false}` |
| `POST /admin/assignments` | `{title,description,is_open,allowed_file_categories,video_audio_required}` |
| `PATCH /admin/assignments/{assignment_id}` | 제목·설명·접수 상태·허용 분류·영상 소리 필수의 변경할 필드만 전달 |
| `GET /admin/dashboard` | `assignment_id,search,group,include_inactive` query. 전체 과제 집계와 필터된 행/제출 목록 |
| `GET /admin/export?assignment_id=...` | 수식 주입을 막은 CSV, 설정 시간대 표시 |
| `GET /admin/storage` | 완료 사용량·예약·실제 임시 바이트·미기록 예약·볼륨 여유·적용 설정 |
| `GET /admin/audit` | 최근 관리 작업 기록, 관리자·UTC 시각·대상. 비밀번호·토큰 미포함 |

`roster/preview`는 UTF-8(BOM 선택) 또는 UTF-16(BOM 필수) TSV를 읽습니다. 첫 행에 `user_id`, `name`이 각각 한 번 필요하고 `group`은 선택입니다. 모든 값을 텍스트로 읽으므로 `001`과 `1`을 구분합니다. 빈 행은 무시하고 탭·줄바꿈을 포함한 큰따옴표 셀을 지원합니다. 10,000명/30열을 넘거나 인코딩·따옴표 구문이 잘못되면 422, 본문이 5MiB를 넘으면 413을 반환합니다. `.xlsx`와 쉼표 구분 CSV는 TSV 명단으로 처리하지 않습니다.

미리보기의 행은 원본 행 번호, ID/이름/그룹, `action` (`new`, `update`, `unchanged`), `errors`, `existing`를 포함합니다. 오류가 있으면 전체 명단을 수정하고 다시 미리보기합니다. 미리보기는 계정을 생성하지 않습니다. `update_existing`을 명시적으로 선택한 경우에만 기존 이름·그룹을 바꾸며 비밀번호·제출·활성 상태는 보존합니다.

관리 화면은 `/roster/jobs`를 사용합니다. 클라이언트는 전송 전에 UUID 등 요청 ID를 만들고 응답 유실 시 같은 ID·같은 명단을 재전송합니다. 같은 요청은 원래 작업을 반환하고 내용을 바꾸면 409입니다. 작업 상태는 `queued`, `running`, `completed`, `failed`, `expired`, `revoked`, `unavailable`, `acknowledged`입니다. `processed_rows`는 계정 반영 전 비밀번호 준비 인원이며, 계정과 암호화 결과는 완료 시 한 트랜잭션으로 확정됩니다. 진행 중 결과 조회는 409, 삭제·만료 등 복구 불가 결과는 410입니다. 삭제된 요청 ID의 재전송은 원래 종료 상태를 반환하며 새 계정을 만들거나 새 비밀번호를 발급하지 않습니다. 실패 후 재작업은 새 요청 ID를 사용합니다.

로그아웃·세션 만료는 이미 접수한 명단 작업을 취소하지 않습니다. 같은 관리자로 다시 로그인하면 작업을 조회할 수 있습니다. 관리자 비밀번호 변경·비활성화 등으로 계정 epoch가 바뀌면 진행 중 반영과 이전 결과 접근을 취소합니다. 다른 관리자는 작업 결과에 접근할 수 없습니다. 서버 재시작 시 미완료 준비를 다시 수행하며 완료 결과는 그대로 복구합니다. 저장소당 작업자는 하나이며 대기·처리는 최대 4건, 보관 중 작업은 최대 32건, 암호화 보관량은 128MiB로 제한합니다.

입력과 임시비밀번호 결과는 작업별 Fernet 키로 암호화하여 SQLite에 넣고 키는 `roster-keys/`에 따로 보관합니다. 결과는 완료 후 1시간, 미완료 입력은 접수 후 1시간까지 보관합니다. 수신 확인·만료·계정 취소 시 키와 암호화 내용을 지우며 요청 ID·건수 등 비밀값 없는 이력은 중복 실행 방지를 위해 남습니다. 서버가 꺼진 동안 만료된 값은 다음 시작에서 정리합니다. 결과를 다운로드한 뒤 닫으면 서버에서 재조회할 수 없습니다.

TSV 미리보기는 5MiB, `/roster/apply`와 `/roster/jobs`의 JSON 본문은 64MiB입니다. JSON 한도는 최대 10,000행과 ID 128자·이름/그룹 각 200자의 최악 유니코드 이스케이프 증가까지 포함합니다. 본문을 읽기 전에 관리자 인증을 검사하고 동시에 버퍼링하는 명단 요청은 2건으로 제한합니다. 기존 동기 `/roster/apply`는 호환용이며 응답 유실 시 결과 복구를 제공하지 않으므로 신규 클라이언트는 작업 API를 사용하세요.

## 제출 파일 조회와 저장 경로

업로드·제출 응답은 수강생의 현재 이름 `name`과 그룹 `group`을 포함합니다. 완료 파일의 `storage_path`는 관리자에게만 반환하며, 전체 현황 CSV의 파일 항목에도 포함합니다. 관리자 다운로드 이름에는 수강생 이름·ID·제출번호·버전이 붙고, 수강생 다운로드는 원본 이름을 사용합니다. 다운로드 인증·일회용 티켓 규칙은 동일합니다. [저장 경로와 기존 데이터 전환](submission-storage.md)
