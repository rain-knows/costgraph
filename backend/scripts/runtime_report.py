"""Emit operational Runtime metrics from the durable PostgreSQL run tables."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="输出 Runtime 运行指标 JSON。")
    parser.add_argument("--since", default="24h", help="时间窗口，例如 24h 或 7d")
    args = parser.parse_args()
    try:
        window = _parse_window(args.since)
        payload = _collect(window)
    except Exception as exc:  # noqa: BLE001 - CLI must expose safe status
        payload = {
            "status": "unavailable",
            "since": args.since,
            "message": "Runtime 数据库暂时不可用。",
            "error_type": type(exc).__name__,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload.get("status") != "error" else 1


def _parse_window(value: str) -> timedelta:
    normalized = value.strip().lower()
    if normalized.endswith("h"):
        return timedelta(hours=float(normalized[:-1]))
    if normalized.endswith("d"):
        return timedelta(days=float(normalized[:-1]))
    raise ValueError("--since 必须使用 h 或 d 后缀")


def _collect(window: timedelta) -> dict[str, object]:
    from sqlalchemy import select

    from app.db.engine import get_session_factory
    from app.db.models import RuntimeAgentRun, RuntimeAgentRunEvent

    cutoff = datetime.now(UTC) - window
    with get_session_factory()() as session:
        runs = session.scalars(
            select(RuntimeAgentRun).where(RuntimeAgentRun.created_at >= cutoff)
        ).all()
        events = session.scalars(
            select(RuntimeAgentRunEvent).where(
                RuntimeAgentRunEvent.created_at >= cutoff
            )
        ).all()
    total = len(runs)
    succeeded = sum(item.status == "succeeded" for item in runs)
    clarification = sum(
        bool((item.result_json or {}).get("outcome") == "needs_clarification")
        for item in runs
    )
    retries = sum(max(0, item.attempt_count - 1) for item in runs)
    budget_failures = sum(
        (item.public_error_json or {}).get("code") == "budget_exceeded" for item in runs
    )
    queue_durations = _stage_durations(runs, "created_at", "started_at")
    workflow_durations = _stage_durations(runs, "started_at", "finalizing_at")
    finalization_durations = _stage_durations(runs, "finalizing_at", "finished_at")
    total_durations = _stage_durations(runs, "created_at", "finished_at")
    tool_durations = [
        (item.finished_at - item.started_at).total_seconds() * 1000
        for item in events
        if item.event_type == "tool" and item.finished_at and item.started_at
    ]
    tool_errors: dict[str, int] = {}
    for event in events:
        if event.event_type != "tool" or event.status not in {
            "error",
            "denied",
            "blocked",
            "cancelled",
        }:
            continue
        code = str((event.payload_json or {}).get("error_code") or "unknown")
        tool_errors[code] = tool_errors.get(code, 0) + 1
    call_total_durations: list[float] = []
    call_execution_durations: list[float] = []
    call_coordination_durations: list[float] = []
    for event in events:
        payload = event.payload_json or {}
        total_duration = _number(payload.get("duration_ms"))
        execution_duration = _number(payload.get("execution_duration_ms"))
        if total_duration is None or execution_duration is None:
            continue
        call_total_durations.append(total_duration)
        call_execution_durations.append(execution_duration)
        call_coordination_durations.append(
            max(0.0, total_duration - execution_duration)
        )
    return {
        "status": "ok",
        "since": cutoff.isoformat(),
        "runs": {
            "total": total,
            "success_rate": round(succeeded / total, 4) if total else 0,
            "clarification_rate": round(clarification / total, 4) if total else 0,
            "retry_count": retries,
            "budget_failure_count": budget_failures,
            "phase_duration_ms": {
                "queue": _percentiles(queue_durations),
                "workflow": _percentiles(workflow_durations),
                "finalization": _percentiles(finalization_durations),
                "total": _percentiles(total_durations),
            },
        },
        "tools": {
            "error_count_by_code": tool_errors,
            "duration_ms": _percentiles(tool_durations),
        },
        "calls": {
            "total_duration_ms": _percentiles(call_total_durations),
            "execution_duration_ms": _percentiles(call_execution_durations),
            "coordination_duration_ms": _percentiles(call_coordination_durations),
        },
    }


def _stage_durations(runs, start_name: str, finish_name: str) -> list[float]:
    values = []
    for run in runs:
        started_at = getattr(run, start_name)
        finished_at = getattr(run, finish_name)
        if started_at and finished_at:
            values.append((finished_at - started_at).total_seconds() * 1000)
    return values


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None}
    ordered = sorted(values)

    def pick(percentile: float) -> float:
        index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
        return round(ordered[index], 3)

    return {"p50": pick(0.5), "p95": pick(0.95)}


if __name__ == "__main__":
    raise SystemExit(main())
