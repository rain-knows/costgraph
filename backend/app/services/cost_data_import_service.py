from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import (
    CostEvent,
    CostEventInput,
    CostRecord,
    DataLoadBatch,
    DataLoadError,
    Part,
)
from app.domain.cost import COST_CODE_GROUP

UPSERT_BATCH_SIZE = 500
AMOUNT_INTEGER_DIGITS = 16
QUANTITY_INTEGER_DIGITS = 14

PART_FIELDS = {
    "part_id",
    "part_number",
    "part_description",
    "part_type",
    "product_family",
    "unit",
    "source_record_id",
    "raw_payload",
}
EVENT_FIELDS = {
    "event_id",
    "event_type",
    "output_batch_id",
    "part_id",
    "period",
    "cost_center_code",
    "cost_center_name",
    "work_order_number",
    "lot_number",
    "process_code",
    "process_name",
    "completion_time",
    "qualified_quantity",
    "defective_quantity",
    "unit",
    "machine_hours",
    "labor_hours",
    "source_record_id",
    "raw_payload",
}
INPUT_FIELDS = {
    "input_id",
    "event_id",
    "source_event_id",
    "consumed_quantity",
    "unit",
    "source_record_id",
    "raw_payload",
}
RECORD_FIELDS = {
    "cost_record_id",
    "event_id",
    "cost_code",
    "amount",
    "currency",
    "incurred_at",
    "source_document_no",
    "source_document_line",
    "source_record_id",
    "raw_payload",
}
PART_TYPES = {
    "raw_material",
    "purchased_semi_finished",
    "work_in_progress",
    "finished_good",
}


@dataclass(frozen=True)
class ImportErrorItem:
    source_table: str
    source_record_id: str | None
    error_code: str
    error_message: str
    raw_payload: dict[str, Any] | None


@dataclass(frozen=True)
class ImportResult:
    batch_id: UUID
    status: str
    total_rows: int
    valid_rows: int
    error_rows: int
    error_summary: dict[str, Any] | None = None
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": str(self.batch_id),
            "status": self.status,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "error_rows": self.error_rows,
            "error_summary": self.error_summary,
            "skipped": self.skipped,
        }


def load_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".json":
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise ValueError(f"输入文件必须是对象数组：{source}")
        return [dict(item) for item in value]
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]
    raise ValueError(f"只支持 JSON 或 CSV 输入：{source}")


