# 팀원 연동 데이터 계약 초안: 장소·이동시간·블로그 근거

현재 범위는 **이미 조회된 데이터의 검증·추출·평가**입니다. 이 처리부는 네이버 검색 API 인증·HTTP 요청,
크롤링, 방문자 리뷰 조회, 엔드포인트 이관을 수행하지 않습니다. 아래 계약은 팀원과 합의할 초안입니다.
모의 데이터는 `data_origin: fixture`로 표시하며 실제 네이버 검색 결과가 아닙니다.

## 현재 연결 상태와 보존한 코드

저장소는 `MannazaKHU/MNZK_backend`이며 `feat/recommendation-rules` 작업은 PR #1로 `new/back`에 머지되었습니다.
기존 지역 검색/API Hub, Maps/Directions의 인증·엔드포인트를 이번 블로그 작업에서 변경하지 않았습니다.
기존 추천 경로는 그대로 장소와 자동차 이동시간을 수집하고 아래 데이터 처리부를 사용합니다.
팀원의 블로그 검색 공급자는 아직 미연결입니다. 제목·요약문 추출은 기존 AI 연동을 사용하는 실제 함수로 구현하고 기본 콜백으로 연결했습니다.
따라서 기본 추천 API 응답은 `blog_enrichment.connection_status: not_connected`이며 후기 정보는 미확인입니다.

범위 조정 전 작성한 다음 초안은 삭제·되돌리기 없이 보존했습니다.

| 파일 | 작성된 내용 | 현재 사용 상태 |
|---|---|---|
| `app/clients/blog_client.py` | 공식 블로그 검색 HTTP 클라이언트 초안 | 활성 추천 경로에서 import/호출하지 않음 |
| `app/services/blog_enrichment.py` | 검색 요청 계획·검색 호출·기존 ChatKHU 추출 조합 초안 | 활성 추천 경로에서 import/호출하지 않음 |
| `app/config.py`, `.env.example` | 블로그 인증·외부 호출 한도 설정 초안 | 값은 보존, 활성 처리부는 블로그 검색 인증정보를 사용하지 않음 |
| `app/services/recommend_service.py` | 보완 호출 위치 | 외부 호출 초안 대신 `evaluate_supplied_inputs`에 연결 |

신규 클라이언트로 실제 네이버 호출은 수행하지 않았습니다. 이 문서의 완료 보고는 모의 검증입니다.
`ENABLE_BLOG_ENRICHMENT=true`나 키 설정만으로 새 블로그 호출이 시작되지 않습니다.
팀원은 기존 인증 설정을 변경하기 전에 실제 API 계약을 별도로 확정해야 합니다.

## 사용할 인터페이스

- 데이터 모델: `app/recommendation_contracts.py`
- 단일 평가 진입점: `await evaluate_supplied_inputs(inputs, conditions, schedule)`
- 실제 기본 추출 함수: `app/services/evidence_extraction.py::extract_condition_evidence`
- 테스트·고급 사용: `extractor=mock_extractor`로 대체하거나 `extractor=None`으로 명시적 비활성화
- 호출 없는 검색어 제안: `prepare_search_tasks(places, conditions, policy)`
- 검증과 정책: `blog_evidence.py`, `recommendation_rules.py`
- 내부 추천 서비스 주입 지점: `recommend(..., meeting_id=..., supplied_blog_searches=...)`. 기본 추출 함수가 자동 사용되므로 팀원이 추출 콜백을 구현할 필요는 없습니다.

`conditions`는 기존 참여자 제출로부터 서버가 정규화한 조건입니다. 팀원 API가 사용자 조건 ID나 점수를
생성할 필요는 없습니다. 모임·참여자 ID는 현재 모임 컨텍스트에서 받습니다.
다른 모임의 조건이나 이동시간이 섞이면 검증 단계에서 거부합니다.
블로그 자료가 다른 place_id를 참조하면 해당 자료만 무시하고 오류 코드를 반환합니다.

