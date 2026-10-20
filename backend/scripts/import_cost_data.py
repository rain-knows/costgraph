"""提交成本数据导入任务（规格 6.3）。

本脚本只**提交**导入任务，不再同一事务直接发布快照：

1. 读取 JSON/CSV 输入并组装四类记录载荷；
2. 载荷写入本地内容寻址对象存储，数据库只落 ``object_key`` / ``size`` / ``sha256``；
3. 通过 ``ImportJobService.submit()`` 创建 ``status=submitted`` 的任务；
4. 可选 ``--auto-approve``：以 ``tenant_admin`` 身份提交，并驱动完整状态机
   （``submitted -> validating -> approval_pending -> approved -> publishing ->
   published``），供开发与端到端验证使用。

校验、投影生成与发布始终由 Worker 完成；本脚本只是驱动方，不写任何事实表。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_IDEMPOTENCY_PREFIX = "cli-import"
SOURCE_FILE_NAME_LIMIT = 128
DRIVE_TIMEOUT_SECONDS = 180.0
DRIVE_POLL_SECONDS = 0.2
TERMINAL_VALIDATION_STATUSES = frozenset({"approval_pending", "validation_failed"})
TERMINAL_PUBLISH_STATUSES = frozenset({"published", "publish_failed"})
#: 无论哪个阶段都不再会自动推进的状态；避免驱动循环空等超时。
FINAL_STATUSES = frozenset(
    {"validation_failed", "rejected", "published", "publish_failed", "cancelled"}
)


def _payload(args: argparse.Namespace) -> dict[str, list[dict[str, Any]]]:
    from app.services.cost_data_import_service import load_records

    return {
        "parts": load_records(args.parts),
        "cost_events": load_records(args.events),
        "cost_event_inputs": load_records(args.inputs),
        "cost_records": load_records(args.records),
    }


def _source_file_name(args: argparse.Namespace) -> str:
    names = [
        Path(value).name
        for value in (args.parts, args.events, args.inputs, args.records)
    ]
    return ", ".join(names)[:SOURCE_FILE_NAME_LIMIT]


def _principal(tenant_id: str, principal_id: str, *, auto_approve: bool):
    from app.domain.authorization import ExecutionPrincipal

    return ExecutionPrincipal(
        principal_id=principal_id,
        tenant_id=tenant_id,
        roles=("tenant_admin",) if auto_approve else ("data_operator",),
    )


def _submit(args: argparse.Namespace, principal: Any, payload: dict[str, list[dict]]):
    from app.schemas.data_admin import ImportJobCreateRequest
    from app.services.import_job_service import ImportJobService

    request = ImportJobCreateRequest(
        source_system=args.source_system,
        source_file_name=_source_file_name(args),
        calculation_rule_version=args.calculation_rule_version,
        payload=payload,
    )
    return ImportJobService().submit(
        principal,
        idempotency_key=args.idempotency_key
        or f"{DEFAULT_IDEMPOTENCY_PREFIX}-{uuid4().hex}",
        request=request,
    )


def _read_status(principal: Any, job_id: UUID) -> str:
    from app.services.import_job_service import ImportJobService

    return ImportJobService().get_job(principal, job_id).status


def _drive(worker: Any, principal: Any, job_id: UUID, targets: frozenset[str]) -> str:
    """驱动 Worker 处理任务，直到进入目标状态或已知终态。

    幂等提交可能返回一个已经结束的旧任务；此时直接返回其终态，避免
    ``--auto-approve`` 因重复执行而把成功任务误报为异常。
    """
    deadline = time.monotonic() + DRIVE_TIMEOUT_SECONDS
    while True:
        status = _read_status(principal, job_id)
        if status in targets:
            return status
        if status in FINAL_STATUSES:
            return status
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"导入任务未在 {DRIVE_TIMEOUT_SECONDS:.0f}s 内到达 {sorted(targets)}，"
                f"当前状态 {status}。"
            )
        if not worker.process_next_import_job():
            time.sleep(DRIVE_POLL_SECONDS)


def _approve(principal: Any, job_id: UUID, reason: str) -> str:
    from app.schemas.data_admin import ImportDecisionRequest
    from app.services.import_job_service import ImportJobService

    record = ImportJobService().decide(
        principal,
        job_id,
        ImportDecisionRequest(decision="approve", reason=reason),
    )
    return record.status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="提交成本 v2 JSON/CSV 导入任务（校验与发布由 Worker 完成）。"
    )
    parser.add_argument(
        "--parts", default=str(PROJECT_ROOT / "data/samples/parts.json")
    )
    parser.add_argument(
        "--events", default=str(PROJECT_ROOT / "data/samples/cost_events.json")
    )
    parser.add_argument(
        "--inputs", default=str(PROJECT_ROOT / "data/samples/cost_event_inputs.json")
    )
    parser.add_argument(
        "--records", default=str(PROJECT_ROOT / "data/samples/cost_records.json")
    )
    parser.add_argument("--tenant-id", default="local-costgraph-tenant")
    parser.add_argument("--principal-id", default="local-costgraph-user")
    parser.add_argument("--source-system", default="costgraph_samples")
    parser.add_argument(
        "--calculation-rule-version",
        default=None,
        help="默认取当前服务端规则版本 CALCULATION_RULE_VERSION。",
    )
    parser.add_argument(
        "--idempotency-key",
        default=None,
        help="缺省自动生成；同租户同键重复提交返回已有任务。",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="以 tenant_admin 身份提交并驱动完整状态机直到发布（开发/端到端验证）。",
    )
    args = parser.parse_args()
    if args.calculation_rule_version is None:
        from app.domain.cost import CALCULATION_RULE_VERSION

        args.calculation_rule_version = CALCULATION_RULE_VERSION

    payload = _payload(args)
    principal = _principal(
        args.tenant_id, args.principal_id, auto_approve=args.auto_approve
    )
    submission = _submit(args, principal, payload)
    job_id = submission.job_id
    output: dict[str, Any] = {
        "job_id": str(job_id),
        "status": submission.status,
        "duplicate": submission.duplicate,
        "snapshot_hash": submission.snapshot_hash,
        "source_object_key": submission.source_object_key,
        "total_rows": submission.total_rows,
    }

    if args.auto_approve:
        from app.worker import AgentWorker

        worker = AgentWorker()
        output["validation_status"] = _drive(
            worker, principal, job_id, TERMINAL_VALIDATION_STATUSES
        )
        if output["validation_status"] == "approval_pending":
            output["decision_status"] = _approve(
                principal, job_id, "CLI --auto-approve"
            )
            output["publish_status"] = _drive(
                worker, principal, job_id, TERMINAL_PUBLISH_STATUSES
            )
        output["status"] = _read_status(principal, job_id)

    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0 if output["status"] in {"submitted", "published"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