def _canonical_hash(payload: dict[str, list[dict[str, Any]]]) -> str:
    normalized = {
        name: sorted(
            rows,
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
        for name, rows in payload.items()
    }
    return hashlib.sha256(
        json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _decimal(value: Any) -> Decimal:
    parsed = Decimal(_text(value))
    if not parsed.is_finite():
        raise InvalidOperation
    return parsed


def _datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_text(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _scale_ok(value: Decimal, scale: int) -> bool:
    return value.as_tuple().exponent >= -scale


def _numeric_range_ok(value: Decimal, integer_digits: int, scale: int) -> bool:
    """Validate the precision represented by the target NUMERIC column."""

    if not value.is_finite() or not _scale_ok(value, scale):
        return False
    digits = value.copy_abs().as_tuple().digits
    exponent = value.copy_abs().as_tuple().exponent
    fractional_digits = max(0, -exponent)
    integer_digits_used = max(0, len(digits) - fractional_digits)
    return fractional_digits <= scale and integer_digits_used <= integer_digits


def _validate_raw_payload(
    errors: list[ImportErrorItem], table: str, row: dict[str, Any]
) -> None:
    value = row.get("raw_payload")
    if value is not None and not isinstance(value, dict):
        errors.append(
            _error(table, row, "INVALID_RAW_PAYLOAD", "raw_payload 必须是 JSON 对象。")
        )


def _error(table: str, row: dict[str, Any], code: str, message: str) -> ImportErrorItem:
    return ImportErrorItem(
        table, _text(row.get("source_record_id")) or None, code, message, row
    )


def _validate_records(
    parts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> list[ImportErrorItem]:
    errors: list[ImportErrorItem] = []
    _validate_unknown_fields(errors, "parts", parts, PART_FIELDS)
    _validate_unknown_fields(errors, "cost_events", events, EVENT_FIELDS)
    _validate_unknown_fields(errors, "cost_event_inputs", inputs, INPUT_FIELDS)
    _validate_unknown_fields(errors, "cost_records", records, RECORD_FIELDS)

    part_id_counts = _counts(parts, "part_id")
    event_id_counts = _counts(events, "event_id")
    output_batch_counts = _counts(events, "output_batch_id")
    input_id_counts = _counts(inputs, "input_id")
    record_id_counts = _counts(records, "cost_record_id")
    global_source_counts = _counts(
        parts + events + inputs + records, "source_record_id"
    )
    part_ids = {value for value in part_id_counts if value}
    event_ids = {value for value in event_id_counts if value}
    output_by_event = {
        _text(row.get("event_id")): row for row in events if _text(row.get("event_id"))
    }
    input_sources: dict[str, list[dict[str, Any]]] = {}
    part_by_id = {
        _text(row.get("part_id")): row for row in parts if _text(row.get("part_id"))
    }

    for table, rows in (
        ("parts", parts),
        ("cost_events", events),
        ("cost_event_inputs", inputs),
        ("cost_records", records),
    ):
        for row in rows:
            _validate_raw_payload(errors, table, row)

    for row in parts:
        part_id = _text(row.get("part_id"))
        if (
            not part_id
            or not _text(row.get("part_number"))
            or not _text(row.get("part_description"))
        ):
            errors.append(_error("parts", row, "REQUIRED_FIELD", "零件标识不能为空。"))
        if part_id and part_id_counts.get(part_id, 0) > 1:
            errors.append(_error("parts", row, "DUPLICATE_PART_ID", "零件 ID 重复。"))
        source_record_id = _text(row.get("source_record_id"))
        if source_record_id and global_source_counts.get(source_record_id, 0) > 1:
            errors.append(
                _error("parts", row, "DUPLICATE_SOURCE_RECORD", "来源记录 ID 重复。")
            )
        if _text(row.get("part_type")) not in PART_TYPES:
            errors.append(_error("parts", row, "UNKNOWN_PART_TYPE", "未知零件类型。"))
        if not _text(row.get("unit")) or not _text(row.get("source_record_id")):
            errors.append(
                _error(
                    "parts", row, "REQUIRED_FIELD", "零件单位和来源记录 ID 不能为空。"
                )
            )
    for row in events:
        event_id = _text(row.get("event_id"))
        output_batch_id = _text(row.get("output_batch_id"))
        if not event_id:
            errors.append(
                _error("cost_events", row, "REQUIRED_FIELD", "事件 ID 不能为空。")
            )
        elif event_id_counts.get(event_id, 0) > 1:
            errors.append(
                _error("cost_events", row, "DUPLICATE_EVENT_ID", "事件 ID 重复。")
            )
        if not output_batch_id:
            errors.append(
                _error("cost_events", row, "REQUIRED_FIELD", "输出批次 ID 不能为空。")
            )
        elif output_batch_counts.get(output_batch_id, 0) > 1:
            errors.append(
                _error(
                    "cost_events",
                    row,
                    "DUPLICATE_OUTPUT_BATCH_ID",
                    "输出批次 ID 重复。",
                )
            )
        source_record_id = _text(row.get("source_record_id"))
        if source_record_id and global_source_counts.get(source_record_id, 0) > 1:
            errors.append(
                _error(
                    "cost_events", row, "DUPLICATE_SOURCE_RECORD", "来源记录 ID 重复。"
                )
            )
        try:
            completion = _datetime(row.get("completion_time"))
            qualified = _decimal(row.get("qualified_quantity"))
            defective = _decimal(row.get("defective_quantity"))
            machine_hours = _decimal(row.get("machine_hours", "0"))
            labor_hours = _decimal(row.get("labor_hours", "0"))
        except (ValueError, InvalidOperation):
            errors.append(
                _error("cost_events", row, "INVALID_VALUE", "事件日期或数量格式非法。")
            )
            continue
        if _text(row.get("event_type")) not in {"purchase", "process"}:
            errors.append(
                _error("cost_events", row, "UNKNOWN_EVENT_TYPE", "未知成本事件类型。")
            )
        if _text(row.get("part_id")) not in part_ids:
            errors.append(
                _error("cost_events", row, "PART_NOT_FOUND", "事件引用了不存在零件。")
            )
        part = part_by_id.get(_text(row.get("part_id")))
        if (
            part
            and _text(row.get("unit"))
            and _text(part.get("unit")) != _text(row.get("unit"))
        ):
            errors.append(
                _error(
                    "cost_events",
                    row,
                    "UNIT_MISMATCH",
                    "事件输出单位必须与零件单位一致。",
                )
            )
        if str(row.get("period")) != completion.strftime("%Y-%m"):
            errors.append(
                _error(
                    "cost_events",
                    row,
                    "PERIOD_DATE_MISMATCH",
                    "期间与完工时间月份不一致。",
                )
            )
        if qualified < 0 or defective < 0 or qualified + defective <= 0:
            errors.append(
                _error("cost_events", row, "INVALID_QUANTITY", "完工数量必须为正。")
            )
        if not _numeric_range_ok(
            qualified, QUANTITY_INTEGER_DIGITS, 4
        ) or not _numeric_range_ok(defective, QUANTITY_INTEGER_DIGITS, 4):
            errors.append(
                _error(
                    "cost_events", row, "QUANTITY_SCALE_EXCEEDED", "数量最多保留 4 位。"
                )
            )
        if (
            machine_hours < 0
            or labor_hours < 0
            or not _numeric_range_ok(machine_hours, 14, 4)
            or not _numeric_range_ok(labor_hours, 14, 4)
        ):
            errors.append(
                _error(
                    "cost_events",
                    row,
                    "INVALID_HOURS",
                    "机器工时和人工工时必须为非负且最多保留 4 位。",
                )
            )
        if (
            not _text(row.get("unit"))
            or not _text(row.get("lot_number"))
            or not _text(row.get("source_record_id"))
        ):
            errors.append(
                _error(
                    "cost_events",
                    row,
                    "REQUIRED_FIELD",
                    "事件单位、批号和来源记录 ID 不能为空。",
                )
            )
        if _text(row.get("event_type")) == "process" and (
            not _text(row.get("process_code")) or not _text(row.get("process_name"))
        ):
            errors.append(
                _error(
                    "cost_events",
                    row,
                    "REQUIRED_PROCESS_FIELD",
                    "工艺事件必须填写工艺代码和名称。",
                )
            )
    for row in inputs:
        input_id = _text(row.get("input_id"))
        target_id = _text(row.get("event_id"))
        source_id = _text(row.get("source_event_id"))
        if not input_id:
            errors.append(
                _error(
                    "cost_event_inputs",
                    row,
                    "REQUIRED_FIELD",
                    "投入关系 ID 不能为空。",
                )
            )
        elif input_id_counts.get(input_id, 0) > 1:
            errors.append(
                _error(
                    "cost_event_inputs", row, "DUPLICATE_INPUT_ID", "投入关系 ID 重复。"
                )
            )
        input_sources.setdefault(source_id, []).append(row)
        source_record_id = _text(row.get("source_record_id"))
        if source_record_id and global_source_counts.get(source_record_id, 0) > 1:
            errors.append(
                _error(
                    "cost_event_inputs",
                    row,
                    "DUPLICATE_SOURCE_RECORD",
                    "来源记录 ID 重复。",
                )
            )
        if target_id not in event_ids or source_id not in event_ids:
            errors.append(
                _error(
                    "cost_event_inputs",
                    row,
                    "EVENT_NOT_FOUND",
                    "投入关系引用了不存在事件。",
                )
            )
        elif target_id == source_id:
            errors.append(
                _error(
                    "cost_event_inputs",
                    row,
                    "SELF_REFERENCE",
                    "投入关系不能引用自身事件。",
                )
            )
        elif _text(output_by_event[target_id].get("event_type")) != "process":
            errors.append(
                _error(
                    "cost_event_inputs",
                    row,
                    "TARGET_NOT_PROCESS",
                    "投入关系目标必须是工艺事件。",
                )
            )
        try:
            quantity = _decimal(row.get("consumed_quantity"))
            if quantity <= 0 or not _numeric_range_ok(
                quantity, QUANTITY_INTEGER_DIGITS, 4
            ):
                raise ValueError
        except (ValueError, InvalidOperation):
            errors.append(
                _error(
                    "cost_event_inputs",
                    row,
                    "INVALID_QUANTITY",
                    "领用数量必须为正且最多 4 位。",
                )
            )
        if source_id in output_by_event and target_id in output_by_event:
            source_event = output_by_event[source_id]
            target_event = output_by_event[target_id]
            source_unit = _text(source_event.get("unit"))
            target_unit = _text(target_event.get("unit"))
            edge_unit = _text(row.get("unit"))
            if edge_unit and source_unit and edge_unit != source_unit:
                errors.append(
                    _error(
                        "cost_event_inputs",
                        row,
                        "UNIT_MISMATCH",
                        "投入单位与来源事件单位不一致。",
                    )
                )
            if source_unit and target_unit and source_unit != target_unit:
                errors.append(
                    _error(
                        "cost_event_inputs",
                        row,
                        "UNIT_MISMATCH",
                        "来源与目标事件单位不一致。",
                    )
                )
    for row in records:
        record_id = _text(row.get("cost_record_id"))
        if not record_id:
            errors.append(
                _error(
                    "cost_records",
                    row,
                    "REQUIRED_FIELD",
                    "费用记录 ID 不能为空。",
                )
            )
        elif record_id_counts.get(record_id, 0) > 1:
            errors.append(
                _error(
                    "cost_records",
                    row,
                    "DUPLICATE_COST_RECORD_ID",
                    "费用记录 ID 重复。",
                )
            )
        if _text(row.get("event_id")) not in event_ids:
            errors.append(
                _error(
                    "cost_records", row, "EVENT_NOT_FOUND", "费用记录引用了不存在事件。"
                )
            )
        source_record_id = _text(row.get("source_record_id"))
        if source_record_id and global_source_counts.get(source_record_id, 0) > 1:
            errors.append(
                _error(
                    "cost_records",
                    row,
                    "DUPLICATE_SOURCE_RECORD",
                    "来源记录 ID 重复。",
                )
            )
        if _text(row.get("cost_code")) not in COST_CODE_GROUP:
            errors.append(
                _error("cost_records", row, "UNKNOWN_COST_CODE", "未知费用代码。")
            )
        try:
            amount = _decimal(row.get("amount"))
            if amount < 0 or not _numeric_range_ok(amount, AMOUNT_INTEGER_DIGITS, 2):
                raise ValueError
            _datetime(row.get("incurred_at"))
        except (ValueError, InvalidOperation):
            errors.append(
                _error(
                    "cost_records", row, "INVALID_VALUE", "费用金额或发生时间格式非法。"
                )
            )
        if _text(row.get("currency")) != "CNY":
            errors.append(
                _error("cost_records", row, "UNSUPPORTED_CURRENCY", "当前只允许 CNY。")
            )
        if not _text(row.get("source_document_no")) or not _text(
            row.get("source_record_id")
        ):
            errors.append(
                _error(
                    "cost_records",
                    row,
                    "REQUIRED_FIELD",
                    "来源单据号和来源记录 ID 不能为空。",
                )
            )

    # Validate source consumption totals. Purchase outputs may be consumed by
    # later process events; only input targets are restricted to process events.
    for event_id, rows in input_sources.items():
        source = output_by_event.get(event_id)
        if source is None:
            continue
        try:
            used = sum(
                (_decimal(item.get("consumed_quantity")) for item in rows), Decimal(0)
            )
            if used > _decimal(source.get("qualified_quantity")):
                for row in rows:
                    errors.append(
                        _error(
                            "cost_event_inputs",
                            row,
                            "OVER_CONSUMPTION",
                            "累计领用超过来源合格数量。",
                        )
                    )
        except InvalidOperation:
            pass

    # Validate the complete relation graph before publishing the snapshot.
    predecessors = {event_id: set() for event_id in event_ids}
    for row in inputs:
        source_id = _text(row.get("source_event_id"))
        target_id = _text(row.get("event_id"))
        if source_id in predecessors and target_id in predecessors:
            predecessors[target_id].add(source_id)
    try:
        list(TopologicalSorter(predecessors).static_order())
    except CycleError:
        errors.append(
            ImportErrorItem(
                "cost_event_inputs",
                None,
                "CYCLE",
                "成本事件投入关系不能形成环路。",
                None,
            )
        )
    return errors


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = _text(row.get(field))
        if value:
            result[value] = result.get(value, 0) + 1
    return result


def _validate_unknown_fields(
    errors: list[ImportErrorItem],
    table: str,
    rows: list[dict[str, Any]],
    allowed: set[str],
) -> None:
    for row in rows:
        unknown = sorted(set(row) - allowed)
        if unknown:
            errors.append(
                _error(
                    table, row, "UNKNOWN_FIELD", f"不允许的字段：{', '.join(unknown)}。"
                )
            )


class CostDataImporter:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def import_records(
        self,
        *,
        tenant_id: str,
        source_system: str,
        parts: list[dict[str, Any]],
        events: list[dict[str, Any]],
        inputs: list[dict[str, Any]],
        records: list[dict[str, Any]],
        source_file: str | None = None,
    ) -> ImportResult:
        payload = {
            "parts": parts,
            "cost_events": events,
            "cost_event_inputs": inputs,
            "cost_records": records,
        }
        snapshot_hash = _canonical_hash(payload)
        errors = _validate_records(parts, events, inputs, records)
        total_rows = sum(len(rows) for rows in payload.values())
        now = datetime.now(UTC)
        session: Session = self._session_factory()
        try:
            existing = session.scalar(
                select(DataLoadBatch)
                .where(
                    DataLoadBatch.tenant_id == tenant_id,
                    DataLoadBatch.source_system == source_system,
                    DataLoadBatch.source_snapshot_hash == snapshot_hash,
                )
                .order_by(desc(DataLoadBatch.created_at))
            )
            if existing:
                session.rollback()
                return _result(existing, skipped=True)
            if not errors:
                errors.extend(
                    self._validate_existing_event_identity(session, tenant_id, events)
                )
            error_rows = len(
                {(item.source_table, item.source_record_id) for item in errors}
            )
            batch_id = uuid4()
            batch = DataLoadBatch(
                batch_id=batch_id,
                tenant_id=tenant_id,
                source_system=source_system,
                source_file=source_file,
                source_snapshot_hash=snapshot_hash,
                status="failed" if errors else "validated",
                total_rows=total_rows,
                valid_rows=total_rows - error_rows,
                error_rows=error_rows,
                error_summary={"codes": _summarize(errors)} if errors else None,
                started_at=now,
                created_at=now,
            )
            session.add(batch)
            session.flush()
            if errors:
                session.add_all(
                    DataLoadError(
                        tenant_id=tenant_id,
                        batch_id=batch_id,
                        source_table=item.source_table,
                        source_record_id=item.source_record_id,
                        error_code=item.error_code,
                        error_message=item.error_message,
                        raw_payload=item.raw_payload,
                        created_at=now,
                    )
                    for item in errors
                )
            else:
                session.execute(
                    update(DataLoadBatch)
                    .where(
                        DataLoadBatch.tenant_id == tenant_id,
                        DataLoadBatch.status == "published",
                    )
                    .values(status="superseded")
                )
                self._upsert(
                    session,
                    Part,
                    parts,
                    tenant_id,
                    source_system,
                    batch_id,
                    now,
                    "part_id",
                )
                self._upsert(
                    session,
                    CostEvent,
                    events,
                    tenant_id,
                    source_system,
                    batch_id,
                    now,
                    "event_id",
                    _event_values,
                )
                self._upsert(
                    session,
                    CostEventInput,
                    inputs,
                    tenant_id,
                    source_system,
                    batch_id,
                    now,
                    "input_id",
                    _input_values,
                )
                self._upsert(
                    session,
                    CostRecord,
                    records,
                    tenant_id,
                    source_system,
                    batch_id,
                    now,
                    "cost_record_id",
                    _record_values,
                )
                batch.status = "published"
            batch.finished_at = datetime.now(UTC)
            session.commit()
            return _result(batch, skipped=False)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _validate_existing_event_identity(
        session: Session, tenant_id: str, events: list[dict[str, Any]]
    ) -> list[ImportErrorItem]:
        """Reject ambiguous cross-snapshot event identity before publish.

        Events are upserted by ``event_id`` so a normal snapshot refresh may
        move the same logical event to the new batch.  Reusing an output batch
        for a different event (or changing an event's output batch while the
        old batch id belongs to another event) would violate the tenant-wide
        output-batch uniqueness invariant and otherwise surface as a raw DB
        ``IntegrityError``.  Turn that case into the same failed-import error
        contract used by the other validation gates.
        """
        output_ids = {
            _text(row.get("output_batch_id"))
            for row in events
            if _text(row.get("output_batch_id"))
        }
        event_ids = {
            _text(row.get("event_id")) for row in events if _text(row.get("event_id"))
        }
        if not output_ids and not event_ids:
            return []
        existing = session.scalars(
            select(CostEvent).where(
                CostEvent.tenant_id == tenant_id,
                (CostEvent.output_batch_id.in_(output_ids))
                | (CostEvent.event_id.in_(event_ids)),
            )
        ).all()
        by_output = {row.output_batch_id: row for row in existing}
        by_event = {row.event_id: row for row in existing}
        errors: list[ImportErrorItem] = []
        seen: set[tuple[str, str, str]] = set()
        for row in events:
            event_id = _text(row.get("event_id"))
            output_batch_id = _text(row.get("output_batch_id"))
            old_by_output = by_output.get(output_batch_id)
            old_by_event = by_event.get(event_id)
            if old_by_output is not None and old_by_output.event_id != event_id:
                key = (event_id, "OUTPUT_BATCH_CONFLICT", output_batch_id)
                if key not in seen:
                    errors.append(
                        _error(
                            "cost_events",
                            row,
                            "OUTPUT_BATCH_CONFLICT",
                            "输出批次 ID 已属于同租户的其他事件。",
                        )
                    )
                    seen.add(key)
            if (
                old_by_event is not None
                and old_by_event.output_batch_id != output_batch_id
            ):
                key = (event_id, "EVENT_OUTPUT_BATCH_CONFLICT", output_batch_id)
                if key not in seen:
                    errors.append(
                        _error(
                            "cost_events",
                            row,
                            "EVENT_OUTPUT_BATCH_CONFLICT",
                            "事件 ID 已绑定其他输出批次。",
                        )
                    )
                    seen.add(key)
        return errors

    @staticmethod
    def _upsert(
        session: Session,
        model: Any,
        rows: list[dict[str, Any]],
        tenant_id: str,
        source_system: str,
        batch_id: UUID,
        now: datetime,
        identity: str,
        mapper=None,
    ) -> None:
        if not rows:
            return
        mapper = mapper or (lambda row: dict(row))
        values = []
        for row in rows:
            value = mapper(row)
            value.update(
                {
                    "tenant_id": tenant_id,
                    "source_system": source_system,
                    "source_record_id": _text(row.get("source_record_id"))
                    or _text(row[identity]),
                    "batch_id": batch_id,
                    "raw_payload": row,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            values.append(value)
        statement = pg_insert(model).on_conflict_do_update(
            index_elements=["tenant_id", identity],
            set_={
                key: getattr(pg_insert(model).excluded, key)
                for key in values[0]
                if key not in {"tenant_id", identity, "created_at"}
            },
        )
        for start in range(0, len(values), UPSERT_BATCH_SIZE):
            session.execute(statement, values[start : start + UPSERT_BATCH_SIZE])


def _event_values(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["completion_time"] = _datetime(value["completion_time"])
    for key in (
        "qualified_quantity",
        "defective_quantity",
        "machine_hours",
        "labor_hours",
    ):
        value[key] = _decimal(value.get(key, "0"))
    return value


def _input_values(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["consumed_quantity"] = _decimal(value["consumed_quantity"])
    return value


def _record_values(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["amount"] = _decimal(value["amount"])
    value["incurred_at"] = _datetime(value["incurred_at"])
    return value


def _summarize(errors: list[ImportErrorItem]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in errors:
        result[item.error_code] = result.get(item.error_code, 0) + 1
    return result


def _result(batch: DataLoadBatch, *, skipped: bool) -> ImportResult:
    return ImportResult(
        batch_id=batch.batch_id,
        status=batch.status,
        total_rows=batch.total_rows,
        valid_rows=batch.valid_rows,
        error_rows=batch.error_rows,
        error_summary=batch.error_summary,
        skipped=skipped,
    )
