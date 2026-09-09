from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.report import CostReportV1


class ArtifactSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    artifact_type: Literal["cost_report"]
    conversation_id: str
    turn_id: str
    message_id: str
    run_id: str
    conversation_title: str
    product_id: str
    product_name: str
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
    report_json: CostReportV1


class ArtifactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ArtifactSummaryResponse]
    total: int
    limit: int
    offset: int
