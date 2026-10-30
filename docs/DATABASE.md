# 데이터베이스 테이블·필드 명세

현재 구현 기준의 DB 구조입니다. 로컬 Docker 환경은 PostgreSQL을 사용하며,
Docker 없이 직접 실행할 때는 SQLite도 사용할 수 있습니다.

- 모델: [app/models.py](../app/models.py)
- 초기 마이그레이션: [0001_meetings.py](../migrations/versions/0001_meetings.py)
- 전원 응답 자동 추천: [0002_group_submissions.py](../migrations/versions/0002_group_submissions.py)
- 백엔드 전용 테이블 RLS: [0003_backend_only_tables.py](../migrations/versions/0003_backend_only_tables.py)
- 입력 검증: [app/schemas.py](../app/schemas.py)
- API 사용법: [API.md](API.md)

## 1. 전체 관계

업무 테이블은 3개이며, Alembic이 별도의 스키마 버전 관리 테이블을 사용합니다.

```text
meetings (모임)
├── 1:N ── members (모임별 참여자)
└── 1:N ── recommendation_runs (추천 실행 이력)

alembic_version (DB 스키마 버전 관리)
```

- `id`는 UUID v4를 생성해 `VARCHAR(36)` 문자열로 저장합니다.
- 표의 타입은 PostgreSQL 기준이며, `TIMESTAMPTZ`는 시간대 정보를 포함하는 타임스탬프입니다.
- 생성 시각은 애플리케이션에서 UTC로 생성합니다. 모임 날짜·시간 입력은 Asia/Seoul 기준입니다.
- 기본값은 별도 표기가 없으면 애플리케이션/ORM에서 설정하며 DB의 `DEFAULT` 제약은 아닙니다.
- 아래의 NULL 허용은 DB 열의 nullable 설정입니다. JSON 값 내부의 `null`과는 구분됩니다.
- `members`, `meeting` 같은 ORM 관계 속성은 별도의 DB 열이 아닙니다.

## 2. meetings — 모임

모임의 목표 인원·설정·처리 상태·공통 일정·현재 추천 결과·최종 장소를 저장합니다.

| 필드 | 타입 | NULL 허용 | 기본값·생성 규칙 | 설명·제약 |
|---|---|:---:|---|---|
| `id` | VARCHAR(36) | 아니요 | UUID v4 | PK, 모임 식별자 |
| `title` | VARCHAR(40) | 아니요 | API 기본값 `우리 모임` | 모임 이름 |
| `expected_member_count` | INTEGER | 아니요 | API 필수, DB fallback `1` | 방장 포함 목표 인원, API에서 1~10 허용 |
| `recommendation_options` | JSON | 아니요 | DB·API 기본값: count 5, radius_m 1000, extra_text 빈 문자열 | 전원 제출 후 사용할 추천 옵션 |
| `invite_code` | VARCHAR(64) | 아니요 | 무작위 URL-safe 문자열 | UNIQUE, 참여용 초대 코드 |
| `candidate_dates` | JSON | 아니요 | 새 모임 기본 `[]` | 빈 배열은 자유 날짜 모드, 명시한 비어 있지 않은 배열은 fixed 모드 |
| `time_range` | JSON | 아니요 | 새 모임 기본 06:00~24:00 | 선택할 수 있는 시간 범위. 24:00은 종료만 허용 |
| `slot_minutes` | INTEGER | 아니요 | 새 모임 기본 `30` | 슬롯 간격. API에서 30 또는 60만 허용 |
| `status` | VARCHAR(20) | 아니요 | `waiting` | 진행 상태 |
| `processing_started_at` | TIMESTAMPTZ | 예 | 없음 | 자동 추천 처리 시작, 10분 초과 시 재요청으로 재처리 가능 |
| `processing_error` | JSON | 예 | 없음 | 실패 code·message |
| `confirmed` | JSON | 예 | 미확정 | 서버가 자동 선택한 공통 날짜·시간과 전원 참석자 ID 배열 |
| `recommendation_result` | JSON | 예 | 추천 없음 | 현재 유효한 추천 결과 전체 |
| `confirmed_place` | JSON | 예 | 미확정 | 최종 선택한 장소 객체 |
| `revision` | INTEGER | 아니요 | `1` | 모임 변경 버전, 동시 수정 충돌 검사 |
| `created_at` | TIMESTAMPTZ | 아니요 | 현재 UTC 시각 | 생성 시각 |

### 상태와 전원 응답 집계

```text
waiting → processing → recommended → finalized
   대기       자동 계산      추천 결과       최종 장소
                  ├── schedule_conflict (공통 시간 없음)
                  └── failed (처리 실패)
```

