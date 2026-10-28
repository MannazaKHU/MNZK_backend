> 이전 CLOVA 기반 시연 버전의 보관 문서입니다. 현재 구현과 실행 방법은 README.md와 docs/API.md를 참고하세요.

# MeetFlow 백엔드 개발 가이드 + 코드

Oct 8, 2026 · @박보경

## 1. 전체 그림

이 문서의 코드를 위에서부터 파일 이름 그대로 복사해 붙이면, `MOCK_MODE=1`에서 키 없이 API 명세서의 모든 API가 돌아갑니다. 그다음 `.env`에 키만 넣으면 실제 HCX와 네이버 API로 바뀝니다.

폴더 구조 (이대로 만들기)

```
meetflow-backend/
├── .env                  ← 키 넣는 곳 (git에 올리지 않기)
├── .env.example
├── requirements.txt
├── app/
│   ├── __init__.py       ← 빈 파일
│   ├── main.py           ← 서버 시작점, 라우터 등록
│   ├── config.py         ← .env 읽기
│   ├── errors.py         ← 에러 모양 통일
│   ├── store.py          ← 메모리 + data/meetings.json 저장
│   ├── schemas.py        ← 요청/응답 모양(Pydantic)
│   ├── prompts.py        ← HCX 프롬프트 + JSON 스키마 (당일 가장 많이 고칠 파일)
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── hcx_client.py    ← CLOVA Studio(HyperCLOVA X) 호출
│   │   └── naver_client.py  ← 네이버 지역 검색 + NCP Maps 지오코딩
│   ├── services/
│   │   ├── __init__.py
│   │   ├── schedule_service.py   ← 겹치는 시간 계산 (AI 없음)
│   │   ├── preference_service.py ← 자연어 → 선호 JSON, 그룹 합치기
│   │   └── recommend_service.py  ← 중간지점 → 후보 → AI 랭킹
│   └── routers/
│       ├── __init__.py
│       ├── meetings.py   ← /api/meetings/...
│       └── misc.py       ← /api/recommend/quick, /api/usage, /health
└── scripts/
    └── smoke_test.py     ← 실제 키가 동작하는지 1분 점검
```

요청 하나가 지나가는 길은 항상 같습니다: 프론트 → `routers`(주소 받기, 입력 검사) → `services`(실제 로직) → `clients`(HCX·네이버 API 호출) → `store`(저장) → 다시 프론트. 버그가 나면 이 순서대로 거꾸로 따라가면 됩니다.

역할 나누기 추천: 1명은 `schedule_service` + 일정 라우터(AI 없음, 입문자에게 적합), 1명은 `prompts.py` 튜닝 + `preference_service`, 1명은 `recommend_service` + 네이버 API, 나머지는 프론트.

## 2. 0단계: 환경 설정 (20분)

Python 3.10 이상과 키 세 종류(CLOVA Studio, 네이버 개발자센터 검색 API, 네이버 클라우드 Maps)만 있으면 됩니다. 키가 없어도 MOCK 모드로 먼저 개발을 시작하세요.

1. 폴더 만들고 가상환경 켜기 (Windows PowerShell 기준)

```bash
mkdir meetflow-backend
cd meetflow-backend
python -m venv .venv
.venv\Scripts\activate        # Mac/Linux: source .venv/bin/activate
```

2. 5장의 `requirements.txt`를 만든 뒤 설치

```bash
pip install -r requirements.txt
```

3. 키 발급 (서로 다른 사이트 세 곳)
   1. CLOVA Studio (HyperCLOVA X): 네이버 클라우드 콘솔 → CLOVA Studio → API 키 → 테스트 앱 키 발급 → `nv-`로 시작하는 값을 `CLOVA_API_KEY`에
   2. 네이버 검색 API (가게·역 찾기): developers.naver.com → Application → 애플리케이션 등록 → 사용 API에서 **검색** 선택 → 비로그인 오픈 API 환경 "WEB 설정"에 `http://localhost` 입력 → Client ID / Client Secret을 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`에
   3. NCP Maps (주소 ↔ 좌표): 네이버 클라우드 콘솔 → Services → Application Services → Maps → Application 등록 → **Geocoding, Reverse Geocoding** 체크 (프론트 지도도 쓰면 Dynamic Map 체크 + 웹 서비스 URL에 `http://localhost:5500`) → 인증 정보의 Client ID / Client Secret을 `NCP_MAPS_KEY_ID`, `NCP_MAPS_KEY`에
4. `.env` 파일 만들기 (`.env.example`을 복사해서 값 채우기)

```bash
copy .env.example .env         # Mac/Linux: cp .env.example .env
```

5. `.gitignore`에 아래 세 줄 추가 (키가 GitHub에 올라가면 크레딧이 털릴 수 있음)

```
.env
.venv/
data/
```

## 3. 꼭 알아야 할 개념 5개

이 다섯 개만 이해하면 코드 전체를 읽을 수 있습니다.

| 개념 | 한 줄 설명 | 코드에서 보이는 모양 |
| --- | --- | --- |
| 라우터 | "이 주소로 오면 이 함수를 실행"이라는 연결표 | `@router.post("/meetings")` |
| Pydantic 모델 | 요청 JSON의 모양을 정해두면 틀린 입력을 FastAPI가 자동으로 422 에러로 막아줌 | `class CreateMeetingReq(BaseModel)` |
| 서비스 | 라우터는 얇게, 진짜 로직은 서비스 함수에 둠. 테스트·교체가 쉬움 | `schedule_service.best_slots(meeting)` |
| Structured Outputs | HCX에게 JSON 스키마를 주면 그 모양 그대로 답하게 강제. `json.loads` 한 번이면 끝 | `hcx.structured(system, user, schema)` |
| MOCK 모드 | 외부 API 대신 미리 만든 가짜 응답을 돌려줌. 크레딧 없이 프론트 연결 가능 | `if settings.MOCK_MODE: return ...` |

비동기(`async def`, `await`)는 "외부 API 기다리는 동안 다른 요청도 받는다"는 뜻입니다. 규칙은 하나: `await`를 쓰는 함수는 `async def`로 만들고, 부를 때도 `await`를 붙입니다.

## 4. 당일 작업 순서

AI 없는 부분을 먼저 완성하고, AI는 MOCK → 실제 순서로 하나씩 켭니다. 각 단계가 끝나면 Swagger(`/docs`)에서 직접 눌러 확인하고 git commit 합니다.

- [ ] 1\. 폴더·가상환경·설치, `MOCK_MODE=1`로 서버 켜고 `/health` 확인 (30분)
- [ ] 2\. 모임 생성·조회·참여 API 확인, 프론트에 연결 (1시간)
- [ ] 3\. 가능 시간 저장 + 겹치는 시간 계산 확인, 1-2 화면 히트맵 연결 (1.5시간)
- [ ] 4\. `scripts/smoke_test.py`로 HCX·네이버 실제 키 동작 확인 (20분)
- [ ] 5\. `MOCK_MODE=0`, 선호 입력 API 실제 HCX로 확인, 프롬프트 다듬기 (1.5시간)
- [ ] 6\. 그룹 추천 API 실제 호출, 결과 카드 화면 연결 (2시간)
- [ ] 7\. quick 추천 + `/api/usage` 연결 (30분)
- [ ] 8\. 시연 시나리오 3회 리허설, 시연용 모임을 미리 만들어 두기 (1시간)

막히면 30분 넘게 붙잡지 말고 MOCK으로 돌려놓은 채 다음 단계로 넘어가세요. 발표는 "동작하는 흐름"이 우선입니다.

## 5. 코드 1: 설정 · 저장소 · 스키마

이 장의 파일은 당일 거의 고칠 일이 없는 뼈대입니다. `app/__init__.py`, `app/clients/__init__.py`, `app/services/__init__.py`, `app/routers/__init__.py`는 **내용 없는 빈 파일**로 만드세요(없으면 import 에러).

`requirements.txt`

```
fastapi>=0.110
uvicorn[standard]>=0.29
httpx>=0.27
pydantic>=2.6
python-dotenv>=1.0
```

`.env.example`

