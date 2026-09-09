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

QUESTION = "查询 FG-001 2026年6月单位成本，并说明成本构成"
EXPECTED_MANUFACTURING_COSTS = [
    Decimal("20000.00"),
    Decimal("8000.00"),
    Decimal("2000.00"),
    Decimal("4000.00"),
    Decimal("3000.00"),
    Decimal("13000.00"),
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
    assert "load_finished_batches" not in clarification_events

    result = run_runtime(_request(QUESTION), runtime_services=runtime_services)
    assert result["outcome"] == "completed"
    report = result["report_json"]
    assert report is not None
    assert report["report_schema_version"] == "2.0"
    assert [
        Decimal(str(group["metric"]["amount"]))
        for group in report["manufacturing_view"]["groups"]
    ] == EXPECTED_MANUFACTURING_COSTS
    assert Decimal(str(report["manufacturing_view"]["total"]["amount"])) == Decimal(
        "50000.00"
    )
    assert Decimal(str(report["summary_cards"][1]["value"])) == Decimal("50000.00")
    assert Decimal(str(report["summary_cards"][0]["value"])) == Decimal("53.50")
    print(result["final_message"])
    print(f"total_cost={report['summary_cards'][1]['value']}")
    print(f"unit_cost={report['summary_cards'][0]['value']}")
    source_events = next(
        table
        for table in report["lineage"]["tables"]
        if table["name"] == "cost_data.cost_events"
    )
    print(f"source_events={source_events['record_count']}")
    print(f"data_snapshot_id={report['data_snapshot_id']}")
    print(f"rule_version={report['rule_version']}")
    print("events=" + ",".join(event["node"] for event in result["events"]))
