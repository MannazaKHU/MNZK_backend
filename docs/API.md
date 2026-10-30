# API v3: 전원 응답 후 자동 추천

그룹 생성 시 지정한 총 인원 N명(방장 포함)이 일정·출발지·선호·비선호를 모두 제출하면
서버가 공통 시간과 장소를 자동으로 계산합니다. 별도의 수동 시간 확정·추천 시작은 필요하지 않습니다.
날짜·시간 입력은 Asia/Seoul 기준 `YYYY-MM-DD`, `HH:MM`입니다.

## 1. 모임 생성

`POST /api/meetings`

```json
{
  "title": "동아리 모임",
  "host_name": "민수",
  "expected_member_count": 4
}
```

위 세 필드가 그룹 만들기 화면의 요청입니다. 날짜·가능 시간·출발지·선호는 이후 각 참여자의 응답 제출에서 보냅니다.
생성 화면에서는 `candidate_dates`를 보내지 않습니다. 서버는 `date_selection_mode: free`인 모임을 만들고,
각 참가자는 달력에서 오늘 이후 날짜를 자유롭게 선택해 나중에 `availability`로 제출합니다. 오늘부터 7일 제한은 없습니다.
`candidate_dates`를 명시하면 해당 날짜로 제한하는 고급 fixed 모임이 되며 기존 연동과의 호환용으로 지원합니다.

공통 일정 범위나 추천 옵션을 별도로 지정하는 경우에만 다음처럼 추가할 수 있습니다.

```json
{
  "title": "동아리 모임",
  "host_name": "민수",
  "expected_member_count": 4,
  "candidate_dates": ["2026-11-01", "2026-11-02"],
  "time_range": {"start": "10:00", "end": "23:00"},
  "slot_minutes": 30,
  "recommendation_options": {
    "count": 3,
    "radius_m": 1000,
    "extra_text": ""
  }
}
```

`expected_member_count`는 필수이며 4명으로 고정되지 않습니다.
현재 인원 상한은 기존 서비스 제한에 따라 10명이며 1~10명 중 설정합니다. 방장도 인원에 포함됩니다.
나머지 필드는 선택 사항입니다. 기본은 자유 날짜, 시간 범위 06:00~24:00, 슬롯 30분,
추천 개수 5개, 반경 1000m입니다. `24:00`은 종료 시각이며 슬롯 시작으로 보낼 수 없습니다.
연도만 있는 `["2026"]`은 날짜가 아니므로 400이며,
수동으로 범위를 지정할 때는 `["2026-11-01", "2026-11-02"]`처럼 전체 날짜를 사용합니다.
자유 날짜 모임의 응답 `candidate_dates`는 제출된 가능 날짜의 합집합이며, 최초에는 빈 배열입니다.
선택 가능한 날짜 목록이 아니므로 이 값으로 달력의 날짜를 제한하지 않습니다.
명시한 날짜 범위로 생성한 fixed 모임에서만 범위 밖 날짜를 `INVALID_DATE`로 거부합니다.
추천 옵션은 마지막 제출자의 요청이 아니라 모임 생성 시 지정한 값을 사용합니다.

응답 `201`:

```json
{
  "meeting": {
    "id": "<meeting-id>",
    "title": "동아리 모임",
    "expected_member_count": 4,
    "date_selection_mode": "free",
    "candidate_dates": [],
    "time_range": {"start": "06:00", "end": "24:00"},
    "slot_minutes": 30,
    "status": "waiting",
    "revision": 1,
    "invite_code": "<invite-code>",
    "progress": {
      "expected_member_count": 4,
      "joined_member_count": 1,
      "submitted_member_count": 0,
      "remaining_member_count": 4,
      "all_submitted": false
    },
    "members": [
      {"id": "<host-id>", "name": "민수", "is_host": true, "submitted_at": null}
    ],
    "confirmed": null,
    "confirmed_place": null,
    "recommendations": [],
    "recommendation_result": null,
    "processing_error": null
  },
  "member_id": "<host-id>",
  "access_token": "<host-token>",
  "token_type": "bearer"
}
```

예시는 일부 필드를 생략했습니다. 모임 객체에는 후보 날짜·시간 범위·슬롯 간격·추천 옵션·생성 시각,
참여자별 availability·availability_submitted·preference도 포함됩니다.
방장은 모임 ID와 초대 코드만 공유하고 방장 토큰은 공유하지 않습니다.
원본 토큰은 생성·참여 응답에서만 반환합니다.