```
# 1이면 HCX·네이버를 호출하지 않고 샘플 응답 사용 (크레딧 0원)
MOCK_MODE=1

# CLOVA Studio API 키 (nv-로 시작)
CLOVA_API_KEY=
CLOVA_BASE_URL=https://clovastudio.stream.ntruss.com
HCX_MODEL=HCX-007
HCX_LIGHT_MODEL=HCX-DASH-002

# 네이버 개발자센터 (검색 API - 지역)
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=

# 네이버 클라우드 플랫폼 Maps (Geocoding, Reverse Geocoding)
NCP_MAPS_KEY_ID=
NCP_MAPS_KEY=
NCP_MAPS_BASE=https://maps.apigw.ntruss.com

DATA_PATH=data/meetings.json
CORS_ORIGINS=*
```

`app/config.py`

```python
"""환경변수(.env)를 읽어서 settings 하나로 모아두는 파일."""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "1") == "1"
    CLOVA_API_KEY: str = os.getenv("CLOVA_API_KEY", "")
    CLOVA_BASE_URL: str = os.getenv("CLOVA_BASE_URL", "https://clovastudio.stream.ntruss.com")
    HCX_MODEL: str = os.getenv("HCX_MODEL", "HCX-007")
    HCX_LIGHT_MODEL: str = os.getenv("HCX_LIGHT_MODEL", "HCX-DASH-002")
    NAVER_CLIENT_ID: str = os.getenv("NAVER_CLIENT_ID", "")
    NAVER_CLIENT_SECRET: str = os.getenv("NAVER_CLIENT_SECRET", "")
    NCP_MAPS_KEY_ID: str = os.getenv("NCP_MAPS_KEY_ID", "")
    NCP_MAPS_KEY: str = os.getenv("NCP_MAPS_KEY", "")
    NCP_MAPS_BASE: str = os.getenv("NCP_MAPS_BASE", "https://maps.apigw.ntruss.com")
    DATA_PATH: str = os.getenv("DATA_PATH", "data/meetings.json")
    CORS_ORIGINS: list = os.getenv("CORS_ORIGINS", "*").split(",")


settings = Settings()
```

`app/errors.py`

```python
"""모든 에러를 {"detail": {"code": ..., "message": ...}} 한 가지 모양으로 통일."""
from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status_code=status, detail={"code": code, "message": message})
```

`app/store.py`

```python
"""DB 대신 쓰는 저장소: 메모리(dict)에 두고, 바뀔 때마다 JSON 파일로 저장.
서버를 껐다 켜도 data/meetings.json 에서 다시 불러온다."""
import json
import os
import secrets
import string
import threading
from datetime import datetime

from app.config import settings
from app.errors import ApiError

_lock = threading.Lock()
_meetings: dict = {}


def _load() -> None:
    if os.path.exists(settings.DATA_PATH):
        with open(settings.DATA_PATH, encoding="utf-8") as f:
            _meetings.update(json.load(f))


def save() -> None:
    with _lock:
        folder = os.path.dirname(settings.DATA_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = settings.DATA_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_meetings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, settings.DATA_PATH)


def new_id(length: int, upper: bool = False) -> str:
    chars = (string.ascii_uppercase if upper else string.ascii_lowercase) + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def new_member(name: str, is_host: bool = False) -> dict:
    return {"id": new_id(8), "name": name, "is_host": is_host,
            "availability": {}, "preference": None}


def create_meeting(title, candidate_dates, time_range, slot_minutes, host_name) -> dict:
    meeting_id = new_id(6, upper=True)
    while meeting_id in _meetings:
        meeting_id = new_id(6, upper=True)
    meeting = {
        "id": meeting_id,
        "title": title,
        "candidate_dates": sorted(set(candidate_dates)),
        "time_range": time_range,
        "slot_minutes": slot_minutes,
        "status": "collecting",
        "confirmed": None,
        "members": [new_member(host_name, is_host=True)],
        "recommendations": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _meetings[meeting_id] = meeting
    save()
    return meeting


def get_meeting(meeting_id: str) -> dict:
    meeting = _meetings.get(meeting_id.upper())
    if not meeting:
        raise ApiError(404, "MEETING_NOT_FOUND", "모임을 찾을 수 없어요. 링크를 다시 확인해 주세요.")
    return meeting


def get_member(meeting: dict, member_id: str) -> dict:
    for m in meeting["members"]:
        if m["id"] == member_id:
            return m
    raise ApiError(404, "MEMBER_NOT_FOUND", "참여자를 찾을 수 없어요. 다시 입장해 주세요.")


_load()
```

`app/schemas.py`

```python
"""요청 JSON의 모양. 여기서 틀리면 FastAPI가 자동으로 400 VALIDATION_ERROR 를 돌려준다."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _check_date(v: str) -> str:
    datetime.strptime(v, "%Y-%m-%d")  # 형식이 틀리면 ValueError
    return v


def _check_time(v: str) -> str:
    datetime.strptime(v, "%H:%M")
    return v


class TimeRange(BaseModel):
    start: str = "10:00"
    end: str = "23:00"

    _v_start = field_validator("start")(_check_time)
    _v_end = field_validator("end")(_check_time)


class CreateMeetingReq(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    host_name: str = Field(min_length=1, max_length=20)
    candidate_dates: list[str] = Field(min_length=1, max_length=14)
    time_range: TimeRange = TimeRange()
    slot_minutes: Literal[30, 60] = 60

    @field_validator("candidate_dates")
    @classmethod
    def check_dates(cls, v):
        return [_check_date(d) for d in v]


class JoinReq(BaseModel):
    name: str = Field(min_length=1, max_length=20)


class AvailabilityReq(BaseModel):
    availability: dict[str, list[str]]


class TextReq(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class Location(BaseModel):
    text: str
    lat: float
    lng: float


class PreferenceReq(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    start_location: Optional[Location] = None


class ConfirmReq(BaseModel):
    member_id: str
    date: str
    start: str
    end: str


class RecommendReq(BaseModel):
    count: int = Field(default=5, ge=1, le=10)
    radius_m: int = Field(default=1000, ge=300, le=3000)
    extra_text: str = Field(default="", max_length=200)


class QuickPerson(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=500)


class QuickReq(BaseModel):
    people: list[QuickPerson] = Field(min_length=1, max_length=10)
    count: int = Field(default=5, ge=1, le=10)
    radius_m: int = Field(default=1000, ge=300, le=3000)
    extra_text: str = Field(default="", max_length=200)
```

## 6. 코드 2: 프롬프트 · HCX · 네이버 클라이언트

HCX 결과가 이상하면 `prompts.py`만 고치세요. 클라이언트 두 파일은 "API 한 번 부르고 결과를 꺼내는" 일만 합니다. 사전 개발 코드(`hcx-message-coach`)의 `hcx_client.py`가 이미 smoke test를 통과했다면, 아래 `chat`/`structured` 두 함수 이름만 맞춰 그 파일을 써도 됩니다.

`app/prompts.py`

