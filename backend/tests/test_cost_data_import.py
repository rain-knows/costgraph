from copy import deepcopy
from pathlib import Path

from app.db.models import Base
from app.services.cost_data_import_service import (
    _canonical_hash,
    _validate_records,
    load_records,
)

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


def _records(name: str):
    return load_records(SAMPLES / name)


def test_cost_metadata_contains_governed_fact_tables_without_factory_dimensions() -> (
    None
):
    expected = {
        "cost_data.products",
        "cost_data.production_outputs",
        "cost_data.process_cost_entries",
        "cost_data.data_load_batches",
        "cost_data.data_load_errors",
    }
    assert expected.issubset(Base.metadata.tables)
    output = Base.metadata.tables["cost_data.production_outputs"]
    entries = Base.metadata.tables["cost_data.process_cost_entries"]
    excluded = {"workshop_id", "line_id", "shift_type", "work_calendar_type"}
    assert excluded.isdisjoint(output.c.keys())
    assert excluded.isdisjoint(entries.c.keys())
    assert str(output.c.qualified_output_qty.type) == "NUMERIC(18, 4)"
    assert str(entries.c.amount.type) == "NUMERIC(18, 2)"


def test_sample_files_are_valid_import_snapshot() -> None:
    assert (
        _validate_records(
            _records("products.json"),
            _records("production_outputs.json"),
            _records("process_cost_entries.json"),
        )
        == []
    )


def test_import_validation_rejects_currency_and_missing_output() -> None:
    products = _records("products.json")
    outputs = [_records("production_outputs.json")[0]]
    entries = [deepcopy(_records("process_cost_entries.json")[0])]
    entries[0]["currency"] = "USD"
    entries[0]["production_date"] = "2026-06-30"
    entries[0]["period"] = "2026-06"

    errors = _validate_records(products, outputs, entries)
    assert {item.error_code for item in errors} == {
        "UNSUPPORTED_CURRENCY",
        "OUTPUT_NOT_FOUND",
        "COST_NOT_FOUND",
    }


def test_snapshot_hash_is_independent_of_row_order() -> None:
    payload = {
        "products": [{"product_id": "P001"}, {"product_id": "P002"}],
        "production_outputs": [],
        "process_cost_entries": [],
    }
    reordered = {**payload, "products": list(reversed(payload["products"]))}
    assert _canonical_hash(payload) == _canonical_hash(reordered)
