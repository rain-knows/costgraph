from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

PUBLIC_TRACE_SCHEMA_VERSION = "2.0"
AUDIT_TRACE_SCHEMA_VERSION = "3.0"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_source_tables(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_source_tables(item)
            for key, item in value.items()
            if key != "source_tables"
        }
    if isinstance(value, list):
        return [_without_source_tables(item) for item in value]
    return value


def build_public_ai_trace(trace: dict[str, Any]) -> dict[str, Any]:
    calls = []
    for call in trace.get("calls", []):
        response = call.get("response") or {}
        calls.append(
            {
                "node": call.get("node"),
                "purpose": call.get("purpose"),
                "provider": call.get("provider"),
                "model": call.get("model"),
                "status": call.get("status"),
                "duration_ms": call.get("duration_ms"),
                "error": call.get("error_code"),
                "finish_reason": response.get("finish_reason"),
                "usage": response.get("usage"),
            }
        )

    calculation = trace.get("deterministic_calculation") or {}
    public_calculation = {
        key: (
            _without_source_tables(calculation.get(key))
            if key == "result"
            else calculation.get(key)
        )
        for key in (
            "formulas",
            "process_arithmetic",
            "summary_arithmetic",
            "result",
            "comparison",
            "date_range",
            "calculation_policy",
        )
        if key in calculation
    }
    public_trace = {
        "schema_version": PUBLIC_TRACE_SCHEMA_VERSION,
        "provider": trace.get("provider"),
        "configured_model": trace.get("configured_model"),
        "calls": calls,
        "guardrails": trace.get("guardrails", []),
        "route_selection": trace.get("route_selection"),
        "clarification_decision": trace.get("clarification_decision"),
        "deterministic_calculation": public_calculation,
        "redacted_fields": [
            "calls.request",
            "calls.response.content",
            "calls.response.reasoning_content",
            "calls.input_facts",
            "prompt",
            "conversation_content",
            "model_content",
            "reasoning_content",
            "deterministic_calculation.input",
            "deterministic_calculation.result.source_tables",
            "conversation_history",
        ],
    }
    # Runtime metadata stays optional for diagnostic traces without persistence.
    for key in (
        "runtime_version",
        "workflow_version",
        "provider_version",
        "tool_versions",
        "capabilities",
        "route_selection",
        "clarification_decision",
    ):
        if key in trace:
            public_trace[key] = _without_source_tables(trace[key])
    if "context_summary" in trace:
        public_trace["context_summary"] = _without_source_tables(
            trace["context_summary"]
        )
    if "policy_decisions" in trace:
        public_trace["policy_decisions"] = _without_source_tables(
            trace["policy_decisions"]
        )
    if "tool_calls" in trace:
        public_trace["tool_calls"] = _without_source_tables(trace["tool_calls"])
    return public_trace


def build_audit_trace(trace: dict[str, Any]) -> dict[str, Any]:
    calls = []
    for call in trace.get("calls", []):
        response = call.get("response") or {}
        calls.append(
            {
                "node": call.get("node"),
                "purpose": call.get("purpose"),
                "provider": call.get("provider"),
                "model": call.get("model"),
                "status": call.get("status"),
                "duration_ms": call.get("duration_ms"),
                "finish_reason": response.get("finish_reason"),
                "usage": response.get("usage"),
                "error_code": call.get("error_code"),
                "request_sha256": call.get("request_sha256"),
                "response_sha256": response.get("response_sha256")
                or call.get("response_sha256"),
                "input_facts_sha256": call.get("input_facts_sha256"),
                "retryable": bool(call.get("retryable", False)),
            }
        )
    calculation = trace.get("deterministic_calculation") or {}
    tool_versions = trace.get("tool_versions", {})
    tool_calls = []
    for call in trace.get("tool_calls", []):
        tool_id = call.get("tool_id")
        summary = call.get("summary")
        tool_calls.append(
            {
                "tool_id": tool_id,
                "version": call.get("version") or tool_versions.get(tool_id),
                "status": call.get("status"),
                "error_code": call.get("error_code"),
                "retryable": bool(call.get("retryable", False)),
                "arguments_sha256": call.get("arguments_sha256"),
                "result_sha256": call.get("result_sha256"),
                "summary_sha256": _canonical_sha256(summary)
                if summary is not None
                else None,
            }
        )
    audit = {
        "schema_version": AUDIT_TRACE_SCHEMA_VERSION,
        "created_at": _now(),
        "provider": trace.get("provider"),
        "configured_model": trace.get("configured_model"),
        "calls": calls,
        "route": {
            key: (trace.get("route_selection") or {}).get(key)
            for key in ("requested_route", "effective_route", "source")
        },
        "deterministic": {
            "input_sha256": calculation.get("input_sha256"),
            "result_sha256": _canonical_sha256(calculation.get("result"))
            if "result" in calculation
            else None,
            "comparison_sha256": _canonical_sha256(calculation.get("comparison"))
            if calculation.get("comparison") is not None
            else None,
        },
        "runtime": {
            "runtime_version": trace.get("runtime_version"),
            "workflow_version": trace.get("workflow_version"),
            "provider_version": trace.get("provider_version"),
            "tool_versions": trace.get("tool_versions", {}),
        },
        "capability_snapshot": trace.get("capabilities", []),
        "policy_decisions": trace.get("policy_decisions", []),
        "tool_calls": tool_calls,
        "tool_calls_sha256": _canonical_sha256(tool_calls),
    }
    if "budget" in trace:
        audit["budget"] = trace["budget"]
    return audit
