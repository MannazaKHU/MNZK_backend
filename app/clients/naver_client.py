"""네이버 API 담당.
- 지역 검색 (네이버 개발자센터 검색 API): 역·가게 이름 -> 좌표, 근처 가게 후보
- 지오코딩 / 리버스 지오코딩 (NCP Maps): 주소 -> 좌표, 좌표 -> 동네 이름
네이버 지역 검색은 '반경 검색'이 없어서 "사당역 파스타"처럼 역 이름을 붙여 검색하고,
거리는 코드로 계산해 너무 먼 곳을 걸러낸다. MAPS_PROVIDER=mock 에서는 샘플 데이터로 흉내낸다."""
import hashlib
import html
import math
import re
from urllib.parse import quote

import httpx

from app.config import settings
from app.errors import ApiError

def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """두 좌표 사이 직선거리(km)."""
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, max(0.0, a))))


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
    # 등록되지 않은 장소도 MOCK 에서 동작하도록 서울 안의 고정 좌표를 만들어 준다
    h = hashlib.md5(text.encode()).digest()
    return {"text": text, "lat": round(37.48 + h[0] / 255 * 0.1, 6),
            "lng": round(126.93 + h[1] / 255 * 0.15, 6)}


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
    headers = {"X-NCP-APIGW-API-KEY-ID": settings.NAVER_CLIENT_ID,
           "X-NCP-APIGW-API-KEY": settings.NAVER_CLIENT_SECRET}

    params = {"query": query, "display": display, "start": 1, "sort": sort}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(settings.NAVER_SEARCH_URL, headers=headers, params=params)
    except httpx.HTTPError:
        raise ApiError(502, "MAP_ERROR", "지도 정보를 불러오지 못했어요.")
    if res.status_code != 200:
        raise ApiError(502, "MAP_ERROR", "지도 정보를 불러오지 못했어요.")
    return [_to_place(it) for it in res.json().get("items", []) if it.get("mapx")]


# ---------- NCP Maps (Geocoding / Reverse Geocoding) ----------
async def _ncp_get(path: str, params: dict) -> dict:
    headers = {"x-ncp-apigw-api-key-id": settings.NCP_MAPS_KEY_ID,
               "x-ncp-apigw-api-key": settings.NCP_MAPS_KEY}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(settings.NCP_MAPS_BASE + path, headers=headers, params=params)
    except httpx.HTTPError:
        raise ApiError(502, "MAP_ERROR", "지도 정보를 불러오지 못했어요.")
    if res.status_code != 200:
        raise ApiError(502, "MAP_ERROR", "지도 정보를 불러오지 못했어요.")
    return res.json()


async def geocode(text: str):
    """'수원역' 또는 '경기 수원시 팔달구 덕영대로 924' -> {"text","lat","lng"}. 못 찾으면 None."""
    if not text:
        return None
    if settings.MAPS_PROVIDER == "mock":
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
    if settings.MAPS_PROVIDER == "mock":
        return "샘플구 샘플동"
    data = await _ncp_get("/map-reversegeocode/v2/gc",
                          {"coords": f"{lng},{lat}", "orders": "legalcode", "output": "json"})
    results = data.get("results", [])
    if not results:
        return None
    region = results[0]["region"]
    parts = [region.get(k, {}).get("name", "") for k in ("area2", "area3")]
    return " ".join(p for p in parts if p) or None


async def driving(start: dict, goal: dict):
    """자동차 길찾기 (NCP Directions 5). {"distance_km","minutes"} / 실패하면 None.
    MAPS_PROVIDER=mock 에서는 직선거리 x1.3 을 도로 거리로, 시속 25km 로 계산한 값."""
    if settings.MAPS_PROVIDER == "mock":
        km = haversine_km(start["lat"], start["lng"], goal["lat"], goal["lng"]) * 1.3
        return {"distance_km": round(km, 1), "minutes": max(1, round(km / 25 * 60))}
    try:
        data = await _ncp_get("/map-direction/v1/driving", {
            "start": f"{start['lng']},{start['lat']}",
            "goal": f"{goal['lng']},{goal['lat']}",
            "option": "trafast",
        })
    except ApiError:
        return None
    routes = data.get("route", {}).get("trafast") or []
    if data.get("code") != 0 or not routes:   # 출발지와 도착지가 너무 가까우면 경로가 없음(code 1)
        return None
    summary = routes[0]["summary"]
    return {"distance_km": round(summary["distance"] / 1000, 1),
            "minutes": summary["duration"] / 60000}


async def nearest_station(lat: float, lng: float):
    """중간 좌표 근처 지하철역. {"name","search_name","lat","lng"} / 못 찾으면 None."""
    if settings.MAPS_PROVIDER == "mock":
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
    if settings.MAPS_PROVIDER == "mock":
        return [p for p in _mock_search(full_query, center) if p["distance_m"] <= radius_m]
    places = await local_search(full_query, display=5)
    for p in places:
        p["distance_m"] = round(haversine_km(center["lat"], center["lng"], p["lat"], p["lng"]) * 1000)
    return [p for p in places if p["distance_m"] <= radius_m]
