from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models import Meeting, RecommendationRun


def day(offset):
    return (datetime.now(ZoneInfo("Asia/Seoul")).date() + timedelta(days=offset)).isoformat()


def create(client, size=2):
    payload = dict(title="만나자쿠테스트", host_name="방장", expected_member_count=size)
    response = client.post("/api/meetings", json=payload)
    assert response.status_code == 201, response.text
    host = response.json()
    path = "/api/meetings/" + host["meeting"]["id"]
    people = [host]
    for index in range(1, size):
        response = client.post(path + "/members", json={"name": f"참여자{index}", "invite_code": host["meeting"]["invite_code"]})
        assert response.status_code == 201
        people.append(response.json())
    return path, people


def headers(person):
    return {"Authorization": "Bearer " + person["access_token"]}


def submit(client, path, person, availability):
    return client.put(path + "/members/me/submission", headers=headers(person), json={
        "availability": availability, "start_location": {"text": "강남역", "lat": 37.4979, "lng": 127.0276},
        "desired_text": "조용한 카페", "avoided_text": "", "conditions": [],
    })


def test_minimal_create_does_not_lock_a_calendar_window(client, database):
    path, people = create(client)
    meeting = people[0]["meeting"]
    assert meeting["date_selection_mode"] == "free" and meeting["candidate_dates"] == []
    assert meeting["time_range"] == {"start": "06:00", "end": "24:00"}
    assert meeting["slot_minutes"] == 30
    with database() as db:
        assert db.get(Meeting, path.rsplit("/", 1)[1]).candidate_dates == []


def test_swagger_example_matches_group_creation_screen(client):
    spec = app.openapi()
    media = spec["paths"]["/api/meetings"]["post"]["requestBody"]["content"]["application/json"]
    payload = media["examples"]["group_screen"]["value"]
    assert set(payload) == {"title", "host_name", "expected_member_count"}
    assert "candidate_dates" not in spec["components"]["schemas"]["CreateMeetingReq"]["required"]
    assert client.post("/api/meetings", json=payload).status_code == 201


def test_independent_months_later_dates_and_half_hour_overlap(client, database):
    path, people = create(client)
    common, first_only, second_only = day(45), day(60), day(90)
    first = submit(client, path, people[0], {common: ["09:00", "09:30"], first_only: ["18:30"]})
    assert first.status_code == 200 and first.json()["status"] == "waiting"
    assert first.json()["candidate_dates"] == sorted([common, first_only])
    last = submit(client, path, people[1], {common: ["09:30", "10:00"], second_only: ["06:00"]})
    assert last.status_code == 200, last.text
    meeting = last.json()
    assert meeting["status"] == "recommended" and meeting["recommendations"]
    assert meeting["date_selection_mode"] == "free"
    assert meeting["candidate_dates"] == sorted([common, first_only, second_only])
    assert {k: meeting["confirmed"][k] for k in ("date", "start", "end")} == {
        "date": common, "start": "09:30", "end": "10:00"}
    schedule = client.get(path + "/schedule?min_hours=0.5", headers=headers(people[0])).json()
    assert schedule["heatmap"][common]["09:30"] == 2
    with database() as db:
        assert db.get(Meeting, path.rsplit("/", 1)[1]).candidate_dates == []


def test_date_union_tracks_replacements_and_schedule_conflicts(client):
    path, people = create(client)
    a, b, c = day(30), day(31), day(32)
    assert submit(client, path, people[0], {a: ["09:30"]}).status_code == 200
    replaced = submit(client, path, people[0], {b: ["09:30"]}).json()
    assert replaced["candidate_dates"] == [b] and replaced["progress"]["submitted_member_count"] == 1
    conflict = submit(client, path, people[1], {c: ["09:30"]}).json()
    assert conflict["status"] == "schedule_conflict"
    assert conflict["candidate_dates"] == [b, c]
    done = submit(client, path, people[0], {c: ["09:30"]}).json()
    assert done["status"] == "recommended" and done["candidate_dates"] == [c]


@pytest.mark.parametrize("invalid", ["2026", "2026-02-30", "2000-01-01"])
def test_bad_or_past_free_dates_do_not_save_a_submission(client, invalid):
    path, people = create(client)
    response = submit(client, path, people[0], {invalid: ["09:00"]})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "INVALID_DATE"
    meeting = client.get(path, headers=headers(people[0])).json()
    assert meeting["candidate_dates"] == [] and meeting["progress"]["submitted_member_count"] == 0


@pytest.mark.parametrize("slot", ["24:00", "05:30", "09:15", "6:00"])
def test_only_painted_grid_slot_starts_are_accepted(client, slot):
    path, people = create(client)
    response = submit(client, path, people[0], {day(30): [slot]})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "INVALID_SLOT"


@pytest.mark.parametrize("slots, start, end", [(["06:00"], "06:00", "06:30"),
                                              (["23:00", "23:30"], "23:00", "24:00")])
def test_grid_boundaries_and_midnight_end(client, slots, start, end):
    path, people = create(client, 1)
    result = submit(client, path, people[0], {day(30): slots})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "recommended"
    assert result.json()["confirmed"]["start"] == start and result.json()["confirmed"]["end"] == end


def test_fixed_date_meetings_keep_their_explicit_restrictions(client):
    from test_meetings import create_group
    path, people = create_group(client, 2)
    assert people[0]["meeting"]["date_selection_mode"] == "fixed"
    response = submit(client, path, people[0], {day(365): ["10:00"]})
    assert response.status_code == 400 and response.json()["detail"]["code"] == "INVALID_DATE"


@pytest.mark.parametrize("dates", [["2026"], ["2026-02-30"]])
def test_invalid_explicit_creation_date_is_still_rejected(client, dates):
    response = client.post("/api/meetings", json={"expected_member_count": 2, "candidate_dates": dates})
    assert response.status_code == 400


def test_selected_date_preview_does_not_save_or_limit_later_submission(client):
    path, people = create(client)
    endpoint = path + "/availability/parse"
    missing = client.post(endpoint, headers=headers(people[0]), json={"text": "저녁 가능"})
    assert missing.status_code == 400 and missing.json()["detail"]["code"] == "DATES_REQUIRED"
    preview = client.post(endpoint, headers=headers(people[0]), json={"text": "저녁 가능", "selected_dates": [day(40)]})
    assert preview.status_code == 200 and list(preview.json()["availability"]) == [day(40)]
    assert preview.json()["availability"][day(40)][-1] == "23:30"
    unchanged = client.get(path, headers=headers(people[0])).json()
    assert unchanged["candidate_dates"] == [] and unchanged["progress"]["submitted_member_count"] == 0
    assert submit(client, path, people[0], {day(50): ["09:30"]}).status_code == 200


def test_concurrent_free_date_submissions_keep_one_result_and_complete_union(client, database):
    path, people = create(client, 3)
    common = day(40)
    assert submit(client, path, people[0], {common: ["18:00", "18:30"]}).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, client, path, person, {common: ["18:00", "18:30"], day(50+i): ["09:30"]})
                   for i, person in enumerate(people[1:])]
        assert all(f.result().status_code == 200 for f in futures)
    meeting = client.get(path, headers=headers(people[0])).json()
    assert meeting["status"] == "recommended" and meeting["progress"]["submitted_member_count"] == 3
    assert meeting["candidate_dates"] == sorted([common, day(50), day(51)])
    with database() as db:
        assert db.scalar(select(func.count()).select_from(RecommendationRun)) == 1