상태 값은 코드에서 관리하며 DB ENUM이나 CHECK 제약은 아닙니다.
가입 인원과 응답 완료 인원은 별개입니다. members.submitted_at이 있는 서로 다른 참여자 수가
expected_member_count와 같을 때만 자동 계산을 시작합니다. 같은 사람의 재전송은 인원을 늘리지 않습니다.

GET 모임 응답의 progress는 저장 열이 아니라 다음 값을 계산한 객체입니다.

| 필드 | 의미 |
|---|---|
| expected_member_count | 생성 시 정한 총 인원, 방장 포함 |
| joined_member_count | 실제 가입한 인원 |
| submitted_member_count | 전체 응답 제출 완료 인원 |
| remaining_member_count | 아직 응답을 기다리는 인원, 미가입 인원 포함 |
| all_submitted | 설정 인원 전원 제출 여부 |

응답 수정 시 기존 공통 시간·추천·최종 장소를 해제하고, 전원 제출 상태이면 다시 자동 처리합니다.
공통 시간은 전원이 가능한 가장 긴 연속 구간으로 자동 선택합니다.
모임 변경마다 revision을 증가시키며 계산 중 입력이 바뀌면 이전 결과는 저장하지 않습니다.
외부 API 실패 시 제출한 응답은 보존하고 processing_error를 저장해 재시도할 수 있습니다.

### candidate_dates / time_range / confirmed

저장된 candidate_dates가 빈 배열인 자유 날짜 모임은 API 응답에서 `date_selection_mode: free`를 반환하고,
응답의 candidate_dates를 현재 참여자들의 availability에 있는 날짜 합집합으로 계산합니다.
공통 시간 계산도 이 합집합을 사용합니다. 따라서 다른 참가자가 선택한 날짜만으로 입력을 제한하지 않습니다.
저장된 비어 있지 않은 배열은 fixed 모임이며 기존 날짜 범위를 유지합니다. DB 컬럼 추가·마이그레이션은 필요 없습니다.
아래는 기존 fixed 모임의 예시입니다.

```json
{
  "candidate_dates": ["2026-11-01", "2026-11-02"],
  "time_range": {
    "start": "10:00",
    "end": "23:00"
  },
  "confirmed": {
    "date": "2026-11-01",
    "start": "18:00",
    "end": "21:00",
    "attending_member_ids": ["참석자 UUID"],
    "missing_member_ids": []
  }
}
```

`attending_member_ids`, `missing_member_ids`는 `members.id`를 담지만,
JSON 내부이므로 DB 외래 키 제약은 없고 코드에서 구성합니다.
현재 자동 처리에서는 전원 공통 시간만 사용합니다. 일부 참석 확정은 지원하지 않으며 missing_member_ids는 빈 배열입니다.

## 3. members — 모임별 참여자

참여자의 권한·인증 정보와 각자의 가능 시간·장소 조건을 저장합니다.
현재는 전역 사용자 계정이 아닌 모임별 참여자 모델입니다.

| 필드 | 타입 | NULL 허용 | 기본값·생성 규칙 | 설명·제약 |
|---|---|:---:|---|---|
| `id` | VARCHAR(36) | 아니요 | UUID v4 | PK, 참여자 식별자 |
| `meeting_id` | VARCHAR(36) | 아니요 | 소속 모임 ID | FK → `meetings.id`, 인덱스 있음 |
| `name` | VARCHAR(20) | 아니요 | 요청에서 입력 | 모임 내 표시 이름 |
| `is_host` | BOOLEAN | 아니요 | `false` | 모임 생성자는 `true` |
| `token_digest` | VARCHAR(64) | 아니요 | 접근 토큰의 SHA-256 해시 | UNIQUE, 원본 토큰은 저장하지 않음 |
| `availability` | JSON | 아니요 | `{}` | 날짜별 가능한 슬롯 시작 시각 배열 |
| `availability_submitted` | BOOLEAN | 아니요 | `false` | 일정 제출 여부 |
| `preference` | JSON | 예 | 미입력 | 선호·예산·분위기·출발지와 원문 |
| `submitted_at` | TIMESTAMPTZ | 예 | 미제출 | 전체 응답 제출 완료 시각. 인원 집계 기준 |
| `created_at` | TIMESTAMPTZ | 아니요 | 현재 UTC 시각 | 참여자 생성 시각 |

### 제약과 인증

- `(meeting_id, name)`은 UNIQUE입니다. 같은 모임 내 이름 중복은 허용하지 않습니다.
- `meeting_id`는 `ON DELETE CASCADE`이므로 모임 삭제 시 참여자도 삭제됩니다.
- 원본 접근 토큰은 생성·참여 API 응답에서만 반환합니다.
- 이후 요청은 `Authorization: Bearer <access_token>`으로 본인을 식별합니다.
- 이름으로 다른 참여자의 입력을 수정할 수 없습니다.
- 전원 제출 시 자동 추천하며 방장만 최종 장소를 확정합니다. 추천 재시도는 모든 참여자가 가능합니다.
- API에서 모임별 최대 인원을 방장 포함 10명으로 제한합니다.

