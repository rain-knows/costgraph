from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.report import CostReportV2


class ArtifactSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    artifact_type: Literal["cost_report"]
    conversation_id: str
    turn_id: str
    message_id: str
    run_id: str
    conversation_title: str
    part_id: str
    part_number: str
    part_description: str
    period: str
    report_sha256: str
    data_snapshot_id: str
    report_schema_version: str
    rule_version: str
    prompt_version: str
    code_version: str
    created_at: datetime
    deleted_at: datetime | None = None


class ArtifactDetailResponse(ArtifactSummaryResponse):
    report_json: CostReportV2


class ArtifactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ArtifactSummaryResponse]
    total: int
    limit: int
    offset: int