## 2. 모임 참여와 조회

`POST /api/meetings/{id}/members`

```json
{"name": "지수", "invite_code": "<invite-code>"}
```

생성과 같은 응답 구조로 모임 객체, 본인 member_id·access_token을 반환합니다.
가입만으로는 입력 완료 인원에 포함되지 않습니다. 설정 인원만큼 참여했다면 추가 가입은 거부합니다.
참여자는 invite_code를 응답받지 않으며 같은 모임의 이름 중복은 허용하지 않습니다.

이후 모임 API에는 본인 토큰을 사용합니다.

```http
Authorization: Bearer <access_token>
```

`GET /api/meetings/{id}`는 최신 모임 객체를 반환합니다.

- `joined_member_count`: 가입한 인원
- `submitted_member_count`: 전체 응답을 제출한 서로 다른 참여자 수
- `remaining_member_count`: 아직 응답을 기다리는 인원. 미가입 인원도 포함
- `all_submitted`: 설정한 인원 전원이 제출했는지
- `status`: 대기·처리·결과·오류 구분

전송 횟수가 아니라 참여자별 완료 여부를 집계합니다.

## 3. 내 응답 한 번에 제출

`PUT /api/meetings/{id}/members/me/submission`

```json
{
  "availability": {
    "2026-11-01": ["18:00", "18:30", "19:00", "19:30"],
    "2026-11-02": ["14:00", "14:30"]
  },
  "start_location_text": "경희대학교 국제캠퍼스",
  "desired_text": "조용한 곳에서 얘기하고 싶고 1인 2만원까지",
  "avoided_text": "회는 피하고 싶어"
}
```

`availability`, `desired_text`, `avoided_text`를 함께 보냅니다.
달력에서 고른 날짜를 availability의 키로, 시간 칠하기에서 선택한 30분 칸의 시작 시각을 값으로 보냅니다.
새로운 자유 날짜 모임은 아직 다른 사람이 제출하지 않은 날짜나 여러 달 뒤 날짜도 받을 수 있습니다.
자유 날짜 모임의 과거 날짜, 잘못된 달력 날짜, 30분 칸에 정렬되지 않은 시각은 거부합니다.
현재 한 참여자가 한 번에 제출할 수 있는 날짜는 최대 14개입니다.
특별한 선호·비선호가 없으면 해당 문자열을 빈 값으로 보낼 수 있습니다.
선택 필드 `conditions`를 명시하면 확인한 구조화 조건을 검색·요약·점수 계산에 우선 사용하며 AI로 다시 해석하지 않습니다.
`conditions: []`는 조건 없음입니다. 생략하면 자연어를 해석하며, 수량·단위 또는 원문 인용이 잘못된 AI 결과는 `502 AI_ERROR`로 반환하고 제출 완료로 저장하지 않습니다.
조건 형식은 [추천 규칙](RECOMMENDATION_RULES.md)을 참고하세요.
출발지 이름 `start_location_text` 또는 아래의 좌표 객체 중 하나는 필요합니다.

```json
{
  "text": "강남역",
  "lat": 37.4979,
  "lng": 127.0276
}
```

위 객체를 `start_location` 필드로 보내면 좌표 조회를 생략합니다.
출발지 조회·입력 검증이 실패하면 전체 응답을 저장하지 않으며 완료 인원도 증가하지 않습니다.
희망·회피 원문과 분석 결과를 서버에 보존합니다.

슬롯은 시작 시각입니다. 30분 설정에서 `18:00`은 18:00~18:30입니다.
fixed 모임만 생성 시 지정한 후보 날짜로 제한하며, 두 모드 모두 모임 시간 범위·슬롯을 검사합니다.
`{"availability": {}}`도 응답 제출로 인정하되
공통 시간이 없으므로 추천 결과 대신 `schedule_conflict`가 될 수 있습니다.

응답 `200`은 최신 **모임 객체**입니다.

- N명 중 N-1명까지: `waiting`, 남은 인원 표시, 추천 결과 없음
- N번째 서로 다른 참여자 제출: `processing`을 DB에 저장한 뒤 자동 계산
- 계산 성공: `recommended`, schedule·추천 목록을 저장하고 반환
- 공통 시간 없음: `schedule_conflict`, `processing_error.code = NO_COMMON_SCHEDULE`
- 외부 호출 등 처리 실패: `failed`, 응답은 보존하고 processing_error 반환

