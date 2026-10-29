from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import store
from app.errors import ApiError
from app.models import Meeting, RecommendationRun
from app.services import submission_service

DAY = "2026-11-01"


def create_group(client, size, joined=None):
    response = client.post("/api/meetings", json={
        "expected_member_count": size, "host_name": "방장",
        "candidate_dates": [DAY], "time_range": {"start": "10:00", "end": "14:00"},
    })
    assert response.status_code == 201, response.text
    host = response.json()
    url = "/api/meetings/" + host["meeting"]["id"]
    people = [host]
    for i in range(1, size if joined is None else joined):
        res = client.post(url + "/members", json={"name": f"참여자{i}", "invite_code": host["meeting"]["invite_code"]})
        assert res.status_code == 201, res.text
        people.append(res.json())
    return url, people


def headers(person):
    return {"Authorization": "Bearer " + person["access_token"]}


def body(**updates):
    return {"availability": {DAY: ["10:00", "11:00", "12:00"]},
            "start_location_text": "강남역", "desired_text": "조용한 파스타집, 2만원",
            "avoided_text": "회", **updates}


def submit(client, url, person, payload=None):
    return client.put(url + "/members/me/submission", headers=headers(person), json=payload or body())


@pytest.mark.parametrize("size", [1, 2, 4, 7, 10])
def test_dynamic_group_waits_for_every_distinct_member(client, database, size):
    url, people = create_group(client, size)
    for index, person in enumerate(people):
        res = submit(client, url, person)
        assert res.status_code == 200, res.text
        data = res.json()
        progress = data["progress"]
        assert progress["expected_member_count"] == size
        assert progress["submitted_member_count"] == index + 1
        assert progress["remaining_member_count"] == size - index - 1
        if index + 1 < size:
            assert data["status"] == "waiting"
            assert data["recommendations"] == []
            assert data["confirmed"] is None
        else:
            assert data["status"] == "recommended"
            assert progress["all_submitted"] is True
            assert len(data["confirmed"]["attending_member_ids"]) == size
            assert data["confirmed"]["missing_member_ids"] == []
            assert data["recommendations"]
            assert len(data["recommendations"][0]["travel"]) == size
    with database() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationRun)) == 1
    # Every participant reads the same saved result.
    for person in people:
        saved = client.get(url, headers=headers(person)).json()
        assert saved["recommendations"] == data["recommendations"]


def test_count_is_explicit_and_validated(client):
    assert client.post("/api/meetings", json={}).status_code == 400
    for count in [0, -1, 11]:
        assert client.post("/api/meetings", json={"expected_member_count": count}).status_code == 400


def test_unjoined_members_are_still_counted_as_waiting(client):
    url, people = create_group(client, 5, joined=1)
    result = submit(client, url, people[0]).json()
    assert result["progress"]["joined_member_count"] == 1
    assert result["progress"]["submitted_member_count"] == 1
    assert result["progress"]["remaining_member_count"] == 4
    assert result["status"] == "waiting"
    assert client.post(url + "/recommend", headers=headers(people[0])).status_code == 409


def test_capacity_uses_configured_size_and_duplicate_names_rejected(client):
    url, people = create_group(client, 2, joined=1)
    invite = people[0]["meeting"]["invite_code"]
    assert client.post(url + "/members", json={"name": "방장", "invite_code": invite}).status_code == 409
    assert client.post(url + "/members", json={"name": "다음", "invite_code": invite}).status_code == 201
    assert client.post(url + "/members", json={"name": "초과", "invite_code": invite}).json()["detail"]["code"] == "MEETING_FULL"


def test_duplicate_submission_and_retry_do_not_recount_or_rebill(client, database):
    url, people = create_group(client, 2)
    first = submit(client, url, people[0]).json()
    again = submit(client, url, people[0]).json()
    assert again["revision"] == first["revision"]
    assert again["progress"]["submitted_member_count"] == 1
    done = submit(client, url, people[1]).json()
    assert submit(client, url, people[1]).json()["revision"] == done["revision"]
    assert client.post(url + "/recommend", headers=headers(people[0])).json()["revision"] == done["revision"]
    with database() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationRun)) == 1


