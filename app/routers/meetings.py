"""Dynamic group submissions -> automatic common-time and place recommendation."""
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from app import store
from app.database import get_session
from app.errors import ApiError
from app.models import Meeting, Member
from app.schemas import AvailabilityParseReq, ConfirmPlaceReq, CreateMeetingReq, JoinReq, SubmissionReq
from app.services import preference_service, schedule_service, submission_service

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
CreateBody = Annotated[CreateMeetingReq, Body(openapi_examples={
    "group_screen": {
        "summary": "그룹 만들기 화면: 이름과 인원만 전송",
        "description": "날짜·시간·추천 옵션은 선택 설정이며 이 화면에서는 보내지 않습니다.",
        "value": {"title": "만나자쿠테스트", "host_name": "성윤", "expected_member_count": 4},
    },
})]


def view(ctx):
    return store.snapshot(ctx.meeting, host=ctx.member.is_host)


def workflow_context(ctx):
    # The workflow opens short independent transactions around writes, never around HTTP calls.
    factory = sessionmaker(bind=ctx.db.get_bind(), expire_on_commit=False)
    meeting_id, member_id, host = ctx.meeting.id, ctx.member.id, ctx.member.is_host
    ctx.db.rollback()
    return factory, meeting_id, member_id, host


@router.post("", status_code=201)
def create_meeting(req: CreateBody, db: DB):
    host, token = store.new_member(req.host_name, is_host=True)
    meeting = Meeting(title=req.title, invite_code=secrets.token_urlsafe(24),
                      expected_member_count=req.expected_member_count,
                      recommendation_options=req.recommendation_options.model_dump(),
                      candidate_dates=req.candidate_dates or [], time_range=req.time_range.model_dump(),
                      slot_minutes=req.slot_minutes, members=[host])
    db.add(meeting)
    db.commit()
    return {"meeting": store.snapshot(meeting, host=True), "member_id": host.id,
            "access_token": token, "token_type": "bearer"}


@router.post("/{meeting_id}/members", status_code=201)
def join_meeting(meeting_id: str, req: JoinReq, db: DB):
    meeting = store.get_meeting(db, meeting_id, lock=True)
    if not secrets.compare_digest(meeting.invite_code, req.invite_code):
        raise ApiError(403, "INVALID_INVITE", "초대 코드가 올바르지 않아요.")
    if len(meeting.members) >= meeting.expected_member_count:
        raise ApiError(400, "MEETING_FULL", "설정한 그룹 인원이 모두 참여했어요.")
    if any(m.name == req.name for m in meeting.members):
        raise ApiError(409, "MEMBER_CONFLICT", "이미 사용 중인 이름이에요.")
    member, token = store.new_member(req.name)
    meeting.members.append(member)
    store.invalidate(meeting)
    store.commit(db, meeting)
    return {"meeting": store.snapshot(meeting), "member_id": member.id,
            "access_token": token, "token_type": "bearer"}


@router.get("/{meeting_id}")
def get_meeting(ctx: Ctx):
    return view(ctx)


@router.put("/{meeting_id}/members/me/submission")
async def submit_response(req: SubmissionReq, ctx: Ctx):
    factory, meeting_id, member_id, host = workflow_context(ctx)
    return await submission_service.submit(factory, meeting_id, member_id, req, host=host)


@router.post("/{meeting_id}/availability/parse")
async def parse_availability(req: AvailabilityParseReq, ctx: Ctx):
    return await schedule_service.parse_availability_text(view(ctx), req.text, selected_dates=req.selected_dates)


@router.get("/{meeting_id}/schedule")
def get_schedule(ctx: Ctx,
                 min_hours: Annotated[float, Query(gt=0, le=24, allow_inf_nan=False)] = 2,
                 top: Annotated[int, Query(ge=1, le=50)] = 5):
    return schedule_service.best_slots(view(ctx), min_hours=min_hours, top=top)


@router.get("/{meeting_id}/preferences/summary")
def preference_summary(ctx: Ctx):
    prefs = [m.preference for m in ctx.meeting.members if m.preference]
    if not prefs:
        raise ApiError(400, "NO_PREFERENCES", "먼저 원하는 조건을 적어 주세요.")
    merged = preference_service.merge_group(prefs)
    return {"responded": len(prefs), "total": ctx.meeting.expected_member_count, **merged,
            "summary": preference_service.rule_summary(merged)}


@router.post("/{meeting_id}/recommend")
async def retry_recommendation(ctx: Ctx):
    factory, meeting_id, _, host = workflow_context(ctx)
    return await submission_service.retry(factory, meeting_id, host=host)


@router.post("/{meeting_id}/place/confirm")
def confirm_place(req: ConfirmPlaceReq, ctx: Ctx):
    store.require_host(ctx.member)
    if req.revision != ctx.meeting.revision:
        raise ApiError(409, "MEETING_CHANGED", "최신 추천 결과를 확인한 뒤 장소를 확정해 주세요.")
    if not store.progress(ctx.meeting)["all_submitted"]:
        raise ApiError(409, "SUBMISSIONS_INCOMPLETE", "설정한 인원 전원이 입력을 완료해야 해요.")
    result = ctx.meeting.recommendation_result
    if ctx.meeting.status not in {"recommended", "finalized"} or not result:
        raise ApiError(409, "RECOMMENDATION_REQUIRED", "추천 결과가 준비된 뒤 장소를 확정해 주세요.")
    selected = next((r for r in result["recommendations"] if r["place"]["id"] == req.place_id), None)
    if selected is None:
        raise ApiError(400, "INVALID_PLACE", "현재 추천 목록의 장소만 확정할 수 있어요.")
    ctx.meeting.confirmed_place = selected["place"]
    ctx.meeting.status = "finalized"
    store.commit(ctx.db, ctx.meeting)
    return view(ctx)