```python
"""HCX 프롬프트와 JSON 스키마 모음. 결과가 마음에 안 들면 이 파일만 고치면 된다."""

# ① 선호 해석: 자연어 -> Preference
PREFERENCE_SYSTEM = """너는 모임 장소 추천 앱의 입력 정리 담당이다.
사용자가 쓴 문장에서 아래 항목만 뽑아 JSON으로 답한다.
- likes: 먹고 싶거나 하고 싶은 것 (음식 종류, 장소 종류, 활동). 짧은 명사로.
- dislikes: 싫다, 못 먹는다, 빼달라고 한 것. 짧은 명사로.
- mood: 원하는 분위기 (예: 조용한, 대화하기 좋은, 넓은, 감성적인).
- budget_max: 1인 최대 예산(원, 정수). 말하지 않았으면 0.
- indoor: 실내를 원하면 indoor, 야외면 outdoor, 언급 없으면 any.
- start_location_text: 출발 위치(역 이름, 동네 이름). 없으면 빈 문자열.
규칙: 문장에 없는 내용은 절대 만들지 말 것. 모르면 빈 배열이나 빈 값."""

PREFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "likes": {"type": "array", "items": {"type": "string"}},
        "dislikes": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "array", "items": {"type": "string"}},
        "budget_max": {"type": "integer"},
        "indoor": {"type": "string", "enum": ["indoor", "outdoor", "any"]},
        "start_location_text": {"type": "string"},
    },
    "required": ["likes", "dislikes", "mood", "budget_max", "indoor", "start_location_text"],
}

# ② 시간 해석: 자연어 -> 날짜별 가능 슬롯
AVAILABILITY_SYSTEM = """너는 일정 정리 담당이다.
후보 날짜(요일 포함)와 선택 가능한 시간 슬롯 목록이 주어진다.
사용자 문장을 읽고, 사용자가 가능하다고 한 날짜와 슬롯만 골라 JSON으로 답한다.
- 슬롯은 반드시 주어진 목록에 있는 값만 쓴다. 날짜도 후보 날짜만 쓴다.
- '저녁'은 18:00 이후, '오후'는 12:00~18:00, '오전'은 12:00 이전, '하루 종일'은 전부.
- note 에는 어떻게 해석했는지 한 문장으로 쓴다."""

AVAILABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "slots": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["date", "slots"],
            },
        },
        "note": {"type": "string"},
    },
    "required": ["days", "note"],
}

# ③ 검색어 생성: 그룹 조건 -> 네이버 지도 검색어
QUERY_SYSTEM = """너는 네이버 지도 검색어를 만드는 담당이다.
그룹의 조건을 보고 실제 가게를 찾기 좋은 짧은 검색어 3~4개를 만든다.
- 검색어는 '파스타', '이탈리안 레스토랑', '조용한 카페'처럼 2~10자 정도의 명사구.
- 싫어하는 것(dislikes)이 들어간 검색어는 만들지 않는다.
- 지역 이름은 넣지 않는다 (역 이름은 코드가 앞에 붙임)."""

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

# ④ 후보 랭킹: 그룹 조건 + 후보 -> 상위 N개
RANK_SYSTEM = """너는 모임 장소 추천 담당이다.
그룹 조건과 실제 가게 후보 목록이 주어진다. 후보 중에서 그룹에 가장 잘 맞는 곳을 요청한 개수만큼 고른다.
규칙:
- 반드시 후보 목록에 있는 id만 쓴다. 목록에 없는 가게를 만들지 않는다.
- score 는 0~100 정수. 좋아하는 것과 분위기가 많이 맞을수록, 중간지점에서 가까울수록 높게.
- reason 은 그룹 구성원에게 말하듯 1~2문장, 존댓말.
- matched 는 이 가게가 만족한 조건 단어들.
- warnings 는 확인이 필요한 점 (예: 가격 정보 없음). 없으면 빈 배열.
- 점수가 높은 순서로 정렬해서 답한다."""

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                    "matched": {"type": "array", "items": {"type": "string"}},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "score", "reason", "matched", "warnings"],
            },
        }
    },
    "required": ["picks"],
}

# ⑤ 그룹 요약 한 줄 (가벼운 모델)
SUMMARY_SYSTEM = """그룹의 장소 조건을 받아서 '이렇게 찾을게요' 카드에 넣을 한 문장(40자 이내)으로 요약한다.
예: 조용하고 2만원 이하인 실내 양식집, 회·술집은 제외
문장만 출력하고 다른 말은 하지 않는다."""
```

`app/clients/hcx_client.py`

````python
"""CLOVA Studio(HyperCLOVA X) 호출 담당.
- chat():       일반 대화 (가벼운 모델, 요약 한 줄 등)
- structured(): JSON 스키마를 지키도록 강제 (HCX-007 Structured Outputs)
호출할 때마다 토큰 사용량을 USAGE 에 쌓는다 (/api/usage 에서 보여줌)."""
import json
import uuid

import httpx

from app.config import settings
from app.errors import ApiError

USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "by_step": {}}


def _record(step: str, usage: dict) -> None:
    USAGE["calls"] += 1
    USAGE["prompt_tokens"] += usage.get("promptTokens", 0)
    USAGE["completion_tokens"] += usage.get("completionTokens", 0)
    USAGE["by_step"][step] = USAGE["by_step"].get(step, 0) + 1


async def _post(model: str, body: dict, step: str) -> str:
    url = f"{settings.CLOVA_BASE_URL}/v3/chat-completions/{model}"
    headers = {
        "Authorization": f"Bearer {settings.CLOVA_API_KEY}",
        "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise ApiError(502, "AI_ERROR", f"AI 서버에 연결하지 못했어요: {e}")

    if res.status_code != 200:
        print(f"[HCX ERROR] {step} {res.status_code} {res.text[:500]}")  # 터미널에서 원인 확인용
        raise ApiError(502, "AI_ERROR", "AI가 잠시 바빠요. 다시 시도해 주세요.")

    data = res.json()
    result = data.get("result") or {}
    _record(step, result.get("usage") or {})
    return (result.get("message") or {}).get("content", "")


def _parse_json(text: str) -> dict:
    """```json ... ``` 같은 껍데기가 붙어 와도 JSON만 꺼낸다."""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    print(f"[HCX JSON PARSE FAIL] {text[:500]}")
    raise ApiError(502, "AI_ERROR", "AI 응답을 해석하지 못했어요. 다시 시도해 주세요.")


async def chat(system: str, user: str, step: str, temperature: float = 0.3,
               max_tokens: int = 300) -> str:
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "topP": 0.8,
        "maxTokens": max_tokens,
    }
    return (await _post(settings.HCX_LIGHT_MODEL, body, step)).strip()


async def structured(system: str, user: str, schema: dict, step: str,
                     temperature: float = 0.1, max_tokens: int = 2000) -> dict:
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "topP": 0.8,
        "maxCompletionTokens": max_tokens,
        "thinking": {"effort": "none"},          # Structured Outputs 는 thinking 끄고 사용
        "responseFormat": {"type": "json", "schema": schema},
    }
    content = await _post(settings.HCX_MODEL, body, step)
    return _parse_json(content)
````

`app/clients/naver_client.py` (네이버 지역 검색은 반경 검색이 없어서 "사당역 파스타"처럼 역 이름을 붙여 찾고, 거리는 코드로 걸러냅니다)

