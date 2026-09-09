from __future__ import annotations

import hashlib
import json
from typing import Any

LINEAGE_SCHEMA_VERSION = "1.0"


def build_lineage(
    *,
    product_id: str,
    period: str,
    date_range: dict[str, str] | None,
    cost_inputs: dict[str, Any] | list[dict[str, Any]],
    source: str = "postgresql_cost_data",
) -> dict[str, Any]:
    records = cost_inputs if isinstance(cost_inputs, list) else [cost_inputs]
    canonical_records = sorted(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        for record in records
    )
    source_ids = {
        "production_outputs": sorted(
            {
                source_id
                for record in records
                for source_id in record.get("source_tables", {}).get(
                    "production_outputs", []
                )
            }
        ),
        "process_cost_entries": sorted(
            {
                source_id
                for record in records
                for source_id in record.get("source_tables", {}).get(
                    "process_cost_entries", []
                )
            }
        ),
    }
    snapshot_material = {
        "product_id": product_id,
        "period": period,
        "date_range": date_range,
        "source_ids": source_ids,
        "records": canonical_records,
    }
    snapshot_id = hashlib.sha256(
        json.dumps(
            snapshot_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "source": source,
        "query_scope": {
            "product_id": product_id,
            "period": period,
            "date_range": date_range,
        },
        "tables": [
            {
                "name": name,
                "record_count": len(ids),
                "record_id_sample": ids[:10],
            }
            for name, ids in source_ids.items()
        ],
        "data_snapshot_id": snapshot_id,
    }
