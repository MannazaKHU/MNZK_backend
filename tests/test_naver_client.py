import asyncio

import httpx
import pytest

from app.clients import naver_client as naver
from app.config import settings
from app.errors import ApiError
from scripts.check_naver import run_checks, warn_env_conflicts


def configure(monkeypatch, handler):
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "naver")
    monkeypatch.setattr(settings, "NAVER_CLIENT_ID", "search-id")
    monkeypatch.setattr(settings, "NAVER_CLIENT_SECRET", "search-secret")
    monkeypatch.setattr(settings, "NCP_MAPS_KEY_ID", "maps-id")
    monkeypatch.setattr(settings, "NCP_MAPS_KEY", "maps-secret")
    monkeypatch.setattr(settings, "NAVER_SEARCH_URL", "https://naverapihub.apigw.ntruss.com/search/v1/local")
    monkeypatch.setattr(settings, "NCP_MAPS_BASE", "https://maps.apigw.ntruss.com/")
    original = httpx.AsyncClient
    monkeypatch.setattr(naver.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def item(**updates):
    return {"title": "<b>가게</b> &amp; 카페", "roadAddress": "서울 샘플로 1",
            "category": "카페", "mapx": "1270276000", "mapy": "374979000", **updates}


def test_search_request_headers_coordinates_and_radius(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [item(), item(title="먼 가게", mapx="1290000000")]})
    configure(monkeypatch, handler)
    places = asyncio.run(naver.search_places("카페", {
        "search_name": "강남역", "lat": 37.4979, "lng": 127.0276}, 1000))
    assert len(places) == 1
    assert places[0]["name"] == "가게 & 카페"
    assert places[0]["lat"] == 37.4979 and places[0]["lng"] == 127.0276
    assert places[0]["distance_m"] == 0
    request = requests[0]
    assert request.url.host == "naverapihub.apigw.ntruss.com"
    assert request.url.path == "/search/v1/local"
    assert request.headers["x-ncp-apigw-api-key-id"] == "search-id"
    assert request.headers["x-ncp-apigw-api-key"] == "search-secret"
    assert dict(request.url.params) == {"query": "강남역 카페", "display": "5", "start": "1",
                                        "sort": "random", "format": "json"}


@pytest.mark.parametrize("response", [
    httpx.Response(403, text="search-secret maps-secret"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"error": "search-secret"}),
    httpx.Response(200, json={"items": None}),
    httpx.Response(200, json={"items": [None]}),
    httpx.Response(200, json={"items": [item(mapx="invalid")]}),
    httpx.Response(200, json={"items": [item(mapy="9999999999")]}),
])
def test_search_failures_are_controlled_and_hide_keys(monkeypatch, response):
    configure(monkeypatch, lambda r: response)
    with pytest.raises(ApiError) as error:
        asyncio.run(naver.local_search("카페"))
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "MAP_ERROR"
    assert "secret" not in str(error.value.detail)


