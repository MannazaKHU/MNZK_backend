"""Complete form submissions and automatic recommendation after N distinct members submit."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import store
from app.errors import ApiError
from app.models import Member, RecommendationRun
from app.services import preference_service, recommend_service, schedule_service

logger = logging.getLogger(__name__)
PROCESSING_TIMEOUT = timedelta(minutes=10)


def _view(factory, meeting_id, host):
    with factory() as db:
        return store.snapshot(store.get_meeting(db, meeting_id), host=host)


def _same_input(member, availability, req):
    return (member.submitted_at is not None and member.availability == availability
            and (member.preference or {}).get("submission_input") == req.model_dump(exclude={"availability"}))


def _fingerprint(member):
    return member.submitted_at, member.availability, member.preference


def _claim(meeting):
    meeting.status = "processing"
    meeting.processing_started_at = datetime.now(timezone.utc)
    meeting.processing_error = None


async def submit(factory, meeting_id, member_id, req, *, host=False):
    with factory() as db:
        meeting = store.get_meeting(db, meeting_id)
        member = next(m for m in meeting.members if m.id == member_id)
        availability = schedule_service.clean_availability(store.snapshot(meeting), req.availability)
        if _same_input(member, availability, req):
            return store.snapshot(meeting, host=host)
        previous = _fingerprint(member)

    # No DB transaction/connection is held during preference parsing or geocoding.
    preference = await preference_service.build_submission_preference(req)
    for attempt in range(store.MAX_MEMBERS):
        with factory() as db:
            meeting = store.get_meeting(db, meeting_id, lock=True)
            member = next(m for m in meeting.members if m.id == member_id)
            if _same_input(member, availability, req):
                return store.snapshot(meeting, host=host)
            if _fingerprint(member) != previous:
                raise ApiError(409, "SUBMISSION_CHANGED", "본인 응답이 다른 요청에서 수정됐어요. 최신 응답을 확인해 주세요.")
            member.availability = availability
            member.availability_submitted = True
            member.preference = preference
            member.submitted_at = datetime.now(timezone.utc)
            store.invalidate(meeting)
            ready = store.progress(meeting)["all_submitted"]
            if ready:
                _claim(meeting)
            try:
                store.commit(db, meeting)
            except ApiError as exc:
                if exc.detail["code"] == "MEETING_CHANGED" and attempt < store.MAX_MEMBERS - 1:
                    continue
                raise
            revision = meeting.revision
            break
    if ready:
        return await process(factory, meeting_id, revision, host=host)
    return _view(factory, meeting_id, host)


def _finish(factory, meeting_id, revision, *, host, status, schedule=None, result=None, error=None):
    with factory() as db:
        meeting = store.get_meeting(db, meeting_id, lock=True)
        if meeting.revision != revision or meeting.status != "processing":
            return store.snapshot(meeting, host=host)
        meeting.confirmed = schedule
        meeting.recommendation_result = result
        meeting.confirmed_place = None
        meeting.processing_error = error
        meeting.processing_started_at = None
        meeting.status = status
        if result is not None:
            db.add(RecommendationRun(meeting_id=meeting.id, input_revision=revision,
                                     request=meeting.recommendation_options, result=result))
        try:
            store.commit(db, meeting)
        except ApiError as exc:
            if exc.detail["code"] == "MEETING_CHANGED":
                return _view(factory, meeting_id, host)
            raise
        return store.snapshot(meeting, host=host)


async def process(factory, meeting_id, revision, *, host=False):
    with factory() as db:
        meeting = store.get_meeting(db, meeting_id)
        if meeting.revision != revision or meeting.status != "processing":
            return store.snapshot(meeting, host=host)
        snapshot = store.snapshot(meeting)
        options = dict(meeting.recommendation_options)
    # Only a time available to every expected participant may reach place recommendation.
    best = schedule_service.best_slots(snapshot, min_hours=snapshot["slot_minutes"] / 60, top=1)["best"]
    if not best or best[0]["available_count"] != snapshot["expected_member_count"]:
        return _finish(factory, meeting_id, revision, host=host, status="schedule_conflict", error={
            "code": "NO_COMMON_SCHEDULE", "message": "전원이 가능한 공통 시간이 없어요. 가능 일정을 수정해 주세요.",
        })
    schedule = {key: best[0][key] for key in ("date", "start", "end")}
    schedule.update(attending_member_ids=[m["id"] for m in snapshot["members"]], missing_member_ids=[])
    try:
        result = await recommend_service.recommend(
            [{"id": m["id"], "name": m["name"], "preference": m["preference"]} for m in snapshot["members"]],
            options["count"], options["radius_m"], options["extra_text"], schedule=schedule,
        )
    except asyncio.CancelledError:
        _finish(factory, meeting_id, revision, host=host, status="failed", schedule=schedule,
                error={"code": "PROCESSING_INTERRUPTED", "message": "처리가 중단됐어요. 추천을 다시 요청해 주세요."})
        raise
    except Exception as exc:
        error = exc.detail if isinstance(exc, ApiError) else {
            "code": "PROCESSING_FAILED", "message": "추천 처리에 실패했어요. 다시 시도해 주세요.",
        }
        logger.warning("Recommendation processing failed: %s", error["code"])
        return _finish(factory, meeting_id, revision, host=host, status="failed", schedule=schedule, error=error)
    return _finish(factory, meeting_id, revision, host=host, status="recommended", schedule=schedule, result=result)


async def retry(factory, meeting_id, *, host=False):
    with factory() as db:
        meeting = store.get_meeting(db, meeting_id, lock=True)
        if not store.progress(meeting)["all_submitted"]:
            raise ApiError(409, "SUBMISSIONS_INCOMPLETE", "설정한 인원 전원이 입력을 완료해야 추천할 수 있어요.")
        if meeting.status in {"recommended", "finalized"}:
            return store.snapshot(meeting, host=host)
        if meeting.status == "processing" and meeting.processing_started_at:
            started = meeting.processing_started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - started < PROCESSING_TIMEOUT:
                return store.snapshot(meeting, host=host)
        # A timed-out request can be reclaimed; its old revision can no longer save results.
        _claim(meeting)
        store.commit(db, meeting)
        revision = meeting.revision
    return await process(factory, meeting_id, revision, host=host)
