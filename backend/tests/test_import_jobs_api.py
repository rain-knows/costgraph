"""``/api/data-admin/imports*`` HTTP 契约测试（规格第 4、5、9 节）。

这些测试只使用 TestClient + 假 repository + 假对象存储，验证可观察的 HTTP
行为：状态码、错误码、幂等语义、权限矩阵、响应体不泄露载荷、审计脱敏。
真实 PostgreSQL 的状态机、租约与事务语义由 ``test_import_jobs_postgres.py``
覆盖。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.context import get_current_principal
from app.domain.authorization import ExecutionPrincipal
from app.main import create_app
from app.repositories.import_job_repository import (
    CANCELLABLE_JOB_STATUSES,
    DECISION_TO_STATUS,
    ImportJobNotFoundError,
    validate_job_transition,
)
from app.repositories.postgres_import_job_repository import _safe_audit_detail
from app.services.import_job_service import ImportJobService, get_import_job_service

SAMPLES = Path(__file__).parents[2] / "data" / "samples"
SELECTED_EVENTS = {"E-RAW-001", "E-SEMI-001", "E-INJ-001", "E-PAINT-001", "E-FG-001"}
PAYLOAD_CANARY = "SECRET_PAYLOAD_CANARY_3f7a"
RULE_VERSION = "cost-rollup-v2"


def _sample_payload() -> dict[str, list[dict]]:
    def load(name: str) -> list[dict]:
        return json.loads((SAMPLES / name).read_text(encoding="utf-8"))

    events = [
        row for row in load("cost_events.json") if row["event_id"] in SELECTED_EVENTS
    ]
    part_ids = {row["part_id"] for row in events}
    return {
        "parts": [row for row in load("parts.json") if row["part_id"] in part_ids],
        "cost_events": events,
        "cost_event_inputs": [
            row
            for row in load("cost_event_inputs.json")
            if row["event_id"] in SELECTED_EVENTS
        ],
        "cost_records": [
            row
            for row in load("cost_records.json")
            if row["event_id"] in SELECTED_EVENTS
        ],
    }


class FakeObjectStore:
    """内存对象存储：接口与 ``LocalImportObjectStore`` 一致，永不写入磁盘。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, data: bytes):
        import hashlib
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Ref:
            object_key: str
            size: int
            sha256: str

        digest = hashlib.sha256(data).hexdigest()
        key = f"imports/{digest[:2]}/{digest}"
        self.objects[key] = data
        return _Ref(object_key=key, size=len(data), sha256=digest)

    def get(self, object_key: str) -> bytes:
        return self.objects[object_key]


class FakeImportJobRepository:
    """内存任务持久层：只实现服务/API 层依赖的行为契约。"""

    def __init__(self) -> None:
        self.jobs: dict[UUID, dict] = {}
        self.audit_events: list[dict] = []

    # ------------------------------------------------------------- 内部工具
    def _matching(self, tenant_id: str, **criteria) -> dict | None:
        for record in self.jobs.values():
            if record["tenant_id"] != tenant_id:
                continue
            if all(record[key] == value for key, value in criteria.items()):
                return record
        return None

    def _require(self, context, job_id: UUID) -> dict:
        record = self.jobs.get(job_id)
        if record is None or record["tenant_id"] != context.principal.tenant_id:
            raise ImportJobNotFoundError(job_id)
        return record

    def _public(self, record: dict) -> dict:
        return {**record, "error_codes": []}

    # ------------------------------------------------------------ 协议方法
    def create_job(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        principal_roles: list[str],
        source_system: str,
        source_object_key: str,
        source_object_size: int,
        source_object_sha256: str,
        snapshot_hash: str,
        calculation_rule_version: str,
        idempotency_key: str,
        total_rows: int,
    ) -> dict:
        existing = self._matching(tenant_id, idempotency_key=idempotency_key)
        if existing is None:
            existing = self._matching(
                tenant_id,
                source_system=source_system,
                snapshot_hash=snapshot_hash,
                calculation_rule_version=calculation_rule_version,
            )
        if existing is not None:
            return {**self._public(existing), "duplicate": True}
        now = datetime.now(UTC)
        record = {
            "job_id": uuid4(),
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "principal_roles": list(principal_roles),
            "source_system": source_system,
            "source_file_name": source_object_key.rsplit("/", 1)[-1][:128],
            "source_object_key": source_object_key,
            "source_object_size": source_object_size,
            "source_object_sha256": source_object_sha256,
            "snapshot_hash": snapshot_hash,
            "calculation_rule_version": calculation_rule_version,
            "idempotency_key": idempotency_key,
            "status": "submitted",
            "total_rows": total_rows,
            "valid_rows": 0,
            "error_rows": 0,
            "error_summary": None,
            "decision": None,
            "decision_reason": None,
            "decided_by": None,
            "decided_at": None,
            "published_batch_id": None,
            "attempt_count": 0,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
        }
        self.jobs[record["job_id"]] = record
        return {**self._public(record), "duplicate": False}

    def list_jobs(self, context, *, status, page, page_size):
        rows = [
            record
            for record in self.jobs.values()
            if record["tenant_id"] == context.principal.tenant_id
            and (status is None or record["status"] == status)
        ]
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        start = (page - 1) * page_size
        return rows[start : start + page_size], len(rows)

    def get_job(self, context, job_id):
        record = self.jobs.get(job_id)
        if record is None or record["tenant_id"] != context.principal.tenant_id:
            return None
        return self._public(record)

    def list_job_errors(self, context, job_id, *, page, page_size):
        record = self.jobs.get(job_id)
        if record is None or record["tenant_id"] != context.principal.tenant_id:
            return None
        return [], 0

    def decide(
        self, context, job_id, *, decision: str, reason, actor_principal_id: str
    ):
        record = self._require(context, job_id)
        target = DECISION_TO_STATUS[decision]
        validate_job_transition(record["status"], target)
        record["status"] = target
        record["decision"] = target
        record["decision_reason"] = reason
        record["decided_by"] = actor_principal_id
        record["decided_at"] = datetime.now(UTC)
        return self._public(record)

    def cancel(self, context, job_id, *, actor_principal_id: str):
        record = self._require(context, job_id)
        validate_job_transition(record["status"], "cancelled")
        record["status"] = "cancelled"
        record["finished_at"] = datetime.now(UTC)
        return self._public(record)

    def append_audit_event(self, **kwargs) -> None:
        self.audit_events.append(
            {**kwargs, "detail": _safe_audit_detail(kwargs.get("detail"))}
        )

    # ------------------------------------------------------- 测试辅助（非协议）
    def force_status(self, job_id: UUID, status: str) -> None:
        self.jobs[job_id]["status"] = status


