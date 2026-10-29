"""Database operations and invalidation of derived meeting results."""
import hashlib
import secrets
from datetime import timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.errors import ApiError
from app.models import Meeting, Member

MAX_MEMBERS = 10


def token_digest(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def new_member(name: str, *, is_host=False):
    token = secrets.token_urlsafe(32)
    return Member(name=name, is_host=is_host, token_digest=token_digest(token),
                  availability={}, availability_submitted=False), token


def get_meeting(session: Session, meeting_id: str, *, lock=False):
    query = select(Meeting).where(Meeting.id == meeting_id)
    if lock:
        query = query.with_for_update()
    meeting = session.scalar(query)
    if meeting is None:
        raise ApiError(404, "MEETING_NOT_FOUND", "모임을 찾을 수 없어요.")
    return meeting


def authenticate(session: Session, meeting_id: str, token: str):
    member = session.scalar(select(Member).where(
        Member.meeting_id == meeting_id, Member.token_digest == token_digest(token)
    ))
    if member is None:
        raise ApiError(401, "INVALID_TOKEN", "이 모임의 참여자 토큰이 필요해요.")
    return get_meeting(session, meeting_id), member


def require_host(member: Member):
    if not member.is_host:
        raise ApiError(403, "HOST_REQUIRED", "모임을 만든 사람만 확정하거나 추천을 요청할 수 있어요.")


def invalidate(meeting: Meeting, *, schedule=False):
    meeting.recommendation_result = None
    meeting.confirmed_place = None
    meeting.confirmed = None
    meeting.processing_started_at = None
    meeting.processing_error = None
    meeting.status = "waiting"


def progress(meeting: Meeting):
    submitted = sum(m.submitted_at is not None for m in meeting.members)
    expected = meeting.expected_member_count
    return {"expected_member_count": expected, "joined_member_count": len(meeting.members),
            "submitted_member_count": submitted, "remaining_member_count": max(0, expected - submitted),
            "all_submitted": len(meeting.members) == expected and submitted == expected}


def commit(session: Session, meeting: Meeting):
    # Every member edit versions the meeting: in-flight AI cannot overwrite newer inputs.
    meeting.revision += 1
    try:
        session.commit()
    except StaleDataError:
        session.rollback()
        raise ApiError(409, "MEETING_CHANGED", "모임 정보가 바뀌었어요. 새로고침 후 다시 시도해 주세요.")
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "MEMBER_CONFLICT", "이미 사용 중인 이름이에요. 다른 이름으로 참여해 주세요.")


def snapshot(meeting: Meeting, *, host=False):
    result = meeting.recommendation_result
    created_at = meeting.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    body = {
        "id": meeting.id, "title": meeting.title,
        "expected_member_count": meeting.expected_member_count,
        "recommendation_options": meeting.recommendation_options,
        "progress": progress(meeting), "processing_error": meeting.processing_error,
        "candidate_dates": meeting.candidate_dates, "time_range": meeting.time_range,
        "slot_minutes": meeting.slot_minutes, "status": meeting.status,
        "confirmed": meeting.confirmed, "confirmed_place": meeting.confirmed_place,
        "revision": meeting.revision, "created_at": created_at.isoformat(),
        "members": [{"id": m.id, "name": m.name, "is_host": m.is_host,
                     "submitted_at": m.submitted_at.isoformat() if m.submitted_at else None,
                     "availability": m.availability, "availability_submitted": m.availability_submitted,
                     "preference": m.preference} for m in meeting.members],
        "recommendation_result": result,
        "recommendations": result["recommendations"] if result else [],
    }
    if host:
        body["invite_code"] = meeting.invite_code
    return body
