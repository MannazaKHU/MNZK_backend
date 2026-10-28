# MeetFlow backend

그룹의 **시간 합의·확정 → 장소 추천 → 장소 최종 확정**을 처리하는 FastAPI 백엔드입니다.
가능 시간과 장소 조건은 시간 확정 전에도 입력할 수 있습니다.

- 여러 모임을 PostgreSQL에 독립적으로 저장합니다.
- 방장·참여자는 각자 발급받은 Bearer 토큰으로 접근합니다.
- AI는 ChatKHU Gateway, 지도는 NAVER API HUB / NCP Maps를 사용합니다.
- AI·지도는 각각 MOCK 모드로 실행할 수 있으며 기본값은 모두 MOCK입니다.

## 로컬 실행: Docker + PostgreSQL

Python 3.11 이상(환경 파일 생성용), Docker Desktop과 Docker Compose가 필요합니다.
프로젝트 루트인 `MNZK_backend`에서 실행하세요.

```sh
python3 scripts/setup_env.py
docker compose up -d --build --wait
```

Windows에서는 첫 명령의 `python3` 대신 `python`을 사용합니다.

- API 문서: http://127.0.0.1:8000/docs
- 서버 상태: http://127.0.0.1:8000/health
- DB·스키마 준비 상태: http://127.0.0.1:8000/health/ready

환경 생성 스크립트는 기존 `.env`를 덮어쓰지 않습니다.
새 파일에는 임의의 로컬 DB 비밀번호를 생성하며, 키나 비밀번호는 Git에 포함하지 않습니다.
Compose는 API를 로컬 호스트에만 공개하고 DB 포트는 외부로 공개하지 않습니다.
API 시작 전에 Alembic 마이그레이션이 실행됩니다.

```sh
docker compose logs --tail 100 api
docker compose down
```

`down` 후에도 PostgreSQL 데이터는 `meetflow_postgres_data` 볼륨에 유지됩니다.
`docker compose down -v`는 데이터를 삭제하므로 일반 종료에 사용하지 마세요.
이 구성은 로컬 개발용이며 외부 서버 배포는 아직 포함하지 않습니다.

## Docker 없이 빠르게 실행: SQLite

macOS / Linux / Git Bash:

```sh
sh scripts/start.sh
```

Windows PowerShell:

```powershell
.\scripts\start.ps1
```

스크립트가 가상환경·의존성·환경 파일·마이그레이션을 준비하고 서버를 시작합니다.
기본 DB는 `data/app.sqlite3`입니다. Compose의 PostgreSQL과 별개의 데이터입니다.

이미 의존성이 설치된 환경에서는 다음 명령으로 실행합니다.

```sh
python -m app
```

`uvicorn app.main:app`을 직접 실행하면 마이그레이션은 자동 실행되지 않습니다.
이 경우 먼저 `python -m alembic upgrade head`를 실행하세요.

## API 흐름

1. `POST /api/meetings`: 방장 이름과 모임 조건으로 생성 → 모임 ID, 방장 토큰, 초대 코드 수신
2. `POST /api/meetings/{id}/members`: 초대 코드와 이름으로 참여 → 참여자 토큰 수신
3. 각자 `PUT /api/meetings/{id}/members/me/availability`로 가능 시간 저장
4. 각자 `PUT /api/meetings/{id}/members/me/preference`로 선호·출발지 저장
5. `GET /api/meetings/{id}/schedule`로 후보 시간 비교
6. 방장이 `POST /api/meetings/{id}/schedule/confirm`으로 시간 확정
7. 방장이 `POST /api/meetings/{id}/recommend`로 장소 추천
8. 방장이 `POST /api/meetings/{id}/place/confirm`으로 장소 최종 확정

생성·참여 외 모임 API에는 `Authorization: Bearer <access_token>`이 필요합니다.
본인 입력에는 이름이나 참여자 ID를 보내지 않습니다. 토큰으로 본인을 식별합니다.
토큰은 생성·참여 응답에서만 반환되며 DB에는 해시로 저장합니다.
초대 코드는 방장 응답에서만 반환됩니다. 동일 모임의 표시 이름은 중복할 수 없습니다.

상태는 `collecting → scheduled → recommended → finalized`입니다.

- 시간 확정은 기본적으로 전원 참석 가능한 구간만 허용합니다.
- 일부 인원만 참석시키려면 방장이 `allow_partial: true`를 명시해야 합니다.
- 일부 참석 확정 시 참석자만 장소 추천과 이동 거리 계산에 포함합니다.
- 추천 전 참석자 전원의 선호·출발지 입력이 필요합니다.
- 일정 변경 또는 새 참여자 추가 시 시간·장소 확정을 모두 해제합니다.
- 선호 변경 시 시간은 유지하고 추천·장소 확정을 해제합니다.
- 외부 API를 기다리는 동안 입력이 바뀌면 결과를 저장하지 않고 `409 MEETING_CHANGED`를 반환합니다.
- 장소 확정 시 추천을 조회했을 때의 `revision`을 보내야 합니다.

요청·응답 예제와 프론트 변경 사항은 [docs/API.md](docs/API.md)에 있습니다.

## ChatKHU 연결