공개 HTTP 엔드포인트나 프론트 제출 요청에 블로그 자료 입력 필드를 추가하지 않았습니다.
팀원 어댑터가 내부 서비스 주입 지점에 결과를 전달하거나 단일 평가 진입점을 호출하면 됩니다.
기존 추천 API도 같은 평가 진입점을 사용하므로 점수 경로가 분리되지 않습니다.

### 최상위 `RecommendationInputs`

| 필드 | 필수 여부 | 값의 생성 주체 / 의미 |
|---|---|---|
| `meeting_id` | 필수 | 기존 백엔드 모임 ID. 네이버가 제공할 값이 아님 |
| `participant_ids` | 필수 | 기존 백엔드 참여자 ID 전원, 최대 10명. 조건 없는 사람도 포함 |
| `places` | 필수 | 팀원이 찾은 후보 목록. 빈 목록 허용 |
| `travel_times` | 선택, 기본 빈 목록 | 장소×참여자별 조회 결과. 빠진 사람은 0분으로 대체하지 않음 |
| `blog_searches` | 선택 | `null`/생략=팀원 연결 없음, `[]`=연결했으나 전달된 검색 없음 |

### `places[]`

| 필드 | 필수 여부 | 의미 / 실제 제공 가능성 |
|---|---|---|
| `id` | 필수 | 백엔드의 안정적인 후보 ID. 지역 검색 원본 ID를 가정하지 않음. 기존 해시 ID 재사용 가능 |
| `name` | 필수 | 지역 검색에서 받은 업체명. 제목의 `<b>` 등은 어댑터에서 제거 |
| `address` | 선택 | 실제 확보한 도로명/지번 주소. 없으면 지점 식별이 어려워 근거 반영을 보류할 수 있음 |
| `branch_name` | 선택 | **명시적으로 확보했을 때만** 지점명. 별도 지점 필드가 API에 있다고 가정하지 않음 |
| `category` | 선택 | 실제 검색 카테고리. 기존 `>` 경로 사용 |
| `lat`, `lng` | 선택 | 기존 지도 표시·이동 조회용 좌표. 이 순수 평가 진입점 자체에는 필수가 아님 |
| `distance_m` | 선택 | 기존 중심점 거리(m), 입력 근거 보완 대상 선택용. 없으면 ID 순으로 뒤에 배치 |
| `phone`, `url` | 선택 | 기존 응답 호환용 |
| `facts` | 선택 | 별도로 검증된 직접 정보만. 지역/블로그 검색이 주차·가격을 확인해 준다고 가정하지 않음 |
| `ordering` | 선택 | 개별 주문·대체 메뉴에 대한 별도 직접 확인 정보만 |

`facts[]`는 `scope`, `target`, `value`, `source`가 필수이며 `unit`, `verified_at`은 선택입니다.
`information_type`은 `confirmed`만 허용합니다. 수량의 단위는 KRW/minutes/people입니다.
직접 정보 출처와 조회·확인 범위에 대한 책임은 공급 어댑터에 있습니다. 날짜가 있으면 시간대 포함 ISO 8601을 사용합니다.
여러 직접 정보가 있으면 최신 `verified_at`을 우선합니다. 기존 날짜 없는 직접 정보도 호환 유지합니다.
블로그 추정을 `facts`로 승격하지 말고 반드시 `blog_searches`로 전달합니다.

`ordering`은 `source`가 필수이고 `individual`, `alternatives_verified`는 선택 bool입니다.
블로그에 다른 메뉴가 있다는 이유로 이 값을 true로 만들지 않습니다.

### `travel_times[]`