```python
"""네이버 API 담당.
- 지역 검색 (네이버 개발자센터 검색 API): 역·가게 이름 -> 좌표, 근처 가게 후보
- 지오코딩 / 리버스 지오코딩 (NCP Maps): 주소 -> 좌표, 좌표 -> 동네 이름
네이버 지역 검색은 '반경 검색'이 없어서 "사당역 파스타"처럼 역 이름을 붙여 검색하고,
거리는 코드로 계산해 너무 먼 곳을 걸러낸다. MOCK_MODE 에서는 샘플 데이터로 흉내낸다."""
import hashlib
import html
import math
import re
from urllib.parse import quote

import httpx

from app.config import settings
from app.errors import ApiError

SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """두 좌표 사이 직선거리(km)."""
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- MOCK 데이터 ----------
MOCK_LOCATIONS = {
    "강남역": (37.4979, 127.0276), "수원역": (37.2656, 127.0000), "사당역": (37.4765, 126.9816),
    "홍대입구역": (37.5572, 126.9245), "신촌역": (37.5552, 126.9369), "서울역": (37.5547, 126.9707),
    "잠실역": (37.5133, 127.1001), "건대입구역": (37.5404, 127.0692), "영통역": (37.2511, 127.0714),
    "판교역": (37.3948, 127.1112), "경희대": (37.2420, 127.0800), "수원": (37.2636, 127.0286),
}
MOCK_PLACES = [
    ("오스테리아 모모", "양식>이탈리아음식"), ("파스타 공방", "양식>이탈리아음식"),
    ("피자 앤 그릴", "양식>피자"), ("고기굽는 집", "한식>육류,고기요리"),
    ("삼겹살 상회", "한식>육류,고기요리"), ("바다회센터", "일식>회"),
    ("스시 하루", "일식>초밥,롤"), ("조용한 서재 카페", "카페,디저트>카페"),
    ("브런치 테이블", "양식>브런치"), ("보드게임 아지트", "여가시설>보드카페"),
    ("밤 술집 이자카야", "술집>이자카야"), ("마라 한그릇", "중식>마라탕"),
    ("방탈출 미스터리룸", "여가시설>방탈출카페"), ("한옥 한정식", "한식>한정식"),
]


def _mock_geocode(text: str):
    for key, (lat, lng) in MOCK_LOCATIONS.items():
        if key in text or text in key:
            return {"text": text, "lat": lat, "lng": lng}
    return None


def _mock_search(query: str, center: dict) -> list:
    words = [w for w in query.split() if w and w != center.get("search_name")]
    hits = [p for p in MOCK_PLACES if any(w in p[0] or w in p[1] for w in words)] or MOCK_PLACES
    places = []
    for name, cat in hits[:5]:
        h = hashlib.md5(name.encode()).digest()
        lat = round(center["lat"] + (h[0] / 255 - 0.5) * 0.008, 6)
        lng = round(center["lng"] + (h[1] / 255 - 0.5) * 0.008, 6)
        places.append({
            "id": hashlib.md5(name.encode()).hexdigest()[:10], "name": name, "category": cat,
            "address": "서울 어딘가 샘플로 12", "lat": lat, "lng": lng, "phone": "",
            "url": f"https://map.naver.com/p/search/{quote(name)}",
            "distance_m": round(haversine_km(center["lat"], center["lng"], lat, lng) * 1000),
        })
    return places


# ---------- 네이버 검색 API (지역) ----------
def _clean(text: str) -> str:
    """검색 결과 제목의 <b>태그, &amp; 같은 것 제거."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def _coord(value) -> float:
    """mapx/mapy 는 WGS84 좌표 x 10,000,000 정수 문자열. 예: '1269816000' -> 126.9816"""
    return int(float(value)) / 10_000_000


def _to_place(item: dict) -> dict:
    name = _clean(item.get("title", ""))
    address = item.get("roadAddress") or item.get("address", "")
    return {
        "id": hashlib.md5((name + address).encode()).hexdigest()[:10],  # 네이버 지역검색은 id가 없어서 만듦
        "name": name,
        "category": item.get("category", ""),
        "address": address,
        "lat": _coord(item["mapy"]),
        "lng": _coord(item["mapx"]),
        "phone": item.get("telephone", ""),
        "url": item.get("link") or f"https://map.naver.com/p/search/{quote(name)}",
        "distance_m": None,
    }


async def local_search(query: str, display: int = 5, sort: str = "random") -> list:
    """네이버 지역 검색. display 는 최대 5개. sort: random(정확도순) / comment(리뷰 많은 순)."""
    headers = {"X-Naver-Client-Id": settings.NAVER_CLIENT_ID,
               "X-Naver-Client-Secret": settings.NAVER_CLIENT_SECRET}
    params = {"query": query, "display": display, "start": 1, "sort": sort}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(SEARCH_URL, headers=headers, params=params)
    except httpx.HTTPError as e:
        raise ApiError(502, "MAP_ERROR", f"지도 정보를 불러오지 못했어요: {e}")
    if res.status_code != 200:
        print(f"[NAVER SEARCH ERROR] {res.status_code} {res.text[:300]}")
        raise ApiError(502, "MAP_ERROR", "지도 정보를 불러오지 못했어요.")
    return [_to_place(it) for it in res.json().get("items", []) if it.get("mapx")]


# ---------- NCP Maps (Geocoding / Reverse Geocoding) ----------
async def _ncp_get(path: str, params: dict) -> dict:
    headers = {"x-ncp-apigw-api-key-id": settings.NCP_MAPS_KEY_ID,
               "x-ncp-apigw-api-key": settings.NCP_MAPS_KEY}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(settings.NCP_MAPS_BASE + path, headers=headers, params=params)
    except httpx.HTTPError as e:
        raise ApiError(502, "MAP_ERROR", f"지도 정보를 불러오지 못했어요: {e}")
    if res.status_code != 200:
        print(f"[NCP MAPS ERROR] {path} {res.status_code} {res.text[:300]}")
        raise ApiError(502, "MAP_ERROR", "지도 정보를 불러오지 못했어요.")
    return res.json()


async def geocode(text: str):
    """'수원역' 또는 '경기 수원시 팔달구 덕영대로 924' -> {"text","lat","lng"}. 못 찾으면 None."""
    if not text:
        return None
    if settings.MOCK_MODE:
        return _mock_geocode(text)
    # 1) 역·건물 이름은 지역 검색이 잘 찾음
    places = await local_search(text, display=1)
    if places:
        return {"text": text, "lat": places[0]["lat"], "lng": places[0]["lng"]}
    # 2) 주소 형태면 NCP 지오코딩
    data = await _ncp_get("/map-geocode/v2/geocode", {"query": text})
    addrs = data.get("addresses", [])
    if not addrs:
        return None
    return {"text": text, "lat": float(addrs[0]["y"]), "lng": float(addrs[0]["x"])}


async def area_name(lat: float, lng: float):
    """좌표 -> '동작구 사당동' (리버스 지오코딩). 못 찾으면 None."""
    if settings.MOCK_MODE:
        return "샘플구 샘플동"
    data = await _ncp_get("/map-reversegeocode/v2/gc",
                          {"coords": f"{lng},{lat}", "orders": "legalcode", "output": "json"})
    results = data.get("results", [])
    if not results:
        return None
    region = results[0]["region"]
    parts = [region.get(k, {}).get("name", "") for k in ("area2", "area3")]
    return " ".join(p for p in parts if p) or None


async def nearest_station(lat: float, lng: float):
    """중간 좌표 근처 지하철역. {"name","search_name","lat","lng"} / 못 찾으면 None."""
    if settings.MOCK_MODE:
        return {"name": "샘플역", "search_name": "샘플역", "lat": lat, "lng": lng}
    try:
        area = await area_name(lat, lng)
    except ApiError:
        area = None
    if not area:
        return None
    dong = area.split()[-1]                                   # '사당동'
    items = await local_search(f"{dong} 지하철역", display=5)
    stations = [p for p in items if "지하철" in p["category"] or p["name"].endswith("역")]
    if not stations:   # 역을 못 찾으면 동네 이름으로 검색
        return {"name": area, "search_name": dong, "lat": lat, "lng": lng}
    best = min(stations, key=lambda p: haversine_km(lat, lng, p["lat"], p["lng"]))
    m = re.match(r"(.+?역)", best["name"])                    # '사당역 4호선' -> '사당역'
    return {"name": best["name"], "search_name": m.group(1) if m else best["name"],
            "lat": best["lat"], "lng": best["lng"]}


async def search_places(query: str, center: dict, radius_m: int) -> list:
    """'{역이름} {검색어}' 로 검색하고, 중간지점에서 radius_m 안의 가게만 돌려준다."""
    full_query = f"{center['search_name']} {query}"
    if settings.MOCK_MODE:
        return _mock_search(full_query, center)
    places = await local_search(full_query, display=5)
    for p in places:
        p["distance_m"] = round(haversine_km(center["lat"], center["lng"], p["lat"], p["lng"]) * 1000)
    return [p for p in places if p["distance_m"] <= radius_m]
```

## 7. 코드 3: 서비스 (일정 · 선호 · 추천)

실제 로직은 전부 여기 있습니다. 모든 AI 호출에는 "규칙 기반 대체"가 붙어 있어서, MOCK 모드에서도 그럴듯하게 동작하고 실제 모드에서 HCX가 실패해도 화면이 멈추지 않습니다.

`app/services/schedule_service.py`

