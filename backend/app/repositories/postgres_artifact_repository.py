from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, load_only

from app.db.engine import get_session_factory
from app.db.models import AgentArtifact
from app.domain.authorization import ExecutionPrincipal
from app.domain.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactPersistenceError,
)
from app.domain.report import CostReportV2


class PostgresArtifactRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def ensure(
        self, record: dict[str, Any], principal: ExecutionPrincipal
    ) -> dict[str, Any]:
        self._require_owner(record, principal)
        values = {**record, "created_at": datetime.fromisoformat(record["created_at"])}
        session: Session = self._session_factory()
        try:
            session.execute(
                pg_insert(AgentArtifact).values(**values).on_conflict_do_nothing()
            )
            session.commit()
            stored = session.scalar(
                select(AgentArtifact).where(
                    AgentArtifact.tenant_id == principal.tenant_id,
                    or_(
                        AgentArtifact.run_id == record["run_id"],
                        AgentArtifact.message_id == record["message_id"],
                    ),
                )
            )
            if stored is None:
                raise ArtifactPersistenceError()
            self._validate_idempotent_match(stored, record)
            return self._to_record(stored)
        except (ArtifactConflictError, ArtifactPersistenceError):
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise ArtifactPersistenceError() from exc
        finally:
            session.close()

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
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [
            AgentArtifact.tenant_id == principal.tenant_id,
            AgentArtifact.principal_id == principal.principal_id,
            AgentArtifact.deleted_at.is_(None)
            if state == "active"
            else AgentArtifact.deleted_at.is_not(None),
        ]
        normalized_query = (query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            filters.append(
                or_(
                    AgentArtifact.part_id.ilike(pattern),
                    AgentArtifact.part_number.ilike(pattern),
                    AgentArtifact.part_description.ilike(pattern),
                    AgentArtifact.period.ilike(pattern),
                    AgentArtifact.conversation_title.ilike(pattern),
                )
            )
        if period:
            filters.append(AgentArtifact.period == period)
        if run_id:
            filters.append(AgentArtifact.run_id == run_id)

        session: Session = self._session_factory()
        try:
            statement = (
                select(AgentArtifact, func.count().over().label("total_count"))
                .options(
                    load_only(
                        AgentArtifact.artifact_id,
                        AgentArtifact.artifact_type,
                        AgentArtifact.tenant_id,
                        AgentArtifact.principal_id,
                        AgentArtifact.conversation_id,
                        AgentArtifact.turn_id,
                        AgentArtifact.message_id,
                        AgentArtifact.run_id,
                        AgentArtifact.conversation_title,
                        AgentArtifact.part_id,
                        AgentArtifact.part_number,
                        AgentArtifact.part_description,
                        AgentArtifact.period,
                        AgentArtifact.report_sha256,
                        AgentArtifact.data_snapshot_id,
                        AgentArtifact.report_schema_version,
                        AgentArtifact.rule_version,
                        AgentArtifact.prompt_version,
                        AgentArtifact.code_version,
                        AgentArtifact.created_at,
                        AgentArtifact.deleted_at,
                    )
                )
                .where(*filters)
                .order_by(AgentArtifact.created_at.desc(), AgentArtifact.artifact_id)
                .limit(limit)
                .offset(offset)
            )
            rows = session.execute(statement).all()
            if rows:
                return [self._to_summary(item) for item, _ in rows], int(rows[0][1])

            total = (
                session.scalar(
                    select(func.count()).select_from(AgentArtifact).where(*filters)
                )
                or 0
            )
            return [], int(total)
        except SQLAlchemyError as exc:
            raise ArtifactPersistenceError() from exc
        finally:
            session.close()

    def get(self, artifact_id: str, principal: ExecutionPrincipal) -> dict[str, Any]:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(AgentArtifact).where(
                    AgentArtifact.artifact_id == artifact_id,
                    AgentArtifact.tenant_id == principal.tenant_id,
                    AgentArtifact.principal_id == principal.principal_id,
                    AgentArtifact.deleted_at.is_(None),
                )
            )
            if row is None:
                raise ArtifactNotFoundError(artifact_id)
            return self._to_record(row)
        except ArtifactNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise ArtifactPersistenceError() from exc
        finally:
            session.close()

    def trash(self, artifact_id: str, principal: ExecutionPrincipal) -> dict[str, Any]:
        return self._set_deleted_at(artifact_id, principal, datetime.now(UTC))

    def restore(
        self, artifact_id: str, principal: ExecutionPrincipal
    ) -> dict[str, Any]:
        return self._set_deleted_at(artifact_id, principal, None)

    def _set_deleted_at(
        self,
        artifact_id: str,
        principal: ExecutionPrincipal,
        deleted_at: datetime | None,
    ) -> dict[str, Any]:
        session: Session = self._session_factory()
        try:
            row = session.scalar(
                select(AgentArtifact)
                .where(
                    AgentArtifact.artifact_id == artifact_id,
                    AgentArtifact.tenant_id == principal.tenant_id,
                    AgentArtifact.principal_id == principal.principal_id,
                )
                .with_for_update()
            )
            if row is None:
                raise ArtifactNotFoundError(artifact_id)
            if (deleted_at is None) != (row.deleted_at is None):
                row.deleted_at = deleted_at
                session.commit()
            return self._to_record(row)
        except ArtifactNotFoundError:
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise ArtifactPersistenceError() from exc
        finally:
            session.close()

    @staticmethod
    def _require_owner(record: dict[str, Any], principal: ExecutionPrincipal) -> None:
        if (
            record["tenant_id"] != principal.tenant_id
            or record["principal_id"] != principal.principal_id
        ):
            raise PermissionError("产出记录不属于当前执行主体。")

    @staticmethod
    def _validate_idempotent_match(
        stored: AgentArtifact, record: dict[str, Any]
    ) -> None:
        fields = (
            "artifact_id",
            "principal_id",
            "conversation_id",
            "turn_id",
            "message_id",
            "run_id",
            "report_sha256",
        )
        if any(getattr(stored, field) != record[field] for field in fields):
            raise ArtifactConflictError(record["run_id"])

    @staticmethod
    def _to_summary(row: AgentArtifact) -> dict[str, Any]:
        return {
            "artifact_id": row.artifact_id,
            "artifact_type": row.artifact_type,
            "tenant_id": row.tenant_id,
            "principal_id": row.principal_id,
            "conversation_id": row.conversation_id,
            "turn_id": row.turn_id,
            "message_id": row.message_id,
            "run_id": row.run_id,
            "conversation_title": row.conversation_title,
            "part_id": row.part_id,
            "part_number": row.part_number,
            "part_description": row.part_description,
            "period": row.period,
            "report_sha256": row.report_sha256,
            "data_snapshot_id": row.data_snapshot_id,
            "report_schema_version": row.report_schema_version,
            "rule_version": row.rule_version,
            "prompt_version": row.prompt_version,
            "code_version": row.code_version,
            "created_at": row.created_at.isoformat(),
            "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        }

    @staticmethod
    def _to_record(row: AgentArtifact) -> dict[str, Any]:
        report = CostReportV2.model_validate(row.report_json).model_dump(mode="json")
        return {
            "artifact_id": row.artifact_id,
            "artifact_type": row.artifact_type,
            "tenant_id": row.tenant_id,
            "principal_id": row.principal_id,
            "conversation_id": row.conversation_id,
            "turn_id": row.turn_id,
            "message_id": row.message_id,
            "run_id": row.run_id,
            "conversation_title": row.conversation_title,
            "part_id": row.part_id,
            "part_number": row.part_number,
            "part_description": row.part_description,
            "period": row.period,
            "report_json": report,
            "report_sha256": row.report_sha256,
            "data_snapshot_id": row.data_snapshot_id,
            "report_schema_version": row.report_schema_version,
            "rule_version": row.rule_version,
            "prompt_version": row.prompt_version,
            "code_version": row.code_version,
            "created_at": row.created_at.isoformat(),
            "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        }