마지막 제출 요청은 추천 계산 완료를 기다립니다.
그동안 다른 참가자는 GET으로 `processing`을 볼 수 있습니다.
응답이 이미 저장된 후 계산에 실패한 경우에도 제출 자체는 성공하므로 HTTP 200과 `status: failed`를 반환합니다.
HTTP 성공 여부와 모임 status를 함께 확인해야 합니다.

동일한 응답 재전송은 인원·버전을 증가시키거나 추천을 중복 실행하지 않습니다.
다른 내용으로 재제출하면 기존 추천·최종 장소를 해제하고, 여전히 전원 제출 상태라면 다시 자동 계산합니다.
동시에 같은 참여자의 서로 다른 응답이 들어오면 `409 SUBMISSION_CHANGED`로 오래된 저장을 막습니다.
다른 참여자의 동시 제출은 짧은 DB 트랜잭션과 버전 검사로 처리합니다.

## 4. 프론트 대기·결과 화면 전환

1. 응답 제출 후 모임 객체의 progress와 status를 표시합니다.
2. waiting이면 `submitted_member_count / expected_member_count`와 남은 인원을 표시합니다.
3. 대기 중인 사용자도 `GET /api/meetings/{id}`를 주기적으로 조회합니다.
4. processing이면 처리 중 화면, recommended이면 추천 결과 화면으로 전환합니다.
5. schedule_conflict이면 일정 수정, failed이면 오류 안내·재시도 버튼을 표시합니다.

서버는 현재 WebSocket·이벤트 푸시를 제공하지 않습니다. 자동 화면 전환에는 프론트의 주기적 조회가 필요합니다.
GET 자체는 추천을 새로 실행하거나 AI 비용을 사용하지 않습니다.

## 5. 서버가 자동 선택하는 시간과 장소

전원에게 공통으로 가능한 연속 구간 중 가장 긴 구간을 선택합니다.
길이가 같으면 빠른 날짜·시각을 우선합니다. 최소 단위는 모임 슬롯 한 개입니다.
이 구간은 전원이 가능한 시간대이며 실제 모임 길이를 별도로 예약하는 기능은 없습니다.
공통 슬롯이 하나도 없으면 일부 인원만으로 추천하지 않습니다.

선택한 날짜·시간은 기존 `confirmed`에 저장하며, 여기에는 전원 참석 ID와 빈 missing_member_ids가 들어갑니다.
이 필드는 현재 서버가 자동 선택한 공통 시간대를 뜻합니다.
참석자 전원의 선호·출발지를 합쳐 장소 검색·필터링·순위·이동 정보 계산을 진행합니다.

`recommendation_result`에는 schedule, center, group_summary, search_queries,
candidate_count, fallback, recommendations가 있습니다.
각 추천은 rank, place, score, reason, travel, travel_text, matched, warnings를 포함합니다.
영업시간·예약 가능 여부는 검증하지 않습니다. warnings를 결과와 함께 표시합니다.

## 6. 실패·중단된 추천 재시도

`POST /api/meetings/{id}/recommend` — 본인 토큰, 요청 본문 없음

- 전원 미제출: `409 SUBMISSIONS_INCOMPLETE`
- 이미 결과가 있음: 기존 모임 객체 반환, AI 재호출 없음
- 처리 중: 진행 중인 모임 객체 반환, 중복 실행 없음
- failed 등 재시도 가능한 상태: 생성 시 지정한 옵션으로 다시 계산
- 서버 중단 등으로 processing 상태가 10분 이상 지속됐다면 재시도 요청으로 재처리 가능

참여자 누구나 재시도할 수 있습니다. 응답은 최신 모임 객체입니다.
지속적인 백그라운드 작업 큐는 없으며 마지막 제출/재시도 요청에서 계산합니다.
계산 도중 입력이 변경되면 이전 결과는 저장하지 않고 최신 상태를 반환합니다.

## 7. 장소 최종 확정

`POST /api/meetings/{id}/place/confirm` — 방장만 호출

```json
{"place_id": "<추천 place.id>", "revision": 8}
```

최신 모임 revision과 현재 추천 목록의 장소 ID를 전송합니다.
응답은 status가 finalized이고 confirmed_place가 있는 모임 객체입니다.

