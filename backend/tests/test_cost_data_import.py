from copy import deepcopy
from pathlib import Path

from app.db.models import Base
from app.services.cost_data_import_service import (
    _canonical_hash,
    _validate_records,
    load_records,
)

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def _snapshot() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    return tuple(
        load_records(SAMPLES / name)
        for name in (
            "parts.json",
            "cost_events.json",
            "cost_event_inputs.json",
            "cost_records.json",
        )
    )  # type: ignore[return-value]


def test_cost_metadata_contains_v2_governed_fact_tables() -> None:
    expected = {
        "cost_data.parts",
        "cost_data.cost_events",
        "cost_data.cost_event_inputs",
        "cost_data.cost_records",
        "cost_data.data_load_batches",
        "cost_data.data_load_errors",
    }
    assert expected.issubset(Base.metadata.tables)

    events = Base.metadata.tables["cost_data.cost_events"]
    inputs = Base.metadata.tables["cost_data.cost_event_inputs"]
    records = Base.metadata.tables["cost_data.cost_records"]
    assert str(events.c.qualified_quantity.type) == "NUMERIC(18, 4)"
    assert str(inputs.c.consumed_quantity.type) == "NUMERIC(18, 4)"
    assert str(records.c.amount.type) == "NUMERIC(18, 2)"
    excluded = {"workshop_id", "line_id", "shift_type", "work_calendar_type"}
    assert excluded.isdisjoint(events.c.keys())
    assert excluded.isdisjoint(records.c.keys())


def test_sample_files_are_valid_import_snapshot() -> None:
    parts, events, inputs, records = _snapshot()
    errors = _validate_records(parts, events, inputs, records)
    assert errors == [], [(item.error_code, item.error_message) for item in errors]


def test_import_validation_rejects_currency_and_missing_event() -> None:
    parts, events, inputs, records = _snapshot()
    bad_records = [deepcopy(records[0])]
    bad_records[0]["currency"] = "USD"
    bad_records[0]["event_id"] = "E-NOT-FOUND"

    errors = _validate_records(parts, events, inputs, bad_records)
    codes = {item.error_code for item in errors}
    assert {"UNSUPPORTED_CURRENCY", "EVENT_NOT_FOUND"}.issubset(codes)


def test_import_validation_rejects_period_mismatch_and_unknown_cost_code() -> None:
    parts, events, inputs, records = _snapshot()
    bad_events = deepcopy(events)
    bad_events[0]["period"] = "2026-07"
    bad_records = [deepcopy(records[0])]
    bad_records[0]["cost_code"] = "not_a_cost_code"

    errors = _validate_records(parts, bad_events, inputs, bad_records)
    codes = {item.error_code for item in errors}
    assert "PERIOD_DATE_MISMATCH" in codes
    assert "UNKNOWN_COST_CODE" in codes


def test_snapshot_hash_is_independent_of_row_order() -> None:
    payload = {
        "parts": [{"part_id": "P001"}, {"part_id": "P002"}],
        "cost_events": [{"event_id": "E001"}, {"event_id": "E002"}],
        "cost_event_inputs": [{"input_id": "I001"}, {"input_id": "I002"}],
        "cost_records": [{"cost_record_id": "C001"}, {"cost_record_id": "C002"}],
    }
    reordered = {key: list(reversed(value)) for key, value in payload.items()}
    assert _canonical_hash(payload) == _canonical_hash(reordered)