```python
"""1-1, 1-2, 그룹 시간 조율. AI 없이 코드로만 계산한다 (크레딧 0원)."""
import json
from datetime import datetime, timedelta

from app import prompts
from app.clients import hcx_client as hcx
from app.config import settings
from app.errors import ApiError

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def all_slots(meeting: dict) -> list:
    """time_range 와 slot_minutes 로 만들 수 있는 슬롯 시작 시각 목록. 예: ['10:00','11:00',...]"""
    start = datetime.strptime(meeting["time_range"]["start"], "%H:%M")
    end = datetime.strptime(meeting["time_range"]["end"], "%H:%M")
    step = timedelta(minutes=meeting["slot_minutes"])
    slots, t = [], start
    while t + step <= end:
        slots.append(t.strftime("%H:%M"))
        t += step
    return slots


def clean_availability(meeting: dict, availability: dict, strict: bool = True) -> dict:
    """후보 날짜·슬롯만 남긴다. strict=True 면 틀린 값이 있을 때 에러."""
    valid_slots = set(all_slots(meeting))
    result = {}
    for date, times in availability.items():
        if date not in meeting["candidate_dates"]:
            if strict:
                raise ApiError(400, "INVALID_DATE", f"{date}는 열려 있는 날짜가 아니에요.")
            continue
        good = sorted({t for t in times if t in valid_slots})
        if strict and len(good) != len(set(times)):
            raise ApiError(400, "INVALID_SLOT", "선택할 수 없는 시간이 들어 있어요.")
        if good:
            result[date] = good
    return result


def heatmap(meeting: dict) -> dict:
    """{날짜: {슬롯: 가능한 인원 수}} - 1-2 화면 색칠용."""
    slots = all_slots(meeting)
    table = {d: {s: 0 for s in slots} for d in meeting["candidate_dates"]}
    for m in meeting["members"]:
        for date, times in m["availability"].items():
            for t in times:
                if date in table and t in table[date]:
                    table[date][t] += 1
    return table


def _end_time(start: str, minutes: int) -> str:
    return (datetime.strptime(start, "%H:%M") + timedelta(minutes=minutes)).strftime("%H:%M")


def best_slots(meeting: dict, min_hours: float = 2, top: int = 5) -> dict:
    """가장 많은 사람이 되는 연속 시간 구간을 순위대로."""
    members = meeting["members"]
    responded = [m for m in members if m["availability"]]
    slots = all_slots(meeting)
    step = meeting["slot_minutes"]
    need = max(1, int(min_hours * 60 // step))

    def find(need_slots: int) -> list:
        best = {}  # (날짜, 가능한 사람들) -> 가장 긴 구간
        for date in meeting["candidate_dates"]:
            who = [{m["name"] for m in members if s in m["availability"].get(date, [])} for s in slots]
            for i in range(len(slots)):
                common = set(who[i])
                for j in range(i, len(slots)):
                    common &= who[j]
                    if not common:
                        break
                    length = j - i + 1
                    if length < need_slots:
                        continue
                    key = (date, frozenset(common))
                    if key not in best or length > best[key][2]:
                        best[key] = (i, j, length)
        items = []
        for (date, people), (i, j, length) in best.items():
            items.append({
                "date": date,
                "start": slots[i],
                "end": _end_time(slots[j], step),
                "hours": round(length * step / 60, 1),
                "available_count": len(people),
                "available_members": sorted(people),
                "missing_members": sorted(m["name"] for m in members if m["name"] not in people),
            })
        items.sort(key=lambda x: (-x["available_count"], -x["hours"], x["date"], x["start"]))
        return items

    result = find(need)
    relaxed = False
    if not result and need > 1:  # 조건이 너무 빡빡하면 1슬롯으로 완화
        result, relaxed = find(1), True

    return {
        "total_members": len(members),
        "responded_members": len(responded),
        "relaxed": relaxed,
        "best": result[:top],
        "heatmap": heatmap(meeting),
    }


async def parse_availability_text(meeting: dict, text: str) -> dict:
    """(Should) '토요일 저녁 다 돼' -> {'availability': {...}, 'note': '...'}"""
    slots = all_slots(meeting)
    if settings.MOCK_MODE:
        if "저녁" in text:
            picked = [s for s in slots if s >= "18:00"]
        elif "오후" in text:
            picked = [s for s in slots if "12:00" <= s < "18:00"]
        else:
            picked = slots
        avail = {d: picked for d in meeting["candidate_dates"]}
        return {"availability": clean_availability(meeting, avail, strict=False),
                "note": "MOCK: 모든 후보 날짜에 같은 시간으로 해석했어요."}

    dates = [f"{d}({WEEKDAYS[datetime.strptime(d, '%Y-%m-%d').weekday()]})"
             for d in meeting["candidate_dates"]]
    user = json.dumps({"후보날짜": dates, "선택가능슬롯": slots, "문장": text}, ensure_ascii=False)
    out = await hcx.structured(prompts.AVAILABILITY_SYSTEM, user,
                               prompts.AVAILABILITY_SCHEMA, step="parse_availability")
    avail = {d.get("date", "")[:10]: d.get("slots", []) for d in out.get("days", [])}
    return {"availability": clean_availability(meeting, avail, strict=False),
            "note": out.get("note", "")}
```

`app/services/preference_service.py`

```python
"""2-1: 자연어 -> 선호 JSON, 그리고 그룹 조건 합치기."""
import json
import re
from collections import Counter

from app import prompts
from app.clients import hcx_client as hcx
from app.clients import naver_client as naver
from app.config import settings
from app.errors import ApiError

# MOCK 및 AI 실패 대비용 간단 규칙
_WORDS = ["파스타", "피자", "고기", "삼겹살", "회", "초밥", "스시", "치킨", "카페", "술집", "한식",
          "중식", "일식", "양식", "분식", "브런치", "마라탕", "보드게임", "방탈출", "노래방", "전시"]
_NEG = ["못", "싫", "빼", "말고", "제외", "안 먹", "별로"]


def _rule_parse(text: str) -> dict:
    likes, dislikes = [], []
    for seg in re.split(r"[,.!?\n]|그리고", text):
        for w in _WORDS:
            idx = seg.find(w)
            if idx == -1:
                continue
            window = seg[idx: idx + len(w) + 6]  # 단어 바로 뒤 몇 글자에 부정어가 있는지
            (dislikes if any(n in window for n in _NEG) else likes).append(w)
    budget = 0
    m = re.search(r"(\d+(?:\.\d+)?)\s*만\s*원", text)
    if m:
        budget = int(float(m.group(1)) * 10000)
    else:
        m = re.search(r"(\d{4,6})\s*원", text)
        if m:
            budget = int(m.group(1))
    mood = [label for key, label in [("조용", "조용한"), ("분위기", "분위기 좋은"),
                                     ("넓", "넓은"), ("얘기", "대화하기 좋은")] if key in text]
    indoor = "outdoor" if "야외" in text else "indoor" if "실내" in text else "any"
    loc = ""
    m = re.search(r"([가-힣A-Za-z0-9]+역)", text) or re.search(
        r"([가-힣A-Za-z0-9]+)에서\s*(출발|가|와|올|갈)", text)
    if m:
        loc = m.group(1)
    return {"likes": list(dict.fromkeys(likes)), "dislikes": list(dict.fromkeys(dislikes)),
            "mood": mood, "budget_max": budget, "indoor": indoor, "start_location_text": loc}


def _str_list(v) -> list:
    return [str(x).strip() for x in (v or []) if str(x).strip()][:10]


async def parse_preference(text: str) -> dict:
    if settings.MOCK_MODE:
        raw = _rule_parse(text)
    else:
        raw = await hcx.structured(prompts.PREFERENCE_SYSTEM, text,
                                   prompts.PREFERENCE_SCHEMA, step="parse_preference")
    budget = raw.get("budget_max") or 0
    indoor = raw.get("indoor") if raw.get("indoor") in ("indoor", "outdoor", "any") else "any"
    return {
        "likes": _str_list(raw.get("likes")),
        "dislikes": _str_list(raw.get("dislikes")),
        "mood": _str_list(raw.get("mood")),
        "budget_max": int(budget) if isinstance(budget, (int, float)) and budget > 0 else None,
        "indoor": indoor,
        "start_location_text": str(raw.get("start_location_text") or "").strip(),
    }


async def build_preference(text: str, start_location: dict = None) -> tuple:
    """(preference, location_found) 를 돌려준다."""
    parsed = await parse_preference(text)
    loc = start_location
    if not loc and parsed["start_location_text"]:
        try:
            loc = await naver.geocode(parsed["start_location_text"])
        except ApiError:
            loc = None
    pref = {
        "raw_text": text,
        "likes": parsed["likes"],
        "dislikes": parsed["dislikes"],
        "mood": parsed["mood"],
        "budget_max": parsed["budget_max"],
        "indoor": parsed["indoor"],
        "start_location": loc,
    }
    return pref, loc is not None


def merge_group(prefs: list) -> dict:
    """여러 명의 조건 합치기: likes 많이 나온 순, dislikes 전부, 예산 최소, 실내 다수결."""
    likes = Counter(w for p in prefs for w in p["likes"])
    dislikes = list(dict.fromkeys(w for p in prefs for w in p["dislikes"]))
    mood = [w for w, _ in Counter(w for p in prefs for w in p["mood"]).most_common()]
    budgets = [p["budget_max"] for p in prefs if p.get("budget_max")]
    votes = Counter(p["indoor"] for p in prefs if p["indoor"] != "any")
    return {
        "likes": [{"word": w, "count": c} for w, c in likes.most_common() if w not in dislikes],
        "dislikes": dislikes,
        "mood": mood,
        "budget_max": min(budgets) if budgets else None,
        "indoor": votes.most_common(1)[0][0] if votes else "any",
    }


def _rule_summary(merged: dict) -> str:
    parts = []
    if merged["mood"]:
        parts.append(", ".join(merged["mood"][:2]))
    if merged["budget_max"]:
        parts.append(f"1인 {merged['budget_max'] // 10000}만원 이하")
    if merged["likes"]:
        parts.append(" · ".join(x["word"] for x in merged["likes"][:3]))
    text = " ".join(parts) or "모두가 편한 곳"
    if merged["dislikes"]:
        text += f", {'·'.join(merged['dislikes'])} 제외"
    return text


async def group_summary(merged: dict) -> str:
    if settings.MOCK_MODE:
        return _rule_summary(merged)
    try:
        return await hcx.chat(prompts.SUMMARY_SYSTEM,
                              json.dumps(merged, ensure_ascii=False), step="summary")
    except ApiError:
        return _rule_summary(merged)
```

