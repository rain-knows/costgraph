from __future__ import annotations

from typing import Any

from app.domain.authorization import ExecutionPrincipal
from app.repositories.artifact_repository import get_artifact_repository

PUBLIC_ARTIFACT_FIELDS = (
    "artifact_id",
    "artifact_type",
    "conversation_id",
    "turn_id",
    "message_id",
    "run_id",
    "conversation_title",
    "part_id",
    "part_number",
    "part_description",
    "period",
    "report_sha256",
    "data_snapshot_id",
    "report_schema_version",
    "rule_version",
    "prompt_version",
    "code_version",
    "created_at",
    "deleted_at",
)


def list_artifacts(
    principal: ExecutionPrincipal,
    *,
    state: str = "active",
    query: str | None = None,
    period: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    items, total = get_artifact_repository().list(
        principal,
        state=state,
        query=query,
        period=period,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_public_artifact(item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_artifact(
    artifact_id: str,
    principal: ExecutionPrincipal,
) -> dict[str, Any]:
    return _public_artifact(
        get_artifact_repository().get(artifact_id, principal),
        include_report=True,
    )


def trash_artifact(
    artifact_id: str,
    principal: ExecutionPrincipal,
) -> None:
    get_artifact_repository().trash(artifact_id, principal)


def restore_artifact(
    artifact_id: str,
    principal: ExecutionPrincipal,
) -> dict[str, Any]:
    return _public_artifact(get_artifact_repository().restore(artifact_id, principal))


def _public_artifact(
    record: dict[str, Any], *, include_report: bool = False
) -> dict[str, Any]:
    public = {field: record[field] for field in PUBLIC_ARTIFACT_FIELDS}
    if include_report:
        public["report_json"] = record["report_json"]
    return public
