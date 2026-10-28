"""Meeting-scoped API: collect availability -> confirm time -> recommend -> confirm place."""
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import store
from app.database import get_session
from app.errors import ApiError
from app.models import Meeting, Member, RecommendationRun
from app.schemas import (AvailabilityReq, ConfirmPlaceReq, ConfirmReq, CreateMeetingReq,
                         JoinReq, PreferenceReq, RecommendReq, TextReq)
from app.services import preference_service, recommend_service, schedule_service

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
bearer = HTTPBearer(auto_error=False)


@dataclass
class Context:
    db: Session
    meeting: Meeting
    member: Member


def context(meeting_id: str, db: Annotated[Session, Depends(get_session)],
            credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "TOKEN_REQUIRED", "Authorization: Bearer 참여자토큰 헤더가 필요해요.")
    meeting, member = store.authenticate(db, meeting_id, credentials.credentials)
    return Context(db, meeting, member)


Ctx = Annotated[Context, Depends(context)]
DB = Annotated[Session, Depends(get_session)]


def view(ctx: Context):
    return store.snapshot(ctx.meeting, host=ctx.member.is_host)


@router.post("", status_code=201)
def create_meeting(req: CreateMeetingReq, db: DB):
    host, token = store.new_member(req.host_name, is_host=True)
    meeting = Meeting(title=req.title, invite_code=secrets.token_urlsafe(24),
                      candidate_dates=req.candidate_dates, time_range=req.time_range.model_dump(),
                      slot_minutes=req.slot_minutes, members=[host])
    db.add(meeting)
    db.commit()
    return {"meeting": store.snapshot(meeting, host=True), "member_id": host.id,
            "access_token": token, "token_type": "bearer"}


@router.post("/{meeting_id}/members", status_code=201)
def join_meeting(meeting_id: str, req: JoinReq, db: DB):
    meeting = store.get_meeting(db, meeting_id)
    if not secrets.compare_digest(meeting.invite_code, req.invite_code):
        raise ApiError(403, "INVALID_INVITE", "초대 코드가 올바르지 않아요.")
    if len(meeting.members) >= store.MAX_MEMBERS:
        raise ApiError(400, "MEETING_FULL", "인원이 가득 찼어요.")
    if any(m.name == req.name for m in meeting.members):
        raise ApiError(409, "MEMBER_CONFLICT", "이미 사용 중인 이름이에요.")
    member, token = store.new_member(req.name)
    meeting.members.append(member)
    store.invalidate(meeting, schedule=True)
    store.commit(db, meeting)
    return {"meeting": store.snapshot(meeting), "member_id": member.id,
            "access_token": token, "token_type": "bearer"}


@router.get("/{meeting_id}")
def get_meeting(ctx: Ctx):
    return view(ctx)


@router.put("/{meeting_id}/members/me/availability")
def save_availability(req: AvailabilityReq, ctx: Ctx):
    availability = schedule_service.clean_availability(view(ctx), req.availability)
    ctx.member.availability = availability
    ctx.member.availability_submitted = True
    store.invalidate(ctx.meeting, schedule=True)
    store.commit(ctx.db, ctx.meeting)
    return view(ctx)


@router.post("/{meeting_id}/availability/parse")
async def parse_availability(req: TextReq, ctx: Ctx):
    return await schedule_service.parse_availability_text(view(ctx), req.text)


@router.get("/{meeting_id}/schedule")
def get_schedule(ctx: Ctx,
                 min_hours: Annotated[float, Query(gt=0, le=24, allow_inf_nan=False)] = 2,
                 top: Annotated[int, Query(ge=1, le=50)] = 5):
    return schedule_service.best_slots(view(ctx), min_hours=min_hours, top=top)