1. [챗쿠 개발자 페이지](https://chat.khu.ac.kr/dashboard/developers)에서 API 키를 발급합니다.
2. 그 화면에 표시된 **Base URL**과 호출 가능한 **모델 ID**를 확인합니다.
3. `.env`를 설정합니다.

```dotenv
AI_PROVIDER=chatkhu
CHATKHU_API_KEY=<발급받은 키>
CHATKHU_BASE_URL=<개발자 화면의 Gateway Base URL>
AI_MODEL=<계정에서 사용 가능한 모델 ID>
```

Base URL에는 `/chat/completions/`를 붙이지 않습니다.
`app/clients/ai_client.py`가 경로·인증·구조화 출력·응답 검증을 담당합니다.
서비스 코드는 특정 모델의 응답 형식에 의존하지 않습니다.
모델 호환성을 위해 temperature 등 선택적 샘플링 인자는 보내지 않습니다.
선택한 모델이 JSON Schema 출력을 지원하는지는 실제 호출로 확인해야 합니다.
키를 브라우저 코드나 Git에 넣지 마세요.

Compose 재시작 시 변경된 환경을 반영합니다.

```sh
docker compose up -d --force-recreate api
```

연결 점검은 의도적으로 별도 명령으로 분리했습니다.

```sh
python -m scripts.smoke_test
python -m scripts.smoke_test --completion
```

첫 명령은 인증과 설정 모델의 접근 가능 여부를 확인합니다.
`--completion`은 실제 구조화 출력 1회를 호출하므로 계정 크레딧이 사용될 수 있습니다.
Docker에서 실행 중이면 다음 명령으로 같은 점검을 할 수 있습니다.

```sh
docker compose exec api python -m scripts.smoke_test
docker compose exec api python -m scripts.smoke_test --completion
```

공식 제공사 문서:
[인증](https://docs.mindlogic.ai/docs/khu/api-gateway/getting-started/authentication),
[Chat Completions·구조화 출력](https://docs.mindlogic.ai/docs/khu/api-gateway/reference/chat-completions).

## 환경변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `APP_ENV` | development / test / production | development |
| `HOST`, `PORT` | 직접 실행 시 서버 주소 | 127.0.0.1, 8000 |
| `DATABASE_URL` | SQLAlchemy DB 연결 문자열 | sqlite:///./data/app.sqlite3 |
| `POSTGRES_PASSWORD` | Compose DB 비밀번호 | setup_env.py가 생성 |
| `CORS_ORIGINS` | 허용 프론트 주소, 쉼표 구분 | localhost:5173, localhost:3000 |
| `AI_PROVIDER` | mock / chatkhu | mock |
| `CHATKHU_API_KEY`, `CHATKHU_BASE_URL`, `AI_MODEL` | 챗쿠 연결 | 미설정 |
| `AI_TIMEOUT_SECONDS`, `AI_MAX_TOKENS` | AI 호출 제한 | 60, 4096 |
| `MAPS_PROVIDER` | mock / naver | mock |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | NAVER API HUB 검색 인증 | 미설정 |
| `NAVER_SEARCH_URL` | 지역 검색 주소 | .env.example 참고 |
| `NCP_MAPS_KEY_ID`, `NCP_MAPS_KEY`, `NCP_MAPS_BASE` | NCP Maps 길찾기·좌표 변환 | .env.example 참고 |

`APP_ENV=production`에서는 docs·OpenAPI·개발용 usage API를 노출하지 않습니다.
기존 `MOCK_MODE=1/0/true/false`도 읽지만 `AI_PROVIDER`와 `MAPS_PROVIDER` 설정이 우선합니다.
CLOVA 관련 변수와 JSON 저장용 `DATA_PATH`는 더 이상 사용하지 않습니다.

## DB와 마이그레이션

- `meetings`: 모임 조건, 상태, 확정 시간·장소, 현재 추천, 버전
- `members`: 모임별 참여자, 토큰 해시, 입력 여부, 가능 시간·선호(JSON)
- `recommendation_runs`: 추천 요청, 생성 당시 버전, 추천 결과 이력

새 마이그레이션:

```sh
python -m alembic revision --autogenerate -m "describe change"
python -m alembic upgrade head
python -m alembic check
```

생성된 마이그레이션을 검토한 뒤 적용하세요.
이전 `data/meetings.json`은 덮어쓰거나 삭제하지 않으며 자동 이관하지 않습니다.
기존 파일에는 신뢰할 수 있는 참여자 식별 정보가 없으므로 새 모임을 만들어 참여자를 다시 초대합니다.

## 검증

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

테스트는 격리된 SQLite DB와 외부 HTTP 모킹을 사용합니다.
실제 API 키·서비스 크레딧·개발 DB를 사용하지 않습니다.
주요 검증 범위는 모임 격리·권한·상태 전이·입력 검증·오래된 결과 저장 방지·Gateway 요청 형식입니다.

## 현재 범위

- 로그인 계정 대신 모임별 참여 토큰을 사용합니다. 계정 연동·토큰 복구·만료는 후속 작업입니다.
- 중간지점은 출발 좌표 평균입니다. 대중교통 이동 시간 최적화는 포함하지 않습니다.
- 장소 검색 정보에는 검증된 가격·영업시간·예약 현황이 없으므로 이를 보장하지 않습니다.
- MOCK의 장소·이동 시간은 개발용 가상 데이터입니다.
- `GET /api/usage`는 development에서만 제공하며 현재 프로세스의 AI 토큰 집계입니다.
- 예전 `/api/meeting`, `/demo`, `/api/recommend/quick` 경로는 제거되었습니다.
- 저장소의 기존 개발 가이드 문서는 이전 CLOVA 기반 시연 버전 자료입니다. 현재 동작은 이 README와 API 문서를 기준으로 합니다.