## 8. 보조 조회·해석

- `GET /api/meetings/{id}/schedule?min_hours=2&top=5`: 후보 시간·히트맵 참고 조회. 전원 자동 처리와 별개이며 수동 시간 확정은 하지 않습니다.
- `POST /api/meetings/{id}/availability/parse`, `{"text":"저녁 가능해","selected_dates":["2026-11-01"]}`: 달력에서 고른 날짜의 자연어 시간 미리 해석. 저장하지 않으며 최종 제출에 포함합니다. 자유 날짜 모임에 제출된 날짜도 없고 selected_dates도 없으면 `400 DATES_REQUIRED`입니다.
- `GET /api/meetings/{id}/preferences/summary`: 현재 제출된 선호의 코드 기반 요약. total은 목표 인원수입니다.

## 9. 오류 처리

| 상태 | 주요 code | 처리 |
|---|---|---|
| 400 | VALIDATION_ERROR / INVALID_DATE / INVALID_SLOT / LOCATION_NOT_FOUND | 입력 수정 |
| 400 | DATES_REQUIRED | 자연어 시간 해석에 사용할 selected_dates 전달 |
| 400 | MEETING_FULL | 설정한 인원 초과 |
| 401 | TOKEN_REQUIRED / INVALID_TOKEN | 본인 모임 토큰 확인 |
| 403 | HOST_REQUIRED / INVALID_INVITE | 방장 권한 또는 초대 코드 확인 |
| 404 | MEETING_NOT_FOUND | 모임 확인 |
| 409 | SUBMISSIONS_INCOMPLETE | 전원 응답 대기 |
| 409 | MEMBER_CONFLICT | 이름 수정 |
| 409 | MEETING_CHANGED / SUBMISSION_CHANGED | 최신 상태·본인 응답 확인 |
| 409 | RECOMMENDATION_REQUIRED | 결과 대기 |
| 502 | AI_ERROR / MAP_ERROR | 응답 저장 전 분석·좌표 조회 실패, 제출 재시도 |
| 503 | DATABASE_UNAVAILABLE | DB 준비 상태 확인 |

처리 단계의 오류는 모임 status와 processing_error로 확인합니다.
원하는 조건을 실제로 검증할 데이터가 없는 경우 추천의 warnings도 함께 표시합니다.

## 10. v2에서 달라진 점

- 생성 시 expected_member_count 필수, recommendation_options 추가
- 일정·선호 개별 저장 대신 `members/me/submission`으로 전체 응답 제출
- `schedule/confirm`과 일부 참석 확정 제거. 전원 공통 시간을 서버가 자동 선택
- `recommend`는 수동 최초 실행 대신 실패·중단 처리 재시도용이며 모임 객체를 반환
- waiting/processing/recommended/finalized/schedule_conflict/failed 및 progress 추가

## 사용자 조건·추천 근거 확장

개인 조건, 필수 자격 검사, 이동·선호·회피 점수와 프론트 추천 이유 토글은
[사용자 조건과 추천 근거 API](RECOMMENDATION_RULES.md)를 참고하세요.
기존 추천 경로와 응답 필드를 유지하고 조건·평가·기여분 필드를 추가했습니다.
전체 요청·응답 시연 데이터는 `examples/recommendation_demo.json`에 있습니다.

## 팀원 API 연동 경계와 후기 근거

`ENABLE_BLOG_ENRICHMENT=true`이고 실제 ChatKHU·지도 모드라면 기존 API HUB 검색 키로 블로그 결과를 자동 수집합니다.
활성 추천 경로는 장소·이동시간·선택적인 블로그 검색 결과를 `RecommendationInputs`로 전달해 같은 정책으로 계산합니다.
자동 수집 시 `blog_enrichment.connection_status: live`, `provider: naver_api_hub`와 실제 검색 호출 수를 반환합니다.
후기 검색·추출 실패는 해당 근거만 미확인으로 처리하며 기존 추천 평가를 계속합니다.
필수 조건은 후기만으로 통과시키지 않습니다. 활성화·한도·검증 범위는 [블로그 연결](BLOG.md)을 참고하세요.
입력 데이터 계약, 필수/선택 항목, 추출 콜백, 모의 요청·응답과 연결 상태는
[팀원 데이터 계약 초안](TEAM_RECOMMENDATION_DATA_CONTRACT.md)을 참고하세요.
