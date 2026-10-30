# Supabase DB + Railway API 배포

## 1. 공개 서비스 구성

```text
심사위원 브라우저
→ 공개 배포된 웹 프론트
→ Railway의 HTTPS API
→ Supabase PostgreSQL

Railway API → ChatKHU / 네이버 API
```

Supabase 연결만으로 로컬 API가 공개되지는 않습니다. 웹 프론트·API도 각각 공개 주소가 필요합니다.
프론트는 Railway API를 호출하고, 현재 앱은 SQLAlchemy로 Supabase PostgreSQL에 접속합니다.
Supabase Auth·Storage·브라우저용 Data API를 사용하도록 앱을 변경한 것은 아닙니다.

## 2. Supabase 연결 문자열 준비

현재 대상 프로젝트 API 주소는 `https://vwlvmlgkqujjgjedcrjn.supabase.co`입니다.
이 주소는 PostgreSQL 연결 문자열이 아닙니다. anon 키나 service_role API 키로 대체할 수 없습니다.

1. Supabase 대시보드에서 해당 프로젝트를 엽니다.
2. 상단 **Connect**를 열고 **Direct — Connection string** 탭에서 연결 문자열을 확인합니다.
3. 연결 방식에서 IPv4 호환 접속이 필요하면 **Session pooler**를 선택합니다. Framework 탭의 Next.js용 URL·publishable key는 DB 연결 문자열이 아닙니다.
4. 표시된 호스트·포트·사용자명을 그대로 복사하고 DB 비밀번호를 채웁니다.
5. 로컬에서 점검하려면 기존 `.env`의 `DATABASE_URL` 값을 교체하고 저장합니다.

URI 형태의 예시는 다음과 같으며 대괄호 부분은 실제 값으로 바꿔야 합니다.
풀러 호스트는 추측해서 만들지 말고 Connect 화면의 값을 사용하세요.

```dotenv
DATABASE_URL=postgresql://postgres.[PROJECT_REF]:[ENCODED_DB_PASSWORD]@[POOLER_HOST]:5432/postgres?sslmode=require
```

비밀번호의 `@`, `:`, `/`, `#`, `%` 등 URI 예약 문자는 퍼센트 인코딩해야 합니다.
코드는 `postgres://`, `postgresql://`, `postgresql+psycopg://`를 받아 설치된 psycopg 드라이버로 연결합니다.
Supabase 연결은 SSL을 요구합니다. 기본 풀이 5개이며 추가 overflow 연결은 기본적으로 만들지 않습니다.
Transaction pooler를 사용하더라도 psycopg prepared statements는 비활성화되어 있습니다.
이 서비스는 지속 실행되는 API이므로 우선 Session pooler 또는 사용 환경에 맞는 direct 연결을 선택하세요.

