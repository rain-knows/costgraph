from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


BatchStatus = Literal[
    "created", "validating", "validated", "published", "superseded", "failed"
]

ImportJobStatus = Literal[
    "submitted",
    "validating",
    "validation_failed",
    "approval_pending",
    "approved",
    "rejected",
    "publishing",
    "published",
    "publish_failed",
    "cancelled",
]

ImportJobDecision = Literal["approve", "reject"]
ImportJobDecisionValue = Literal["approved", "rejected"]
ImportJobOutcome = Literal["success", "failure"]
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class DataAdminBatch(StrictModel):
    batch_id: UUID
    status: BatchStatus
    job_status: ImportJobStatus | None = None
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


class ImportJobCreateRequest(StrictModel):
    """``POST /api/data-admin/imports`` 请求体。

    ``payload`` 只允许出现在请求里；对象存储落盘后，响应与数据库都只保留
    ``object_key`` / ``size`` / ``sha256``。
    """

    source_system: str = Field(min_length=1, max_length=64)
    source_file_name: str = Field(min_length=1, max_length=128)
    calculation_rule_version: str = Field(min_length=1, max_length=64)
    payload: dict[str, list[dict]]


class ImportDecisionRequest(StrictModel):
    decision: ImportJobDecision
    reason: str | None = Field(default=None, max_length=500)


class ImportJobSubmission(StrictModel):
    """``POST /api/data-admin/imports`` 的 200 响应。"""

    job_id: UUID
    status: ImportJobStatus
    duplicate: bool
    source_system: str = Field(max_length=64)
    source_file_name: str | None = Field(default=None, max_length=128)
    source_object_key: str = Field(max_length=512)
    source_object_size: int = Field(ge=0)
    source_object_sha256: str = Field(pattern=SHA256_PATTERN)
    snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    calculation_rule_version: str = Field(max_length=64)
    total_rows: int = Field(ge=0)
    created_at: datetime


class ImportJob(StrictModel):
    """导入任务响应模型；字段与持久层 ``_job_dict`` 一一对应。

    租约内部字段（``lease_owner`` / ``lease_expires_at`` / ``available_at``）
    不出现在响应里；``payload`` 与对象内容永远不出现。
    """

    job_id: UUID
    tenant_id: str = Field(max_length=64)
    principal_id: str = Field(max_length=128)
    status: ImportJobStatus
    source_system: str = Field(max_length=64)
    source_file_name: str | None = Field(default=None, max_length=128)
    source_object_key: str = Field(max_length=512)
    source_object_size: int = Field(ge=0)
    source_object_sha256: str = Field(pattern=SHA256_PATTERN)
    snapshot_hash: str = Field(pattern=SHA256_PATTERN)
    calculation_rule_version: str = Field(max_length=64)
    total_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    error_rows: int = Field(ge=0)
    error_summary: dict[str, int] | None = None
    decision: ImportJobDecisionValue | None = None
    decision_reason: str | None = Field(default=None, max_length=500)
    decided_by: str | None = Field(default=None, max_length=128)
    decided_at: datetime | None = None
    published_batch_id: UUID | None = None
    attempt_count: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duplicate: bool = False


class ImportJobDetail(ImportJob):
    error_codes: list[DataAdminErrorCodeCount] = Field(default_factory=list)


class ImportJobList(StrictModel):
    items: list[ImportJob]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


def import_job_response(record: Mapping[str, Any]) -> dict[str, Any]:
    """从持久层 dict 中挑出响应字段。

    持久层还会返回租约内部字段（``lease_owner`` / ``lease_expires_at`` /
    ``available_at``）；响应模型是 ``extra="forbid"``，因此 API 层应使用本函
    数而不是直接 ``model_validate(record)``。
    """
    return {name: record[name] for name in ImportJob.model_fields if name in record}


def import_job_detail_response(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **import_job_response(record),
        "error_codes": list(record.get("error_codes") or []),
    }


def import_job_submission_response(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: record[name]
        for name in ImportJobSubmission.model_fields
        if name in record
    }
