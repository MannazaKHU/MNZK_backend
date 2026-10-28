"""2-1: 자연어 -> 선호 JSON, 그리고 그룹 조건 합치기."""
import json
import re
from collections import Counter

from app import prompts
from app.clients import ai_client as ai
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
    # '경희대 국제캠퍼스에서 출발', '코엑스 근처야', '강남역' 순서로 찾는다 (장소 이름은 최대 3단어)
    place = r"((?:[가-힣A-Za-z0-9]+\s){0,2}[가-힣A-Za-z0-9]+?)"
    m = (re.search(place + r"\s*에서\s*(출발|가|와|올|갈|만나)", text)
         or re.search(place + r"\s*(근처|부근|쪽|앞)", text)
         or re.search(r"([가-힣A-Za-z0-9]+역)", text))
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
        raw = await ai.structured(prompts.PREFERENCE_SYSTEM, text,
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


def rule_summary(merged: dict) -> str:
    parts = []
    if merged["mood"]:
        parts.append(", ".join(merged["mood"][:2]))
    if merged["budget_max"]:
        parts.append(f"1인 {merged['budget_max']:,}원 이하")
    if merged["likes"]:
        parts.append(" · ".join(x["word"] for x in merged["likes"][:3]))
    text = " ".join(parts) or "모두가 편한 곳"
    if merged["dislikes"]:
        text += f", {'·'.join(merged['dislikes'])} 제외"
    return text


async def group_summary(merged: dict) -> str:
    if settings.MOCK_MODE:
        return rule_summary(merged)
    try:
        return await ai.chat(prompts.SUMMARY_SYSTEM,
                              json.dumps(merged, ensure_ascii=False), step="summary")
    except ApiError:
        return rule_summary(merged)