def test_processing_status_is_visible_before_external_call_finishes(client, database, monkeypatch):
    url, people = create_group(client, 2)
    submit(client, url, people[0])
    original = submission_service.recommend_service.recommend

    async def observe(*args, **kwargs):
        with database() as db:
            saved = db.get(Meeting, people[0]["meeting"]["id"])
            assert saved.status == "processing"
            assert saved.recommendation_result is None
            assert store.progress(saved)["all_submitted"] is True
        return await original(*args, **kwargs)

    monkeypatch.setattr(submission_service.recommend_service, "recommend", observe)
    assert submit(client, url, people[1]).json()["status"] == "recommended"


def test_no_common_time_never_calls_place_recommendation(client, monkeypatch):
    url, people = create_group(client, 2)
    submit(client, url, people[0], body(availability={DAY: ["10:00"]}))
    calls = []

    async def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError("no common time must not call maps or AI")

    monkeypatch.setattr(submission_service.recommend_service, "recommend", unexpected)
    result = submit(client, url, people[1], body(availability={DAY: ["12:00"]})).json()
    assert result["status"] == "schedule_conflict"
    assert result["processing_error"]["code"] == "NO_COMMON_SCHEDULE"
    assert result["progress"]["all_submitted"] is True
    assert result["recommendations"] == []
    assert calls == []


def test_empty_availability_is_a_submission_but_has_no_common_time(client):
    url, people = create_group(client, 1)
    result = submit(client, url, people[0], body(availability={})).json()
    assert result["progress"]["submitted_member_count"] == 1
    assert result["status"] == "schedule_conflict"


def test_invalid_form_or_unresolved_origin_is_not_partially_saved(client, database, monkeypatch):
    url, people = create_group(client, 1)
    for payload in [body(availability={DAY: ["25:00"]}), body(start_location_text=""),
                    body(start_location={"text": "x", "lat": 999, "lng": 127})]:
        assert submit(client, url, people[0], payload).status_code == 400
    async def missing(text):
        return None
    monkeypatch.setattr(submission_service.preference_service.naver, "geocode", missing)
    assert submit(client, url, people[0]).json()["detail"]["code"] == "LOCATION_NOT_FOUND"
    with database() as db:
        member = db.get(Meeting, people[0]["meeting"]["id"]).members[0]
        assert member.submitted_at is None
        assert member.preference is None
        assert member.availability == {}


def test_edit_reprocesses_without_double_counting_and_preserves_source_conditions(client, database):
    url, people = create_group(client, 2)
    submit(client, url, people[0])
    previous = submit(client, url, people[1]).json()
    result = submit(client, url, people[0], body(desired_text="피자", avoided_text="시끄러운 곳")).json()
    assert result["status"] == "recommended"
    assert result["progress"]["submitted_member_count"] == 2
    pref = next(m["preference"] for m in result["members"] if m["is_host"])
    assert pref["desired_text"] == "피자"
    assert pref["avoided_text"] == "시끄러운 곳"
    assert result["revision"] > previous["revision"]
    with database() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationRun)) == 2


def test_external_failure_keeps_forms_and_allows_any_participant_to_retry(client, monkeypatch):
    url, people = create_group(client, 2)
    submit(client, url, people[0])
    original = submission_service.recommend_service.recommend
    async def unavailable(*args, **kwargs):
        raise ApiError(502, "MAP_ERROR", "지도 호출 실패")
    monkeypatch.setattr(submission_service.recommend_service, "recommend", unavailable)
    failed = submit(client, url, people[1]).json()
    assert failed["status"] == "failed"
    assert failed["processing_error"]["code"] == "MAP_ERROR"
    assert failed["progress"]["submitted_member_count"] == 2
    monkeypatch.setattr(submission_service.recommend_service, "recommend", original)
    retry = client.post(url + "/recommend", headers=headers(people[1]))
    assert retry.status_code == 200
    assert retry.json()["status"] == "recommended"