`app/services/recommend_service.py`

```python
"""3-1: 중간지점 -> 후보 검색 -> AI 랭킹 -> 이동 정보."""
import json

from app import prompts
from app.clients import hcx_client as hcx
from app.clients import naver_client as naver
from app.clients.naver_client import haversine_km
from app.config import settings
from app.errors import ApiError
from app.services.preference_service import group_summary, merge_group

MAX_CANDIDATES = 20


def _rule_queries(merged: dict) -> list:
    words = [x["word"] for x in merged["likes"]][:4]
    return words or ["맛집"]


async def make_queries(merged: dict, extra_text: str) -> list:
    if settings.MOCK_MODE:
        return _rule_queries(merged)
    user = json.dumps({"그룹조건": merged, "추가요청": extra_text}, ensure_ascii=False)
    try:
        out = await hcx.structured(prompts.QUERY_SYSTEM, user, prompts.QUERY_SCHEMA,
                                   step="make_queries", temperature=0.3)
        queries = [q.strip() for q in out.get("queries", []) if q and q.strip()][:4]
        return queries or _rule_queries(merged)
    except ApiError:
        return _rule_queries(merged)


def _rule_rank(merged: dict, candidates: list, count: int) -> list:
    words = [x["word"] for x in merged["likes"]] + merged["mood"]
    scored = []
    for c in candidates:
        text = c["name"] + " " + c["category"]
        matched = [w for w in words if w in text]
        dist = c.get("distance_m") or 1000
        score = min(100, 50 + 15 * len(matched) + max(0, 20 - dist // 50))
        scored.append({"id": c["id"], "score": score, "matched": matched,
                       "reason": "그룹이 원한 조건과 가깝고 중간지점에서 가까운 곳이에요.",
                       "warnings": ["가격·분위기는 네이버 지도에서 확인해 주세요"]})
    scored.sort(key=lambda x: -x["score"])
    return scored[:count]


async def rank(merged: dict, candidates: list, count: int, extra_text: str) -> list:
    if settings.MOCK_MODE:
        return _rule_rank(merged, candidates, count)
    slim = [{"id": c["id"], "name": c["name"], "category": c["category"],
             "distance_m": c.get("distance_m")} for c in candidates]
    user = json.dumps({"그룹조건": merged, "추가요청": extra_text,
                       "고를개수": count, "후보": slim}, ensure_ascii=False)
    out = await hcx.structured(prompts.RANK_SYSTEM, user, prompts.RANK_SCHEMA,
                               step="rank", temperature=0.2)
    valid_ids = {c["id"] for c in candidates}
    picks, seen = [], set()
    for p in out.get("picks", []):
        pid = str(p.get("id", ""))
        if pid in valid_ids and pid not in seen:      # AI가 지어낸 id 는 버린다
            seen.add(pid)
            p["id"] = pid
            p["score"] = max(0, min(100, int(p.get("score", 0))))
            picks.append(p)
    if len(picks) < count:                            # 모자라면 규칙 결과로 채움
        for extra in _rule_rank(merged, [c for c in candidates if c["id"] not in seen], count):
            if len(picks) >= count:
                break
            picks.append(extra)
    return picks[:count]


async def recommend(people: list, count: int, radius_m: int, extra_text: str = "") -> dict:
    """people: [{"name": "보경", "preference": {...}}, ...]"""
    with_pref = [p for p in people if p.get("preference")]
    if not with_pref:
        raise ApiError(400, "NO_PREFERENCES", "먼저 원하는 조건을 적어 주세요.")
    merged = merge_group([p["preference"] for p in with_pref])

    # 1) 중간지점
    locs = [(p["name"], p["preference"]["start_location"]) for p in with_pref
            if p["preference"].get("start_location")]
    if not locs:
        raise ApiError(400, "NO_LOCATION", "출발 위치를 역 이름으로 적어 주세요.")
    lat = sum(l["lat"] for _, l in locs) / len(locs)
    lng = sum(l["lng"] for _, l in locs) / len(locs)
    try:
        station = await naver.nearest_station(lat, lng)
    except ApiError:
        station = None
    if not station:  # 역을 못 찾으면 중간에 가장 가까운 사람의 출발지를 기준으로
        _, near = min(locs, key=lambda x: haversine_km(lat, lng, x[1]["lat"], x[1]["lng"]))
        station = {"name": near["text"], "search_name": near["text"],
                   "lat": near["lat"], "lng": near["lng"]}
    center = station

    # 2) 검색어 -> 후보 수집
    queries = await make_queries(merged, extra_text)
    candidates = {}
    for q in queries:
        for place in await naver.search_places(q, center, radius_m):
            if len(candidates) < MAX_CANDIDATES:
                candidates.setdefault(place["id"], place)

    # 3) 싫어하는 것 제거 (코드)
    filtered = [c for c in candidates.values()
                if not any(d in c["name"] or d in c["category"] for d in merged["dislikes"])]
    if not filtered:
        raise ApiError(404, "NO_CANDIDATES", "조건에 맞는 곳이 근처에 없어요. 범위를 넓혀 볼까요?")

    # 4) AI 랭킹 (실패하면 규칙으로 대체)
    fallback = False
    try:
        picks = await rank(merged, filtered, count, extra_text)
    except ApiError:
        picks, fallback = _rule_rank(merged, filtered, count), True

    # 5) 결과 조립 + 이동 정보
    by_id = {c["id"]: c for c in filtered}
    recs = []
    for i, p in enumerate(picks, start=1):
        place = by_id[p["id"]]
        travel = []
        for name, loc in locs:
            km = haversine_km(loc["lat"], loc["lng"], place["lat"], place["lng"])
            travel.append({"member": name, "distance_km": round(km, 1),
                           "est_minutes": round(km / 20 * 60 + 10)})
        recs.append({"rank": i, "place": place, "score": p["score"], "reason": p["reason"],
                     "matched": p.get("matched", []), "warnings": p.get("warnings", []),
                     "travel": travel})

    return {
        "center": center,
        "group_summary": await group_summary(merged),
        "search_queries": queries,
        "candidate_count": len(filtered),
        "fallback": fallback,
        "recommendations": recs,
    }
```

## 8. 코드 4: 라우터 · main.py · smoke test

라우터는 "주소 → 서비스 함수" 연결만 합니다. API 명세서의 경로와 1:1로 대응하니, 명세서 번호를 보면서 읽으세요.

`app/routers/meetings.py`

