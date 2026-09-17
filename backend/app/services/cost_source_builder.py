from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_finished_batch_sources(
    parts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    source_system: str,
) -> list[dict[str, Any]]:
    """Build minimal, stable ancestor subgraphs for every finished root."""
    part_by_id = {str(row["part_id"]): dict(row) for row in parts}
    sources_by_target: dict[str, list[str]] = defaultdict(list)
    consumed_event_ids: set[str] = set()
    for edge in inputs:
        target = str(edge["event_id"])
        source = str(edge["source_event_id"])
        sources_by_target[target].append(source)
        consumed_event_ids.add(source)

    finished_part_ids = {
        part_id
        for part_id, part in part_by_id.items()
        if part.get("part_type") == "finished_good"
    }
    roots = [
        event
        for event in events
        if event.get("event_type") == "process"
        and str(event.get("part_id")) in finished_part_ids
        and str(event.get("event_id")) not in consumed_event_ids
    ]
    result: list[dict[str, Any]] = []
    for root in roots:
        root_id = str(root["event_id"])
        needed = {root_id}
        pending = [root_id]
        while pending:
            target = pending.pop()
            for source in sources_by_target.get(target, ()):
                if source not in needed:
                    needed.add(source)
                    pending.append(source)

        selected_events = [
            dict(event) for event in events if str(event["event_id"]) in needed
        ]
        selected_inputs = [
            dict(edge)
            for edge in inputs
            if str(edge["event_id"]) in needed
            and str(edge["source_event_id"]) in needed
        ]
        selected_records = []
        for record in records:
            if str(record["event_id"]) not in needed:
                continue
            item = dict(record)
            item.setdefault("source_document_line", None)
            item["source_system"] = source_system
            item["raw_payload"] = dict(record)
            selected_records.append(item)
        part_ids = {str(event["part_id"]) for event in selected_events}
        result.append(
            {
                "root_event_id": root_id,
                "parts": [
                    part_by_id[part_id] for part_id in part_by_id if part_id in part_ids
                ],
                "events": selected_events,
                "inputs": selected_inputs,
                "records": selected_records,
            }
        )
    return result
