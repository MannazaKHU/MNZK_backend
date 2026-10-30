# 블로그 검색과 추천 근거 연결

지역 검색에 사용하던 **같은 NAVER API HUB Application**에서 블로그 API를 추가합니다.
`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`를 그대로 사용하며 별도 블로그 키는 필요 없습니다.
요청 주소는 `https://naverapihub.apigw.ntruss.com/search/v1/blog`, 인증은
`X-NCP-APIGW-API-KEY-ID` / `X-NCP-APIGW-API-KEY`입니다.
[공식 블로그 검색 문서](https://api.ncloud-docs.com/docs/naver-api-hub-search-blog)를 기준으로 구현했습니다.

## 켜는 방법

기존 ChatKHU·지역 검색·Maps 설정을 유지하고 로컬 `.env`와 Railway Variables에 아래 값을 설정합니다.

```dotenv
AI_PROVIDER=chatkhu
MAPS_PROVIDER=naver
ENABLE_BLOG_ENRICHMENT=true
```

URL과 한도는 기본값이 있어 생략할 수 있습니다.

```dotenv
NAVER_BLOG_URL=https://naverapihub.apigw.ntruss.com/search/v1/blog
BLOG_MAX_CANDIDATES=5
BLOG_QUERIES_PER_CANDIDATE=2
BLOG_RESULTS_PER_QUERY=5
BLOG_MAX_REQUESTS=10
BLOG_CONCURRENCY=3
BLOG_TIMEOUT_SECONDS=5
BLOG_EXTRACTION_TIMEOUT_SECONDS=20
BLOG_MAX_AGE_DAYS=180
```

이전 초안의 `NAVER_BLOG_CLIENT_ID`, `NAVER_BLOG_CLIENT_SECRET`는 사용하지 않습니다.
서버를 재시작/재배포해야 변경값이 반영됩니다. `GET /health`의 `blog_enrichment_enabled`로
활성화 설정 반영을 확인할 수 있습니다. health는 외부 인증 테스트를 수행하지 않습니다.

기존 결과는 자동으로 바뀌지 않습니다. 새 모임 또는 입력을 수정해 재계산되는 모임에서 확인합니다.
`ENABLE_BLOG_ENRICHMENT=false`, AI mock, 지도 mock이면 자동 외부 블로그 검색을 하지 않습니다.

## 인증 확인

```sh
.venv/bin/python -m scripts.check_naver --blog
```

블로그 검색을 실제로 1회 호출합니다. 이용량이 발생하며 AI 호출·DB 저장은 없습니다.
비활성화 설정이나 mock 상태에서도 명시적인 확인 명령은 실제 API를 호출합니다.
성공하면 `NAVER API HUB Blog Search: OK`를 표시합니다.
`BLOG_AUTH_ERROR, HTTP 401/403`이면 기존 Application의 키와 블로그 API 이용 설정을 확인합니다.
키와 외부 응답 본문은 출력하지 않습니다.

`--all`은 지역 검색·블로그 검색·지도 API 3개를 합쳐 5회 호출합니다.

## 처리 흐름과 한도

1. 기존 지역 검색으로 실제 후보를 수집하고 전원의 자동차 이동 시간을 조회합니다.
2. 직접 확인되지 않은 조건이 있는 후보를 중심점 거리·장소 ID 순으로 최대 5개 선택합니다.
3. 장소명·지점·주소와 중립적인 주제(메뉴 종류/가격/분위기/주차 등)로 검색합니다.
   한 후보당 최대 2개, 전체 최대 10개의 서로 다른 검색어만 호출하고 같은 검색어는 재사용합니다.
4. 검색이 성공하고 제목·요약문이 있는 후보만 기존 ChatKHU로 조건 근거를 추출합니다.
   기본 한도에서 추출은 후보당 최대 1회, 총 최대 5회입니다. AI 이용량·크레딧이 발생할 수 있습니다.
5. 기존 평가부에서 업체·지점 일치, 원문 인용, 작성일·조회일·모임 상황을 검증한 뒤 같은 점수 정책으로 계산합니다.

블로그 본문·방문자 리뷰 페이지를 열거나 크롤링하지 않습니다. 검색 결과의 제목·요약문만 사용합니다.
수집된 결과는 내부 `RecommendationInputs.blog_searches` 계약으로 전달합니다.
단독 `evaluate_supplied_inputs`는 계속 HTTP 검색을 수행하지 않습니다.
`recommend(..., supplied_blog_searches=batches)`로 자료를 명시한 경우 자동 검색은 생략하며,
빈 배열 `[]`도 명시적으로 공급한 결과로 존중합니다.

## 결과와 실패 처리

기존 모임 API를 그대로 사용하고 `recommendation_result.blog_enrichment`에서 연결 상태를 확인합니다.

- `connection_status: live`, `provider: naver_api_hub`: 자동 검색 어댑터 사용.
- `search_requests_made` / `external_requests_made`: 이 추천에서 시도한 실제 블로그 검색 요청 수.
  `external_requests_scope: search_only`이므로 AI 추출 호출 수는 별도 `extraction_attempt_count`를 확인합니다.
- `data_origins: [live]`, 후보별 `searches`, `failures`, `rejected_claims`: 데이터 출처와 미적용 사유.
- 유효한 일반 후기 근거는 75/25점의 추정입니다. `evidence_level: review_estimate`로 표시합니다.
- 필수 조건은 후기만으로 충족/위반을 확정하지 않습니다. 직접 근거가 부족하면 `needs_verification`을 유지합니다.
- 인증 오류·429·타임아웃·결과 없음·AI 오류는 해당 보완 정보만 미확인으로 남기고 기존 장소·이동 평가를 계속합니다.

인용·출처·작성일 등 검증된 근거와 상태는 저장하지만, 검색 결과 원본 `description` 전체를 응답/DB에 저장하지 않습니다.
모든 필수 조건이 보류되거나 이동 정보가 부족하면 기존과 같이 검증된 추천 목록이 비어 있을 수 있습니다.

## 검증 범위

2026-10-30 기준 기존 검색 키로 실제 블로그 인증 확인을 1회 수행해 성공했습니다.
전체 추천 → 추출 → 점수 → 저장/재조회와 실패·한도·동시성 처리는 HTTP·AI 모의 응답과 임시 SQLite로 검증합니다.
실제 ChatKHU 블로그 추출이나 Railway에서의 전체 추천은 별도 실사용 확인이 필요합니다.