```python
"""/api/meetings/... 주소들. 입력 받고 -> 서비스 부르고 -> 저장하고 -> 돌려준다."""
from fastapi import APIRouter

from app import store
from app.errors import ApiError
from app.schemas import (AvailabilityReq, ConfirmReq, CreateMeetingReq, JoinReq,
                         PreferenceReq, RecommendReq, TextReq)
from app.services import preference_service, recommend_service, schedule_service

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
MAX_MEMBERS = 10


# ---------- 4. 모임 ----------
@router.post("", status_code=201)
def create_meeting(req: CreateMeetingReq):
    if req.time_range.start >= req.time_range.end:
        raise ApiError(400, "VALIDATION_ERROR", "시작 시간이 끝 시간보다 빨라야 해요.")
    meeting = store.create_meeting(req.title, req.candidate_dates, req.time_range.model_dump(),
                                   req.slot_minutes, req.host_name)
    return {"meeting_id": meeting["id"], "host_member_id": meeting["members"][0]["id"],
            "share_path": f"/join.html?m={meeting['id']}", "meeting": meeting}


@router.get("/{meeting_id}")
def get_meeting(meeting_id: str):
    return store.get_meeting(meeting_id)


@router.post("/{meeting_id}/members", status_code=201)
def join_meeting(meeting_id: str, req: JoinReq):
    meeting = store.get_meeting(meeting_id)
    if len(meeting["members"]) >= MAX_MEMBERS:
        raise ApiError(400, "MEETING_FULL", "인원이 가득 찼어요.")
    if any(m["name"] == req.name for m in meeting["members"]):
        raise ApiError(409, "NAME_TAKEN", "같은 이름이 이미 있어요. 다른 이름을 써 주세요.")
    member = store.new_member(req.name)
    meeting["members"].append(member)
    store.save()
    return member


# ---------- 5. 일정 ----------
@router.put("/{meeting_id}/members/{member_id}/availability")
def save_availability(meeting_id: str, member_id: str, req: AvailabilityReq):
    meeting = store.get_meeting(meeting_id)
    member = store.get_member(meeting, member_id)
    member["availability"] = schedule_service.clean_availability(meeting, req.availability)
    store.save()
    return member


@router.post("/{meeting_id}/members/{member_id}/availability/parse")
async def parse_availability(meeting_id: str, member_id: str, req: TextReq):
    meeting = store.get_meeting(meeting_id)
    store.get_member(meeting, member_id)
    return await schedule_service.parse_availability_text(meeting, req.text)


@router.get("/{meeting_id}/schedule")
def get_schedule(meeting_id: str, min_hours: float = 2, top: int = 5):
    meeting = store.get_meeting(meeting_id)
    return schedule_service.best_slots(meeting, min_hours=min_hours, top=top)


@router.post("/{meeting_id}/schedule/confirm")
def confirm_schedule(meeting_id: str, req: ConfirmReq):
    meeting = store.get_meeting(meeting_id)
    member = store.get_member(meeting, req.member_id)
    if not member["is_host"]:
        raise ApiError(403, "NOT_HOST", "방장만 확정할 수 있어요.")
    if req.date not in meeting["candidate_dates"]:
        raise ApiError(400, "INVALID_DATE", "열려 있는 날짜만 고를 수 있어요.")
    meeting["confirmed"] = {"date": req.date, "start": req.start, "end": req.end}
    meeting["status"] = "scheduled"
    store.save()
    return meeting


# ---------- 6. 선호 ----------
@router.post("/{meeting_id}/members/{member_id}/preference")
async def save_preference(meeting_id: str, member_id: str, req: PreferenceReq):
    meeting = store.get_meeting(meeting_id)
    member = store.get_member(meeting, member_id)
    loc = req.start_location.model_dump() if req.start_location else None
    pref, found = await preference_service.build_preference(req.text, loc)
    member["preference"] = pref
    store.save()
    return {"member_id": member_id, "preference": pref, "location_found": found}


@router.get("/{meeting_id}/preferences/summary")
async def preference_summary(meeting_id: str):
    meeting = store.get_meeting(meeting_id)
    prefs = [m["preference"] for m in meeting["members"] if m["preference"]]
    if not prefs:
        raise ApiError(400, "NO_PREFERENCES", "먼저 원하는 조건을 적어 주세요.")
    merged = preference_service.merge_group(prefs)
    return {"responded": len(prefs), "total": len(meeting["members"]), **merged,
            "summary": await preference_service.group_summary(merged)}


# ---------- 7. 추천 ----------
@router.post("/{meeting_id}/recommend")
async def recommend(meeting_id: str, req: RecommendReq):
    meeting = store.get_meeting(meeting_id)
    people = [{"name": m["name"], "preference": m["preference"]} for m in meeting["members"]]
    result = await recommend_service.recommend(people, req.count, req.radius_m, req.extra_text)
    meeting["recommendations"] = result["recommendations"]
    meeting["status"] = "recommended"
    store.save()
    return result
```

`app/routers/misc.py`

```python
"""모임 없이 쓰는 빠른 추천, 토큰 사용량, 헬스체크."""
from fastapi import APIRouter

from app.clients.hcx_client import USAGE
from app.config import settings
from app.schemas import QuickReq
from app.services import preference_service, recommend_service

router = APIRouter(tags=["misc"])


@router.get("/health")
def health():
    return {"ok": True, "mock": settings.MOCK_MODE}


@router.get("/api/usage")
def usage():
    return USAGE


@router.post("/api/recommend/quick")
async def quick_recommend(req: QuickReq):
    people = []
    for p in req.people:
        pref, _ = await preference_service.build_preference(p.text)
        people.append({"name": p.name, "preference": pref})
    return await recommend_service.recommend(people, req.count, req.radius_m, req.extra_text)
```

`app/main.py`

```python
"""서버 시작점. 실행: uvicorn app.main:app --reload"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import meetings, misc

app = FastAPI(title="MeetFlow API", version="1.0")

# 프론트(다른 포트/도메인)에서 호출할 수 있게 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 입력 형식 오류(422)를 명세서의 400 VALIDATION_ERROR 모양으로 바꿈
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse(status_code=400, content={"detail": {
        "code": "VALIDATION_ERROR",
        "message": f"입력값을 다시 확인해 주세요 ({where}: {first.get('msg', '')})",
    }})


app.include_router(meetings.router)
app.include_router(misc.router)
```

`scripts/smoke_test.py`

```python
"""실제 키가 동작하는지 1분 점검. 실행: python -m scripts.smoke_test
MOCK_MODE 와 상관없이 실제 HCX·네이버를 1번씩 호출한다 (크레딧 아주 조금 사용)."""
import asyncio
import traceback

from app.config import settings

settings.MOCK_MODE = False  # 강제로 실제 호출

from app.clients import hcx_client as hcx  # noqa: E402
from app.clients import naver_client as naver  # noqa: E402


async def check(name, coro):
    try:
        result = await coro
        print(f"[OK]   {name}: {str(result)[:150]}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: {getattr(e, 'detail', e)}")
        traceback.print_exc(limit=1)


async def main():
    print("CLOVA 키:", "있음" if settings.CLOVA_API_KEY else "없음",
          "/ 네이버 검색 키:", "있음" if settings.NAVER_CLIENT_ID else "없음",
          "/ NCP Maps 키:", "있음" if settings.NCP_MAPS_KEY_ID else "없음")
    await check("HCX chat (가벼운 모델)", hcx.chat("한 단어로만 답해.", "안녕?", step="smoke"))
    await check("HCX structured (HCX-007)", hcx.structured(
        "사용자 문장에서 음식 이름만 뽑아라.", "파스타랑 피자 먹고 싶어",
        {"type": "object", "properties": {"foods": {"type": "array", "items": {"type": "string"}}},
         "required": ["foods"]}, step="smoke"))
    await check("네이버 지역검색", naver.local_search("강남역 파스타", display=3))
    await check("NCP 지오코딩", naver._ncp_get("/map-geocode/v2/geocode", {"query": "불정로 6"}))
    await check("NCP 리버스 지오코딩", naver.area_name(37.4765, 126.9816))
    await check("중간지점 지하철역", naver.nearest_station(37.4765, 126.9816))
    print("토큰 사용:", hcx.USAGE)


if __name__ == "__main__":
    asyncio.run(main())
```