| 필드 | 필수 여부 | 의미 |
|---|---|---|
| `place_id`, `member_id` | 필수 | 후보·참여자와 연결. 중복 쌍, 다른 참여자·장소 참조 거부 |
| `status` | 필수 | `success`, `failed`, `not_requested` |
| `duration` | 성공 시 필수 | 실제 조회한 0 이상 유한 수치. 실패/미조회 시 null |
| `unit` | 성공 시 필수 | `minutes`, `seconds`, `milliseconds`. Directions 응답 단위는 어댑터에서 명시 |
| `mode` | 선택, 기본 unknown | `driving`, `transit`, `walking`, `unknown`. 현재 일반 점수는 전원 driving만 지원 |
| `source` | 선택 | 공급자/시연 식별. 예: fixture:driving. 인증정보는 포함하지 않음 |
| `failure_code` | 선택 | `timeout`, `no_route`, `upstream_error`, `not_requested` |
| `member`, `origin_text`, `straight_km`, `driving_km`, `text` | 선택 | 기존 표시용 정보. 직선거리는 이동시간 대체값으로 사용하지 않음 |

이동시간이 한 명이라도 없거나 수단이 지원되지 않으면 총점·일반 순위가 null입니다.
선호·필수 조건 검사는 그대로 반환합니다. 평균에서 누락 참여자를 제외하지 않습니다.

### `blog_searches[]`: 완료된 검색 1회

| 필드 | 필수 여부 | 의미 / 생성 주체 |
|---|---|---|
| `place_id` | 필수 | 검색 대상 후보 ID, 백엔드/팀원 어댑터가 연결 |
| `query` | 필수 | 실제 사용한 검색어. 검색 응답 원본에 없으므로 어댑터가 요청에서 보존 |
| `status` | 필수 | `success`, `failed`, `timeout`, `rate_limited`, `not_requested`, `limit_exceeded`. 어댑터가 표준화 |
| `fetched_at` | 선택 | 시간대 있는 ISO 8601 조회 시각. API 작성일과 다름. 어댑터가 기록하며 없으면 점수 반영 보류 |
| `items` | 선택, 기본 [] | 실제 반환된 제목·요약문. 성공 외 상태는 빈 목록이어야 함 |
| `failure_code` | 선택 | keys_missing/auth_error/timeout/rate_limit/upstream_error/invalid_response/provider_unavailable |
| `data_origin` | 선택, 기본 unverified | `fixture`, `live`, `unverified`. 출처 성격이며 사실 확정 수준을 뜻하지 않음 |

원본 오류 본문, 요청 헤더, 토큰, 키, 스택 트레이스는 계약에 넣지 않습니다.
검색 성공·결과 없음은 `status: success, items: []`이며 메뉴 미판매·주차 불가 증거가 아닙니다.

### `items[]`: 블로그 검색 결과의 제한된 필드

| 필드 | 필수 여부 | 의미 |
|---|---|---|
| `url` | 필수 | API의 link를 매핑. 실제 반환된 HTTP(S) URL만. 페이지를 열거나 크롤링하지 않음 |
| `title` | 선택, 기본 빈 문자열 | API 반환 글 제목. 값이 없으면 만들지 않음 |
| `description` | 선택, 기본 빈 문자열 | API 반환 요약문. 본문/방문자 리뷰가 아님 |
| `postdate` | 선택 | API의 작성일(YYYYMMDD), 또는 어댑터가 동일 날짜를 ISO YYYY-MM-DD로 변환. 없으면 null |

주소·좌표·별도 업체 ID·방문일·주차 확정 여부·전체 메뉴·개별 주문 여부는 블로그 검색 API 제공값으로
요구하지 않습니다. `fetched_at`과 query/place_id 등 어댑터 메타데이터를 API가 직접 반환한다고 가정하지 않습니다.

## 조건 추출 콜백과 검증