### availability

```json
{
  "2026-11-01": ["18:00", "19:00", "20:00"],
  "2026-11-02": ["14:00", "15:00"]
}
```

60분 슬롯에서 `18:00`은 18:00~19:00에 참석 가능하다는 뜻입니다.
모임의 후보 날짜와 선택 가능한 슬롯만 저장할 수 있습니다.

| availability_submitted | availability | 의미 |
|:---:|---|---|
| `false` | `{}` | 아직 일정을 제출하지 않음 |
| `true` | `{}` | 제출했지만 가능한 시간이 없음 |
| `true` | 날짜·슬롯 데이터 | 가능한 시간을 제출함 |

### preference

| JSON 필드 | 값 형태 | 설명 |
|---|---|---|
| `raw_text` | 문자열 | 선호·비선호 원문을 분석용으로 구성한 문장 |
| `start_location_text` | 문자열 | 사용자가 별도로 입력한 출발지 |
| `desired_text` | 문자열 | 원하는 조건 원문 |
| `avoided_text` | 문자열 | 피할 조건 원문 |
| `submission_input` | 객체 | 날짜별 availability를 제외한 원래 제출 필드. 동일 요청 판정에 사용 |
| `likes` | 문자열 배열 | 좋아하는 음식·활동·장소 종류 |
| `dislikes` | 문자열 배열 | 피하고 싶은 조건 |
| `mood` | 문자열 배열 | 원하는 분위기 |
| `budget_max` | 정수 또는 null | 1인 최대 예산, 원 단위 |
| `indoor` | 문자열 | `indoor`, `outdoor`, `any` |
| `start_location` | 객체 또는 null | 출발지 이름·위도·경도 |

```json
{
  "raw_text": "강남역에서 출발. 조용한 파스타집, 2만원까지",
  "likes": ["파스타"],
  "dislikes": [],
  "mood": ["조용한"],
  "budget_max": 20000,
  "indoor": "any",
  "start_location": {
    "text": "강남역",
    "lat": 37.4979,
    "lng": 127.0276
  }
}
```

출발지 좌표는 위도 -90~90, 경도 -180~180 범위를 입력 검증합니다.
일정·출발지·선호·비선호를 하나의 제출 요청으로 함께 저장합니다. 입력 검증·출발지 조회가 실패하면 부분 저장하거나 제출 완료로 집계하지 않습니다.

## 4. recommendation_runs — 추천 실행 이력

저장에 성공한 추천 요청과 결과를 보관합니다. 실패하거나 버전 충돌로 취소된 결과는 저장하지 않습니다.

| 필드 | 타입 | NULL 허용 | 기본값·생성 규칙 | 설명·제약 |
|---|---|:---:|---|---|
| `id` | VARCHAR(36) | 아니요 | UUID v4 | PK, 추천 실행 식별자 |
| `meeting_id` | VARCHAR(36) | 아니요 | 대상 모임 ID | FK → `meetings.id`, 인덱스 있음 |
| `input_revision` | INTEGER | 아니요 | 전원 제출 후 처리 시작 당시 모임 버전 | 어떤 버전의 입력으로 생성했는지 기록 |
| `request` | JSON | 아니요 | 추천 요청 본문 | 개수·반경·추가 조건 |
| `result` | JSON | 아니요 | 추천 결과 전체 | 중심지·순위·이동 정보 등 |
| `created_at` | TIMESTAMPTZ | 아니요 | 현재 UTC 시각 | 추천 실행 기록 생성 시각 |

`meeting_id`는 `ON DELETE CASCADE`입니다.
현재 추천은 `meetings.recommendation_result`에 저장하고 이전 추천은 이 테이블에 남깁니다.
선호 변경으로 현재 추천을 해제하더라도 기존 실행 이력은 유지됩니다.

### request

```json
{
  "count": 3,
  "radius_m": 1000,
  "extra_text": "대화하기 좋은 곳"
}
```

API에서 `count`는 1~10, `radius_m`은 300~3000m, `extra_text`는 최대 200자로 검증합니다.

### result / meetings.recommendation_result

| JSON 필드 | 값 형태 | 설명 |
|---|---|---|
| `schedule` | 객체 | 확정 날짜·시간·참석자 |
| `center` | 객체 | 중심지의 `name`, `search_name`, `lat`, `lng` |
| `group_summary` | 문자열 | 그룹 선호 요약 |
| `search_queries` | 문자열 배열 | 후보 검색에 사용한 검색어 |
| `candidate_count` | 정수 | 비선호 조건을 걸러낸 후보 수 |
| `fallback` | Boolean | AI 순위 생성 실패로 규칙 기반 추천을 사용했는지 |
| `recommendations` | 객체 배열 | 추천 순위·장소·이동 정보 |

