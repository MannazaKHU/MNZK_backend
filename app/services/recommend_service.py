"""3-1: 중간지점 -> 후보 검색 -> AI 랭킹 -> 이동 정보."""
import asyncio
import json

from app import prompts
from app.clients import ai_client as ai
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
        out = await ai.structured(prompts.QUERY_SYSTEM, user, prompts.QUERY_SCHEMA,
                                   step="make_queries")
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
        dist = c["distance_m"] if c.get("distance_m") is not None else 1000
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
    out = await ai.structured(prompts.RANK_SYSTEM, user, prompts.RANK_SCHEMA,
                               step="rank")
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


def _travel(name: str, loc, place: dict, route) -> dict:
    """한 출발지 -> 추천 장소. route 는 naver.driving() 결과(실패하면 None). loc 이 없으면 출발지 미입력."""
    if not loc:
        return {"member": name, "from": None, "straight_km": None, "driving_km": None,
                "driving_minutes": None, "text": f"{name}: 출발지 미입력 - 거리·시간을 계산할 수 없어요"}
    km = round(haversine_km(loc["lat"], loc["lng"], place["lat"], place["lng"]), 1)
    origin = f"{loc['text']}({name})"
    if route:
        text = f"{origin}에서 직선 {km}km · 도로 {route['distance_km']}km - 자동차 약 {route['minutes']}분"
    else:
        text = f"{origin}에서 직선 {km}km - 자동차 경로 없음"
    return {"member": name, "from": loc["text"], "straight_km": km,
            "driving_km": route["distance_km"] if route else None,
            "driving_minutes": route["minutes"] if route else None,
            "text": text}


async def recommend(people: list, count: int, radius_m: int, extra_text: str = "", *, schedule: dict) -> dict:
    """people: [{"name": "보경", "preference": {...}}, ...]"""
    with_pref = [p for p in people if p.get("preference")]
    if not with_pref:
        raise ApiError(400, "NO_PREFERENCES", "먼저 원하는 조건을 적어 주세요.")
    merged = merge_group([p["preference"] for p in with_pref])

    # 1) 중간지점
    locs = [(p["name"], p["preference"]["start_location"]) for p in with_pref
            if p["preference"].get("start_location")]
    if not locs:
        raise ApiError(400, "NO_LOCATION", "출발 위치를 함께 적어 주세요. 역, 학교, 건물, 동네, 주소 모두 괜찮아요. (예: 코엑스에서 출발)")
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
    # Time is supplied as context, but search results do not contain verified opening hours.
    context = json.dumps({"확정일정": schedule, "추가요청": extra_text}, ensure_ascii=False)
    queries = await make_queries(merged, context)
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
        picks = await rank(merged, filtered, count, context)
    except ApiError:
        picks, fallback = _rule_rank(merged, filtered, count), True

    # 5) 결과 조립 + 이동 정보 (추천 장소 x 출발지 길찾기는 한꺼번에 병렬 호출)
    by_id = {c["id"]: c for c in filtered}
    places = [by_id[p["id"]] for p in picks]
    # 출발지를 안 적은 사람도 결과에서 빠지지 않게 참여자 전원을 넣는다
    travelers = [(p["name"], (p.get("preference") or {}).get("start_location")) for p in people]

    async def no_route():
        return None

    routes = await asyncio.gather(*(naver.driving(loc, place) if loc else no_route()
                                    for place in places for _, loc in travelers))
    recs = []
    for i, (p, place) in enumerate(zip(picks, places), start=1):
        travel = [_travel(name, loc, place, routes[(i - 1) * len(travelers) + j])
                  for j, (name, loc) in enumerate(travelers)]
        recs.append({"rank": i, "place": place, "score": p["score"], "reason": p["reason"],
                     "travel": travel, "travel_text": [t["text"] for t in travel],
                     "matched": p.get("matched", []),
                     "warnings": list(dict.fromkeys(p.get("warnings", []) + [
                         "확정 시간의 영업 여부와 예약 가능 여부는 확인이 필요해요.",
                     ]))})

    return {
        "schedule": schedule,
        "center": center,
        "group_summary": await group_summary(merged),
        "search_queries": queries,
        "candidate_count": len(filtered),
        "fallback": fallback,
        "recommendations": recs,
    }
