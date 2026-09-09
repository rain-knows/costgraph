from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="运行制造业成本 Agent 回归评测集。")
    parser.add_argument("--live", action="store_true", help="允许使用已配置的真实模型")
    parser.add_argument(
        "--trials",
        type=_positive_int,
        default=1,
        help="每个 Case 独立执行次数；只有全部 Trial 通过才判定 Case 通过",
    )
    args = parser.parse_args()

    if not args.live:
        os.environ["DEEPSEEK_API_KEY"] = ""
    from app.agent.graph import run_runtime
    from app.agent.runtime_contracts import RuntimeRunRequest
    from app.domain.conversation import empty_conversation_context

    runtime_services = None
    if not args.live:
        import app.agent.tools as agent_tools
        from evaluation.fixture_cost_repository import CostFixtureRepository
        from evaluation.fixture_model_provider import build_fixture_runtime_services

        agent_tools.cost_repository = CostFixtureRepository()
        runtime_services = build_fixture_runtime_services()

    cases = json.loads(
        (BACKEND_ROOT / "evaluation" / "cases.json").read_text(encoding="utf-8")
    )
    results: list[dict[str, object]] = []
    failed = 0
    passed_trials = 0
    failed_trials = 0
    for case in cases:
        trial_results: list[dict[str, object]] = []
        for trial in range(args.trials):
            conversation_id = f"eval-conversation-{uuid4().hex}"
            conversation_context = empty_conversation_context()
            recent_messages: list[dict[str, str]] = []
            clarification_count = 0
            result: dict = {}
            for index, question in enumerate(case["turns"]):
                result = run_runtime(
                    RuntimeRunRequest(
                        question=question,
                        conversation_id=conversation_id,
                        turn_id=f"eval-turn-{uuid4().hex}",
                        message_id=(
                            f"eval-{case['id']}-{trial}-{index}-{uuid4().hex[:8]}"
                        ),
                        requested_capabilities=["system_help", "cost_calculation"],
                        conversation_context=conversation_context,
                        recent_messages=recent_messages,
                        include_internal=True,
                    ),
                    runtime_services=runtime_services,
                )
                conversation_context = result.pop("_conversation_context")
                result.pop("_audit_trace", None)
                recent_messages = [
                    *recent_messages,
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": result["final_message"]},
                ][-6:]
                if result["outcome"] == "needs_clarification":
                    clarification_count += 1

            errors = validate_case(result, case["expect"], clarification_count)
            trial_passed = not errors
            passed_trials += int(trial_passed)
            failed_trials += int(not trial_passed)
            trial_results.append(
                {
                    "trial": trial + 1,
                    "passed": trial_passed,
                    "errors": errors,
                    "run_id": result.get("run_id"),
                    "outcome": result.get("outcome"),
                    "route": (result.get("status_bar") or {}).get("effective_route"),
                    "event_count": len(result.get("events") or []),
                }
            )

        passed = all(bool(item["passed"]) for item in trial_results)
        errors = [
            f"trial {item['trial']}: {error}"
            for item in trial_results
            for error in item["errors"]
        ]
        failed += int(not passed)
        results.append(
            {
                "case_id": case["id"],
                "passed": passed,
                "errors": errors,
                "trials": args.trials,
                "passed_trials": sum(bool(item["passed"]) for item in trial_results),
                "failed_trials": sum(
                    not bool(item["passed"]) for item in trial_results
                ),
                "run_id": trial_results[-1]["run_id"],
            }
        )

    summary = {
        "mode": "live" if args.live else "offline",
        "passed": len(results) - failed,
        "failed": failed,
        "trials_per_case": args.trials,
        "passed_trials": passed_trials,
        "failed_trials": failed_trials,
        "cases": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("--trials 必须大于 0")
    return parsed


def validate_case(result: dict, expect: dict, clarification_count: int) -> list[str]:
    errors: list[str] = []
    report = result.get("report_json")
    checks = {
        "outcome": result.get("outcome"),
        "effective_route": (result.get("status_bar") or {}).get("effective_route"),
        "has_report": report is not None,
        "period": report.get("period") if report else None,
        "unit_cost": report["summary_cards"][0]["value"] if report else None,
        "total_cost": report["summary_cards"][1]["value"] if report else None,
        "clarification_count": clarification_count,
        "event_nodes": [event.get("node") for event in result.get("events", [])],
        "effective_capabilities": (
            (result.get("status_bar") or {}).get("authorization") or {}
        ).get("effective_capabilities", []),
        "trace_redacted": _trace_is_redacted(report),
        "clarification_before_cost_data": _clarification_precedes_cost_data(result),
        "runtime_events_valid": _runtime_events_valid(result),
        "authorization_is_server_owned": _authorization_is_server_owned(result),
        "trace_has_runtime_metadata": _trace_has_runtime_metadata(report),
    }
    for key, expected_value in expect.items():
        if checks.get(key) != expected_value:
            errors.append(
                f"{key}: expected={expected_value!r}, actual={checks.get(key)!r}"
            )
    mandatory_checks = {
        "runtime_events_valid": checks["runtime_events_valid"],
        "authorization_is_server_owned": checks["authorization_is_server_owned"],
        "trace_has_runtime_metadata": checks["trace_has_runtime_metadata"],
        "trace_redacted": checks["trace_redacted"],
        "clarification_before_cost_data": checks["clarification_before_cost_data"],
    }
    for key, passed in mandatory_checks.items():
        if not passed and key not in expect:
            errors.append(f"{key}: expected=True, actual=False")
    return errors


def _trace_is_redacted(report: dict | None) -> bool:
    if not report:
        return True
    trace = report.get("ai_trace") or {}
    return all(
        "request" not in call and "response" not in call
        for call in trace.get("calls", [])
    ) and "input" not in (trace.get("deterministic_calculation") or {})


def _clarification_precedes_cost_data(result: dict) -> bool:
    events = result.get("events", [])
    clarification_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.get("node") == "clarification_gate"
        ),
        None,
    )
    batch_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.get("node") == "load_finished_batches"
        ),
        None,
    )
    return batch_index is None or (
        clarification_index is not None and clarification_index < batch_index
    )


def _runtime_events_valid(result: dict) -> bool:
    events = result.get("events", [])
    if not isinstance(events, list):
        return False
    return all(
        isinstance(event.get("node"), str)
        and event.get("status") in {"success", "error", "waiting", "blocked"}
        for event in events
    )


def _authorization_is_server_owned(result: dict) -> bool:
    status_bar = result.get("status_bar") or {}
    authorization = status_bar.get("authorization") or {}
    effective = set(authorization.get("effective_capabilities") or [])
    authorized = set(authorization.get("authorized_capabilities") or [])
    server_allowed = set(authorization.get("server_allowed_capabilities") or [])
    return effective <= authorized and effective <= server_allowed


def _trace_has_runtime_metadata(report: dict | None) -> bool:
    if not report:
        return True
    trace = report.get("ai_trace") or {}
    return (
        trace.get("schema_version") == "2.0"
        and bool(trace.get("runtime_version"))
        and bool(trace.get("workflow_version"))
        and all(
            forbidden not in call
            for call in trace.get("calls", [])
            for forbidden in ("request", "prompt", "messages", "content")
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
