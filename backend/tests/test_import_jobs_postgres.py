"""导入任务在真实 PostgreSQL 上的租约、状态机与发布语义（规格阶段一）。

``test_import_jobs_api.py`` 用假仓储验证 HTTP 契约，``test_import_job_state_machine.py``
只验证常量映射。这里补上两者都无法覆盖的真实数据库行为：

- ``FOR UPDATE SKIP LOCKED`` 领取：并发 Worker 不会重复领取同一任务；
- 租约过期回收、可重试退避、不可重试终态；
- 未审批任务不可发布（``approved`` 门禁）；
- 发布失败时旧快照保持 ``published``，任务可重试；
- 新快照提交后旧快照转 ``superseded``，查询只读当前 ``published``。

全部用例需要 ``TEST_COST_DATABASE_URL``；未配置时跳过，避免把设计当成证据。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import OperationalError

from app.db.engine import get_session_factory, reset_engine
from app.db.models import (
    CostEvent,
    CostEventInput,
    CostRecord,
    DataImportJob,
    DataLoadBatch,
    DataLoadError,
    FinishedBatchCostProjection,
    GovernanceAuditEvent,
    Part,
)
from app.domain.authorization import (
    DataScope,
    ExecutionPrincipal,
    build_execution_context,
)
from app.domain.cost import CALCULATION_RULE_VERSION
from app.repositories.import_job_repository import (
    ImportJobLeaseLostError,
    ImportJobNotFoundError,
    ImportJobStateConflictError,
)
from app.repositories.postgres_import_job_repository import (
    RETRY_BACKOFF_SECONDS,
    PostgresImportJobRepository,
)
from app.services.cost_data_import_service import CostDataImporter
from app.services.import_job_service import (
    _canonical_hash,
    encode_import_payload,
    normalize_import_payload,
)
from app.services.import_object_store import LocalImportObjectStore
from app.worker import AgentWorker, classify_import_job_exception

SAMPLES = Path(__file__).parents[2] / "data" / "samples"
SELECTED_EVENTS = {"E-RAW-001", "E-SEMI-001", "E-INJ-001", "E-PAINT-001", "E-FG-001"}


def _load(name: str) -> list[dict]:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _sample_payload() -> dict[str, list[dict]]:
    """零件、事件、投入与成本记录互相自洽的一小份样本（含多步工艺链）。"""
    events = [
        row for row in _load("cost_events.json") if row["event_id"] in SELECTED_EVENTS
    ]
    part_ids = {row["part_id"] for row in events}
    return {
        "parts": [row for row in _load("parts.json") if row["part_id"] in part_ids],
        "cost_events": events,
        "cost_event_inputs": [
            row
            for row in _load("cost_event_inputs.json")
            if row["event_id"] in SELECTED_EVENTS
        ],
        "cost_records": [
            row
            for row in _load("cost_records.json")
            if row["event_id"] in SELECTED_EVENTS
        ],
    }


def _job_row(job_id: UUID) -> DataImportJob | None:
    """直接读任务行，绕过仓储的租约与状态断言。"""
    with get_session_factory()() as session:
        return session.get(DataImportJob, job_id)


def _force_lease_expiry(job_id: UUID) -> None:
    with get_session_factory()() as session:
        session.execute(
            update(DataImportJob)
            .where(DataImportJob.job_id == job_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()


def _make_available_now(job_id: UUID) -> None:
    with get_session_factory()() as session:
        session.execute(
            update(DataImportJob)
            .where(DataImportJob.job_id == job_id)
            .values(available_at=datetime.now(UTC) - timedelta(days=1))
        )
        session.commit()


def _published_batches(tenant_id: str) -> set[str]:
    with get_session_factory()() as session:
        rows = session.scalars(
            select(DataLoadBatch.batch_id).where(
                DataLoadBatch.tenant_id == tenant_id,
                DataLoadBatch.status == "published",
            )
        ).all()
    return {str(value) for value in rows}


@pytest.fixture
def import_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """真实 PostgreSQL + 受控对象存储目录；离开时按租户清理全部行。"""
    database_url = os.getenv("TEST_COST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_COST_DATABASE_URL，跳过真实 PostgreSQL 导入任务测试。")
    monkeypatch.setenv("COST_DATABASE_URL", database_url)
    monkeypatch.setenv("IMPORT_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    reset_engine()
    tenant_id = f"import-job-test-{uuid4().hex}"
    yield {
        "repository": PostgresImportJobRepository(),
        "store": LocalImportObjectStore(),
        "importer": CostDataImporter(),
        "payload": _sample_payload(),
        "tenant_id": tenant_id,
    }
    with get_session_factory()() as session:
        # 审计表有"只允许追加"触发器，清理测试租户需要临时停用。
        session.execute(
            text(
                "ALTER TABLE cost_data.governance_audit_events "
                "DISABLE TRIGGER trg_governance_audit_events_append_only"
            )
        )
        try:
            session.execute(
                delete(GovernanceAuditEvent).where(
                    GovernanceAuditEvent.tenant_id == tenant_id
                )
            )
        finally:
            session.execute(
                text(
                    "ALTER TABLE cost_data.governance_audit_events "
                    "ENABLE TRIGGER trg_governance_audit_events_append_only"
                )
            )
        session.execute(
            delete(DataLoadError).where(DataLoadError.tenant_id == tenant_id)
        )
        session.execute(
            delete(FinishedBatchCostProjection).where(
                FinishedBatchCostProjection.tenant_id == tenant_id
            )
        )
        session.execute(delete(CostRecord).where(CostRecord.tenant_id == tenant_id))
        session.execute(
            delete(CostEventInput).where(CostEventInput.tenant_id == tenant_id)
        )
        session.execute(delete(CostEvent).where(CostEvent.tenant_id == tenant_id))
        session.execute(delete(Part).where(Part.tenant_id == tenant_id))
        session.execute(
            delete(DataLoadBatch).where(DataLoadBatch.tenant_id == tenant_id)
        )
        session.execute(
            delete(DataImportJob).where(DataImportJob.tenant_id == tenant_id)
        )
        session.commit()
    reset_engine()


def _submit(import_jobs, *, payload: dict | None = None) -> dict:
    normalized = normalize_import_payload(payload or import_jobs["payload"])
    reference = import_jobs["store"].put(encode_import_payload(normalized))
    return import_jobs["repository"].create_job(
        tenant_id=import_jobs["tenant_id"],
        principal_id="operator-1",
        principal_roles=["data_operator"],
        source_system="erp",
        source_object_key=reference.object_key,
        source_object_size=reference.size,
        source_object_sha256=reference.sha256,
        snapshot_hash=_canonical_hash(normalized),
        calculation_rule_version=CALCULATION_RULE_VERSION,
        idempotency_key=uuid4().hex,
        total_rows=sum(len(rows) for rows in normalized.values()),
    )


def _outcome(import_jobs):
    payload = import_jobs["payload"]
    return import_jobs["importer"].validate_payload(
        tenant_id=import_jobs["tenant_id"],
        source_system="erp",
        parts=payload["parts"],
        events=payload["cost_events"],
        inputs=payload["cost_event_inputs"],
        records=payload["cost_records"],
    )


def _to_approval_pending(import_jobs, job_id: UUID) -> None:
    repository = import_jobs["repository"]
    repository.claim_next_job("worker-setup")
    assert repository.complete_validation(
        job_id, "worker-setup", outcome=_outcome(import_jobs)
    ) == ("approval_pending")


def _approve(import_jobs, job_id: UUID) -> None:
    import_jobs["repository"].decide(
        _approval_context(import_jobs["tenant_id"]),
        job_id,
        decision="approve",
        reason="校验结果已复核",
        actor_principal_id="approver-1",
    )


def _approval_context(tenant_id: str, scope: DataScope | None = None):
    context = build_execution_context(
        "manual",
        ["system_help"],
        requested_permissions=["data_import.approve"],
        principal=ExecutionPrincipal(
            principal_id="approver-1",
            tenant_id=tenant_id,
            roles=("data_approver",),
        ),
    )
    return context.model_copy(update={"data_scope": scope or context.data_scope})


# ------------------------------------------------------------------ 租约领取


def test_claim_uses_skip_locked_and_does_not_double_claim(import_jobs) -> None:
    """``FOR UPDATE SKIP LOCKED`` 让并发 Worker 各拿一个任务，不会重复领取。"""
    repository = import_jobs["repository"]
    first = _submit(import_jobs)
    second_payload = _sample_payload()
    second_payload["parts"][0]["unit"] = "kg"
    second = _submit(import_jobs, payload=second_payload)
    with get_session_factory()() as session:
        session.execute(
            update(DataImportJob)
            .where(DataImportJob.job_id.in_([first["job_id"], second["job_id"]]))
            .values(available_at=datetime.now(UTC) - timedelta(days=1))
        )
        session.commit()

    claimed_first = repository.claim_next_job("worker-a")
    assert claimed_first is not None
    assert claimed_first["claimed_from"] == "submitted"
    assert claimed_first["status"] == "validating"
    assert claimed_first["attempt_count"] == 1
    first_row = _job_row(first["job_id"])
    assert first_row is not None and first_row.lease_owner == "worker-a"

    claimed_second = repository.claim_next_job("worker-b")
    assert claimed_second is not None
    assert claimed_second["job_id"] != claimed_first["job_id"]
    second_row = _job_row(second["job_id"])
    assert second_row is not None and second_row.lease_owner == "worker-b"

    # 两个任务都被租约持有，第三次领取必须返回 None。
    assert repository.claim_next_job("worker-c") is None

    assert {claimed_first["job_id"], claimed_second["job_id"]} == {
        first["job_id"],
        second["job_id"],
    }
    for job_id in (first["job_id"], second["job_id"]):
        row = _job_row(job_id)
        assert row is not None
        assert row.lease_expires_at is not None
        assert row.lease_expires_at > datetime.now(UTC)


def test_expired_lease_is_reclaimed_in_same_working_status(import_jobs) -> None:
    """租约过期的 ``validating`` 任务被回收，状态不变、尝试次数递增。"""
    repository = import_jobs["repository"]
    job = _submit(import_jobs)

    assert repository.claim_next_job("worker-a") is not None
    _force_lease_expiry(job["job_id"])

    reclaimed = repository.claim_next_job("worker-b")
    assert reclaimed is not None
    assert reclaimed["job_id"] == job["job_id"]
    assert reclaimed["claimed_from"] == "validating"
    assert reclaimed["status"] == "validating"
    assert reclaimed["attempt_count"] == 2
    reclaimed_row = _job_row(job["job_id"])
    assert reclaimed_row is not None and reclaimed_row.lease_owner == "worker-b"


def test_stale_worker_cannot_complete_after_lease_taken(import_jobs) -> None:
    """失去租约的旧 Worker 写结果必须失败，不得覆盖新 Worker 的工作。"""
    repository = import_jobs["repository"]
    job = _submit(import_jobs)
    assert repository.claim_next_job("worker-a") is not None
    _force_lease_expiry(job["job_id"])
    assert repository.claim_next_job("worker-b") is not None

    with pytest.raises(ImportJobLeaseLostError):
        repository.complete_validation(
            job["job_id"], "worker-a", outcome=_outcome(import_jobs)
        )

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "validating"
    assert row.lease_owner == "worker-b"


def test_heartbeat_only_renews_for_lease_owner(import_jobs) -> None:
    repository = import_jobs["repository"]
    job = _submit(import_jobs)
    assert repository.claim_next_job("worker-a") is not None

    assert repository.renew_job_lease(job["job_id"], "worker-b") is False
    assert repository.renew_job_lease(job["job_id"], "worker-a") is True


# ---------------------------------------------------------------- 失败与重试


def test_retryable_failure_backs_off_and_stays_claimable(import_jobs) -> None:
    """可重试失败留在工作状态、清租约、按退避窗口延后 ``available_at``。"""
    repository = import_jobs["repository"]
    job = _submit(import_jobs)
    assert repository.claim_next_job("worker-a") is not None

    assert (
        repository.fail_job(
            job["job_id"],
            "worker-a",
            retryable=True,
            error_code="database_unavailable",
        )
        == "validating"
    )

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "validating"
    assert row.lease_owner is None
    assert row.lease_expires_at is None
    assert row.finished_at is None
    elapsed = (row.available_at - datetime.now(UTC)).total_seconds()
    assert 0 < elapsed <= RETRY_BACKOFF_SECONDS[0] + 5

    # 退避窗口内不可领取；到期后可被下一个 Worker 接手。
    assert repository.claim_next_job("worker-b") is None
    _make_available_now(job["job_id"])
    reclaimed = repository.claim_next_job("worker-b")
    assert reclaimed is not None
    assert reclaimed["attempt_count"] == 2


def test_retryable_failures_stop_at_attempt_limit(import_jobs) -> None:
    repository = import_jobs["repository"]
    job = _submit(import_jobs)

    for expected_attempt in range(1, 5):
        _make_available_now(job["job_id"])
        claimed = repository.claim_next_job(f"worker-{expected_attempt}")
        assert claimed is not None
        assert claimed["attempt_count"] == expected_attempt
        status = repository.fail_job(
            job["job_id"],
            f"worker-{expected_attempt}",
            retryable=True,
            error_code="database_unavailable",
        )
        if expected_attempt < 4:
            assert status == "validating"
        else:
            assert status == "validation_failed"

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "validation_failed"
    assert row.attempt_count == 4
    assert repository.claim_next_job("worker-final") is None


def test_expired_lease_at_attempt_limit_is_not_reclaimed(import_jobs) -> None:
    """Worker 崩溃在最后一次领取后也不能绕过总尝试次数上限。"""
    repository = import_jobs["repository"]
    job = _submit(import_jobs)

    for expected_attempt in range(1, 5):
        _make_available_now(job["job_id"])
        claimed = repository.claim_next_job(f"crashed-worker-{expected_attempt}")
        assert claimed is not None
        assert claimed["attempt_count"] == expected_attempt
        _force_lease_expiry(job["job_id"])

    assert repository.claim_next_job("recovery-worker") is None
    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "validation_failed"
    assert row.attempt_count == 4
    assert row.finished_at is not None
    with get_session_factory()() as session:
        audit = session.scalar(
            select(GovernanceAuditEvent).where(
                GovernanceAuditEvent.job_id == job["job_id"],
                GovernanceAuditEvent.action == "validation_failed",
            )
        )
    assert audit is not None
    assert audit.detail["error_code"] == "import_job_attempt_limit_exceeded"


def test_non_retryable_validation_failure_is_terminal(import_jobs) -> None:
    repository = import_jobs["repository"]
    job = _submit(import_jobs)
    assert repository.claim_next_job("worker-a") is not None

    assert (
        repository.fail_job(
            job["job_id"],
            "worker-a",
            retryable=False,
            error_code="import_payload_invalid",
        )
        == "validation_failed"
    )

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "validation_failed"
    assert row.finished_at is not None
    assert repository.claim_next_job("worker-b") is None


def test_publish_failed_is_the_only_retry_back_edge(import_jobs) -> None:
    """``publishing`` 不可重试失败转 ``publish_failed``，后者仍是可领取状态。"""
    repository = import_jobs["repository"]
    job = _submit(import_jobs)
    _to_approval_pending(import_jobs, job["job_id"])
    _approve(import_jobs, job["job_id"])

    publishing = repository.claim_next_job("worker-b")
    assert publishing is not None
    assert publishing["claimed_from"] == "approved"
    assert publishing["status"] == "publishing"

    assert (
        repository.fail_job(
            job["job_id"],
            "worker-b",
            retryable=False,
            error_code="import_payload_invalid",
        )
        == "publish_failed"
    )
    assert _job_row(job["job_id"]).status == "publish_failed"

    _make_available_now(job["job_id"])
    retry = repository.claim_next_job("worker-c")
    assert retry is not None
    assert retry["claimed_from"] == "publish_failed"
    assert retry["status"] == "publishing"

    # Attempts include the initial validation claim. A repeatedly failing
    # publish must not become an unbounded claim loop through publish_failed.
    assert retry["attempt_count"] == 3
    assert (
        repository.fail_job(
            job["job_id"],
            "worker-c",
            retryable=True,
            error_code="database_unavailable",
        )
        == "publishing"
    )
    _make_available_now(job["job_id"])
    final_attempt = repository.claim_next_job("worker-d")
    assert final_attempt is not None
    assert final_attempt["attempt_count"] == 4
    assert (
        repository.fail_job(
            job["job_id"],
            "worker-d",
            retryable=True,
            error_code="database_unavailable",
        )
        == "publish_failed"
    )
    assert repository.claim_next_job("worker-final") is None


def test_classify_import_job_exception_separates_retryable_errors() -> None:
    assert classify_import_job_exception(ValueError("坏载荷")) == (
        False,
        "import_payload_invalid",
    )
    assert classify_import_job_exception(OperationalError("x", {}, Exception())) == (
        True,
        "database_unavailable",
    )
    assert classify_import_job_exception(OSError("存储不可用")) == (
        True,
        "import_object_store_unavailable",
    )


# ------------------------------------------------------------------ 审批门禁


def test_unapproved_job_is_not_claimable_for_publish(import_jobs) -> None:
    """``approval_pending`` 不在可领取集合内：未审批任务不可发布。"""
    repository = import_jobs["repository"]
    job = _submit(import_jobs)
    _to_approval_pending(import_jobs, job["job_id"])

    assert _job_row(job["job_id"]).status == "approval_pending"
    assert repository.claim_next_job("worker-b") is None
    assert _job_row(job["job_id"]).status == "approval_pending"


def test_decision_requires_approval_pending_state(import_jobs) -> None:
    job = _submit(import_jobs)
    with pytest.raises(ImportJobStateConflictError):
        _approve(import_jobs, job["job_id"])


def test_decision_is_tenant_isolated(import_jobs) -> None:
    job = _submit(import_jobs)
    _to_approval_pending(import_jobs, job["job_id"])

    with pytest.raises(ImportJobNotFoundError):
        import_jobs["repository"].decide(
            _approval_context(f"other-{import_jobs['tenant_id']}"),
            job["job_id"],
            decision="approve",
            reason=None,
            actor_principal_id="approver-x",
        )


# -------------------------------------------------------- 发布原子性与快照并存


def test_publish_preserves_previous_snapshot_until_new_commit(import_jobs) -> None:
    """哈希不匹配时不写入任何行；成功发布后旧快照在同一事务转 ``superseded``。"""
    tenant_id = import_jobs["tenant_id"]
    importer = import_jobs["importer"]
    payload = import_jobs["payload"]
    outcome = _outcome(import_jobs)
    second = _sample_payload()
    second["parts"][0]["unit"] = "kg"
    second_hash = _canonical_hash(normalize_import_payload(second))

    first_batch = uuid4()
    importer.publish_validated_snapshot(
        tenant_id=tenant_id,
        source_system="erp",
        batch_id=first_batch,
        source_file="erp.json",
        source_snapshot_hash=outcome.snapshot_hash,
        calculation_rule_version=CALCULATION_RULE_VERSION,
        parts=payload["parts"],
        events=payload["cost_events"],
        inputs=payload["cost_event_inputs"],
        records=payload["cost_records"],
    )
    assert _published_batches(tenant_id) == {str(first_batch)}

    second_batch = uuid4()
    with pytest.raises(ValueError):
        importer.publish_validated_snapshot(
            tenant_id=tenant_id,
            source_system="erp",
            batch_id=second_batch,
            source_file="erp.json",
            source_snapshot_hash="0" * 64,
            calculation_rule_version=CALCULATION_RULE_VERSION,
            parts=second["parts"],
            events=second["cost_events"],
            inputs=second["cost_event_inputs"],
            records=second["cost_records"],
        )
    assert _published_batches(tenant_id) == {str(first_batch)}
    with get_session_factory()() as session:
        assert (
            session.scalar(
                select(DataLoadBatch.status).where(
                    DataLoadBatch.batch_id == second_batch
                )
            )
            is None
        )

    importer.publish_validated_snapshot(
        tenant_id=tenant_id,
        source_system="erp",
        batch_id=second_batch,
        source_file="erp.json",
        source_snapshot_hash=second_hash,
        calculation_rule_version=CALCULATION_RULE_VERSION,
        parts=second["parts"],
        events=second["cost_events"],
        inputs=second["cost_event_inputs"],
        records=second["cost_records"],
    )
    assert _published_batches(tenant_id) == {str(second_batch)}
    with get_session_factory()() as session:
        assert (
            session.scalar(
                select(DataLoadBatch.status).where(
                    DataLoadBatch.batch_id == first_batch
                )
            )
            == "superseded"
        )


# ------------------------------------------------------- Worker 端到端推进状态


@pytest.fixture
def worker(import_jobs) -> AgentWorker:
    from evaluation.fixture_model_provider import FixtureModelProvider

    instance = AgentWorker()
    instance.import_job_repository = import_jobs["repository"]
    instance.object_store = import_jobs["store"]
    instance.importer = import_jobs["importer"]
    # 只替换模型提供者，Worker 的租约、心跳和失败分类走真实实现。
    from app.agent import providers

    providers.get_model_provider = lambda: FixtureModelProvider()
    return instance


def test_worker_walks_full_state_machine(import_jobs, worker: AgentWorker) -> None:
    """submitted -> validating -> approval_pending -> approved -> publishing -> published。"""
    job = _submit(import_jobs)

    assert worker.process_next_import_job() is True
    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "approval_pending"
    assert row.valid_rows == row.total_rows
    assert row.error_rows == 0

    _approve(import_jobs, job["job_id"])
    assert worker.process_next_import_job() is True

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "published"
    assert row.published_batch_id is not None
    assert _published_batches(import_jobs["tenant_id"]) == {str(row.published_batch_id)}
    with get_session_factory()() as session:
        actions = session.scalars(
            select(GovernanceAuditEvent.action)
            .where(GovernanceAuditEvent.tenant_id == import_jobs["tenant_id"])
            .order_by(GovernanceAuditEvent.created_at)
        ).all()
    assert "validated" in actions
    assert "published" in actions


def test_worker_validation_failure_writes_redacted_errors(
    import_jobs, worker: AgentWorker
) -> None:
    """校验失败的任务不可查询为成本事实，错误行脱敏且批次标记 ``failed``。"""
    broken = {
        key: [dict(row) for row in value]
        for key, value in import_jobs["payload"].items()
    }
    broken["parts"][0]["unit"] = ""
    job = _submit(import_jobs, payload=broken)

    assert worker.process_next_import_job() is True
    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "validation_failed"
    assert row.error_rows and row.error_rows > 0

    with get_session_factory()() as session:
        assert (
            session.scalar(
                select(DataLoadBatch.status).where(
                    DataLoadBatch.batch_id == job["job_id"]
                )
            )
            == "failed"
        )
        errors = session.scalars(
            select(DataLoadError.error_message).where(
                DataLoadError.batch_id == job["job_id"]
            )
        ).all()
    assert errors
    # 错误只在读取层脱敏：行内文本保持原样，供内部追查；API 永不下发它。
    with get_session_factory()() as session:
        raw = session.scalars(
            select(DataLoadError.error_message).where(
                DataLoadError.batch_id == job["job_id"]
            )
        ).all()
    assert any("不能为空" in str(message) for message in raw)
    listed, _ = import_jobs["repository"].list_job_errors(
        _approval_context(import_jobs["tenant_id"]), job["job_id"], page=1, page_size=20
    )
    assert listed
    assert {item["error_message"] for item in listed} == {"导入校验错误详情已脱敏。"}
    assert _published_batches(import_jobs["tenant_id"]) == set()


def test_worker_publish_failure_keeps_previous_snapshot(
    import_jobs, worker: AgentWorker
) -> None:
    """发布阶段失败时旧快照保持 published，任务进入 ``publish_failed`` 并可重试。"""
    tenant_id = import_jobs["tenant_id"]
    baseline_batch = uuid4()
    payload = import_jobs["payload"]
    baseline_payload = {
        key: [dict(row) for row in rows] for key, rows in payload.items()
    }
    baseline_payload["parts"][0]["unit"] = "kg"
    baseline_hash = _canonical_hash(normalize_import_payload(baseline_payload))
    import_jobs["importer"].publish_validated_snapshot(
        tenant_id=tenant_id,
        source_system="erp",
        batch_id=baseline_batch,
        source_file="baseline.json",
        source_snapshot_hash=baseline_hash,
        calculation_rule_version=CALCULATION_RULE_VERSION,
        parts=baseline_payload["parts"],
        events=baseline_payload["cost_events"],
        inputs=baseline_payload["cost_event_inputs"],
        records=baseline_payload["cost_records"],
    )

    job = _submit(import_jobs)
    assert worker.process_next_import_job() is True
    _approve(import_jobs, job["job_id"])
    claimed = worker.import_job_repository.claim_next_job(worker.worker_id)
    assert claimed is not None
    assert claimed["status"] == "publishing"

    # 发布前把任务载荷换成与快照哈希不一致的内容，触发不可重试的发布失败。
    corrupt = import_jobs["store"].put(
        encode_import_payload(
            normalize_import_payload(
                {
                    "parts": [],
                    "cost_events": [],
                    "cost_event_inputs": [],
                    "cost_records": [],
                }
            )
        )
    )
    with get_session_factory()() as session:
        session.execute(
            update(DataImportJob)
            .where(DataImportJob.job_id == job["job_id"])
            .values(source_object_key=corrupt.object_key)
        )
        session.commit()
    claimed["source_object_key"] = corrupt.object_key

    worker._execute_import_job(claimed)

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "publish_failed"
    assert _published_batches(tenant_id) == {str(baseline_batch)}
    with get_session_factory()() as session:
        actions = session.scalars(
            select(GovernanceAuditEvent.action).where(
                GovernanceAuditEvent.tenant_id == tenant_id,
                GovernanceAuditEvent.job_id == job["job_id"],
            )
        ).all()
    assert "publishing" in actions
    assert "publish_failed" in actions

    # 已批准任务的发布失败只能重试发布：恢复载荷后被下一个 Worker 接手到 published。
    assert row.attempt_count == 2
    with get_session_factory()() as session:
        session.execute(
            update(DataImportJob)
            .where(DataImportJob.job_id == job["job_id"])
            .values(source_object_key=job["source_object_key"])
        )
        session.commit()
    retried = worker.import_job_repository.claim_next_job(worker.worker_id)
    assert retried is not None
    assert retried["claimed_from"] == "publish_failed"
    assert retried["status"] == "publishing"
    retried["source_object_key"] = job["source_object_key"]
    worker._execute_import_job(retried)

    row = _job_row(job["job_id"])
    assert row is not None
    assert row.status == "published"
    assert _published_batches(tenant_id) == {str(row.published_batch_id)}
    with get_session_factory()() as session:
        assert (
            session.scalar(
                select(DataLoadBatch.status).where(
                    DataLoadBatch.batch_id == baseline_batch
                )
            )
            == "superseded"
        )