## 9. 실행과 테스트

위 코드는 MOCK 모드에서 전체 흐름(모임 생성 → 참여 3명 → 가능 시간 → 겹치는 시간 → 확정 → 선호 입력 → 그룹 추천 → quick 추천 → 에러 응답)을 미리 돌려 확인했습니다. 실제 HCX·네이버 API 경로도 가짜 응답으로 확인했지만, 진짜 키로는 행사장에서 smoke test를 꼭 먼저 돌리세요.

서버 켜기 (반드시 `meetflow-backend` 폴더에서)

```bash
uvicorn app.main:app --reload --port 8000
```

브라우저에서 `http://localhost:8000/health` → `{"ok":true,"mock":true}`가 보이면 성공입니다. `http://localhost:8000/docs`를 열면 모든 API를 버튼으로 테스트할 수 있습니다(각 API의 "Try it out" → JSON 붙여넣기 → "Execute").

실제 키 점검 (`.env`에 키 넣은 뒤)

```bash
python -m scripts.smoke_test
```

여섯 줄이 모두 `[OK]`면 `.env`의 `MOCK_MODE=0`으로 바꾸고 서버를 다시 켭니다. `[FAIL]`이면 터미널에 찍힌 `[HCX ERROR]` 또는 `[NAVER SEARCH ERROR] / [NCP MAPS ERROR]` 줄을 보고 10장 표에서 원인을 찾으세요.

Swagger로 시연 흐름 따라하기 (이 순서 그대로 리허설)

1. `POST /api/meetings` 에 아래를 넣고 실행 → 응답의 `meeting_id`, `host_member_id` 메모

```json
{"title": "동아리 뒤풀이", "host_name": "보경", "candidate_dates": ["2026-10-10", "2026-10-11"]}
```

2. `POST /api/meetings/{meeting_id}/members` 에 `{"name": "선협"}` → 응답의 `id` 메모 (한 명 더 반복)
3. 각자 `PUT .../members/{member_id}/availability`

```json
{"availability": {"2026-10-10": ["18:00", "19:00", "20:00"]}}
```

4. `GET .../schedule` → `best[0]`이 모두 되는 시간인지 확인 → 방장 id로 `POST .../schedule/confirm`
5. 각자 `POST .../members/{member_id}/preference`

```json
{"text": "수원역에서 출발해. 파스타나 피자 먹고 싶고 조용한 곳이면 좋겠어. 회는 못 먹어."}
```

```json
{"text": "강남역에서 가, 고기 좋아 회는 싫어. 1인 2만원 이하로"}
```

6. `POST .../recommend` 에 `{"count": 3}` → 장소 3개, 이유, 이동 시간 확인
7. `GET /api/usage` → 토큰 사용량을 발표 화면에 보여주기

시연 직전 팁: 실제 모드로 위 흐름을 한 번 돌려 둔 모임을 만들어 두면, `data/meetings.json`에 결과가 저장되어 발표 중 네트워크가 끊겨도 `GET /api/meetings/{id}`로 추천 결과를 다시 보여줄 수 있습니다.

## 10. 프론트 연결 예시와 자주 나는 에러

프론트는 아래 `api()` 함수 하나만 복사해 쓰면 에러 처리까지 통일됩니다. `member_id`는 `localStorage`에 저장해 새로고침해도 같은 사람으로 남게 합니다.

`frontend/api.js`

```javascript
const BASE = "http://localhost:8000";

async function api(method, path, body) {
  const res = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.detail?.message || "문제가 생겼어요");
    throw data.detail;
  }
  return data;
}

// 사용 예: 모임 만들기
async function createMeeting() {
  const r = await api("POST", "/api/meetings", {
    title: "동아리 뒤풀이",
    host_name: "보경",
    candidate_dates: ["2026-10-10", "2026-10-11"],
  });
  localStorage.setItem("member_" + r.meeting_id, r.host_member_id);
  location.href = "/date.html?m=" + r.meeting_id;
}

// 사용 예: 추천 받기
async function getRecommendations(meetingId) {
  const r = await api("POST", `/api/meetings/${meetingId}/recommend`, { count: 5 });
  r.recommendations.forEach((rec) => console.log(rec.rank, rec.place.name, rec.reason));
}
```

자주 나는 에러

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'app'` | `app` 폴더 안에서 실행함 | 한 칸 위 `meetflow-backend` 폴더에서 실행 |
| `ModuleNotFoundError: No module named 'fastapi'` | 가상환경이 꺼져 있음 | `.venv\Scripts\activate` 후 다시 실행 |
| 브라우저 콘솔에 `CORS` 에러 | 프론트를 파일로 직접 열었거나 주소 불일치 | `CORS_ORIGINS=*` 확인, 프론트는 `python -m http.server 5500`으로 띄우기 |
| `[HCX ERROR] ... 401` | API 키 오타 또는 만료 | 콘솔에서 키 다시 복사, 앞뒤 공백 제거 |
| `[HCX ERROR] ... 400` 이고 responseFormat 언급 | Structured Outputs 필드명이 콘솔 문서와 다름 | 문서 예시와 `hcx_client.structured()`의 body를 비교해 이름만 수정 |
| `[HCX ERROR] ... 429` | 호출 한도 초과 | 잠깐 기다리기, 시연 직전 반복 호출 자제 |
| `[NAVER SEARCH ERROR] 401` | 검색 API Client ID/Secret 오타 | 개발자센터 → 내 애플리케이션에서 다시 복사 |
| `[NAVER SEARCH ERROR] 403` | 애플리케이션에 "검색" API가 추가 안 됨 | 내 애플리케이션 → API 설정 → 검색 추가 |
| `[NCP MAPS ERROR] 401` | NCP Maps 키 오타, 또는 Geocoding/Reverse Geocoding 체크 안 함 | 콘솔 → Maps → Application 수정에서 두 API 체크 |
| `[NCP MAPS ERROR] 404` 또는 연결 실패 | 엔드포인트 도메인이 계정 문서와 다름 | 콘솔 API 문서의 주소를 `.env`의 `NCP_MAPS_BASE`에 넣기 (예전 도메인 `https://naveropenapi.apigw.ntruss.com`) |
| `NO_LOCATION` | 출발지 문장에서 위치를 못 찾음 | "OO역에서 출발"처럼 역 이름을 넣어 다시 입력 |
| `NO_CANDIDATES` | 역 주변 검색 결과가 반경 밖이거나 모두 비선호 | `radius_m`을 2000\~3000으로 늘리기 |
| 후보가 3\~5개뿐 | 네이버 지역 검색은 검색어당 최대 5개 | 정상. 프롬프트에서 검색어 수를 늘리거나 `sort=comment` 검색을 한 번 더 추가 |
| 추천 결과에 `"fallback": true` | HCX 랭킹이 실패해 규칙으로 대체됨 | 터미널의 `[HCX ...]` 줄 확인, 동작은 정상 |
| 서버 재시작 후 데이터가 없음 | `data/` 폴더가 다른 위치에 생김 | 항상 같은 폴더에서 서버 실행 |

결과 화면에 네이버 지도 띄우기 (프론트, NCP Maps의 Dynamic Map 사용)

```html
<div id="map" style="width:100%;height:320px"></div>
<script src="https://oapi.map.naver.com/openapi/v3/maps.js?ncpKeyId=여기에_NCP_MAPS_KEY_ID"></script>
<script>
  // r = POST /recommend 응답
  function drawMap(r) {
    const map = new naver.maps.Map("map", {
      center: new naver.maps.LatLng(r.center.lat, r.center.lng),
      zoom: 15,
    });
    r.recommendations.forEach((rec) => {
      new naver.maps.Marker({
        position: new naver.maps.LatLng(rec.place.lat, rec.place.lng),
        map,
        title: `${rec.rank}. ${rec.place.name}`,
      });
    });
  }
</script>
```

지도 스크립트 주소의 키 파라미터 이름(`ncpKeyId`)과 웹 서비스 URL 등록은 NCP 콘솔의 Maps 가이드와 한 번 대조하세요. 키는 프론트에 노출되므로 Dynamic Map에만 쓰고, 검색 API 키(Client Secret)는 절대 프론트에 넣지 않습니다.
