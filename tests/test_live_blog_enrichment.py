"""The automatic API-HUB path uses fake HTTP and AI; never production data or credits."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.clients import blog_client
from app.config import settings
from app.services import recommend_service
from app.services.blog_enrichment import collect_searches
from app.services.recommendation_pipeline import EvidencePolicy
from app.services.recommendation_rules import normalize


@pytest.fixture
def live_path(monkeypatch):
    data = json.loads((Path(__file__).parent / "fixtures/team_blog_input.json").read_text(encoding="utf-8-sig"))
    monkeypatch.setattr(settings, "ENABLE_BLOG_ENRICHMENT", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "chatkhu")
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "naver")
    monkeypatch.setattr(settings, "NAVER_CLIENT_ID", "test-hub-id")
    monkeypatch.setattr(settings, "NAVER_CLIENT_SECRET", "test-hub-secret")
    monkeypatch.setattr(settings, "NAVER_BLOG_URL", "https://naverapihub.apigw.ntruss.com/search/v1/blog")
    for name, value in dict(BLOG_MAX_CANDIDATES=5, BLOG_QUERIES_PER_CANDIDATE=2,
                            BLOG_RESULTS_PER_QUERY=5, BLOG_MAX_REQUESTS=10, BLOG_CONCURRENCY=3,
                            BLOG_TIMEOUT_SECONDS=5, BLOG_EXTRACTION_TIMEOUT_SECONDS=20, BLOG_MAX_AGE_DAYS=180).items():
        monkeypatch.setattr(settings, name, value)
    async def station(lat, lng):
        return dict(name="fixture", search_name="fixture", lat=lat, lng=lng)
    async def queries(*args):
        return ["카페"]
    async def places(*args):
        return [{**data["inputs"]["places"][0], "lat": 37, "lng": 127}]
    async def route(*args):
        return dict(minutes=12, distance_km=1)
    monkeypatch.setattr(recommend_service.naver, "nearest_station", station)
    monkeypatch.setattr(recommend_service, "make_queries", queries)
    monkeypatch.setattr(recommend_service.naver, "search_places", places)
    monkeypatch.setattr(recommend_service.naver, "driving", route)
    return data


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    def wrapped(request):
        assert request.url.path == "/search/v1/blog"
        return handler(request)
    monkeypatch.setattr(blog_client.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(wrapped), **kw))


def mock_extraction(monkeypatch, data):
    calls = []
    async def ai(system, user, schema, step):
        assert step == "blog_extract"
        payload = json.loads(user)
        calls.append(payload)
        return {"claims": [{**data["mock_claim"], "place_id": payload["place"]["id"],
                            "condition_id": payload["conditions"][0]["id"]}]}
    monkeypatch.setattr(recommend_service.ai, "structured", ai)
    return calls


def request_body(data, **changes):
    from test_meetings import body
    return body(start_location=dict(text="fixture", lat=37, lng=127),
                conditions=[{**data["condition_items"][0], **changes}])


@pytest.mark.parametrize("strength", ["desired", "required"])
def test_api_collects_extracts_scores_and_persists(client, live_path, monkeypatch, strength):
    from test_meetings import create_group, submit, headers
    data = live_path
    calls = mock_extraction(monkeypatch, data)
    requests = []
    doc = data["inputs"]["blog_searches"][0]["items"][0]
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [dict(title=doc["title"], description=doc["description"],
            link=doc["url"], postdate=datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d"))]})
    mock_http(monkeypatch, handler)
    url, people = create_group(client, 1)
    response = submit(client, url, people[0], request_body(data, strength=strength))
    assert response.status_code == 200, response.text
    result = response.json()["recommendation_result"]
    assert response.json()["status"] == "recommended"
    assert len(requests) == len(calls) == 1
    report = result["blog_enrichment"]
    assert report["connection_status"] == "live" and report["provider"] == "naver_api_hub"
    assert report["search_requests_made"] == report["external_requests_made"] == 1
    row = result["candidate_evaluations"][0]
    if strength == "desired":
        assert result["recommendations"] and row["raw_scores"]["P"] == 75
        assert row["estimated_condition_count"] == 1 and row["confirmed_condition_count"] == 0
    else:
        assert result["recommendations"] == [] and row["eligibility"] == "needs_verification"
        assert row["confirmed_condition_count"] == 0
    assert '"description":' not in json.dumps(result)
    latest = client.get(url, headers=headers(people[0])).json()["recommendation_result"]
    assert latest == result


@pytest.mark.parametrize("status, failure", [(403, "auth_error"), (429, "rate_limit"), (503, "upstream_error")])
def test_optional_search_failure_keeps_recommendation(client, live_path, monkeypatch, status, failure):
    from test_meetings import create_group, submit
    async def forbidden(*args, **kwargs):
        pytest.fail("Failed search must not invoke AI extraction")
    monkeypatch.setattr(recommend_service.ai, "structured", forbidden)
    mock_http(monkeypatch, lambda r: httpx.Response(status, text="test-hub-secret private error body"))
    url, people = create_group(client, 1)
    response = submit(client, url, people[0], request_body(live_path))
    assert response.status_code == 200 and response.json()["status"] == "recommended"
    result = response.json()["recommendation_result"]
    assert result["recommendations"][0]["raw_scores"]["P"] == 50
    assert result["blog_enrichment"]["failures"][0]["failure_code"] == failure
    assert "test-hub-secret" not in json.dumps(result) and "private error body" not in json.dumps(result)


@pytest.mark.parametrize("reason", ["disabled", "mock_maps", "mock_ai", "no_conditions", "supplied_empty"])
def test_no_accidental_external_calls(client, live_path, monkeypatch, reason):
    from test_meetings import create_group, submit
    def forbidden(*args, **kwargs):
        pytest.fail("This path must not create an external HTTP client")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    if reason == "disabled":
        monkeypatch.setattr(settings, "ENABLE_BLOG_ENRICHMENT", False)
    elif reason == "mock_maps":
        monkeypatch.setattr(settings, "MAPS_PROVIDER", "mock")
    elif reason == "mock_ai":
        monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    elif reason == "supplied_empty":
        original = recommend_service.recommend
        async def supplied(*args, **kwargs):
            return await original(*args, **kwargs, supplied_blog_searches=[])
        monkeypatch.setattr(recommend_service, "recommend", supplied)
    payload = request_body(live_path)
    if reason == "no_conditions":
        payload["conditions"] = []
    url, people = create_group(client, 1)
    response = submit(client, url, people[0], payload)
    assert response.status_code == 200 and response.json()["recommendations"]


def test_global_budget_concurrency_and_neutral_queries(live_path, monkeypatch):
    places = [dict(id=f"p{i}", name=f"식당{i} 강남점", address="서울 강남구 역삼동", distance_m=i) for i in range(7)]
    conditions = normalize([*live_path["condition_items"], dict(category="cost", target="price_per_person",
                           operator="lte", value=20000, unit="KRW", raw_text="2만원 이하")], "member")
    active = maximum = 0
    queries = []
    async def search(query, display):
        nonlocal active, maximum
        queries.append(query)
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.005)
        active -= 1
        return []
    monkeypatch.setattr(blog_client, "search_blogs", search)
    monkeypatch.setattr(settings, "BLOG_CONCURRENCY", 2)
    result = asyncio.run(collect_searches(places, conditions, EvidencePolicy(max_candidates=3, max_queries=4)))
    assert result.request_count == len(queries) == 4 and maximum == 2
    assert len({b.place_id for b in result.batches}) <= 3
    assert not any("조용한" in q for q in queries)
    assert all(b.status == "success" and b.data_origin == "live" for b in result.batches)


def test_duplicate_queries_share_one_request(live_path, monkeypatch):
    places = [dict(id=f"p{i}", name="같은식당 강남점", address="서울 강남구 역삼동") for i in range(2)]
    calls = []
    async def search(query, display):
        calls.append(query)
        return []
    monkeypatch.setattr(blog_client, "search_blogs", search)
    result = asyncio.run(collect_searches(places, normalize(live_path["condition_items"], "member"), EvidencePolicy()))
    assert result.request_count == len(calls) == 1
    assert {b.place_id for b in result.batches} == {"p0", "p1"}


def test_timeout_is_a_failed_batch(live_path, monkeypatch):
    async def slow(*args):
        await asyncio.sleep(1)
        return []
    monkeypatch.setattr(blog_client, "search_blogs", slow)
    monkeypatch.setattr(settings, "BLOG_TIMEOUT_SECONDS", .001)
    result = asyncio.run(collect_searches(live_path["inputs"]["places"],
                         normalize(live_path["condition_items"], "member"), EvidencePolicy()))
    assert result.request_count == 1
    assert result.batches[0].status == "timeout" and result.batches[0].failure_code == "timeout"
