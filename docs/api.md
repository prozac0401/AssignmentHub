# API 참조

외부 클라이언트는 인스턴스의 **단일 공개 주소**를 사용합니다. 예시는 `http://192.168.0.10:8501`이며 실제 안내 주소로 바꿉니다. API 기본 경로는 `/api`입니다. HTML 업로더는 `/upload`, 로컬 정적 자산은 `/upload-static/`, Streamlit 화면은 `/ui/{instance_id}/`입니다. 외부 `/`는 해당 Streamlit 경로로 이동합니다. 내부 loopback 포트는 클라이언트에 안내하지 않습니다.

## 인증과 오류

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
| 429 | 동시 전송 슬롯 또는 로그인 시도 제한. 제한된 대기·재시도 |
| 503 / 507 | DB/저장 장치 실패. 성공으로 표시하지 않고 공간·권한 점검 후 상태 확인 |

## 계정·화면 연결

| 메서드·경로 | 본문 / 결과 |
| --- | --- |
| `GET /health` | `{status, instance_id}` |
| `GET /info` | 과정명, 인스턴스 ID, 시간대, 공개 용량·청크·동시성 설정, `ui_path` |
| `POST /auth/login` | `{user_id,password}` → 로그인 결과 |
| `GET /auth/me` | 현재 사용자. 제한 세션에서도 허용 |
| `POST /auth/password` | `{current_password,new_password,confirm_password}` → 변경 안내; 재로그인 필요 |
| `POST /auth/logout` | 해당 로그인 및 연결 세션 무효화 |
| `POST /auth/bridge` | 직접 로그인한 정상 세션에서 `{code,expires_in:120}` 발급. 연결로 파생된 세션은 재발급 불가 |
| `POST /auth/exchange` | `{code}` → 연결된 로그인 결과. 코드는 한 번만 사용 |

일회용 코드는 Streamlit과 업로더 사이의 명시적 연결에 사용합니다. 연결 세션이 다시 연결 세션을 만드는 중첩은 거절합니다. 업로더는 직접 로그인도 지원합니다. 토큰은 브라우저 JavaScript 메모리에만 보관하며 새로고침 후 재로그인합니다.

## 과제·업로드·이력

| 메서드·경로 | 의미 |
| --- | --- |
| `GET /assignments` | 과제 목록 `{id,title,description,is_open}` |
| `GET /quota` | `used_bytes,reserved_bytes,quota_bytes,max_file_bytes,chunk_bytes,max_files,min_free_bytes` |
| `GET /uploads` | 본인 미완료·취소·만료·실패 작업 목록 |
| `POST /uploads` | 고정 파일 묶음 시작 및 용량 예약 |
| `GET /uploads/{upload_id}` | 소유권 검사 후 상태와 파일별 확정 offset |
| `PATCH /uploads/{upload_id}/files/{file_id}?offset=N` | 인증된 원시 청크 수신 |
| `POST /uploads/{upload_id}/verify` | 전 파일 실제 크기·SHA-256 확인. 성공 시 `finalizing`; 아직 제출 완료 아님 |
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

## 다운로드

| 메서드·경로 | 의미 |
| --- | --- |
| `GET /files/{file_id}/download` | bearer 인증 후 완료 파일 스트리밍; 소유자 또는 관리자만 허용 |
| `POST /files/{file_id}/ticket` | bearer 인증 후 120초 일회용 다운로드 `{ticket,expires_in}` |
| `POST /downloads` | `application/x-www-form-urlencoded` 본문 `ticket=...`으로 브라우저 기본 다운로드 |

다운로드는 `Content-Disposition: attachment`와 실제 크기를 사용합니다. 티켓은 URL query에 넣지 않습니다. 본래 세션의 로그아웃·초기화·만료도 적용되며 전송 중 세션 유효성을 재확인합니다. 서버는 파일 전체를 메모리에 올리지 않습니다.

## 관리자

모든 `/admin/*` 경로는 정상 관리자 세션을 요구합니다. 명단으로 관리자 권한을 부여하지 않습니다.

| 메서드·경로 | 본문 / 결과 |
| --- | --- |
| `GET /admin/users?search=...` | ID·이름·그룹 검색, 활성·변경 필요 상태 |
| `POST /admin/roster/preview` | 원시 `.xlsx` 본문, 최대 5MiB → `{rows,errors,valid}` |
| `POST /admin/roster/apply` | `{rows:[{user_id,name,group}],update_existing:false}` → 새 계정의 일회성 임시비밀번호와 수정 건수 |
| `POST /admin/users/{internal_user_id}/reset` | 새 임시비밀번호 발급, 기존 세션 무효화 |
| `PATCH /admin/users/{internal_user_id}` | `{active:true 또는 false}` |
| `POST /admin/assignments` | `{title,description,is_open}` |
| `PATCH /admin/assignments/{assignment_id}` | 제목·설명·접수 상태의 변경할 필드만 전달 |
| `GET /admin/dashboard` | `assignment_id,search,group,include_inactive` query. 전체 과제 집계와 필터된 행/제출 목록 |
| `GET /admin/export?assignment_id=...` | 수식 주입을 막은 CSV, 설정 시간대 표시 |
| `GET /admin/storage` | 완료 사용량·예약·실제 임시 바이트·미기록 예약·볼륨 여유·적용 설정 |
| `GET /admin/audit` | 최근 관리 작업 기록, 관리자·UTC 시각·대상. 비밀번호·토큰 미포함 |

`roster/preview`의 행은 행 번호, ID/이름/그룹, `action` (`new`, `update`, `unchanged`), `errors`, `existing`를 포함합니다. 오류가 있으면 전체 명단을 수정하고 다시 미리보기합니다. 재등록은 비밀번호 초기화가 아닙니다. `update_existing`을 명시적으로 선택한 경우에만 기존 이름·그룹을 바꾸며 비밀번호·제출·활성 상태는 보존합니다.
