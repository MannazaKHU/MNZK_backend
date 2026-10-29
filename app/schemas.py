"""Validated inputs. Dates and HH:MM times are interpreted in Asia/Seoul."""
import re
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def check_date(value: str):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("YYYY-MM-DD 형식으로 입력해 주세요.")
    date.fromisoformat(value)
    return value


def check_time(value: str):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("HH:MM 형식으로 입력해 주세요.")
    return value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRange(Input):
    start: str = "10:00"
    end: str = "23:00"
    _start = field_validator("start")(check_time)
    _end = field_validator("end")(check_time)

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("시작 시간은 끝 시간보다 빨라야 해요.")
        return self


def default_dates():
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    return [(today + timedelta(days=i)).isoformat() for i in range(7)]


class CreateMeetingReq(Input):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] = "우리 모임"
    host_name: Name = "방장"
    expected_member_count: int = Field(ge=1, le=10, description="방장 포함 총 참여 인원. 생성 시 필수")
    recommendation_options: "RecommendReq" = Field(default_factory=lambda: RecommendReq())
    candidate_dates: list[str] = Field(default_factory=default_dates, min_length=1, max_length=14)
    time_range: TimeRange = Field(default_factory=TimeRange)
    slot_minutes: Literal[30, 60] = 60

    @field_validator("candidate_dates")
    @classmethod
    def dates(cls, values):
        return sorted({check_date(v) for v in values})

    @model_validator(mode="after")
    def has_full_slot(self):
        start = datetime.strptime(self.time_range.start, "%H:%M")
        end = datetime.strptime(self.time_range.end, "%H:%M")
        if (end - start).total_seconds() < self.slot_minutes * 60:
            raise ValueError("선택 범위에 최소 한 개의 시간 슬롯이 있어야 해요.")
        return self


class JoinReq(Input):
    name: Name
    invite_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TextReq(Input):
    text: Text


class Location(Input):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class SubmissionReq(Input):
    availability: dict[str, list[str]] = Field(max_length=14)
    start_location_text: str = Field(default="", max_length=200)
    start_location: Location | None = None
    desired_text: str = Field(max_length=500)
    avoided_text: str = Field(max_length=500)

    @model_validator(mode="after")
    def location_required(self):
        if self.start_location is None and not self.start_location_text.strip():
            raise ValueError("출발지 이름 또는 좌표를 입력해 주세요.")
        return self


class RecommendReq(Input):
    count: int = Field(default=5, ge=1, le=10)
    radius_m: int = Field(default=1000, ge=300, le=3000)
    extra_text: str = Field(default="", max_length=200)


class ConfirmPlaceReq(Input):
    place_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, description="추천 결과를 조회했을 때의 모임 revision")