@router.post("/{meeting_id}/schedule/confirm")
def confirm_schedule(req: ConfirmReq, ctx: Ctx):
    store.require_host(ctx.member)
    confirmed = schedule_service.validate_confirmation(view(ctx), req)
    store.invalidate(ctx.meeting, schedule=True)
    ctx.meeting.confirmed = confirmed
    ctx.meeting.status = "scheduled"
    store.commit(ctx.db, ctx.meeting)
    return view(ctx)


@router.put("/{meeting_id}/members/me/preference")
async def save_preference(req: PreferenceReq, ctx: Ctx):
    loc = req.start_location.model_dump() if req.start_location else None
    pref, found = await preference_service.build_preference(req.text, loc)
    ctx.member.preference = pref
    store.invalidate(ctx.meeting)
    store.commit(ctx.db, ctx.meeting)
    return {"meeting": view(ctx), "location_found": found}


@router.get("/{meeting_id}/preferences/summary")
def preference_summary(ctx: Ctx):
    members = ctx.meeting.members
    if ctx.meeting.confirmed:
        attending = ctx.meeting.confirmed["attending_member_ids"]
        members = [m for m in members if m.id in attending]
    prefs = [m.preference for m in members if m.preference]
    if not prefs:
        raise ApiError(400, "NO_PREFERENCES", "먼저 원하는 조건을 적어 주세요.")
    merged = preference_service.merge_group(prefs)
    # Local summary avoids charging AI credits for repeated page refreshes.
    return {"responded": len(prefs), "total": len(members), **merged,
            "summary": preference_service.rule_summary(merged)}


@router.post("/{meeting_id}/recommend")
async def recommend(req: RecommendReq, ctx: Ctx):
    store.require_host(ctx.member)
    if not ctx.meeting.confirmed:
        raise ApiError(409, "SCHEDULE_REQUIRED", "날짜와 시간을 먼저 확정해 주세요.")
    attendees = ctx.meeting.confirmed["attending_member_ids"]
    members = [m for m in ctx.meeting.members if m.id in attendees]
    if any(not m.preference for m in members):
        raise ApiError(409, "PREFERENCES_INCOMPLETE", "참석자 전원의 장소 조건을 먼저 입력해 주세요.")
    if any(not m.preference.get("start_location") for m in members):
        raise ApiError(409, "LOCATIONS_INCOMPLETE", "참석자 전원의 출발 위치를 먼저 입력해 주세요.")
    revision = ctx.meeting.revision
    result = await recommend_service.recommend(
        [{"id": m.id, "name": m.name, "preference": m.preference} for m in members],
        req.count, req.radius_m, req.extra_text, schedule=ctx.meeting.confirmed,
    )
    ctx.db.add(RecommendationRun(meeting_id=ctx.meeting.id, input_revision=revision,
                                 request=req.model_dump(), result=result))
    ctx.meeting.recommendation_result = result
    ctx.meeting.confirmed_place = None
    ctx.meeting.status = "recommended"
    store.commit(ctx.db, ctx.meeting)
    return {"meeting_id": ctx.meeting.id, "revision": ctx.meeting.revision, **result}


@router.post("/{meeting_id}/place/confirm")
def confirm_place(req: ConfirmPlaceReq, ctx: Ctx):
    store.require_host(ctx.member)
    if req.revision != ctx.meeting.revision:
        raise ApiError(409, "MEETING_CHANGED", "최신 추천 결과를 확인한 뒤 장소를 확정해 주세요.")
    result = ctx.meeting.recommendation_result
    if not ctx.meeting.confirmed or not result:
        raise ApiError(409, "RECOMMENDATION_REQUIRED", "시간 확정 후 장소 추천을 먼저 받아 주세요.")
    selected = next((r for r in result["recommendations"] if r["place"]["id"] == req.place_id), None)
    if selected is None:
        raise ApiError(400, "INVALID_PLACE", "현재 추천 목록의 장소만 확정할 수 있어요.")
    ctx.meeting.confirmed_place = selected["place"]
    ctx.meeting.status = "finalized"
    store.commit(ctx.db, ctx.meeting)
    return view(ctx)
