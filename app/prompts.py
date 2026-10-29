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
- 개인별 선호 검색어를 보존한다. 다른 사람의 비선호만으로 검색어를 제거하지 않는다.
- 지역 이름은 넣지 않는다 (역 이름은 코드가 앞에 붙임)."""

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

# ⑤ 그룹 요약 한 줄
SUMMARY_SYSTEM = """그룹의 장소 조건을 받아서 '이렇게 찾을게요' 카드에 넣을 한 문장(40자 이내)으로 요약한다.
예: 조용하고 2만원 이하인 실내 양식집, 회·술집은 제외
문장만 출력하고 다른 말은 하지 않는다."""
from app.conditions import Condition
PREFERENCE_SCHEMA['properties']['conditions'] = {'type':'array','items':Condition.model_json_schema()}
PREFERENCE_SCHEMA['required'].append('conditions')
PREFERENCE_SYSTEM += """
conditions 배열에 실제 언급한 개별 요구만 구조화한다. 점수·순위는 생성하지 않는다.
category: kind/cost/atmosphere/environment/accessibility/other.
direction: preference/avoidance, strength: desired/required(명시한 필수만).
scope: menu/shared_activity/place. 메뉴 싫음과 장소 자체 싫음을 구분한다.
target은 대상, alternatives는 OR 대안이며 AND로 바꾸지 않는다.
raw_text는 사용자 원문 정확한 부분 문자열. 불명확하면 needs_confirmation=true.
operator: eq/lte/gte. 수량은 value, unit(KRW/minutes/people), 없으면 null.
말하지 않은 조건과 대체 메뉴 선호를 추정하지 않는다. 충돌하는 의견도 보존한다.
"""
QUERY_SYSTEM += '\n다른 사람의 비선호 때문에 선호 검색어를 제거하지 않는다. 비선호는 일괄 장소 제외가 아니다.'
