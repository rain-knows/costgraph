from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domain.authorization import ExecutionPrincipal
from app.domain.report import CostReportV2


class ArtifactRepository(Protocol):
    def ensure(
        self, record: dict[str, Any], principal: ExecutionPrincipal
    ) -> dict[str, Any]: ...

    def list(
        self,
        principal: ExecutionPrincipal,
        *,
        state: str = "active",
        query: str | None,
        period: str | None,
        run_id: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get(
        self, artifact_id: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]: ...

    def trash(
        self, artifact_id: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]: ...

    def restore(
        self, artifact_id: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]: ...


def _canonical_report(report: dict[str, Any]) -> tuple[dict[str, Any], str]:
    normalized = CostReportV2.model_validate(report).model_dump(mode="json")
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return normalized, hashlib.sha256(encoded).hexdigest()


def build_artifact_record(
    *,
    conversation_id: str,
    conversation_title: str,
    turn_id: str,
    message_id: str,
    result: dict[str, Any],
    created_at: str,
    principal: ExecutionPrincipal,
) -> dict[str, Any]:
    report, report_sha256 = _canonical_report(result["report_json"])
    run_id = str(result["run_id"])
    artifact_id = f"art_{uuid5(NAMESPACE_URL, f'{principal.tenant_id}:{run_id}').hex}"
    return {
        "artifact_id": artifact_id,
        "artifact_type": "cost_report",
        "tenant_id": principal.tenant_id,
        "principal_id": principal.principal_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "message_id": message_id,
        "run_id": run_id,
        "conversation_title": conversation_title,
        "part_id": report["part"]["part_id"],
        "part_number": report["part"]["part_number"],
        "part_description": report["part"]["part_description"],
        "period": report["period"],
        "report_json": report,
        "report_sha256": report_sha256,
        "data_snapshot_id": report["data_snapshot_id"],
        "report_schema_version": report["report_schema_version"],
        "rule_version": report["rule_version"],
        "prompt_version": report["prompt_version"],
        "code_version": report["code_version"],
        "created_at": created_at,
        "deleted_at": None,
    }


def get_artifact_repository() -> ArtifactRepository:
    from app.repositories.postgres_artifact_repository import (
        PostgresArtifactRepository,
    )

    return PostgresArtifactRepository()
