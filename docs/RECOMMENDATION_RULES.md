# 사용자 조건과 추천 근거 API

기존 `PUT /api/meetings/{meeting_id}/members/me/submission`과 자동 추천 흐름을 사용합니다.
요청에는 기존 참여자 Bearer 토큰을 넣습니다. 추천 재시도는 기존 `POST /api/meetings/{meeting_id}/recommend`,
결과 조회는 `GET /api/meetings/{meeting_id}`입니다. 기존 `recommendations[].place/score/rank/reason/travel/matched/warnings`를 유지합니다.
점수는 이제 소수이고, AI 점수와 비선호 키워드 일괄 제외는 사용하지 않습니다.

요청 예시 (기존 모임의 후보 날짜·슬롯에 맞춰 availability를 지정):

```json
{
  "availability": {"2026-11-01": ["10:00", "11:00"]},
  "start_location_text": "강남역",
  "desired_text": "짬뽕 좋아",
  "avoided_text": "주차가 없으면 안 돼",
  "conditions": [
    {"category":"kind","target":"짬뽕","direction":"preference","strength":"desired","scope":"menu","raw_text":"짬뽕 좋아"},
    {"category":"accessibility","target":"parking","direction":"preference","strength":"required","scope":"place","raw_text":"주차가 없으면 안 돼"}
  ]
}
```

`conditions` 생략 시 기존 ChatKHU 연동이 자연어를 구조화합니다. 명시한 배열은 확인·수정한 사용자 조건으로 사용하며,
빈 배열은 조건 없음입니다. mock 추출기는 제한된 단어·비용·분위기만 지원하고 알 수 없는 절은 확인 필요로 둡니다.
명시한 `conditions`는 AI로 다시 해석하지 않으며 후보 검색·그룹 요약·점수 계산에 동일하게 사용합니다.
원문과 구조화 조건이 다르면 확인된 구조화 조건을 우선합니다. 잘못된 AI 조건은 `502 AI_ERROR`로 반환하고 제출 완료로 저장하지 않습니다.
AI가 반환한 raw_text는 원문 부분 문자열이어야 합니다. 구조는 Pydantic으로 검증하고 AI는 점수·순위를 결정하지 않습니다.
`extra_text`는 검색 맥락이며 개인의 필수 조건을 대신하지 않습니다. 개인 요구는 제출 조건에 넣습니다.

여섯 종류는 kind(음식·활동·장소 종류), cost(비용), atmosphere(분위기), environment(공간·환경),
accessibility(편의·접근성), other(기타)입니다. strength는 desired/required,
방향은 preference/avoidance, 범위는 menu/shared_activity/place입니다.
OR는 target과 alternatives로 표현합니다. 가격 상한 예시는
`{"category":"cost","target":"price_per_person","operator":"lte","value":20000,"unit":"KRW","raw_text":"2만원 이하"}`입니다.
수량에는 단위가 필수이며, 지원 단위는 KRW/minutes/people입니다.
조건 id와 member_id는 서버가 실제 참여자에서 생성하며 요청에서 임의로 지정하지 않습니다.

## 응답과 프론트 토글

모임 응답의 `recommendation_result`에 아래 데이터가 있습니다.

- `meeting_id`, `conditions`: 모임·참여자별 조건. 동일인의 동일 요구는 중복 제거하고 필수가 있으면 필수로 합칩니다.
  조용함 선호/시끄러움 회피는 동일 요구로 합칩니다. 다른 사람의 의견은 보존합니다.
- `recommendations`: eligible이고 총점이 계산된 후보만, 요청 count 이내. 빈 목록도 정상 처리 결과입니다.
- `candidate_evaluations`: 검색된 모든 후보. 미확인·제외·이동정보 누락 후보도 여기에서 확인합니다.
- `recommendation_status`: available / no_verified_recommendations.
  후자는 `message: "조건을 확인한 추천 후보가 없음"`으로 표시하고 candidate_evaluations에서 원인을 확인합니다.
- 후보별 `eligibility`, `rank`, `score`, `score_status`, `score_failure_reason`.
- `raw_scores`(D/P/N), `weights`, `contributions`: 토글의 점수 상세. 원점수·기여분·총점은 반올림하지 않습니다.
- `travel`: 참여자별 member_id·자동차 분·거리·출처. `travel_summary`: 평균·최대 분, 자동차 수단, 이동 점수.
- `personal.preference/avoidance`: 참여자별 세부 종류 평균과 개인 평균.
- `condition_evaluations`: 조건마다 satisfied/unsatisfied/unknown, 점수, evidence, sources, evidence_facts.
- `required_checks`: 필수 조건 검사. 필수 조건은 P/N 점수에서 제외합니다.
- `confirmed_condition_count`, `total_condition_count`, `unknown_condition_count`, `conflicts`.
- `policy_version`, `policy`: 실제 적용한 기본 정책과 `weights`의 재조정 가중치.
- 1순위 `comparison`: 2순위 place_id, contribution_deltas, total_delta. 같은 계산 결과로 reason을 생성합니다.
  비교 후보가 없으면 comparison과 비교 문장을 생성하지 않습니다. 점수는 만족 확률이 아닙니다.

