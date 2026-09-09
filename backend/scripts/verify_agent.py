import sys
from decimal import Decimal
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import app.agent.tools as agent_tools
from app.agent.graph import run_runtime
from app.agent.runtime_contracts import RuntimeRunRequest
from evaluation.fixture_cost_repository import CostFixtureRepository
from evaluation.fixture_model_provider import build_fixture_runtime_services

QUESTION = "查询产品A 2026年6月单位成本，并说明成本构成"
EXPECTED_PROCESS_COSTS = [
    Decimal("61200.00"),
    Decimal("29000.00"),
    Decimal("26400.00"),
    Decimal("22400.00"),
]


def _request(question: str) -> RuntimeRunRequest:
    return RuntimeRunRequest(
        question=question,
        requested_capabilities=["cost_calculation"],
    )


if __name__ == "__main__":
    # This verification script is offline by design.  Both model and cost facts
    # are explicit fixtures; live DeepSeek verification is a separate command.
    agent_tools.cost_repository = CostFixtureRepository()
    runtime_services = build_fixture_runtime_services()

    clarification = run_runtime(
        _request("查一下成本"), runtime_services=runtime_services
    )
    assert clarification["outcome"] == "needs_clarification"
    clarification_events = [event["node"] for event in clarification["events"]]
    assert "load_cost_inputs" not in clarification_events

    result = run_runtime(_request(QUESTION), runtime_services=runtime_services)
    assert result["outcome"] == "completed"
    report = result["report_json"]
    assert report is not None
    assert [
        Decimal(str(item["total_cost"])) for item in report["process_cost_breakdown"]
    ] == EXPECTED_PROCESS_COSTS
    assert Decimal(str(report["summary_cards"][1]["value"])) == Decimal("139000.00")
    assert Decimal(str(report["summary_cards"][0]["value"])) == Decimal("13.90")
    print(result["final_message"])
    print(f"total_cost={report['summary_cards'][1]['value']}")
    print(f"unit_cost={report['summary_cards'][0]['value']}")
    source_outputs = next(
        table
        for table in report["lineage"]["tables"]
        if table["name"] == "production_outputs"
    )
    print(f"source_outputs={source_outputs['record_count']}")
    print(f"data_snapshot_id={report['data_snapshot_id']}")
    print(f"rule_version={report['rule_version']}")
    print("events=" + ",".join(event["node"] for event in result["events"]))