def test_search_timeout_is_controlled(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("secret upstream detail", request=request)
    configure(monkeypatch, handler)
    with pytest.raises(ApiError) as error:
        asyncio.run(naver.local_search("카페"))
    assert "secret" not in str(error.value.detail)
    assert "Timeout" in error.value.detail["message"]


def test_maps_http_failure_is_visible_in_smoke_but_optional_in_recommendation(monkeypatch, capsys):
    configure(monkeypatch, lambda r: httpx.Response(401, text="maps-secret private error body"))
    assert asyncio.run(run_checks(search=False, maps=True)) == 1
    output = capsys.readouterr().out
    assert output.count("HTTP 401") == 3
    assert "maps-secret" not in output and "private error body" not in output
    assert asyncio.run(naver.driving({"lat": 37.5, "lng": 127.0}, {"lat": 37.6, "lng": 127.1})) is None


def test_network_error_does_not_look_like_authentication_failure(monkeypatch, capsys):
    def handler(request):
        raise httpx.ConnectError("maps-secret private detail", request=request)
    configure(monkeypatch, handler)
    assert asyncio.run(run_checks(search=False, maps=True)) == 1
    output = capsys.readouterr().out
    assert output.count("ConnectError") == 3
    assert "maps-secret" not in output and "private detail" not in output
    assert "인증" not in output


def test_exported_environment_conflict_warns_without_values(monkeypatch, capsys, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("NCP_MAPS_KEY=file-secret\n")
    monkeypatch.setenv("NCP_MAPS_KEY", "exported-secret")
    warn_env_conflicts(["NCP_MAPS_KEY"], env_path=env_path)
    output = capsys.readouterr().out
    assert "exported environment overrides .env: NCP_MAPS_KEY" in output
    assert "unset NCP_MAPS_KEY" in output
    assert "file-secret" not in output and "exported-secret" not in output


def test_geocode_falls_back_to_maps_when_search_has_no_results(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/search/v1/local":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"status": "OK", "addresses": [{"y": "37.4979", "x": "127.0276"}]})
    configure(monkeypatch, handler)
    assert asyncio.run(naver.geocode("서울 샘플로 1")) == {
        "text": "서울 샘플로 1", "lat": 37.4979, "lng": 127.0276}
    assert len(requests) == 2
    assert requests[1].url.path == "/map-geocode/v2/geocode"
    assert requests[1].headers["x-ncp-apigw-api-key-id"] == "maps-id"
    assert requests[1].headers["x-ncp-apigw-api-key"] == "maps-secret"
    assert requests[1].headers["accept"] == "application/json"


def test_maps_request_coordinates_and_units(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/map-reversegeocode/v2/gc":
            return httpx.Response(200, json={"status": {"code": 0}, "results": [{"region": {
                "area2": {"name": "강남구"}, "area3": {"name": "역삼동"}}}]})
        return httpx.Response(200, json={"code": 0, "route": {"trafast": [{"summary": {
            "distance": 12345, "duration": 900000}}]}})
    configure(monkeypatch, handler)
    assert asyncio.run(naver.area_name(37.4979, 127.0276)) == "강남구 역삼동"
    assert requests[0].url.params["coords"] == "127.0276,37.4979"
    assert asyncio.run(naver.driving({"lat": 37.4979, "lng": 127.0276},
                                     {"lat": 37.5133, "lng": 127.1001})) == {"distance_km": 12.3, "minutes": 15}
    assert requests[1].url.path == "/map-direction/v1/driving"
    assert dict(requests[1].url.params) == {"start": "127.0276,37.4979", "goal": "127.1001,37.5133", "option": "trafast"}


@pytest.mark.parametrize("operation, payload", [
    (lambda: naver.address_geocode("주소"), {"status": "SYSTEM_ERROR", "addresses": []}),
    (lambda: naver.address_geocode("주소"), {"status": "OK", "addresses": [{"x": "invalid", "y": "37.5"}]}),
    (lambda: naver.area_name(37.5, 127.0), {"status": {"code": 1}, "results": []}),
    (lambda: naver.area_name(37.5, 127.0), {"status": {"code": 0}, "results": [None]}),
])
def test_bad_maps_payload_is_controlled(monkeypatch, operation, payload):
    configure(monkeypatch, lambda r: httpx.Response(200, json=payload))
    with pytest.raises(ApiError) as error:
        asyncio.run(operation())
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "MAP_ERROR"


@pytest.mark.parametrize("data", [{"code": 1}, {"code": 0, "route": None},
                                  {"code": 0, "route": {"trafast": [{"summary": {"distance": "NaN", "duration": 1}}]}}])
def test_bad_route_is_unavailable_instead_of_failing_recommendation(monkeypatch, data):
    configure(monkeypatch, lambda r: httpx.Response(200, json=data))
    assert asyncio.run(naver.driving({"lat": 37.5, "lng": 127.0}, {"lat": 37.6, "lng": 127.1})) is None


def test_smoke_calls_all_four_real_paths_even_with_mock_setting(monkeypatch, capsys):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        payload = {
            "/search/v1/local": {"items": [item()]},
            "/map-geocode/v2/geocode": {"status": "OK", "addresses": [{"x": "127.0", "y": "37.5"}]},
            "/map-reversegeocode/v2/gc": {"status": {"code": 0}, "results": [{"region": {"area2": {"name": "강남구"}}}]},
            "/map-direction/v1/driving": {"code": 0, "route": {"trafast": [{"summary": {"distance": 1000, "duration": 60000}}]}},
        }
        return httpx.Response(200, json=payload[request.url.path])
    configure(monkeypatch, handler)
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "mock")
    assert asyncio.run(run_checks(search=True, maps=True)) == 0
    assert len(paths) == 4
    assert settings.MAPS_PROVIDER == "mock"
    assert capsys.readouterr().out.count(": OK") == 4


def test_missing_key_does_not_make_requests(monkeypatch, capsys):
    def handler(request):
        pytest.fail("Missing credentials must prevent external requests")
    configure(monkeypatch, handler)
    monkeypatch.setattr(settings, "NCP_MAPS_KEY", "")
    assert asyncio.run(run_checks(search=True, maps=True)) == 1
    assert capsys.readouterr().out.strip() == "Missing configuration: NCP_MAPS_KEY"
