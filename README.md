# MeetFlow backend

그룹의 **N명 전원 응답 → 공통 시간·장소 자동 추천 → 장소 최종 확정**을 처리하는 FastAPI 백엔드입니다.
각 참가자는 가능한 날짜·시간·출발지·선호·비선호를 한 번에 제출합니다.

- 여러 모임을 PostgreSQL에 독립적으로 저장합니다.
- 방장·참여자는 각자 발급받은 Bearer 토큰으로 접근합니다.
- AI는 ChatKHU Gateway, 지도는 NAVER API HUB / NCP Maps를 사용합니다.
- AI·지도는 각각 MOCK 모드로 실행할 수 있으며 기본값은 모두 MOCK입니다.

## 공개 배포: Supabase + Railway

공개 웹 → Railway API → Supabase PostgreSQL 구성으로 배포합니다.
Supabase 프로젝트 URL이 아닌 PostgreSQL 연결 URI를 `DATABASE_URL`로 사용합니다.
Railway 빌드·마이그레이션·시작·헬스체크 설정은 `railway.json`에 포함되어 있습니다.
연결 점검과 설정 순서는 [배포 가이드](docs/DEPLOYMENT.md)를 참고하세요.
실제 클라우드 계정 연결과 공개 배포 완료 여부는 별도 확인이 필요합니다.

로컬 Docker API를 Supabase에 연결할 때는 `.env`의 DB URI 설정 후 다음 명령을 사용합니다.

```sh
docker compose -f compose.yaml -f compose.dev.yaml -f compose.supabase.yaml up -d --build --wait api
```

## 개발 환경

IDE에서는 `MNZK_backend` 폴더 자체를 여세요. 프로젝트의 `.venv`를 Python 인터프리터로 사용합니다.
`.vscode/settings.json`에 인터프리터·환경 파일·pytest 탐색 경로를 설정했습니다.
이미 다른 인터프리터를 선택한 IDE에서는 Python 인터프리터 선택 메뉴에서 `.venv`를 선택하세요.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/setup_env.py
```

Windows에서는 `python -m venv .venv`, `.venv\Scripts\Activate.ps1`로 생성·활성화합니다.
환경 생성 스크립트는 기존 `.env`를 유지합니다.

개발용 API와 PostgreSQL 실행:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d --build --wait
```

