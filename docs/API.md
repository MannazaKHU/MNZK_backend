# API v2: 프론트 연동 계약

기존 단일 모임 API에서 여러 모임 API로 변경되었습니다. 예전 경로는 호환하지 않습니다.
시간·날짜 입력은 Asia/Seoul 기준 `YYYY-MM-DD`, `HH:MM`입니다.

## 모임 생성

`POST /api/meetings`

```json
{
  "title": "동아리 모임",
  "host_name": "민수",
  "candidate_dates": ["2026-11-01", "2026-11-02"],
  "time_range": {"start": "10:00", "end": "23:00"},
  "slot_minutes": 60
}
```

모든 필드는 선택 사항입니다. 기본 날짜는 한국 시간 기준 오늘부터 7일입니다.

응답 `201`:

```json
{
  "meeting": {
    "id": "<meeting-id>",
    "title": "동아리 모임",
    "status": "collecting",
    "revision": 1,
    "invite_code": "<invite-code>",
    "members": [{"id": "<host-id>", "name": "민수", "is_host": true}],
    "confirmed": null,
    "confirmed_place": null,
    "recommendations": [],
    "recommendation_result": null
  },
  "member_id": "<host-id>",
  "access_token": "<host-token>",
  "token_type": "bearer"
}
```

예시는 일부 필드를 생략했습니다. 모임 응답에는 후보 날짜·시간 범위·슬롯 간격·생성 시각,
참여자별 availability·availability_submitted·preference도 포함됩니다.
방장은 모임 ID와 초대 코드만 다른 참여자에게 전달합니다. 방장 토큰은 공유하지 않습니다.

## 모임 참여와 조회

`POST /api/meetings/{id}/members` (인증 없이 초대 코드로 참여)

```json
{"name": "지수", "invite_code": "<invite-code>"}
```

생성과 동일한 응답 구조로 본인 `member_id`, `access_token`을 반환합니다.
참여자는 `invite_code`를 응답받지 않습니다. 이름 중복은 `409 MEMBER_CONFLICT`입니다.

이후 모든 모임 요청에 다음 헤더를 사용합니다.

```http
Authorization: Bearer <본인의 access_token>
```

`GET /api/meetings/{id}`로 최신 상태·입력·추천을 조회합니다.
DB에 저장된 토큰 해시는 응답에 포함되지 않습니다.

## 가능 시간 입력

`PUT /api/meetings/{id}/members/me/availability`

```json
{"availability": {"2026-11-01": ["18:00", "19:00", "20:00"]}}
```

슬롯은 시작 시각입니다. `18:00` 슬롯은 60분 설정일 때 18~19시를 의미합니다.
본문에 이름을 보내지 않습니다. 토큰 소유자의 입력 전체를 교체합니다.
`{"availability": {}}`도 제출로 기록하며, 가능한 시간이 없다는 뜻입니다.
응답은 최신 모임 객체입니다. 일정 변경은 기존 시간·장소 확정을 모두 해제합니다.

`POST /api/meetings/{id}/availability/parse`

```json
{"text": "일요일 저녁에 가능해"}
```

`{"availability": {...}, "note": "..."}`를 반환할 뿐 저장하지 않습니다.
사용자가 검토한 뒤 PUT으로 저장합니다.
MOCK 해석은 실제 날짜·자연어 해석이 아니라 단순 개발용 규칙입니다.

## 후보 시간과 시간 확정

`GET /api/meetings/{id}/schedule?min_hours=2&top=5`

응답: `total_members`, `responded_members`, `relaxed`, `best`, `heatmap`.
best 항목은 날짜, start, end, hours, available_count, available_members, missing_members를 포함합니다.
최소 시간은 슬롯 수로 올림 계산하며 조건을 만족하는 구간이 전혀 없으면
`relaxed: true`와 함께 짧은 후보를 제안합니다. 이 제안 자체는 확정이 아닙니다.

`POST /api/meetings/{id}/schedule/confirm` — 방장만 호출

```json
{"date": "2026-11-01", "start": "18:00", "end": "21:00"}
```

후보 날짜·슬롯 정렬·시간 범위·실제 참석 가능 여부를 검사합니다.
전원이 참석할 수 없으면 409를 반환합니다.
방장이 일부 참석을 선택했다면 `"allow_partial": true`를 추가할 수 있습니다.
이때 confirmed의 `attending_member_ids`, `missing_member_ids`로 참석자를 명시합니다.
아무도 참석할 수 없는 구간은 확정할 수 없습니다.
응답은 `status: scheduled`인 모임 객체입니다.

