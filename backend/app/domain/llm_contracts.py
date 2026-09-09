from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["system_help", "cost_calculation"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class IntentSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["cost_query", "cost_breakdown", "variance_analysis", "unknown"]
    product_text: str = Field(default="", max_length=200)
    period: str = ""
    start_date: str = ""
    end_date: str = ""

    @model_validator(mode="after")
    def validate_temporal_slots(self) -> IntentSlots:
        if self.period:
            try:
                date.fromisoformat(f"{self.period}-01")
            except ValueError as exc:
                raise ValueError("period 必须是有效的 YYYY-MM") from exc
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("start_date 和 end_date 必须同时提供")
        if self.start_date:
            date.fromisoformat(self.start_date)
            date.fromisoformat(self.end_date)
        return self