`app/` 소스 변경은 자동으로 서버를 다시 로드합니다.
의존성 변경 시 위 명령으로 다시 빌드하고, 새 마이그레이션 추가 시에는 아래처럼 적용합니다.

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec api python -m alembic upgrade head
```

개발 환경 확인·종료:

```sh
python -m pip check
python -m pytest -q
docker compose -f compose.yaml -f compose.dev.yaml logs --tail 50 api
docker compose -f compose.yaml -f compose.dev.yaml down
```

IDE·로컬 테스트는 `.venv`, 실행 서버는 Docker의 PostgreSQL 구성을 사용합니다.
직접 `python -m app`을 실행하면 `.env`의 기본 SQLite를 사용하므로 Docker DB와 데이터가 다릅니다.
Docker API가 8000번 포트를 사용 중일 때 로컬 API를 함께 실행하려면 `PORT=8001 python -m app`처럼 다른 포트를 지정하세요.
외부 키가 없는 기본 `.env`에서는 AI·지도 모두 MOCK 모드로 동작합니다.

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
이 Compose 구성은 로컬 개발용입니다. 외부 배포는 `railway.json`과 [배포 가이드](docs/DEPLOYMENT.md)를 사용합니다.

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

1. `POST /api/meetings`: `expected_member_count`에 방장 포함 총 인원 N명(현재 1~10명)을 지정해 생성
2. `POST /api/meetings/{id}/members`: 초대 코드·이름으로 참여하고 본인 토큰 수신
3. 각자 `PUT /api/meetings/{id}/members/me/submission`으로 일정·출발지·선호·비선호를 한 번에 제출
4. N명 미만 제출 시 `waiting`과 남은 인원 표시
5. N명 전원 제출 시 서버가 `processing`으로 전환하고 공통 시간·장소를 자동 계산
6. `GET /api/meetings/{id}`로 상태를 조회해 `recommended` 결과 표시
7. 방장이 `POST /api/meetings/{id}/place/confirm`으로 최종 장소 선택

모임 생성 시 인원수는 필수이며 4명으로 고정되지 않습니다.
가입 인원과 응답 완료 인원은 별개입니다. 같은 사람의 중복 제출은 완료 인원을 늘리지 않습니다.
응답 수정 시 이전 결과를 해제하고 전원 제출 상태이면 다시 자동 처리합니다.
개별 일정·선호 저장과 방장 수동 시간 확정 방식은 전체 응답 제출 방식으로 변경되었습니다.

생성·참여 외 모임 API에는 `Authorization: Bearer <access_token>`이 필요합니다.
원본 토큰은 생성·참여 때만 반환하며 DB에는 해시로 저장합니다.
방장만 초대 코드 조회·최종 장소 확정이 가능하고, 상태 조회·본인 응답·실패한 추천 재시도는 모든 참여자가 가능합니다.

상태는 `waiting → processing → recommended → finalized`입니다.
공통 시간이 없으면 `schedule_conflict`, 처리 실패는 `failed`와 `processing_error`를 반환합니다.
공통 시간은 전원이 가능한 가장 긴 연속 구간으로 자동 선택합니다.
서버가 반환한 공통 시간대와 추천 결과는 프론트가 표시합니다.

마지막 제출 요청에서 추천 계산을 진행하며 그동안 다른 참여자는 GET으로 processing을 확인할 수 있습니다.
프론트의 자동 화면 전환은 상태를 주기적으로 조회해 구현합니다. 현재 이벤트 푸시나 지속 작업 큐는 없습니다.
이미 저장한 동일 응답·추천 재요청은 결과를 중복 생성하지 않습니다.
서버 중단으로 processing 상태가 10분 이상 지속되면 `POST /api/meetings/{id}/recommend`로 재시도할 수 있습니다.

요청·응답 예제는 [docs/API.md](docs/API.md)에 있습니다.
프론트 버튼·입력 항목과 API 연결 관계는 [프론트 연동 검토](docs/FRONTEND_REVIEW.md)에 정리했습니다.

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
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_CONNECT_TIMEOUT` | PostgreSQL 연결 풀·추가 연결·접속 제한(초) | 5, 0, 10 |
| `POSTGRES_PASSWORD` | Compose DB 비밀번호 | setup_env.py가 생성 |
| `CORS_ORIGINS` | 허용 프론트 주소, 쉼표 구분 | localhost:5173, localhost:3000 |
| `AI_PROVIDER` | mock / chatkhu | mock |
| `CHATKHU_API_KEY`, `CHATKHU_BASE_URL`, `AI_MODEL` | 챗쿠 연결 | 미설정 |
| `AI_TIMEOUT_SECONDS`, `AI_MAX_TOKENS` | AI 호출 제한 | 60, 4096 |
| `MAPS_PROVIDER` | mock / naver | mock |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | NAVER API HUB 검색 인증 | 미설정 |
| `NAVER_SEARCH_URL` | 지역 검색 주소 | .env.example 참고 |
| `NCP_MAPS_KEY_ID`, `NCP_MAPS_KEY`, `NCP_MAPS_BASE` | NCP Maps 길찾기·좌표 변환 | .env.example 참고 |
| `MAPS_TIMEOUT_SECONDS` | 네이버 API 요청별 제한 시간(초) | 10 |

`APP_ENV=production`에서는 docs·OpenAPI·개발용 usage API를 노출하지 않습니다.
기존 `MOCK_MODE=1/0/true/false`도 읽지만 `AI_PROVIDER`와 `MAPS_PROVIDER` 설정이 우선합니다.

네이버 키 발급·환경변수·개별 연결 확인·Railway 적용은 [네이버 연동 가이드](docs/NAVER.md)를 참고하세요.
CLOVA 관련 변수와 JSON 저장용 `DATA_PATH`는 더 이상 사용하지 않습니다.

## DB와 마이그레이션

테이블별 필드·관계·JSON 구조는 [데이터베이스 명세](docs/DATABASE.md)에 정리했습니다.

- `meetings`: 목표 인원, 추천 옵션, 처리 상태, 공통 시간·장소, 현재 추천, 버전
- `members`: 모임별 참여자, 토큰 해시, 전체 응답 제출 시각, 가능 시간·선호(JSON)
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