각 `recommendations` 항목:

| JSON 필드 | 값 형태 | 설명 |
|---|---|---|
| `rank` | 정수 | 추천 순위 |
| `place` | 객체 | 장소 정보 |
| `score` | 숫자 | 추천 점수, 0~100 |
| `reason` | 문자열 | 추천 이유 |
| `matched` | 문자열 배열 | 만족한 선호 조건 |
| `warnings` | 문자열 배열 | 가격·영업 여부 등 확인 사항 |
| `travel` | 객체 배열 | 참석자별 이동 정보 |
| `travel_text` | 문자열 배열 | 이동 정보 표시 문장 |

`place` 내부 필드:

| 필드 | 값 형태 | 설명 |
|---|---|---|
| `id` | 문자열 | 검색 결과를 바탕으로 생성한 장소 식별자 |
| `name` | 문자열 | 장소 이름 |
| `category` | 문자열 | 장소 분류 |
| `address` | 문자열 | 주소 |
| `lat` / `lng` | 숫자 | 위도 / 경도 |
| `phone` | 문자열 | 전화번호, 없으면 빈 문자열 |
| `url` | 문자열 | 장소 안내 또는 지도 링크 |
| `distance_m` | 숫자 | 추천 중심지에서의 직선거리, m |

`travel` 항목 내부 필드:

| 필드 | 값 형태 | 설명 |
|---|---|---|
| `member` | 문자열 | 참여자 표시 이름 |
| `from` | 문자열 또는 null | 출발지 이름 |
| `straight_km` | 숫자 또는 null | 직선거리, km |
| `driving_km` | 숫자 또는 null | 자동차 도로 거리, km |
| `driving_minutes` | 숫자 또는 null | 자동차 이동 시간, 분 |
| `text` | 문자열 | 화면에 표시할 이동 설명 |

`meetings.confirmed_place`에는 선택한 `place` 객체를 저장합니다.
별도의 장소 테이블이 없으므로 `place.id`는 DB 외래 키가 아닙니다.
코드가 현재 추천 목록에 있는 장소인지 확인한 뒤 최종 확정합니다.
영업시간·가격·예약 가능 여부는 현재 데이터로 보장하지 않습니다.

## 5. alembic_version — 스키마 버전 관리

Alembic이 생성·관리하는 내부 테이블입니다.

| 필드 | 설명 |
|---|---|
| `version_num` | 현재 적용된 마이그레이션 버전 |

직접 수정하는 대신 Alembic 명령을 사용합니다.

```sh
python -m alembic current
python -m alembic upgrade head
python -m alembic check
```

## 6. 현재 저장 설계의 범위

- 전역 사용자 계정·로그인 세션 테이블은 없습니다. 같은 사람도 모임마다 별도 참여자로 생성됩니다.
- 가능 시간·선호·장소·참석자 목록은 별도 관계 테이블 대신 JSON에 저장합니다.
- JSON 내부 참조에는 DB 외래 키 제약이 없으며 애플리케이션에서 검증합니다.
- 입력 검증 규칙과 상태 전이는 모두 DB 제약으로 구현된 것은 아닙니다.
- 이전 `data/meetings.json` 파일은 현재 DB와 별개이며 자동으로 이관하지 않습니다.

## 7. 기존 데이터 이관

0002 마이그레이션은 기존 모임·추천 이력을 삭제하지 않습니다.
기존 모임의 목표 인원은 실제 가입한 인원으로 설정하고, 일정 제출 여부와 출발지를 포함한 선호가 있는 참여자는 제출 완료로 이관합니다.
새 모임은 반드시 API 생성 요청에 목표 인원을 명시해야 합니다.

## 8. Supabase의 접근 경로

Railway API가 PostgreSQL URI로 직접 연결합니다. 프론트는 Supabase 테이블을 직접 읽지 않고 기존 모임별 Bearer 토큰으로 API를 호출합니다.
0003은 PostgreSQL에서 업무 테이블과 alembic_version의 RLS를 활성화하며 브라우저 역할용 허용 정책은 추가하지 않습니다.
현재 배포는 마이그레이션으로 테이블을 생성한 DB 소유자 연결을 사용합니다. 별도 런타임 DB 역할은 추가 권한·정책 설계가 필요합니다.
SQLite에서는 해당 마이그레이션의 RLS 작업을 생략합니다. 자세한 연결 절차는 [DEPLOYMENT.md](DEPLOYMENT.md)를 참고하세요.