`examples/recommendation_demo.json`은 실행한 계산 결과 전체를 포함한 독립 시연 픽스처입니다.
실제 Supabase 데이터로 오해하지 않도록 fixture 출처를 사용합니다. 실제 DB에 삽입하지 않습니다.

## 근거 범위와 미확인

네이버 지역검색에서 확보하는 값은 이름·카테고리·주소·좌표·연락처·링크입니다.
장소 종류는 카테고리 경로의 정확한 항목 일치만 양성 근거로 사용합니다.
이름에 단어가 있거나 검색 결과에 나왔다는 이유로 메뉴·분위기를 확인했다고 판단하지 않습니다.
카테고리 불일치도 해당 특성 없음의 증거가 아닙니다.

실서비스에는 메뉴 목록·메뉴 전체성·개별 주문 여부·피할 메뉴 외의 실제 선택지,
1인 가격·주차·접근성·공간·분위기·영업시간·예약 가능 여부 근거가 없습니다.
이들은 unknown으로 남으며 필수 조건이면 needs_verification입니다.
추가 근거는 내부 장소 데이터의 `facts: [{target,scope,value,unit?,source}]`,
주문 정보는 `ordering: {individual,alternatives_verified,source}`로 어댑터에서 공급할 수 있습니다.
현재 공개 API에서 클라이언트가 장소 근거를 등록하는 기능은 추가하지 않았습니다.
비선호 메뉴가 실제 존재하는 경우에도 개별 주문·대안이 확인되어야 회피 100점입니다.
공동 활동을 임의로 분리하지 않으며 동일 메뉴 강제는 미해결 충돌로 남습니다.

현재 이동 연동은 NCP Directions 5의 자동차입니다. 대중교통은 지원하지 않습니다.
실제 API의 밀리초는 소수 분으로 변환하며, 직선거리는 설명용으로만 사용합니다.
mock 지도는 직선거리 기반 시연 추정이며 `travel_source/source: mock_estimate`로 명시합니다.
한 명이라도 자동차 시간이 없으면 score=null, rank=null, score_status=unavailable입니다.
누락 인원을 평균에서 제외하거나 0분으로 대체하지 않습니다. 60분 이상도 자격 검사로 제외하지 않습니다.

## 정책과 저장

`app/services/recommendation_rules.py`의 불변 Policy에서 버전, D/P/N 가중치,
60분 기준, 평균·최대 비중, 미확인 50점을 변경합니다. 테스트에는 별도 Policy를 전달할 수도 있습니다.
팀은 가중치·이동 기준, 미확인 중간 점수, 실측 교통수단, 데이터 검증 출처·갱신 주기,
동의어 사전 확대, 조건 확인 UI와 실제 근거 수집 방법을 추가로 결정해야 합니다.
P와 N 각각은 조건→세부 종류→참여자 순 평균으로 참여자에게 동일 비중을 줍니다.
최종 점수 전체에서 모든 개인의 영향력이 동일하다는 뜻은 아닙니다.

기존 members.preference JSON과 meetings.recommendation_result / recommendation_runs JSON을 재사용하므로
DB 마이그레이션이 필요 없습니다. 모임 인증·revision·기존 결과 무효화 동작도 재사용합니다.
기존에 저장된 추천 결과는 소급 변경하지 않으며 새 제출로 재계산됩니다.

검증 명령: `.venv/bin/python -m pytest -q` (Windows: `.venv\Scripts\python.exe -m pytest -q`).
외부 AI·지도 호출 없이 SQLite 테스트 DB와 mock/픽스처만 사용합니다.

PR #1 검토 후 검증: `136 passed` (기존 테스트·회귀 테스트 포함). Starlette TestClient의 httpx 사용 중단 예정 경고 1건이 있습니다.
발견 사항과 보완 내용은 [PR #1 검토 기록](PR_1_REVIEW.md)을 참고하세요.
시연 픽스처 재생성: `.venv\Scripts\python.exe -m examples.generate_recommendation_demo`.

범위 확장/조정: 후기 기반 추정(75/25), 확정/추정/미확인 집계와 팀원 데이터 입력 인터페이스가 추가되었습니다.
최신 연결 상태와 데이터 계약은 [팀원 데이터 계약 초안](TEAM_RECOMMENDATION_DATA_CONTRACT.md)을 따릅니다.
기본 추천 경로의 신규 블로그 HTTP 연결은 미연결이며 모의 데이터로 검증합니다.
