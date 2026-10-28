from app import store
from app.errors import ApiError
from app.models import Meeting, RecommendationRun
from sqlalchemy import select, func
import pytest


def populate(client, group):
    for headers, place in [(group["hh"], "강남역"), (group["gh"], "사당역")]:
        res = client.put(group["url"] + "/members/me/availability", headers=headers,
                         json={"availability": {"2026-10-28": ["10:00", "11:00", "12:00"]}})
        assert res.status_code == 200, res.text
        res = client.put(group["url"] + "/members/me/preference", headers=headers,
                         json={"text": f"{place}에서 출발. 파스타 좋아. 2만원"})
        assert res.status_code == 200, res.text


def confirm(client, group, **overrides):
    return client.post(group["url"] + "/schedule/confirm", headers=group["hh"],
                       json={"date": "2026-10-28", "start": "10:00", "end": "13:00", **overrides})


def recommend(client, group):
    return client.post(group["url"] + "/recommend", headers=group["hh"], json={"count": 3})


def test_complete_flow_persists_across_sessions(client, group, database):
    populate(client, group)
    best = client.get(group["url"] + "/schedule", headers=group["hh"]).json()["best"][0]
    assert best["available_count"] == 2
    assert recommend(client, group).json()["detail"]["code"] == "SCHEDULE_REQUIRED"
    assert confirm(client, group).json()["status"] == "scheduled"
    response = recommend(client, group)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["schedule"]["start"] == "10:00"
    assert result["recommendations"]
    assert len(result["recommendations"][0]["travel"]) == 2
    assert all(r["warnings"] for r in result["recommendations"])
    selected = result["recommendations"][0]["place"]["id"]
    response = client.post(group["url"] + "/place/confirm", headers=group["hh"],
                           json={"place_id": selected, "revision": result["revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "finalized"
    with database() as session:
        meeting = session.get(Meeting, group["id"])
        assert meeting.confirmed_place["id"] == selected
        assert session.scalar(select(func.count()).select_from(RecommendationRun)) == 1
        assert all(len(m.token_digest) == 64 for m in meeting.members)
        assert group["host"]["access_token"] not in str(meeting.members[0].__dict__)


def test_meeting_isolation_and_host_permissions(client, group):
    other = client.post("/api/meetings", json={}).json()
    assert client.get(group["url"]).status_code == 401
    assert client.get(group["url"], headers={
        "Authorization": f"Bearer {other['access_token']}"
    }).status_code == 401
    snapshot = client.get(group["url"], headers=group["gh"]).json()
    assert "invite_code" not in snapshot
    assert "token_digest" not in str(snapshot)
    for endpoint, body in [
        ("/schedule/confirm", {"date": "2026-10-28", "start": "10:00", "end": "12:00"}),
        ("/recommend", {}),
        ("/place/confirm", {"place_id": "x", "revision": 1}),
    ]:
        assert client.post(group["url"] + endpoint, headers=group["gh"], json=body).status_code == 403
    assert client.post(group["url"] + "/members", json={"name": "침입", "invite_code": "bad"}).status_code == 403
    assert client.post(group["url"] + "/members", json={
        "name": "참여자", "invite_code": group["host"]["meeting"]["invite_code"]
    }).status_code == 409
    # Old name-based mutation is rejected, not silently used as an identity.
    assert client.put(group["url"] + "/members/me/availability", headers=group["hh"],
                      json={"name": "참여자", "availability": {}}).status_code == 400


@pytest.mark.parametrize("body", [
    {"start": "25:99"}, {"start": "9:00"}, {"end": "09:00"},
    {"start": "10:30"}, {"end": "14:30"}, {"date": "2026-10-29"},
])
def test_invalid_confirmation(client, group, body):
    populate(client, group)
    assert confirm(client, group, **body).status_code == 400
    assert client.get(group["url"], headers=group["hh"]).json()["confirmed"] is None


def test_partial_attendance_is_explicit_and_excludes_absent_members(client, group):
    client.put(group["url"] + "/members/me/availability", headers=group["hh"],
               json={"availability": {"2026-10-28": ["10:00", "11:00", "12:00"]}})
    assert confirm(client, group).status_code == 409
    response = confirm(client, group, allow_partial=True)
    assert response.status_code == 200
    assert response.json()["confirmed"]["attending_member_ids"] == [group["host"]["member_id"]]
    client.put(group["url"] + "/members/me/preference", headers=group["hh"],
               json={"text": "강남역에서 출발. 파스타"})
    response = recommend(client, group)
    assert response.status_code == 200, response.text
    assert len(response.json()["recommendations"][0]["travel"]) == 1


def test_input_edits_invalidate_derived_results(client, group):
    populate(client, group)
    confirm(client, group)
    result = recommend(client, group).json()
    client.put(group["url"] + "/members/me/preference", headers=group["gh"],
               json={"text": "사당역에서 출발. 피자"})
    snapshot = client.get(group["url"], headers=group["hh"]).json()
    assert snapshot["status"] == "scheduled"
    assert snapshot["confirmed"] is not None
    assert snapshot["recommendations"] == []
    assert client.post(group["url"] + "/place/confirm", headers=group["hh"], json={
        "place_id": result["recommendations"][0]["place"]["id"], "revision": result["revision"]
    }).status_code == 409
    client.put(group["url"] + "/members/me/availability", headers=group["gh"],
               json={"availability": {}})
    snapshot = client.get(group["url"], headers=group["hh"]).json()
    assert snapshot["status"] == "collecting"
    assert snapshot["confirmed"] is None
    assert snapshot["confirmed_place"] is None
    schedule = client.get(group["url"] + "/schedule", headers=group["hh"]).json()
    assert schedule["responded_members"] == 2


def test_new_member_invalidates_time_confirmation(client, group):
    populate(client, group)
    confirm(client, group)
    joined = client.post(group["url"] + "/members", json={
        "name": "새참여자", "invite_code": group["host"]["meeting"]["invite_code"]
    })
    assert joined.status_code == 201
    assert joined.json()["meeting"]["confirmed"] is None


def test_all_attendees_need_preferences_and_locations(client, group):
    for headers in [group["hh"], group["gh"]]:
        client.put(group["url"] + "/members/me/availability", headers=headers,
                   json={"availability": {"2026-10-28": ["10:00", "11:00", "12:00"]}})
    confirm(client, group)
    assert recommend(client, group).json()["detail"]["code"] == "PREFERENCES_INCOMPLETE"
    for headers in [group["hh"], group["gh"]]:
        client.put(group["url"] + "/members/me/preference", headers=headers, json={"text": "파스타"})
    assert recommend(client, group).json()["detail"]["code"] == "LOCATIONS_INCOMPLETE"


def test_schedule_minimum_duration_and_query_validation(client, group):
    for headers in [group["hh"], group["gh"]]:
        client.put(group["url"] + "/members/me/availability", headers=headers,
                   json={"availability": {"2026-10-28": ["10:00", "11:00"]}})
    res = client.get(group["url"] + "/schedule?min_hours=2.5", headers=group["hh"])
    assert res.status_code == 200
    assert res.json()["relaxed"] is True
    for query in ["min_hours=nan", "min_hours=inf", "min_hours=0", "top=-1", "top=51"]:
        assert client.get(group["url"] + "/schedule?" + query, headers=group["hh"]).status_code == 400


def test_old_endpoints_cannot_bypass_schedule(client):
    assert client.post("/api/recommend/quick", json={}).status_code == 404
    assert client.post("/api/meeting/demo").status_code == 404


def test_database_rejects_stale_writes(group, database):
    with database() as first, database() as second:
        a = first.get(Meeting, group["id"])
        b = second.get(Meeting, group["id"])
        a.title = "new"
        store.commit(first, a)
        b.title = "stale"
        with pytest.raises(ApiError) as error:
            store.commit(second, b)
        assert error.value.status_code == 409
    with database() as session:
        assert session.get(Meeting, group["id"]).title == "new"


def test_in_flight_recommendation_is_not_saved_after_edit(client, group, database, monkeypatch):
    from app.routers import meetings
    populate(client, group)
    confirm(client, group)

    async def delayed(*args, **kwargs):
        with database() as session:
            meeting = session.get(Meeting, group["id"])
            store.invalidate(meeting, schedule=True)
            store.commit(session, meeting)
        return {"recommendations": []}

    monkeypatch.setattr(meetings.recommend_service, "recommend", delayed)
    response = recommend(client, group)
    assert response.status_code == 409, response.text
    with database() as session:
        assert session.get(Meeting, group["id"]).confirmed is None
        assert session.scalar(select(func.count()).select_from(RecommendationRun)) == 0


def test_invite_code_rejects_non_ascii_without_server_error(client, group):
    response = client.post(group["url"] + "/members", json={"name": "새참여자", "invite_code": "잘못된코드"})
    assert response.status_code == 400


def test_invalid_place_is_not_finalized(client, group):
    populate(client, group)
    confirm(client, group)
    result = recommend(client, group).json()
    response = client.post(group["url"] + "/place/confirm", headers=group["hh"], json={
        "place_id": "invented-place", "revision": result["revision"],
    })
    assert response.status_code == 400
    assert client.get(group["url"], headers=group["hh"]).json()["confirmed_place"] is None


def test_ai_ranking_failure_uses_real_candidates(client, group, monkeypatch):
    from app.services import recommend_service
    populate(client, group)
    confirm(client, group)

    async def unavailable(*args, **kwargs):
        raise ApiError(502, "AI_ERROR", "unavailable")

    monkeypatch.setattr(recommend_service, "rank", unavailable)
    response = recommend(client, group)
    assert response.status_code == 200, response.text
    assert response.json()["fallback"] is True
    assert response.json()["recommendations"]


def test_map_failure_does_not_save_fabricated_recommendations(client, group, monkeypatch):
    from app.services import recommend_service
    populate(client, group)
    confirm(client, group)

    async def unavailable(*args, **kwargs):
        raise ApiError(502, "MAP_ERROR", "unavailable")

    monkeypatch.setattr(recommend_service.naver, "search_places", unavailable)
    response = recommend(client, group)
    assert response.status_code == 502
    meeting = client.get(group["url"], headers=group["hh"]).json()
    assert meeting["status"] == "scheduled"
    assert meeting["recommendations"] == []