`EvidenceExtractor`는 async `(place, unresolved_conditions, documents, schedule) -> {"claims": [...]}` 콜백입니다.
기본값은 실제 함수 `extract_condition_evidence`입니다. 검색 데이터와 문서가 전달되면 이 함수가
기존 `ai_client.structured(..., step="blog_extract")`와 현재 ChatKHU 설정을 사용합니다.
새 AI 제공자·인증·엔드포인트·HTTP 클라이언트는 추가하지 않았습니다.
검색 결과가 없거나 실패했거나 공급자가 미연결이면 AI를 호출하지 않습니다.
`AI_PROVIDER=mock`이면 실제 근거를 임의 생성하지 않고 `EXTRACTION_UNAVAILABLE`로 미확인 처리합니다.
AI 오류·구조 오류·타임아웃은 기존 근거 검증/오류 경계에서 처리하여 50점·미확인으로 남깁니다.
명시적으로 `extractor=None`을 넘긴 경우에만 `EXTRACTOR_NOT_CONNECTED`로 처리합니다.
테스트는 기본 실제 함수의 내부 `ai_client.structured`를 모의 처리하고, 일부 테스트는 모의 콜백을 주입합니다.
실제 AI 요청 검증은 수행하지 않았습니다.

추출 결과 구조는 `blog_evidence.Claim` / `Extraction`에서 검증합니다. 조건 ID와 대상은 서버 입력을 참조합니다.
모델은 document_index, 인용 필드·문구, 장소 일치 상태, 대상 속성에 대한 관찰, 상황 인용문만 반환합니다.
**URL·날짜를 모델이 생성하지 않습니다.** 모르는 필드를 추가하거나 원문에 없는 인용문을 만들면 거부합니다.
제목·요약문은 외부 자료로 취급하고 `blog_evidence.SYSTEM`의 지시문 방어 규칙을 적용해야 합니다.
기본 추출 함수는 `SYSTEM` 방어 지시와 검증 스키마를 기존 AI 어댑터에 전달합니다. 활성 경로에서 네이버 검색/블로그 URL 요청은 하지 않습니다.

서버는 반환된 제목·요약문의 HTML 강조 태그를 제거하고 엔티티를 디코딩한 텍스트에서 정확한 부분 문자열을 검증합니다.
이후 원본 배열에서 URL·작성일·조회 시각을 결합합니다. 인용만 일치해도 의미가 확정되는 것은 아닙니다.
업체 전체 이름과 지점/구체 지역이 맞지 않거나, 다른 지점이 드러나거나, 여러 업체의 설명이 섞이면 보류/제외합니다.
브랜드 이름·검색어만으로 일치를 확정하지 않습니다. 별도 지점명이 없는 업체는 주소의 동/도로 등 식별 단서를 요구합니다.

평일·주말·낮·오전·오후·점심·저녁·밤은 현재 모임 날짜와 전체 시간 구간이 일치할 때만 사용할 수 있습니다.
유료·예약·공휴일·그 외 적용 상황은 현재 확인 정보가 부족하므로 보류합니다.
모임 상황이 없거나 애매해도 보류합니다. 이 제한은 보수적인 초기 정책이며 실제 영업 보장이 아닙니다.

180일 이내 작성일과 유효한 조회 시각이 있는 근거만 추정 점수에 사용합니다. 작성일 누락/과거/미래,
조회 시각 누락/미래는 참고 근거로만 남깁니다. 기간은 `EvidencePolicy.max_age_days`로 조정합니다.

## 점수·상태·설명 응답

- 확정 사실: 기존 100/0점. 최신 직접 정보가 후기보다 우선하며 후기와의 충돌은 별도 표시합니다.
- 일반 희망 조건의 후기 기반 추정: 원하는 결과를 지지하면 75, 충돌하면 25, 상충·보류·미확인은 50.
- 필수 조건: 후기만으로 통과/위반 확정하지 않고 `needs_verification` 유지.
- 기존 개인별→종류별 평균, OR, 가중치 재조정, 이동 점수와 순위 규칙 유지.
- 메뉴 판매 언급은 개별 주문/대안 메뉴 확인을 대신하지 않습니다. 다른 필수 조건도 별도로 검사합니다.

점수는 `Policy` 한 곳에서 바꾸며 버전은 `recommendation-v2-blog`입니다. 75/25와 180일은 팀 합의가 필요한 임시값입니다.
OR 대안 중 한 가지 충족 지지와 다른 대안 불충족은 그 자체로 상충 후기가 아닙니다.
같은 대상을 두고 상반된 유효 후기들이 있으면 미확인 50점입니다.

