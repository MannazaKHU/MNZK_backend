"""중간지점 -> 후보 검색 -> 참여자별 자동차 경로 -> 검증된 정책 순위."""
import asyncio
import json

from app import prompts
from app.clients import ai_client as ai
from app.clients import naver_client as naver
from app.clients.naver_client import haversine_km
from app.config import settings
from app.errors import ApiError
from app.services.preference_service import rule_summary, merge_group
from app.services.evidence_extraction import extract_condition_evidence

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


def _travel(name: str, loc, place: dict, route) -> dict:
    """한 출발지 -> 추천 장소. route 는 naver.driving() 결과(실패하면 None). loc 이 없으면 출발지 미입력."""
    if not loc:
        return {"member": name, "from": None, "straight_km": None, "driving_km": None,
                "driving_minutes": None, "text": f"{name}: 출발지 미입력 - 거리·시간을 계산할 수 없어요"}
    km = round(haversine_km(loc["lat"], loc["lng"], place["lat"], place["lng"]), 1)
    origin = f"{loc['text']}({name})"
    if route:
        text = f"{origin}에서 직선 {km}km · 도로 {route['distance_km']:.1f}km - 자동차 약 {route['minutes']:.1f}분"
    else:
        text = f"{origin}에서 직선 {km}km - 자동차 경로 없음"
    return {"member": name, "from": loc["text"], "straight_km": km,
            "driving_km": route["distance_km"] if route else None,
            "driving_minutes": route["minutes"] if route else None,
            "text": text}


async def recommend(people: list, count: int, radius_m: int, extra_text: str = "", *, schedule: dict, meeting_id: str = "service-input",
                    supplied_blog_searches=None, evidence_extractor=extract_condition_evidence) -> dict:
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


    from app.services.recommendation_rules import normalize
    from app.services.preference_service import extract_conditions
    conditions = []
    for person in people:
        pref = person.get('preference') or {}
        items = pref.get('conditions')
        if items is None: items = extract_conditions(pref.get('raw_text',''), pref)
        conditions.extend(normalize(items,person.get('id',person['name'])))
    places = list(candidates.values())
    if not places: raise ApiError(404,'NO_CANDIDATES','검색된 장소가 없습니다.')
    travelers = [(p.get('id',p['name']),p['name'],(p.get('preference') or {}).get('start_location')) for p in people]
    route_limit = asyncio.Semaphore(8)
    async def route_for(loc,place):
        if not loc: return None
        try:
            async with route_limit:
                return await naver.driving(loc,place)
        except ApiError: return None
    routes = await asyncio.gather(*(route_for(loc,place) for place in places for _,_,loc in travelers))
    travels = {}
    for i,place in enumerate(places):
        travels[place['id']] = [{**_travel(name,loc,place,routes[i*len(travelers)+j]),'member_id':mid,'mode':'driving',
                               'source':'mock_estimate' if settings.MAPS_PROVIDER=='mock' else 'naver.directions5'}
                              for j,(mid,name,loc) in enumerate(travelers)]
    # Existing search/Directions stay upstream. Team results enter through a data-only contract.
    from app.recommendation_contracts import RecommendationInputs, TravelData
    from app.services.recommendation_pipeline import EvidencePolicy, evaluate_supplied_inputs
    from app.services.blog_enrichment import configured_policy, collect_searches
    evidence_policy = EvidencePolicy()
    collected = None
    if (supplied_blog_searches is None and settings.ENABLE_BLOG_ENRICHMENT
            and settings.MAPS_PROVIDER == 'naver' and settings.AI_PROVIDER == 'chatkhu'):
        evidence_policy = configured_policy()
        collected = await collect_searches(places, conditions, evidence_policy)
        supplied_blog_searches = collected.batches
    travel_inputs = []
    for pid, rows in travels.items():
        for row in rows:
            travel_inputs.append(TravelData(
                place_id=pid, member_id=row['member_id'],
                status='success' if row['driving_minutes'] is not None else 'failed',
                duration=row['driving_minutes'], unit='minutes', mode='driving', source=row['source'],
                member=row['member'], origin_text=row['from'], straight_km=row['straight_km'],
                driving_km=row['driving_km'], text=row['text']))
    inputs = RecommendationInputs(meeting_id=meeting_id,
                                  participant_ids=[p.get('id', p['name']) for p in people],
                                  places=places, travel_times=travel_inputs, blog_searches=supplied_blog_searches)
    result = await evaluate_supplied_inputs(inputs, conditions, schedule, extractor=evidence_extractor,
                                           evidence_policy=evidence_policy)
    if collected is not None:
        result['blog_enrichment'].update(connection_status='live', provider='naver_api_hub',
                                         external_requests_made=collected.request_count,
                                         search_requests_made=collected.request_count)
    result['recommendations'] = result['recommendations'][:count]
    return {**result, 'schedule': schedule, 'center': center, 'group_summary': rule_summary(merged),
            'search_conditions': {'label': '검색 조건', 'queries': queries, 'center_name': center.get('name'),
                                  'radius_m': radius_m, 'extra_text': extra_text},
            'search_queries': queries, 'candidate_count': len(places), 'fallback': False,
            'travel_mode': 'driving',
            'travel_source': 'mock_estimate' if settings.MAPS_PROVIDER == 'mock' else 'naver.directions5'}
