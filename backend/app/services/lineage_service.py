from __future__ import annotations

import hashlib
import json
from typing import Any

LINEAGE_SCHEMA_VERSION = "2.0"


def build_lineage(
    *,
    part_id: str,
    period: str,
    batch_sources: list[dict[str, Any]],
    source: str = "postgresql_cost_data",
) -> dict[str, Any]:
    if source != "postgresql_cost_data":
        raise ValueError(f"不支持的成本事实来源：{source}")
    if not batch_sources:
        raise ValueError("数据血缘至少需要一个最终批次来源")

    source_ids = {
        "cost_data.parts": _ids(batch_sources, "parts", "part_id"),
        "cost_data.cost_events": _ids(batch_sources, "events", "event_id"),
        "cost_data.cost_event_inputs": _ids(batch_sources, "inputs", "input_id"),
        "cost_data.cost_records": _ids(batch_sources, "records", "cost_record_id"),
    }
    if part_id not in source_ids["cost_data.parts"]:
        source_ids["cost_data.parts"].append(part_id)
        source_ids["cost_data.parts"].sort()

    canonical_sources = sorted(
        json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        for item in batch_sources
    )
    snapshot_material = {
        "part_id": part_id,
        "period": period,
        "source_ids": source_ids,
        "records": canonical_sources,
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
        "query_scope": {"part_id": part_id, "period": period},
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


def _ids(
    batch_sources: list[dict[str, Any]], collection: str, id_field: str
) -> list[str]:
    values: set[str] = set()
    for source in batch_sources:
        for item in source.get(collection, []):
            if isinstance(item, dict) and item.get(id_field):
                values.add(str(item[id_field]))
        singular = source.get(collection[:-1])
        if isinstance(singular, dict) and singular.get(id_field):
            values.add(str(singular[id_field]))

        # Agent calculation results carry the canonical source graph under
        # ``trace`` rather than exposing raw table collections at the top
        # level.  Include those IDs in lineage as well, while retaining
        # support for repository sources that already provide collections.
        trace = source.get("trace")
        if isinstance(trace, dict):
            trace_collection = {
                "parts": "nodes",
                "events": "nodes",
                "inputs": "edges",
                "records": "records",
            }.get(collection)
            if trace_collection:
                for item in trace.get(trace_collection, []):
                    if not isinstance(item, dict):
                        continue
                    candidate = item.get(id_field)
                    if collection == "parts" and isinstance(item.get("part"), dict):
                        candidate = item["part"].get(id_field)
                    if candidate:
                        values.add(str(candidate))
    return sorted(values)