## 장소 조건 입력

`PUT /api/meetings/{id}/members/me/preference`

```json
{
  "text": "파스타 좋아하고 회는 싫어. 1인 2만원까지.",
  "start_location": {"text": "강남역", "lat": 37.4979, "lng": 127.0276}
}
```

start_location은 선택 사항이며 생략 시 자연어에서 출발지를 추출해 좌표를 조회합니다.
좌표를 못 찾으면 `location_found: false`이며 프론트에서 출발지를 다시 입력받아야 합니다.
시간 확정 전에도 입력할 수 있습니다.
응답은 `{"meeting": {...}, "location_found": true}`입니다.
선호 수정은 시간 확정을 유지하며 장소 추천·최종 장소만 해제합니다.

`GET /api/meetings/{id}/preferences/summary`는 코드로 합친 그룹 선호를 반환합니다.
확정 후에는 참석자를 기준으로 집계합니다. 조회만으로 AI 비용을 사용하지 않습니다.

## 추천과 최종 장소 확정

`POST /api/meetings/{id}/recommend` — 방장만 호출

```json
{"count": 3, "radius_m": 1000, "extra_text": "대화하기 좋은 곳"}
```

시간 확정과 참석자 전원의 선호·출발지 입력이 필요합니다.
응답은 `meeting_id`, `revision`, `schedule`, `center`, `group_summary`,
`search_queries`, `candidate_count`, `fallback`, `recommendations`를 포함합니다.
recommendations에는 place, score, reason, travel, travel_text, matched, warnings가 있습니다.
정확한 개수보다 후보가 적으면 가능한 후보만 반환합니다.
AI 순위 생성 실패 시 규칙 기반 결과와 `fallback: true`를 반환할 수 있습니다.
지도 검색 실패는 대체 장소를 만들지 않고 오류를 반환합니다.

확정 시간은 추천 맥락으로 전달하지만 영업 여부·예약 가능 여부를 검증하지는 않습니다.
각 추천의 warnings를 화면에 표시하세요.
GET 모임 응답의 `recommendation_result`에서도 동일한 추천 본문을 복원할 수 있습니다.

`POST /api/meetings/{id}/place/confirm` — 방장만 호출

```json
{"place_id": "<추천 place.id>", "revision": 8}
```

revision에는 최신 추천 응답 또는 최신 모임 조회에서 받은 값을 사용합니다.
현재 추천 목록에 있는 장소만 선택할 수 있습니다.
응답은 `status: finalized`, `confirmed_place: {...}`인 모임 객체입니다.

## 충돌·오류 처리

```json
{"detail": {"code": "SCHEDULE_REQUIRED", "message": "날짜와 시간을 먼저 확정해 주세요."}}
```

| 상태 | 주요 code | 프론트 처리 |
|---|---|---|
| 400 | VALIDATION_ERROR / INVALID_DATE / INVALID_SLOT / INVALID_PLACE | 입력 수정 |
| 401 | TOKEN_REQUIRED / INVALID_TOKEN | 해당 모임의 본인 토큰 확인 |
| 403 | HOST_REQUIRED / INVALID_INVITE | 방장 권한 또는 초대 코드 확인 |
| 404 | MEETING_NOT_FOUND / NO_CANDIDATES | 모임 또는 추천 조건 확인 |
| 409 | MEMBERS_UNAVAILABLE / NO_ATTENDEES | 시간 조율 재진행 또는 일부 참석 선택 |
| 409 | SCHEDULE_REQUIRED | 시간 확정 단계로 이동 |
| 409 | PREFERENCES_INCOMPLETE / LOCATIONS_INCOMPLETE | 참석자 입력 수집 |
| 409 | MEETING_CHANGED | GET으로 갱신 후 사용자에게 재시도 안내 |
| 409 | MEMBER_CONFLICT | 다른 표시 이름으로 참여 |
| 502 | AI_ERROR / MAP_ERROR | 실패 표시 후 사용자 재시도 |
| 503 | DATABASE_UNAVAILABLE | 서비스 준비 상태 확인 |

API는 오래된 비동기 추천 결과를 저장하지 않습니다.
409를 무조건 자동 재시도하면 AI 호출 비용이 반복될 수 있으므로 사용자가 갱신된 상태를 확인하게 합니다.
