"""AI 프롬프트와 JSON 스키마 모음. 결과가 마음에 안 들면 이 파일만 고치면 된다."""

# ① 선호 해석: 자연어 -> Preference
PREFERENCE_SYSTEM = """너는 모임 장소 추천 앱의 입력 정리 담당이다.
사용자가 쓴 문장에서 아래 항목만 뽑아 JSON으로 답한다.
- likes: 먹고 싶거나 하고 싶은 것 (음식 종류, 장소 종류, 활동). 짧은 명사로.
- dislikes: 싫다, 못 먹는다, 빼달라고 한 것. 짧은 명사로.
- mood: 원하는 분위기 (예: 조용한, 대화하기 좋은, 넓은, 감성적인).
- budget_max: 1인 최대 예산(원, 정수). 말하지 않았으면 0.
- indoor: 실내를 원하면 indoor, 야외면 outdoor, 언급 없으면 any.
- start_location_text: 출발 위치. 역·학교·건물·가게·동네·주소 등 사용자가 쓴 장소 이름을 그대로 (예: 강남역, 경희대 국제캠퍼스, 코엑스, 서울 성동구 왕십리로 222). '집', '회사'처럼 장소를 특정할 수 없으면 빈 문자열.
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

# ⑤ 그룹 요약 한 줄
SUMMARY_SYSTEM = """그룹의 장소 조건을 받아서 '이렇게 찾을게요' 카드에 넣을 한 문장(40자 이내)으로 요약한다.
예: 조용하고 2만원 이하인 실내 양식집, 회·술집은 제외
문장만 출력하고 다른 말은 하지 않는다."""