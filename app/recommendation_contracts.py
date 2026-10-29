"""Transport-neutral team data contract. No API credentials, endpoints or HTTP clients."""
from typing import Awaitable, Callable, Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PlaceData(Contract):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default='', max_length=500)
    category: str = Field(default='', max_length=300)
    branch_name: str | None = Field(default=None, max_length=100)
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    phone: str = ''
    url: str = ''
    # Explicit direct facts only; never put blog observations into these fields.
    facts: list['DirectFact'] = Field(default_factory=list, max_length=100)
    ordering: 'DirectOrdering | None' = None


class DirectFact(Contract):
    scope: Literal['menu', 'shared_activity', 'place']
    target: str = Field(min_length=1, max_length=100)
    value: bool | float
    unit: Literal['KRW', 'minutes', 'people'] | None = None
    source: str = Field(min_length=1, max_length=500)
    information_type: Literal['confirmed'] = 'confirmed'
    verified_at: datetime | None = None


class DirectOrdering(Contract):
    individual: bool | None = None
    alternatives_verified: bool | None = None
    source: str = Field(min_length=1, max_length=500)


class TravelData(Contract):
    place_id: str = Field(min_length=1, max_length=100)
    member_id: str = Field(min_length=1, max_length=100)
    status: Literal['success', 'failed', 'not_requested']
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    unit: Literal['minutes', 'seconds', 'milliseconds'] | None = None
    mode: Literal['driving', 'transit', 'walking', 'unknown'] = 'unknown'
    source: str = Field(default='team_input', max_length=100)
    failure_code: Literal['timeout', 'no_route', 'upstream_error', 'not_requested'] | None = None
    # Existing display fields are optional and are not used as travel-time fallbacks.
    member: str | None = None
    origin_text: str | None = None
    straight_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    driving_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    text: str = ''

    @model_validator(mode='after')
    def valid_duration(self):
        if self.status == 'success' and (self.duration is None or self.unit is None):
            raise ValueError('Successful travel requires a duration and its unit')
        if self.status != 'success' and self.duration is not None:
            raise ValueError('Failed/not-requested travel must not supply a duration')
        return self


class BlogDocument(Contract):
    title: str = Field(default='', max_length=1000)
    description: str = Field(default='', max_length=4000)
    url: str = Field(min_length=1, max_length=4000)
    postdate: str | None = Field(default=None, max_length=30)


class BlogSearchBatch(Contract):
    place_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=1000)
    status: Literal['success', 'failed', 'timeout', 'rate_limited', 'not_requested', 'limit_exceeded']
    fetched_at: datetime | None = None
    items: list[BlogDocument] = Field(default_factory=list, max_length=100)
    failure_code: Literal['keys_missing', 'auth_error', 'timeout', 'rate_limit', 'upstream_error',
                          'invalid_response', 'provider_unavailable'] | None = None
    data_origin: Literal['fixture', 'live', 'unverified'] = 'unverified'

    @field_validator('fetched_at')
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('fetched_at requires a timezone')
        return value

    @model_validator(mode='after')
    def failed_has_no_documents(self):
        if self.status != 'success' and self.items:
            raise ValueError('Only successful searches can provide documents')
        return self


class RecommendationInputs(Contract):
    meeting_id: str = Field(min_length=1, max_length=100)
    participant_ids: list[str] = Field(min_length=1, max_length=10)
    places: list[PlaceData] = Field(max_length=100)
    travel_times: list[TravelData] = Field(default_factory=list, max_length=1000)
    # None means no team integration connected; [] means connected but no batches supplied.
    blog_searches: list[BlogSearchBatch] | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def identities(self):
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError('Duplicate participants')
        ids = [p.id for p in self.places]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate place IDs')
        seen = set()
        for t in self.travel_times:
            key = t.place_id, t.member_id
            if t.place_id not in ids or t.member_id not in self.participant_ids or key in seen:
                raise ValueError('Invalid or duplicate travel reference')
            seen.add(key)
        return self


# Extraction uses the existing AI adapter by default when snippets exist; tests inject/mock it.
EvidenceExtractor = Callable[[dict, list[dict], list[dict], dict], Awaitable[dict]]
