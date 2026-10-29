from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["kind", "cost", "atmosphere", "environment", "accessibility", "other"]
    target: str = Field(min_length=1, max_length=100)
    alternatives: list[str] = Field(default_factory=list, max_length=10)
    direction: Literal["preference", "avoidance"] = "preference"
    strength: Literal["desired", "required"] = "desired"
    scope: Literal["menu", "shared_activity", "place"] = "place"
    raw_text: str = Field(min_length=1, max_length=500)
    needs_confirmation: bool = False
    operator: Literal["eq", "lte", "gte"] = "eq"
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: Literal["KRW", "minutes", "people"] | None = None

    @model_validator(mode='after')
    def structured_quantity(self):
        if (self.value is None) != (self.unit is None):
            raise ValueError('수량과 단위를 함께 입력해 주세요.')
        if self.operator != 'eq' and self.value is None:
            raise ValueError('비교 연산에는 수량이 필요합니다.')
        if self.value is not None and self.value < 0:
            raise ValueError('수량은 0 이상이어야 합니다.')
        return self
