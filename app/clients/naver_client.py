"""네이버 API 담당.
- 지역 검색 (NAVER API HUB): 역·가게 이름 -> 좌표, 근처 가게 후보
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
    lat, lng = _coord(item["mapy"]), _coord(item["mapx"])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Invalid coordinates")
    return {
        "id": hashlib.md5((name + address).encode()).hexdigest()[:10],  # 네이버 지역검색은 id가 없어서 만듦
        "name": name,
        "category": item.get("category", ""),
        "address": address,
        "lat": lat,
        "lng": lng,
        "phone": item.get("telephone", ""),
        "url": item.get("link") or f"https://map.naver.com/p/search/{quote(name)}",
        "distance_m": None,
    }


async def _get_json(url: str, headers: dict, params: dict, service: str) -> dict:
    """외부 오류 본문/인증 정보는 클라이언트 응답에 노출하지 않는다."""
    try:
        async with httpx.AsyncClient(timeout=settings.MAPS_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={**headers, "Accept": "application/json"}, params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        raise ApiError(502, "MAP_ERROR", f"{service} 호출에 실패했어요. "
                       f"(HTTP {error.response.status_code}) 인증·이용 설정을 확인해 주세요.") from None
    except httpx.TimeoutException:
        raise ApiError(502, "MAP_ERROR", f"{service} 요청 시간이 초과됐어요. (Timeout)") from None
    except httpx.HTTPError as error:
        # 예외 문자열에는 URL 등이 포함될 수 있으므로 타입 이름만 전달한다.
        raise ApiError(502, "MAP_ERROR", f"{service} 네트워크 연결에 실패했어요. "
                       f"({type(error).__name__})") from None
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Expected object")
        return data
    except ValueError:
        raise ApiError(502, "MAP_ERROR", f"{service} 응답 형식이 올바르지 않아요.") from None


async def local_search(query: str, display: int = 5, sort: str = "random") -> list:
    """네이버 지역 검색. display 는 최대 5개. sort: random(정확도순) / comment(리뷰 많은 순)."""
    headers = {"X-NCP-APIGW-API-KEY-ID": settings.NAVER_CLIENT_ID,
           "X-NCP-APIGW-API-KEY": settings.NAVER_CLIENT_SECRET}

    params = {"query": query, "display": display, "start": 1, "sort": sort, "format": "json"}
    data = await _get_json(settings.NAVER_SEARCH_URL, headers, params, "네이버 지역 검색")
    try:
        items = data["items"]
        if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
            raise ValueError("Invalid items")
        # 좌표가 없는 검색 결과는 추천 후보로 사용하지 않는다.
        return [_to_place(it) for it in items if it.get("mapx") and it.get("mapy")]
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ApiError(502, "MAP_ERROR", "네이버 지역 검색 응답 형식이 올바르지 않아요.") from None


# ---------- NCP Maps (Geocoding / Reverse Geocoding) ----------
async def _ncp_get(path: str, params: dict) -> dict:
    headers = {"x-ncp-apigw-api-key-id": settings.NCP_MAPS_KEY_ID,
               "x-ncp-apigw-api-key": settings.NCP_MAPS_KEY}
    return await _get_json(settings.NCP_MAPS_BASE.rstrip("/") + path, headers, params, "NCP Maps")


async def address_geocode(text: str):
    """주소를 NCP Geocoding으로 직접 조회 (지역 검색과 별도)."""
    data = await _ncp_get("/map-geocode/v2/geocode", {"query": text})
    try:
        if data["status"] != "OK" or not isinstance(data["addresses"], list):
            raise ValueError("Geocoding failed")
        if not data["addresses"]:
            return None
        address = data["addresses"][0]
        lat, lng = float(address["y"]), float(address["x"])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("Invalid coordinates")
        return {"text": text, "lat": lat, "lng": lng}
    except (KeyError, TypeError, ValueError, IndexError):
        raise ApiError(502, "MAP_ERROR", "NCP Geocoding 응답 형식이 올바르지 않아요.") from None


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
    return await address_geocode(text)


async def area_name(lat: float, lng: float):
    """좌표 -> '동작구 사당동' (리버스 지오코딩). 못 찾으면 None."""
    if settings.MAPS_PROVIDER == "mock":
        return "샘플구 샘플동"
    data = await _ncp_get("/map-reversegeocode/v2/gc",
                          {"coords": f"{lng},{lat}", "orders": "legalcode", "output": "json"})
    try:
        if data["status"]["code"] != 0 or not isinstance(data["results"], list):
            raise ValueError("Reverse geocoding failed")
        if not data["results"]:
            return None
        region = data["results"][0]["region"]
        parts = [region.get(k, {}).get("name", "") for k in ("area2", "area3")]
        return " ".join(p for p in parts if p) or None
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        raise ApiError(502, "MAP_ERROR", "NCP Reverse Geocoding 응답 형식이 올바르지 않아요.") from None


async def driving(start: dict, goal: dict, *, raise_on_error: bool = False):
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
        routes = data.get("route", {}).get("trafast") or []
        if data.get("code") != 0 or not routes:
            return None
        summary = routes[0]["summary"]
        distance, duration = float(summary["distance"]), float(summary["duration"])
        if not (0 <= distance < math.inf and 0 <= duration < math.inf):
            return None
        return {"distance_km": round(distance / 1000, 1),
                "minutes": max(1, round(duration / 60000))}
    except ApiError:
        if raise_on_error:
            raise
        return None
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError):
        return None


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
