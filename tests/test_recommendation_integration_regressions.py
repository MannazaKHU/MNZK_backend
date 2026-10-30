"""Production-path regressions from PR #1 review; no real API requests."""
import asyncio

import httpx
import pytest

from app.clients import naver_client as naver
from app.config import settings
from app.conditions import Condition
from app.errors import ApiError
from app.schemas import SubmissionReq
from app.services import preference_service
from app.services.recommend_service import make_queries
from app.services.recommendation_rules import score_candidates


def preference_payload(conditions):
    return dict(likes=[], dislikes=[], mood=[], budget_max=0, indoor="any",
                start_location_text="", conditions=conditions)


@pytest.mark.parametrize("conditions, expected", [
    ([dict(category="kind", target="카페", scope="place", raw_text="카페로 수정")], ["카페"]),
    ([], ["맛집"]),
])
def test_confirmed_conditions_replace_original_text_without_ai(monkeypatch, conditions, expected):
    async def forbidden(*args, **kwargs):
        pytest.fail("Confirmed conditions must not be reinterpreted by AI")
    monkeypatch.setattr(settings, "AI_PROVIDER", "chatkhu")
    monkeypatch.setattr(preference_service.ai, "structured", forbidden)
    req = SubmissionReq(availability={}, desired_text="파스타", avoided_text="",
                        start_location=dict(text="fixture", lat=37, lng=127), conditions=conditions)
    pref = asyncio.run(preference_service.build_submission_preference(req))
    # Query generation has a development/mock path independent of preference parsing.
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    merged = preference_service.merge_group([pref])
    assert asyncio.run(make_queries(merged, "")) == expected
    assert "파스타" not in merged["likes"]


def test_stored_conditions_remain_authoritative_over_legacy_fields():
    condition = Condition(category="kind", target="카페", scope="place", raw_text="카페").model_dump()
    pref = dict(likes=["파스타"], dislikes=["카페"], mood=[], budget_max=None, indoor="any",
                conditions=[condition])
    merged = preference_service.merge_group([pref])
    assert merged["likes"] == [dict(word="카페", count=1)]
    assert merged["dislikes"] == []
    assert merged["conditions"] == [condition]
    # Legacy records without structured conditions retain their old search fields.
    pref.pop("conditions")
    assert preference_service.merge_group([pref])["likes"] == [dict(word="파스타", count=1)]


def test_group_query_context_preserves_both_members_opinions():
    like = Condition(category="kind", target="짬뽕", scope="menu", raw_text="짬뽕 좋아").model_dump()
    avoid = {**like, "direction": "avoidance", "raw_text": "짬뽕 싫어"}
    result = preference_service.merge_group([dict(conditions=[like]), dict(conditions=[avoid])])
    assert result["likes"] == [dict(word="짬뽕", count=1)]
    assert result["dislikes"] == ["짬뽕"]
    assert len(result["conditions"]) == 2


@pytest.mark.parametrize("changes", [
    dict(operator="lte", value=20000, unit=None),
    dict(raw_text="원문에 없는 조건"),
    dict(raw_text="원하는 조건: 2만원 이하"),
])
def test_bad_ai_conditions_are_controlled_without_discarding_requirements(monkeypatch, changes):
    condition = Condition(category="cost", target="price_per_person", raw_text="2만원 이하",
                          operator="lte", value=20000, unit="KRW", strength="required").model_dump()
    condition.update(changes)
    async def structured(*args, **kwargs):
        return preference_payload([condition])
    monkeypatch.setattr(settings, "AI_PROVIDER", "chatkhu")
    monkeypatch.setattr(preference_service.ai, "structured", structured)
    req = SubmissionReq(availability={}, desired_text="2만원 이하", avoided_text="",
                        start_location=dict(text="fixture", lat=37, lng=127))
    with pytest.raises(ApiError) as error:
        asyncio.run(preference_service.build_submission_preference(req))
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_ERROR"


def test_invalid_ai_condition_does_not_mark_member_as_submitted(client, monkeypatch):
    from test_meetings import create_group, submit, body, headers
    url, people = create_group(client, 2)
    async def structured(*args, **kwargs):
        return preference_payload([dict(category="cost", target="price", raw_text="2만원 이하",
                                       value=20000, unit=None, operator="lte")])
    monkeypatch.setattr(settings, "AI_PROVIDER", "chatkhu")
    monkeypatch.setattr(preference_service.ai, "structured", structured)
    response = submit(client, url, people[0], body(desired_text="2만원 이하", avoided_text=""))
    assert response.status_code == 502
    latest = client.get(url, headers=headers(people[0])).json()
    assert latest["progress"]["submitted_member_count"] == 0
    assert latest["status"] == "waiting"


def test_directions_precision_changes_actual_ranking(monkeypatch):
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "naver")
    def handler(request):
        duration = 90600 if request.url.params["goal"].startswith("127.1,") else 149400
        return httpx.Response(200, json={"code": 0, "route": {"trafast": [
            {"summary": {"distance": 1000, "duration": duration}}]}})
    original = httpx.AsyncClient
    monkeypatch.setattr(naver.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    places = [dict(id="a", lat=37, lng=127.2), dict(id="b", lat=37, lng=127.1)]
    async def routes():
        return await asyncio.gather(*(naver.driving(dict(lat=37, lng=127), p) for p in places))
    results = asyncio.run(routes())
    assert [r["minutes"] for r in results] == pytest.approx([2.49, 1.51])
    ranked, _ = score_candidates([], places, {
        p["id"]: [dict(member_id="member", duration=r["minutes"], unit="minutes", mode="driving")]
        for p, r in zip(places, results)})
    assert [r["place_id"] for r in ranked] == ["b", "a"]
    assert ranked[0]["score"] > ranked[1]["score"]


@pytest.mark.parametrize("duration", [0, 30000, 90000])
def test_directions_does_not_round_or_floor_minutes(monkeypatch, duration):
    monkeypatch.setattr(settings, "MAPS_PROVIDER", "naver")
    async def maps(*args, **kwargs):
        return {"code": 0, "route": {"trafast": [{"summary": {"distance": 100, "duration": duration}}]}}
    monkeypatch.setattr(naver, "_ncp_get", maps)
    result = asyncio.run(naver.driving(dict(lat=37, lng=127), dict(lat=37.1, lng=127.1)))
    assert result["minutes"] == duration / 60000