기존 `status`는 직접 확인 여부(satisfied/unsatisfied/unknown)를 유지합니다. 후기 75/25도 직접 확인은 unknown입니다.
프론트는 추가된 `evidence_level: confirmed/review_estimate/unknown`,
`assessment_status: satisfied/unsatisfied/unknown/estimated_support/estimated_conflict`와 점수를 함께 사용합니다.

토글에 사용할 주요 필드:

- `condition_evaluations[].review_evidence`: URL·작성일·조회 시각·인용문·data_origin·장소 일치·상황 제한
- `usable`: 장소·날짜·상황·인용 검증 결과, `applied`: 실제 일반 점수 반영 여부
- `unused_reasons`: 필수 직접확인 필요, 잘못된 지점, 오래된 글, 상충, OR 미완성 등의 사유
- `review_conflict`, `evidence`, `sources`: 판단 이유와 출처. 모의 자료의 설명에도 모의 데이터임을 표시
- `confirmed_condition_count`, `estimated_condition_count`, `unknown_condition_count`: 합은 total_condition_count
- `raw_scores`, `personal`, `weights`, `contributions`, `comparison`: 기존 점수·개인별 평가·순위 설명
- `blog_enrichment`: 연결·검색 성공/실패 상태, 미선택/미처리 사유, 처리 한도, 데이터 출처 성격

`search_requests_made: 0`은 이 담당 처리부가 검색 요청을 하지 않는다는 뜻입니다.
호환 필드 `external_requests_made`도 검색만 가리키며 `external_requests_scope: search_only`로 명시합니다.
`extraction_attempt_count`는 추출 실행 시도 횟수이며 실제 HTTP 요청 성공 횟수가 아닙니다.
AI 오류·미설정·모의 테스트도 추출 시도에 포함됩니다. 실제 AI 검증 여부와 별도로 해석해야 합니다.
팀원 어댑터가 실제로 몇 회 검색했는지를 대신 보고하는 값은 아닙니다.
검색 원문 전체는 DB에 저장하지 않습니다. 기존 추천 결과 JSON에는 검증한 근거 인용문과 메타데이터가 남습니다.
별도 검색 원문 테이블/지속 캐시/DB 마이그레이션은 추가하지 않습니다.

## 처리 한도와 팀원 책임

기본 `EvidencePolicy`: 거리·ID 순 미확인 후보 최대 5곳, 후보별 검색 결과 묶음 최대 2개,
묶음당 5개 글, 서로 다른 검색어 최대 10개, 추출 동시 실행 최대 3개, 추출 제한시간 20초입니다.
범위 밖 후보를 삭제하거나 불충족 처리하지 않습니다. URL/검색어 중복은 요청 내에서 제거하며 글 수가 많아도 점수를 더하지 않습니다.

이것은 **제공된 결과를 소비하는 한도**입니다. 이미 끝난 외부 호출 횟수를 줄일 수 없으므로,
팀원은 `prepare_search_tasks` 결과/동일 정책을 사용해 실제 호출·재시도·타임아웃·캐시·동시성을 제한해야 합니다.
검색 실패/키 누락은 표준 실패 상태로 전달합니다. 이 담당 로직은 이를 미확인과 구조화된 사유로 처리합니다.

## 실행 가능한 모의 예시

입력: `tests/fixtures/team_blog_input.json`의 inputs, condition_items, schedule.
이 JSON의 블로그 글·장소·URL은 직접 작성했으며 `.invalid` 도메인은 외부 호출 대상이 아닙니다.

```python
inputs = RecommendationInputs.model_validate(fixture['inputs'])
conditions = normalize(fixture['condition_items'], 'fixture-member')
result = await evaluate_supplied_inputs(inputs, conditions, fixture['schedule'], extractor=mock_extractor)
```

