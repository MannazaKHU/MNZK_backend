# 네이버 검색·지도 연결

현재 백엔드는 NAVER API HUB 지역 검색과 NCP Maps REST API를 사용합니다.
ChatKHU 설정과 독립적이며, `MAPS_PROVIDER=mock`이면 샘플 장소로 동작합니다.
키를 채운 것만으로 실제 호출로 전환되지는 않습니다.

## 1. 발급할 키와 사용할 API

| 서비스 | 활성화할 API | 환경변수 | 서버에서 하는 일 |
|---|---|---|---|
| NAVER API HUB | 검색 / 지역 검색 | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 역·건물 검색, 실제 가게 후보 수집 |
| NCP Maps | Geocoding | `NCP_MAPS_KEY_ID`, `NCP_MAPS_KEY` | 주소 → 출발지 좌표 |
| NCP Maps | Reverse Geocoding | 같은 Maps 키 | 중간 좌표 → 검색할 동네 |
| NCP Maps | Directions 5 | 같은 Maps 키 | 참여자별 자동차 이동 거리·시간 |

NAVER Cloud 콘솔에서 NAVER API HUB와 Maps의 Application을 각각 등록하고,
이용할 API를 선택한 뒤 인증 정보를 복사합니다. 기존 developers.naver.com 검색 키는
현재 코드의 API HUB 인증용으로 사용하지 않습니다. 두 서비스의 키를 서로 바꿔 넣지 마세요.

공식 안내: [API HUB Application 등록](https://guide.ncloud-docs.com/docs/apihub-application),
[Maps Application 등록](https://guide.ncloud-docs.com/docs/application-maps-app-vpc),
[지역 검색](https://api.ncloud-docs.com/docs/naver-api-hub-search-local),
[Geocoding](https://api.ncloud-docs.com/docs/application-maps-geocoding),
[Reverse Geocoding](https://api.ncloud-docs.com/docs/application-maps-reversegeocoding),
[Directions 5](https://api.ncloud-docs.com/docs/ko/application-maps-directions5).

## 2. 로컬 `.env`

기존 AI·DB 설정은 유지하고 아래 항목에 발급받은 값을 넣습니다.

```dotenv
MAPS_PROVIDER=mock
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
NAVER_SEARCH_URL=https://naverapihub.apigw.ntruss.com/search/v1/local
NCP_MAPS_KEY_ID=
NCP_MAPS_KEY=
NCP_MAPS_BASE=https://maps.apigw.ntruss.com
MAPS_TIMEOUT_SECONDS=10
```

`NAVER_SEARCH_URL`과 `NCP_MAPS_BASE`는 일반적으로 바꾸지 않습니다.
현재 연결 방식은 API HUB와 Maps의 `X-NCP-APIGW-API-KEY-ID` / `X-NCP-APIGW-API-KEY` 헤더를 사용합니다.

## 3. 서비스 전환 전 개별 확인

프로젝트 루트에서 실행합니다. 아래 명령은 `MAPS_PROVIDER=mock` 상태여도 **실제 외부 API**를 호출합니다.
검색은 1회, 지도는 3회이며 이용량·과금이 발생할 수 있습니다. AI 호출이나 모임 데이터 저장은 없습니다.
키·외부 응답 본문은 출력하지 않습니다.

검색만 확인:

```sh
.venv/bin/python -m scripts.check_naver --search
```

지도 API 3개 확인:

```sh
.venv/bin/python -m scripts.check_naver --maps
```

모두 한 번에 확인:

```sh
.venv/bin/python -m scripts.check_naver --all
```

각 API가 `OK`인지 확인합니다. `Missing configuration`이면 표시된 변수부터 채웁니다.
`FAIL`이면 해당 Application의 키, API 선택·이용 설정, 호출 제한, 네트워크를 확인합니다.
Directions는 경로가 없거나 응답이 잘못된 경우에도 FAIL로 표시합니다.

실패 메시지는 `HTTP 401/403` 등 네이버 서버의 HTTP 상태와 `ConnectError`·`Timeout` 같은
네트워크 오류를 구분합니다. HTTP 상태는 인증·권한·이용 한도 등 서버 응답을 확인할 때 사용하고,
네트워크 오류라면 인터넷·VPN·프록시·인증서 설정을 확인합니다. 키와 외부 오류 본문은 표시하지 않습니다.
`exported environment overrides .env` 경고가 나오면 현재 터미널의 환경변수가 `.env`보다 우선 적용된 것입니다.
출력된 `unset ...` 명령으로 해당 터미널 변수를 해제하고 다시 실행하면 `.env` 설정을 사용합니다.

모두 확인되면 `.env`를 `MAPS_PROVIDER=naver`로 바꾸고 API 서버를 재시작합니다.
`GET /health`의 `maps_provider`가 `naver`로 바뀌었는지 확인하세요.
health는 설정 표시이고, 실제 인증 확인은 위 명령으로 합니다.

## 4. Railway 적용

Railway 서비스의 Variables에 위 항목과 동일한 키·URL을 등록하고 `MAPS_PROVIDER=naver`로 설정한 뒤 재배포합니다.
로컬 `.env` 변경은 Railway에 자동 반영되지 않습니다.
새 연결 확인 명령은 변경된 Docker 이미지 배포 후 컨테이너에서도
`python -m scripts.check_naver --all`로 실행할 수 있습니다.

배포 후 새 모임을 만들어 모든 참여자가 입력했을 때 실제 장소 추천이 나오는지 확인합니다.
기존 mock 추천이 저장된 모임은 자동으로 다시 추천되지 않습니다.

## 5. 프론트와의 역할

프론트는 기존 모임·입력·상태 조회 API를 그대로 사용합니다. 별도의 검색 프록시 API는 추가하지 않습니다.
백엔드가 출발지를 좌표로 바꾸고, 인원 입력 완료 후 중간 지역과 후보 장소를 검색해 추천 결과를 저장합니다.
프론트는 저장된 추천의 `place` 좌표·주소·링크와 이동 정보를 화면에 표시합니다.

웹 화면에 실제 지도를 삽입하려면 프론트에서 Maps의 **Dynamic Map**을 별도로 설정하고
사용할 웹 주소를 등록해야 합니다. 이 문서의 서버용 Secret/API Key는 프론트 코드에 넣지 않습니다.
지도 삽입 없이 주소·네이버 지도 링크만 표시하는 화면은 현재 API 응답으로 구성할 수 있습니다.

## 현재 결과의 한계와 오류 처리

- 지역 검색은 검색어당 최대 5개 결과를 주므로 여러 검색어를 사용합니다. 검색 결과와 중심점 사이 직선거리로 반경을 필터링합니다.
- 이동 시간은 Directions 5의 **자동차 기준**입니다. 대중교통·도보 소요 시간이 아닙니다.
- 영업 시간·가격·예약 가능 여부는 네이버 검색 결과만으로 확인하지 않습니다. 추천 응답의 `warnings`를 표시해야 합니다.
- 지역 검색 실패 시 가짜 장소로 대체하지 않고 `MAP_ERROR`를 반환합니다. 입력 처리·추천 실패 상태와 재시도는 기존 API 계약을 따릅니다.
- 자동차 길찾기 실패는 해당 이동 정보를 비워 추천을 계속합니다. 이동 정보가 없는 항목은 프론트에서 조회 불가로 표시합니다.