def _principal(role: str, tenant_id: str = "tenant-a") -> ExecutionPrincipal:
    return ExecutionPrincipal(
        principal_id=f"{role}-principal", tenant_id=tenant_id, roles=(role,)
    )


@pytest.fixture
def import_api():
    repository = FakeImportJobRepository()
    store = FakeObjectStore()
    app = create_app()
    app.dependency_overrides[get_import_job_service] = lambda: ImportJobService(
        repository, store
    )
    app.dependency_overrides[get_current_principal] = lambda: _principal("tenant_admin")
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, repository, store, app
    finally:
        app.dependency_overrides.clear()


def _submit(
    client: TestClient,
    *,
    key: str = "submit-key-1",
    source_system: str = "mes",
    rule_version: str = RULE_VERSION,
    payload: dict | None = None,
):
    return client.post(
        "/api/data-admin/imports",
        headers={"Idempotency-Key": key},
        json={
            "source_system": source_system,
            "source_file_name": "cost_events.json",
            "calculation_rule_version": rule_version,
            "payload": payload if payload is not None else _sample_payload(),
        },
    )


def test_submit_returns_submitted_job_without_payload_or_object_content(import_api):
    client, repository, store, _ = import_api
    payload = _sample_payload()
    payload["parts"][0]["part_description"] = PAYLOAD_CANARY
    response = _submit(client, payload=payload)
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]

    body = response.json()
    assert body["status"] == "submitted"
    assert body["duplicate"] is False
    assert body["source_system"] == "mes"
    assert body["calculation_rule_version"] == RULE_VERSION
    # 响应只暴露由对象键派生的文件名，不含上传时的原始文件名/载荷。
    assert body["source_file_name"] == body["source_object_key"].rsplit("/", 1)[-1]
    assert set(body) == {
        "job_id",
        "status",
        "duplicate",
        "source_system",
        "source_file_name",
        "source_object_key",
        "source_object_size",
        "source_object_sha256",
        "snapshot_hash",
        "calculation_rule_version",
        "total_rows",
        "created_at",
    }
    assert PAYLOAD_CANARY not in response.text
    assert "payload" not in response.text

    job = repository.jobs[UUID(body["job_id"])]
    assert job["source_object_key"] in store.objects
    assert PAYLOAD_CANARY.encode() in store.objects[job["source_object_key"]]
    assert PAYLOAD_CANARY not in json.dumps(
        repository.audit_events, ensure_ascii=False, default=str
    )

    detail = client.get(f"/api/data-admin/imports/{body['job_id']}")
    assert detail.status_code == 200
    assert PAYLOAD_CANARY not in detail.text
    assert "payload" not in detail.text


