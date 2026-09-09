from decimal import Decimal

import pytest

from app.domain.cost import roll_up_cost_graph


def _event(event_id: str, *, qualified: str = "1", defective: str = "0") -> dict:
    return {
        "event_id": event_id,
        "event_type": "process",
        "output_batch_id": f"B-{event_id}",
        "part_id": f"P-{event_id}",
        "period": "2026-06",
        "completion_time": "2026-06-01T00:00:00+00:00",
        "qualified_quantity": qualified,
        "defective_quantity": defective,
        "unit": "pcs",
    }


def _record(event_id: str, amount: str, code: str = "raw_material") -> dict:
    return {
        "cost_record_id": f"C-{event_id}",
        "event_id": event_id,
        "cost_code": code,
        "amount": amount,
    }


def _edge(input_id: str, target: str, source: str, quantity: str) -> dict:
    return {
        "input_id": input_id,
        "event_id": target,
        "source_event_id": source,
        "consumed_quantity": quantity,
        "unit": "pcs",
    }


def test_rollup_rejects_cycles() -> None:
    events = [_event("A"), _event("B")]
    inputs = [_edge("I-AB", "B", "A", "1"), _edge("I-BA", "A", "B", "1")]

    with pytest.raises(ValueError, match="环路"):
        roll_up_cost_graph(events, inputs, [_record("A", "1.00")])


def test_rollup_rejects_cumulative_overconsumption() -> None:
    events = [_event("A", qualified="10"), _event("B")]
    inputs = [_edge("I-1", "B", "A", "6"), _edge("I-2", "B", "A", "5")]

    with pytest.raises(ValueError, match="超过合格数量"):
        roll_up_cost_graph(events, inputs, [_record("A", "10.00")])


def test_partial_allocation_and_last_edge_absorbs_rounding_tail() -> None:
    events = [_event("A", qualified="3"), _event("B")]
    inputs = [_edge("I-1", "B", "A", "1"), _edge("I-2", "B", "A", "2")]
    result = roll_up_cost_graph(events, inputs, [_record("A", "10.00")])

    assert result.edge_ratios["I-1"] == Decimal("0.333333")
    assert result.edge_ratios["I-2"] == Decimal("0.666667")
    assert result.edge_allocations["I-1"]["raw_material"] == Decimal("3.33")
    assert result.edge_allocations["I-2"]["raw_material"] == Decimal("6.67")
    assert (
        sum(
            (result.edge_allocations[key]["raw_material"] for key in ("I-1", "I-2")),
            Decimal(0),
        )
        == result.accumulated["A"]["raw_material"]
        == Decimal("10.00")
    )


def test_defective_quantity_is_not_transferable_but_cost_is_carried() -> None:
    events = [_event("A", qualified="95", defective="5"), _event("B")]
    inputs = [_edge("I-1", "B", "A", "95")]
    result = roll_up_cost_graph(events, inputs, [_record("A", "100.00")])

    # The source's entire accumulated cost is carried by the qualified output;
    # defective quantity changes display quantity but not the transfer base.
    assert result.edge_ratios["I-1"] == Decimal("1.000000")
    assert result.edge_allocations["I-1"]["raw_material"] == Decimal("100.00")
    assert result.inherited["B"]["raw_material"] == Decimal("100.00")


def test_full_allocation_rounding_never_creates_negative_edge_cost() -> None:
    events = [_event("A", qualified="4"), _event("B")]
    inputs = [_edge(f"I-{index}", "B", "A", "1") for index in range(4)]
    result = roll_up_cost_graph(events, inputs, [_record("A", "0.02")])
    allocations = [
        result.edge_allocations[f"I-{index}"]["raw_material"] for index in range(4)
    ]

    assert all(value >= 0 for value in allocations)
    assert sum(allocations, Decimal(0)) == Decimal("0.02")
