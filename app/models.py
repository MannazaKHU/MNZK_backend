"""Persistent meetings, members and recommendation history."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def new_id():
    return str(uuid4())


def utc_now():
    return datetime.now(timezone.utc)


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(40))
    expected_member_count: Mapped[int] = mapped_column(Integer, server_default="1")
    recommendation_options: Mapped[dict] = mapped_column(JSON, server_default='{"count":5,"radius_m":1000,"extra_text":""}')
    invite_code: Mapped[str] = mapped_column(String(64), unique=True)
    candidate_dates: Mapped[list] = mapped_column(JSON)
    time_range: Mapped[dict] = mapped_column(JSON)
    slot_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recommendation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed_place: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    members: Mapped[list["Member"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan", lazy="selectin",
        order_by="Member.created_at, Member.id",
    )
    __mapper_args__ = {"version_id_col": revision}


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("meeting_id", "name", name="uq_member_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(20))
    is_host: Mapped[bool] = mapped_column(Boolean, default=False)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    availability: Mapped[dict] = mapped_column(JSON, default=dict)
    availability_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    preference: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    meeting: Mapped[Meeting] = relationship(back_populates="members")


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    input_revision: Mapped[int] = mapped_column(Integer)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