def test_stale_processing_cannot_overwrite_new_input(client, database, monkeypatch):
    url, people = create_group(client, 1)
    original = submission_service.recommend_service.recommend
    async def delayed(*args, **kwargs):
        with database() as db:
            meeting = db.get(Meeting, people[0]["meeting"]["id"])
            meeting.members[0].submitted_at = None
            store.invalidate(meeting)
            store.commit(db, meeting)
        return await original(*args, **kwargs)
    monkeypatch.setattr(submission_service.recommend_service, "recommend", delayed)
    response = submit(client, url, people[0])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "waiting"
    assert response.json()["recommendations"] == []
    with database() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationRun)) == 0


def test_retry_does_not_duplicate_active_processing_and_can_reclaim_timeout(client, database):
    url, people = create_group(client, 1)
    submit(client, url, people[0])
    with database() as db:
        meeting = db.get(Meeting, people[0]["meeting"]["id"])
        store.invalidate(meeting)
        meeting.status = "processing"
        meeting.processing_started_at = datetime.now(timezone.utc)
        store.commit(db, meeting)
    assert client.post(url + "/recommend", headers=headers(people[0])).json()["status"] == "processing"
    with database() as db:
        meeting = db.get(Meeting, people[0]["meeting"]["id"])
        meeting.processing_started_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        store.commit(db, meeting)
    assert client.post(url + "/recommend", headers=headers(people[0])).json()["status"] == "recommended"


def test_isolation_and_final_place_permissions(client):
    url, people = create_group(client, 2)
    _, other = create_group(client, 1)
    assert client.get(url).status_code == 401
    assert submit(client, url, other[0]).status_code == 401
    for person in people:
        final = submit(client, url, person).json()
    pick = {"place_id": final["recommendations"][0]["place"]["id"], "revision": final["revision"]}
    assert client.post(url + "/place/confirm", headers=headers(people[1]), json=pick).status_code == 403
    assert client.post(url + "/place/confirm", headers=headers(people[0]), json={**pick, "place_id": "fake"}).status_code == 400
    res = client.post(url + "/place/confirm", headers=headers(people[0]), json=pick)
    assert res.status_code == 200
    assert res.json()["status"] == "finalized"
    guest_view = client.get(url, headers=headers(people[1])).json()
    assert "invite_code" not in guest_view
    assert "token_digest" not in str(guest_view)


def test_retired_manual_or_partial_routes_cannot_bypass_gate(client):
    url, people = create_group(client, 3, joined=1)
    hh = headers(people[0])
    assert client.post(url + "/schedule/confirm", headers=hh, json={}).status_code == 404
    assert client.put(url + "/members/me/availability", headers=hh, json={}).status_code == 404
    assert client.put(url + "/members/me/preference", headers=hh, json={}).status_code == 404
    assert client.post("/api/recommend/quick", json={}).status_code == 404
    assert client.post(url + "/recommend", headers=hh).json()["detail"]["code"] == "SUBMISSIONS_INCOMPLETE"


def test_concurrent_final_submissions_create_one_recommendation(client, database):
    import asyncio
    import httpx
    from app.main import app
    url, people = create_group(client, 3)
    submit(client, url, people[0])

    async def finish_together():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as concurrent:
            return await asyncio.gather(*(
                concurrent.put(url + "/members/me/submission", headers=headers(person), json=body())
                for person in people[1:]
            ))

    results = asyncio.run(finish_together())
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert client.get(url, headers=headers(people[0])).json()["status"] == "recommended"
    with database() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationRun)) == 1