공식 안내: [연결 방법·IPv4·풀러 선택](https://supabase.com/docs/guides/database/connecting-to-postgres).

## 3. 연결과 테이블 생성 확인

프로젝트 폴더에서 먼저 연결·테이블 상태만 검사합니다. 연결 문자열과 키는 출력하지 않습니다.

```sh
.venv/bin/python -m scripts.check_database --supabase --project-ref vwlvmlgkqujjgjedcrjn
```

연결은 성공했지만 테이블이 없다고 나오면 프로젝트 마이그레이션을 적용합니다.

```sh
.venv/bin/python -m scripts.check_database --supabase --project-ref vwlvmlgkqujjgjedcrjn --migrate
```

`--project-ref`는 다른 프로젝트의 연결 문자열에 실수로 적용하는 것을 방지합니다.
명령은 meetings·members·recommendation_runs·alembic_version을 확인합니다.
마이그레이션은 로컬 테스트 모임을 자동 복사하지 않습니다. 공개 서비스에서 새 모임을 만들면 Supabase에 저장됩니다.

Supabase의 `public` 스키마를 사용하지만 테이블은 백엔드 전용입니다.
0003 마이그레이션은 해당 테이블의 RLS를 활성화하고 브라우저용 허용 정책은 추가하지 않습니다.
런타임에는 테이블을 생성한 DB 소유자 연결을 사용합니다. 프론트 접근은 Railway API의 모임별 Bearer 토큰을 거칩니다.
나중에 별도 DB 역할을 사용한다면 해당 역할의 권한·RLS 정책도 함께 설계해야 합니다.
공식 안내: [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).

### 로컬 Docker와의 차이

기존 `compose.yaml`은 `DATABASE_URL`을 Docker의 로컬 DB로 덮어씁니다.
따라서 `.env`만 바꿔도 기존 Docker API가 Supabase로 전환되는 것은 아닙니다.
위 `.venv` 점검 명령은 `.env`를 직접 읽어 Supabase에 연결합니다.
Docker API도 Supabase를 사용하려면 마지막에 Supabase 설정 파일을 추가합니다.

```sh
docker compose -f compose.yaml -f compose.dev.yaml -f compose.supabase.yaml up -d --build --wait api
```

API는 동일한 http://127.0.0.1:8000 주소에서 Supabase를 사용합니다.
기존 로컬 DB 컨테이너·볼륨은 남아 있지만 이 모드의 API는 해당 DB에 새 데이터를 저장하지 않습니다.
새 마이그레이션·종료 명령도 세 Compose 파일을 동일하게 지정하세요.

로컬 API로 Supabase를 시험하려면 기존 Docker API와 다른 포트를 사용하세요.

```sh
PORT=8001 .venv/bin/python -m app
```

이때 API 문서는 http://127.0.0.1:8001/docs 입니다.
Railway에는 로컬 Compose를 올리지 않고 아래 Dockerfile·Railway 설정을 사용합니다.

## 4. Railway 서비스 생성

1. Railway에서 백엔드 서비스를 만들고 GitHub의 백엔드 저장소·브랜치를 연결합니다.
2. 저장소 루트에 app·Dockerfile·railway.json이 있으면 서비스 Root Directory는 `/`입니다.
3. 상위 저장소 안에 MNZK_backend가 들어 있는 구성으로 연결했다면 Root Directory를 `/MNZK_backend`로 지정합니다.
4. 프로젝트 루트의 `railway.json`을 사용하도록 확인합니다. 코드가 GitHub에 반영되어 있어야 GitHub 배포에 포함됩니다.
5. 아래 환경변수를 서비스 Variables에 설정한 뒤 배포합니다.
6. Networking에서 공개 도메인을 생성하고 API HTTPS 주소를 확인합니다.

현재 로컬 폴더는 Git 저장소가 중첩되어 있으므로 **실제 백엔드 저장소**가 무엇인지 확인하고 연결하세요.

`railway.json`의 동작:

- Dockerfile로 이미지 빌드
- pre-deploy: `python -m alembic upgrade head`
- 서버 시작: `python -m app --skip-migrations`
- Railway가 주입한 PORT와 HOST 0.0.0.0으로 수신
- `/health/ready`가 성공한 뒤 공개 트래픽 전환
- 실패 시 제한된 횟수로 재시작

초기에는 API 복제본 하나로 시작합니다. 자동 추천은 요청 처리 중 실행되며 별도 작업 큐는 없습니다.
배포 중 처리 요청이 중단되면 기존 API의 실패·중단 재시도 규칙을 사용합니다.

공식 안내: [Railway 설정 파일](https://docs.railway.com/config-as-code/reference),
[포트·헬스체크](https://docs.railway.com/deployments/healthchecks).

## 5. Railway Variables

실제 비밀번호·키는 Railway Variables에 설정하고 저장소에 커밋하지 않습니다.
로컬 `.env`는 이미지에 포함되지 않습니다.

| 변수 | 설정 |
|---|---|
| `APP_ENV` | `production` |
| `HOST` | `0.0.0.0` (Dockerfile 기본값도 동일) |
| `PORT` | Railway 제공값 사용. 로컬 8000을 강제로 복사하지 않음 |
| `DATABASE_URL` | 위에서 점검한 Supabase PostgreSQL URI |
| `DB_POOL_SIZE` | `5` |
| `DB_MAX_OVERFLOW` | `0` |
| `DB_CONNECT_TIMEOUT` | `10` |
| `CORS_ORIGINS` | 실제 공개 웹 프론트 origin, 여러 개면 쉼표 구분 |
| `AI_PROVIDER` | `chatkhu` |
| `CHATKHU_API_KEY` | 현재 발급한 실제 키 |
| `CHATKHU_BASE_URL` | 키를 발급한 챗쿠 화면의 Gateway Base URL |
| `AI_MODEL` | 사용할 실제 모델 ID |
| `AI_TIMEOUT_SECONDS` | `60` |
| `AI_MAX_TOKENS` | `4096` 또는 검증한 설정값 |
| `MAPS_PROVIDER` | 아직 지도 키가 없다면 `mock`, 실제 장소 검색은 `naver` |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | MAPS_PROVIDER=naver일 때 NAVER API HUB 인증 |
| `NCP_MAPS_KEY_ID`, `NCP_MAPS_KEY` | MAPS_PROVIDER=naver일 때 NCP Maps 인증 |
| `ENABLE_BLOG_ENRICHMENT` | 블로그 근거 자동 보완 시 true. 같은 API HUB Application에 블로그 API 추가 필요 |

`POSTGRES_PASSWORD`는 로컬 Compose용이며 Railway가 Supabase에 연결할 때는 DATABASE_URL에 포함된 비밀번호를 사용합니다.
production에서는 임시 SQLite 파일을 DB로 사용할 수 없습니다.
`/docs`, `/openapi.json`, 개발용 `/api/usage`도 production에서는 제공하지 않습니다.

## 6. 프론트 공개 주소 연결

프론트 API 기본 주소를 백엔드 Railway 서비스의 HTTPS 주소로 설정합니다.
정확한 환경변수 이름은 프론트 프로젝트의 프레임워크·코드에 맞게 지정합니다.

프론트 공개 주소는 `https://mannajaku-flutter-production-75f1.up.railway.app`이며,
프론트 서버의 내부 수신 포트는 `8080`입니다.
백엔드 Railway 서비스의 Variables에 다음 값을 설정합니다.

```dotenv
CORS_ORIGINS=https://mannajaku-flutter-production-75f1.up.railway.app
```

공개 HTTPS 접속은 Railway가 내부 `8080` 포트로 전달하므로 공개 주소나 CORS origin에
`:8080`을 붙이지 않습니다. 프론트 내부 포트 때문에 백엔드의 `PORT`를 변경할 필요는 없습니다.
로컬에서 프론트를 `8080`으로 실행할 때는 `http://localhost:8080` 또는
`http://127.0.0.1:8080`을 사용합니다. 두 주소는 `.env.example`의 허용 목록에 포함되어 있습니다.
로컬 `.env` 변경은 Railway Variables에 자동 반영되지 않으므로 배포 환경에도 별도로 적용합니다.
심사위원 브라우저가 localhost API를 호출하도록 배포하면 접근할 수 없습니다.
Supabase DB URI와 ChatKHU 키는 백엔드에만 설정합니다.
이 프로젝트의 프론트는 Supabase Data API를 직접 호출할 필요가 없습니다.

## 7. 공개 배포 후 확인

1. Railway 주소의 `/health`와 `/health/ready` 응답 확인
2. 공개 웹에서 모임 생성·초대·서로 다른 참가자 응답 제출
3. N명 미만 waiting, N명 완료 후 recommended 전환 확인
4. 다른 브라우저·기기에서 같은 모임의 결과 조회
5. API 재배포 후에도 Supabase 데이터 유지 확인

지도가 MOCK인 상태에서는 장소·이동 정보가 샘플입니다. 실제 장소 시연 전에는 지도 API도 연결해야 합니다.