def test_missing_or_blank_idempotency_key_is_422(import_api):
    client, repository, _, _ = import_api
    body = {
        "source_system": "mes",
        "source_file_name": "cost_events.json",
        "calculation_rule_version": RULE_VERSION,
        "payload": {"parts": []},
    }
    missing = client.post("/api/data-admin/imports", json=body)
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "idempotency_key_required"
    blank = client.post(
        "/api/data-admin/imports", headers={"Idempotency-Key": "   "}, json=body
    )
    assert blank.status_code == 422
    assert blank.json()["detail"]["code"] == "idempotency_key_required"
    too_long = client.post(
        "/api/data-admin/imports", headers={"Idempotency-Key": "k" * 129}, json=body
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["code"] == "idempotency_key_required"
    assert repository.jobs == {}


def test_duplicate_submission_returns_existing_job_without_second_audit(import_api):
    client, repository, _, _ = import_api
    first = _submit(client, key="dup-key")
    assert first.status_code == 200
    job_id = first.json()["job_id"]

    same_key = _submit(client, key="dup-key")
    assert same_key.status_code == 200
    assert same_key.json()["job_id"] == job_id
    assert same_key.json()["duplicate"] is True

    same_snapshot_different_key = _submit(
        client, key="another-key", source_system="mes"
    )
    assert same_snapshot_different_key.status_code == 200
    assert same_snapshot_different_key.json()["job_id"] == job_id
    assert same_snapshot_different_key.json()["duplicate"] is True

    assert len(repository.jobs) == 1
    assert [event["action"] for event in repository.audit_events] == ["submitted"]


def test_same_snapshot_with_new_rule_version_creates_new_job(import_api):
    client, repository, _, _ = import_api
    first = _submit(client, key="rule-v1", rule_version="cost-rollup-v1")
    second = _submit(client, key="rule-v2", rule_version="cost-rollup-v2")
    assert first.json()["job_id"] != second.json()["job_id"]
    assert second.json()["duplicate"] is False
    assert len(repository.jobs) == 2


def test_unknown_payload_table_is_rejected_without_creating_job(import_api):
    client, repository, _, _ = import_api
    response = _submit(
        client,
        payload={
            "parts": [],
            "cost_events": [],
            "cost_event_inputs": [],
            "cost_records": [],
            "ledger_entries": [{"id": "x"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "import_payload_invalid"
    assert repository.jobs == {}


def test_viewer_is_denied_on_every_import_endpoint(import_api):
    client, _, _, app = import_api
    app.dependency_overrides[get_current_principal] = lambda: _principal("viewer")
    assert _submit(client).status_code == 403
    assert (
        _submit(client).json()["detail"]["code"] == "data_import_submit_access_denied"
    )
    job_id = uuid4()
    for path in (
        "/api/data-admin/imports",
        f"/api/data-admin/imports/{job_id}",
        f"/api/data-admin/imports/{job_id}/errors",
    ):
        response = client.get(path)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "data_management_access_denied"
        assert response.headers["X-Request-ID"]
    for path in (
        f"/api/data-admin/imports/{job_id}/decision",
        f"/api/data-admin/imports/{job_id}/cancel",
    ):
        response = client.post(path, json={"decision": "approve"})
        assert response.status_code == 403


def test_data_operator_can_submit_read_and_cancel_but_not_approve(import_api):
    client, repository, _, app = import_api
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_operator"
    )
    submitted = _submit(client)
    assert submitted.status_code == 200
    job_id = submitted.json()["job_id"]

    # data_operator 拥有 data_management.read，可读但不可审批。
    assert client.get("/api/data-admin/imports").status_code == 200
    assert client.get(f"/api/data-admin/imports/{job_id}").status_code == 200
    decision = client.post(
        f"/api/data-admin/imports/{job_id}/decision", json={"decision": "approve"}
    )
    assert decision.status_code == 403
    assert decision.json()["detail"]["code"] == "data_import_approve_access_denied"

    cancelled = client.post(f"/api/data-admin/imports/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert repository.jobs[UUID(job_id)]["status"] == "cancelled"


def test_data_approver_can_read_and_decide_but_not_submit(import_api):
    client, repository, _, app = import_api
    submitted = _submit(client)
    job_id = submitted.json()["job_id"]
    repository.force_status(UUID(job_id), "approval_pending")

    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_approver"
    )
    assert _submit(client, key="approver-submit").status_code == 403
    assert client.get("/api/data-admin/imports").status_code == 200
    assert client.get(f"/api/data-admin/imports/{job_id}").status_code == 200
    assert client.get(f"/api/data-admin/imports/{job_id}/errors").status_code == 200
    assert client.post(f"/api/data-admin/imports/{job_id}/cancel").status_code == 403

    approved = client.post(
        f"/api/data-admin/imports/{job_id}/decision",
        json={"decision": "approve", "reason": "校验结果已复核"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["decision"] == "approved"
    assert approved.json()["decided_by"] == "data_approver-principal"
    assert repository.jobs[UUID(job_id)]["decision_reason"] == "校验结果已复核"
    assert [event["action"] for event in repository.audit_events] == [
        "submitted",
        "approved",
    ]


def test_decision_on_non_approval_pending_status_is_409(import_api):
    client, repository, _, app = import_api
    submitted = _submit(client)
    job_id = submitted.json()["job_id"]
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_approver"
    )
    for status in ("submitted", "validating", "published", "cancelled"):
        repository.force_status(UUID(job_id), status)
        response = client.post(
            f"/api/data-admin/imports/{job_id}/decision", json={"decision": "approve"}
        )
        assert response.status_code == 409, status
        assert response.json()["detail"]["code"] == "import_job_state_conflict"
        assert response.json()["detail"]["details"] == {
            "current_status": status,
            "target_status": "approved",
        }
    repository.force_status(UUID(job_id), "approval_pending")
    rejected = client.post(
        f"/api/data-admin/imports/{job_id}/decision", json={"decision": "reject"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_cancel_is_409_outside_cancellable_statuses(import_api):
    client, repository, _, app = import_api
    submitted = _submit(client)
    job_id = submitted.json()["job_id"]
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_operator"
    )
    for status in ("approved", "publishing", "published", "rejected"):
        repository.force_status(UUID(job_id), status)
        response = client.post(f"/api/data-admin/imports/{job_id}/cancel")
        assert response.status_code == 409, status
        assert response.json()["detail"]["code"] == "import_job_state_conflict"
    for status in CANCELLABLE_JOB_STATUSES:
        repository.force_status(UUID(job_id), status)
        response = client.post(f"/api/data-admin/imports/{job_id}/cancel")
        assert response.status_code == 200, status
        assert response.json()["status"] == "cancelled"
        assert repository.jobs[UUID(job_id)]["status"] == "cancelled"


def test_cross_tenant_reads_and_writes_are_404(import_api):
    client, repository, _, app = import_api
    submitted = _submit(client)
    job_id = submitted.json()["job_id"]
    repository.force_status(UUID(job_id), "approval_pending")

    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "tenant_admin", tenant_id="tenant-b"
    )
    for path in (
        f"/api/data-admin/imports/{job_id}",
        f"/api/data-admin/imports/{job_id}/errors",
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "import_job_not_found"
    decision = client.post(
        f"/api/data-admin/imports/{job_id}/decision", json={"decision": "approve"}
    )
    assert decision.status_code == 404
    cancel = client.post(f"/api/data-admin/imports/{job_id}/cancel")
    assert cancel.status_code == 404
    assert client.get("/api/data-admin/imports").json()["total"] == 0
    assert repository.jobs[UUID(job_id)]["status"] == "approval_pending"


def test_audit_events_are_append_only_and_free_of_secrets(import_api):
    client, repository, _, app = import_api
    payload = _sample_payload()
    payload["cost_records"][0]["source_document_no"] = PAYLOAD_CANARY
    submitted = _submit(client, payload=payload)
    job_id = submitted.json()["job_id"]
    repository.force_status(UUID(job_id), "approval_pending")
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_approver"
    )
    client.post(
        f"/api/data-admin/imports/{job_id}/decision",
        json={"decision": "approve", "reason": f"canary={PAYLOAD_CANARY}"},
    )

    events = repository.audit_events
    assert [event["action"] for event in events] == ["submitted", "approved"]
    assert all(event["tenant_id"] == "tenant-a" for event in events)
    assert all(event["target_type"] == "import_job" for event in events)
    assert all(event["outcome"] == "success" for event in events)
    serialized = json.dumps(events, ensure_ascii=False, default=str)
    for canary in (PAYLOAD_CANARY, "PO-", "SRC-", "password", "postgresql://"):
        assert canary not in serialized
    for event in events:
        assert set(event["detail"]) <= {
            "status",
            "previous_status",
            "total_rows",
            "valid_rows",
            "error_rows",
            "attempt_count",
            "error_code",
            "retryable",
            "error_codes",
            "batch_id",
            "job_id",
            "decision",
        }


def test_tenant_admin_has_full_matrix_and_reason_length_is_bounded(import_api):
    client, repository, _, _ = import_api
    submitted = _submit(client)
    job_id = submitted.json()["job_id"]
    assert client.get("/api/data-admin/imports").status_code == 200
    assert client.get(f"/api/data-admin/imports/{job_id}").status_code == 200
    assert client.get(f"/api/data-admin/imports/{job_id}/errors").status_code == 200
    repository.force_status(UUID(job_id), "approval_pending")
    too_long = client.post(
        f"/api/data-admin/imports/{job_id}/decision",
        json={"decision": "approve", "reason": "x" * 501},
    )
    assert too_long.status_code == 422
    assert client.post(f"/api/data-admin/imports/{job_id}/cancel").status_code == 200
