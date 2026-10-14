from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


BatchStatus = Literal[
    "created", "validating", "validated", "published", "superseded", "failed"
]


class DataAdminBatch(StrictModel):
    batch_id: UUID
    status: BatchStatus
    source_system: str = Field(max_length=64)
    source_file: str | None = Field(default=None, max_length=128)
    source_snapshot_hash: str = Field(min_length=64, max_length=64)
    calculation_rule_version: str = Field(max_length=64)
    total_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    error_rows: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_row_counts(self) -> DataAdminBatch:
        if self.valid_rows + self.error_rows > self.total_rows:
            raise ValueError("批次有效行与错误行不能超过总行数")
        return self

    started_at: datetime
    finished_at: datetime | None
    created_at: datetime


class DataAdminBatchList(StrictModel):
    items: list[DataAdminBatch]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class DataAdminErrorCodeCount(StrictModel):
    error_code: str
    count: int = Field(ge=0)


class DataAdminBatchDetail(DataAdminBatch):
    error_codes: list[DataAdminErrorCodeCount]


class DataAdminError(StrictModel):
    error_id: int = Field(ge=1)
    source_table: str = Field(max_length=64)
    source_record_id: str | None = Field(default=None, max_length=128)
    error_code: str = Field(max_length=64)
    error_message: str = Field(max_length=1000)
    created_at: datetime


class DataAdminErrorList(StrictModel):
    items: list[DataAdminError]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class PublishedSnapshot(StrictModel):
    batch: DataAdminBatch | None
    fact_rows: dict[str, int]
    projection_rows: int = Field(ge=0)
    part_count: int = Field(ge=0)
    period_start: str | None
    period_end: str | None
    rule_version_matches: bool | None


class DataQualitySummary(StrictModel):
    published: bool
    error_rows: int = Field(ge=0)
    fact_rows: dict[str, int]
    projection_rows: int = Field(ge=0)
    part_count: int = Field(ge=0)
    period_start: str | None
    period_end: str | None
    rule_version_matches: bool | None