```powershell
.venv\Scripts\python.exe -m examples.run_supplied_recommendation
.venv\Scripts\python.exe -m pytest -q
```

응답 예시 발췌(전체는 `examples/team_recommendation_result.json`):

```json
{
  "meeting_id": "fixture-meeting",
  "blog_enrichment": {"connection_status":"supplied","external_requests_made":0,"data_origins":["fixture"]},
  "recommendations": [{
    "eligibility":"eligible","rank":1,"score":77.85714285714286,
    "raw_scores":{"D":80.0,"P":75,"N":null},
    "confirmed_condition_count":0,"estimated_condition_count":1,"unknown_condition_count":0,
    "condition_evaluations":[{
      "status":"unknown","evidence_level":"review_estimate","assessment_status":"estimated_support","score":75,
      "review_evidence":[{"url":"https://fixture.invalid/blog/1","postdate":"2026-10-20","data_origin":"fixture","applied":true}]
    }]
  }]
}
```

실제 네이버 검색 및 실제 AI 요청 검증은 수행하지 않았습니다. 팀원 코드 준비 후 계약 필드 매핑과 실제 자료의 식별·추출 정확도를
별도로 검증해야 합니다. 특히 짧은 요약문만으로 업체/시간/가격 맥락을 알 수 없는 글은 계속 보류할 수 있습니다.

최종 검증 완료: 전체 `91 passed` (기존 52개 + 공급 데이터/근거 검증 31개 + 기본 AI 추출 연결 검증 8개).
Starlette TestClient의 httpx 사용 중단 예정 경고 1건만 있습니다. `git diff --check`도 통과했습니다.
실제 외부 API 검증은 수행하지 않았으며 테스트 DB는 임시 SQLite만 사용했습니다.
기존 Supabase/업무 DB 데이터 변경, 커밋·푸시·병합은 수행하지 않았습니다.


## 담당 경계 확정과 이번 연결 변경

이전 보고 당시에는 실제 함수가 없었던 것이 아니라, 비활성 초안 `blog_enrichment.extract()`에 기존 AI 호출 구현이
있고 활성 평가 경로의 기본값이 None이었습니다. 이번에 독립 모듈 `evidence_extraction.py`의 실제 함수로 분리하여
`evaluate_supplied_inputs`와 `recommend`의 기본 콜백에 연결했습니다. 비활성 검색 초안은 계속 보존합니다.

| 담당 | 완료/남은 작업 |
|---|---|
| 내 담당 완료 | 기본 AI 추출 함수·방어 프롬프트·스키마, 인용·장소·날짜·상황 검증, 필수 조건/일반 점수, 근거·실패 사유 반환 |
| 팀원 담당 | 실제 검색 인증·호출·한도·타임아웃, 계약에 맞는 완료된 결과 공급 |
| 팀원이 넘길 필드 | `place_id`, `query`, `status`, `items[].title/description/url/postdate`, `fetched_at`, 선택 `failure_code/data_origin` |
| 팀원이 연결할 함수 | `recommend(..., supplied_blog_searches=batches)` 또는 장소·이동시간도 함께 준비했다면 `evaluate_supplied_inputs(inputs, conditions, schedule)` |
| 팀원이 구현할 필요 없는 항목 | AI 추출 콜백·근거 검증·점수 계산·추천 이유 생성 |
| 실제 검증 미실시 | 실제 네이버 검색 응답 수신과 실제 ChatKHU 요청. 모든 테스트의 AI 응답은 모의 처리 |

AI 연결이 이미 설정된 환경에서는 팀원 자료를 공급하면 기본 콜백이 실행됩니다. 기존 `AI_PROVIDER`,
`CHATKHU_API_KEY`, `CHATKHU_BASE_URL`, `AI_MODEL` 설정을 그대로 사용하며 이 문서나 테스트에 값을 넣지 않습니다.
공급자 미연결을 유지하는 기본 API 흐름은 AI 블로그 추출도 실행하지 않습니다.
