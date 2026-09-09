from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.engine import get_session_factory
from app.db.models import (
    DataLoadBatch,
    DataLoadError,
    ProcessCostEntry,
    Product,
    ProductionOutput,
)

ALLOWED_COST_ITEMS = {"material", "labor", "equipment", "energy", "overhead"}
UPSERT_BATCH_SIZE = 500
POSTGRES_INTEGER_MAX = 2_147_483_647
AMOUNT_INTEGER_DIGITS = 16
QUANTITY_INTEGER_DIGITS = 14


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
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_date(value: Any) -> date:
    return date.fromisoformat(_text(value))


def _parse_decimal(value: Any) -> Decimal:
    return Decimal(_text(value))


def _parse_integer(value: Any) -> int:
    parsed = _parse_decimal(value)
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ValueError("value is not an integer")
    return int(parsed)


def _exceeds_numeric_range(value: Decimal, integer_digits: int) -> bool:
    return abs(value) >= Decimal(10) ** integer_digits


def _period_matches(period: str, production_date: date) -> bool:
    return period == production_date.strftime("%Y-%m")


def _error(
    source_table: str,
    row: dict[str, Any],
    code: str,
    message: str,
) -> ImportErrorItem:
    return ImportErrorItem(
        source_table=source_table,
        source_record_id=_text(
            row.get("source_record_id")
            or row.get("output_record_id")
            or row.get("cost_entry_id")
            or row.get("product_id")
        )
        or None,
        error_code=code,
        error_message=message,
        raw_payload=row,
    )


def _duplicate_errors(
    rows: Iterable[dict[str, Any]], source_table: str, id_key: str
) -> list[ImportErrorItem]:
    seen: set[str] = set()
    errors: list[ImportErrorItem] = []
    for row in rows:
        record_id = _text(row.get(id_key))
        if record_id and record_id in seen:
            errors.append(
                _error(
                    source_table,
                    row,
                    "DUPLICATE_SOURCE_RECORD",
                    f"来源记录 ID 重复：{record_id}",
                )
            )
        if record_id:
            seen.add(record_id)
    return errors


def _validate_records(
    products: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> list[ImportErrorItem]:
    errors: list[ImportErrorItem] = []
    for name, rows in (
        ("products", products),
        ("production_outputs", outputs),
        ("process_cost_entries", entries),
    ):
        if not rows:
            errors.append(_error(name, {}, "EMPTY_DATASET", f"{name} 数据集不能为空。"))
    errors.extend(_duplicate_errors(products, "products", "product_id"))
    errors.extend(_duplicate_errors(outputs, "production_outputs", "output_record_id"))
    errors.extend(_duplicate_errors(entries, "process_cost_entries", "cost_entry_id"))
    errors.extend(_duplicate_errors(products, "products", "source_record_id"))
    errors.extend(_duplicate_errors(outputs, "production_outputs", "source_record_id"))
    errors.extend(
        _duplicate_errors(entries, "process_cost_entries", "source_record_id")
    )

    product_ids: set[str] = set()
    for row in products:
        product_id = _text(row.get("product_id"))
        if not product_id or not _text(row.get("product_name")):
            errors.append(
                _error("products", row, "REQUIRED_FIELD", "产品 ID 和名称不能为空。")
            )
        if product_id:
            product_ids.add(product_id)

    output_keys: set[tuple[str, str]] = set()
    output_rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    unit_by_product: dict[str, str] = {}
    for row in outputs:
        product_id = _text(row.get("product_id"))
        try:
            production_date = _parse_date(row.get("production_date"))
            quantity = _parse_decimal(row.get("qualified_output_qty"))
            if not quantity.is_finite():
                raise InvalidOperation
        except (ValueError, InvalidOperation):
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "INVALID_VALUE",
                    "生产日期或合格产量格式非法。",
                )
            )
            continue
        period = _text(row.get("period"))
        if (
            not _text(row.get("output_record_id"))
            or not product_id
            or not period
            or not _text(row.get("unit"))
        ):
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "REQUIRED_FIELD",
                    "记录 ID、产品、期间和单位不能为空。",
                )
            )
        if not _period_matches(period, production_date):
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "PERIOD_DATE_MISMATCH",
                    "期间与生产日期月份不一致。",
                )
            )
        if quantity <= 0:
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "NON_POSITIVE_OUTPUT",
                    "合格产量必须大于零。",
                )
            )
        if quantity.as_tuple().exponent < -4:
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "QUANTITY_SCALE_EXCEEDED",
                    "合格产量最多保留 4 位小数。",
                )
            )
        if _exceeds_numeric_range(quantity, QUANTITY_INTEGER_DIGITS):
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "QUANTITY_PRECISION_EXCEEDED",
                    "合格产量超出 NUMERIC(18,4) 可表示范围。",
                )
            )
        unit = _text(row.get("unit"))
        known_unit = unit_by_product.get(product_id)
        if product_id and unit and known_unit and unit != known_unit:
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "PRODUCT_UNIT_MISMATCH",
                    f"同一产品的产量单位不一致：{known_unit} / {unit}",
                )
            )
        elif product_id and unit:
            unit_by_product[product_id] = unit
        if product_id not in product_ids:
            errors.append(
                _error(
                    "production_outputs",
                    row,
                    "PRODUCT_NOT_FOUND",
                    f"导入批次中不存在产品：{product_id}",
                )
            )
        output_key = (product_id, _text(row.get("production_date")))
        output_keys.add(output_key)
        output_rows_by_key[output_key] = row

    entry_keys: set[tuple[str, str]] = set()
    for row in entries:
        product_id = _text(row.get("product_id"))
        try:
            production_date = _parse_date(row.get("production_date"))
            amount = _parse_decimal(row.get("amount"))
            if not amount.is_finite():
                raise InvalidOperation
        except (ValueError, InvalidOperation):
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "INVALID_VALUE",
                    "生产日期或成本金额格式非法。",
                )
            )
            continue
        try:
            process_sort = _parse_integer(row.get("process_sort"))
        except (InvalidOperation, ValueError):
            errors.append(
                _error(
                    "process_cost_entries", row, "INVALID_VALUE", "工序顺序必须是整数。"
                )
            )
        else:
            if not 1 <= process_sort <= POSTGRES_INTEGER_MAX:
                errors.append(
                    _error(
                        "process_cost_entries",
                        row,
                        "PROCESS_SORT_OUT_OF_RANGE",
                        f"工序顺序必须在 1 到 {POSTGRES_INTEGER_MAX} 之间。",
                    )
                )
        period = _text(row.get("period"))
        item = _text(row.get("cost_item"))
        if (
            not _text(row.get("cost_entry_id"))
            or not product_id
            or not period
            or not _text(row.get("process_code"))
            or not _text(row.get("process_name"))
            or not _text(row.get("currency"))
        ):
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "REQUIRED_FIELD",
                    "记录 ID、产品、期间、工序和币种不能为空。",
                )
            )
        if not _period_matches(period, production_date):
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "PERIOD_DATE_MISMATCH",
                    "期间与生产日期月份不一致。",
                )
            )
        if item not in ALLOWED_COST_ITEMS:
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "UNKNOWN_COST_ITEM",
                    f"未知成本项目：{item}",
                )
            )
        if _text(row.get("currency")) != "CNY":
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "UNSUPPORTED_CURRENCY",
                    "当前只允许 CNY。",
                )
            )
        if amount < 0:
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "NEGATIVE_AMOUNT",
                    "当前导入契约不接受负数成本。",
                )
            )
        if amount.as_tuple().exponent < -2:
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "AMOUNT_SCALE_EXCEEDED",
                    "成本金额最多保留 2 位小数。",
                )
            )
        if _exceeds_numeric_range(amount, AMOUNT_INTEGER_DIGITS):
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "AMOUNT_PRECISION_EXCEEDED",
                    "成本金额超出 NUMERIC(18,2) 可表示范围。",
                )
            )
        if product_id not in product_ids:
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "PRODUCT_NOT_FOUND",
                    f"导入批次中不存在产品：{product_id}",
                )
            )
        if (product_id, _text(row.get("production_date"))) not in output_keys:
            errors.append(
                _error(
                    "process_cost_entries",
                    row,
                    "OUTPUT_NOT_FOUND",
                    "成本记录没有对应的日级产量记录。",
                )
            )
        entry_keys.add((product_id, _text(row.get("production_date"))))

    for missing_key in sorted(output_keys - entry_keys):
        errors.append(
            _error(
                "production_outputs",
                output_rows_by_key[missing_key],
                "COST_NOT_FOUND",
                "日级产量记录没有对应的工序成本记录。",
            )
        )
    return errors


class CostDataImporter:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def import_records(
        self,
        *,
        tenant_id: str,
        source_system: str,
        products: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        entries: list[dict[str, Any]],
        source_file: str | None = None,
    ) -> ImportResult:
        tenant_id = tenant_id.strip()
        source_system = source_system.strip()
        if not tenant_id:
            raise ValueError("tenant_id 不能为空。")
        if not source_system:
            raise ValueError("source_system 不能为空。")
        if len(tenant_id) > 64:
            raise ValueError("tenant_id 最多 64 个字符。")
        if len(source_system) > 64:
            raise ValueError("source_system 最多 64 个字符。")
        source_file = _text(source_file) or None
        if source_file is not None and len(source_file) > 500:
            raise ValueError("source_file 最多 500 个字符。")
        started_at = _now()
        payload = {
            "products": products,
            "production_outputs": outputs,
            "process_cost_entries": entries,
        }
        snapshot_hash = _canonical_hash(payload)
        total_rows = sum(len(rows) for rows in payload.values())
        errors = _validate_records(products, outputs, entries)
        invalid_rows = _count_error_rows(errors)
        error_summary = {"codes": _summarize_error_codes(errors)} if errors else None
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
            if existing is not None:
                session.rollback()
                return _result_from_batch(existing, skipped=True)

            batch_id = uuid4()
            write_time = _now()
            batch = DataLoadBatch(
                batch_id=batch_id,
                tenant_id=tenant_id,
                source_system=source_system,
                source_file=source_file,
                source_snapshot_hash=snapshot_hash,
                status="failed" if errors else "validated",
                total_rows=total_rows,
                valid_rows=total_rows - invalid_rows,
                error_rows=invalid_rows,
                error_summary=error_summary,
                started_at=started_at,
                finished_at=None,
                created_at=started_at,
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
                        created_at=write_time,
                    )
                    for item in errors
                )
                batch.finished_at = _now()
            else:
                session.execute(
                    update(DataLoadBatch)
                    .where(
                        DataLoadBatch.tenant_id == tenant_id,
                        DataLoadBatch.status == "published",
                        DataLoadBatch.batch_id != batch_id,
                    )
                    .values(status="superseded")
                )
                self._upsert_products(
                    session, tenant_id, source_system, batch_id, products, write_time
                )
                self._upsert_outputs(
                    session, tenant_id, source_system, batch_id, outputs, write_time
                )
                self._upsert_entries(
                    session, tenant_id, source_system, batch_id, entries, write_time
                )
                batch.status = "published"
                batch.finished_at = _now()
                session.flush()
            session.commit()
            return ImportResult(
                batch_id=batch_id,
                status=batch.status,
                total_rows=total_rows,
                valid_rows=batch.valid_rows,
                error_rows=batch.error_rows,
                error_summary=batch.error_summary,
            )
        except IntegrityError as exc:
            session.rollback()
            diagnostics = getattr(getattr(exc, "orig", None), "diag", None)
            constraint_name = getattr(diagnostics, "constraint_name", None)
            if constraint_name == "uq_data_load_batches_snapshot":
                existing = session.scalar(
                    select(DataLoadBatch)
                    .where(
                        DataLoadBatch.tenant_id == tenant_id,
                        DataLoadBatch.source_system == source_system,
                        DataLoadBatch.source_snapshot_hash == snapshot_hash,
                    )
                    .order_by(desc(DataLoadBatch.created_at))
                )
                if existing is None:
                    raise
                return _result_from_batch(existing, skipped=True)
            if constraint_name != "uq_data_load_batches_active_tenant":
                raise
            existing = session.scalar(
                select(DataLoadBatch).where(
                    DataLoadBatch.tenant_id == tenant_id,
                    DataLoadBatch.status == "published",
                )
            )
            if (
                existing is None
                or existing.source_system != source_system
                or existing.source_snapshot_hash != snapshot_hash
            ):
                raise RuntimeError("同一租户存在并发发布的其他成本数据批次。") from exc
            return _result_from_batch(existing, skipped=True)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _upsert_products(
        session: Session,
        tenant_id: str,
        source_system: str,
        batch_id: UUID,
        rows: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        values = [
            {
                "tenant_id": tenant_id,
                "product_id": _text(row["product_id"]),
                "product_name": _text(row["product_name"]),
                "spec": _text(row.get("spec")) or None,
                "is_active": True,
                "source_system": source_system,
                "source_record_id": _text(row.get("source_record_id"))
                or _text(row["product_id"]),
                "batch_id": batch_id,
                "created_at": now,
                "updated_at": now,
            }
            for row in rows
        ]
        insert_statement = pg_insert(Product)
        statement = insert_statement.on_conflict_do_update(
            index_elements=["tenant_id", "product_id"],
            set_={
                key: getattr(insert_statement.excluded, key)
                for key in (
                    "product_name",
                    "spec",
                    "is_active",
                    "source_system",
                    "source_record_id",
                    "batch_id",
                    "updated_at",
                )
            },
        )
        for chunk in _chunks(values):
            session.execute(statement, chunk)

    @staticmethod
    def _upsert_outputs(
        session: Session,
        tenant_id: str,
        source_system: str,
        batch_id: UUID,
        rows: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        values = [
            {
                "tenant_id": tenant_id,
                "output_record_id": _text(row["output_record_id"]),
                "product_id": _text(row["product_id"]),
                "period": _text(row["period"]),
                "production_date": _parse_date(row["production_date"]),
                "qualified_output_qty": _parse_decimal(row["qualified_output_qty"]),
                "unit": _text(row["unit"]),
                "source_system": source_system,
                "source_record_id": _text(row.get("source_record_id"))
                or _text(row["output_record_id"]),
                "source_batch": _text(row.get("source_batch")) or None,
                "batch_id": batch_id,
                "raw_payload": row,
                "created_at": now,
                "updated_at": now,
            }
            for row in rows
        ]
        insert_statement = pg_insert(ProductionOutput)
        update_keys = (
            [
                key
                for key in values[0]
                if key not in {"tenant_id", "output_record_id", "created_at"}
            ]
            if values
            else []
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=["tenant_id", "output_record_id"],
            set_={key: getattr(insert_statement.excluded, key) for key in update_keys},
        )
        for chunk in _chunks(values):
            session.execute(statement, chunk)

    @staticmethod
    def _upsert_entries(
        session: Session,
        tenant_id: str,
        source_system: str,
        batch_id: UUID,
        rows: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        values = [
            {
                "tenant_id": tenant_id,
                "cost_entry_id": _text(row["cost_entry_id"]),
                "product_id": _text(row["product_id"]),
                "period": _text(row["period"]),
                "production_date": _parse_date(row["production_date"]),
                "process_code": _text(row["process_code"]),
                "process_name": _text(row["process_name"]),
                "process_sort": _parse_integer(row["process_sort"]),
                "cost_item": _text(row["cost_item"]),
                "amount": _parse_decimal(row["amount"]),
                "currency": _text(row["currency"]),
                "source_system": source_system,
                "source_record_id": _text(row.get("source_record_id"))
                or _text(row["cost_entry_id"]),
                "source_batch": _text(row.get("source_batch")) or None,
                "batch_id": batch_id,
                "raw_payload": row,
                "created_at": now,
                "updated_at": now,
            }
            for row in rows
        ]
        insert_statement = pg_insert(ProcessCostEntry)
        update_keys = (
            [
                key
                for key in values[0]
                if key not in {"tenant_id", "cost_entry_id", "created_at"}
            ]
            if values
            else []
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=["tenant_id", "cost_entry_id"],
            set_={key: getattr(insert_statement.excluded, key) for key in update_keys},
        )
        for chunk in _chunks(values):
            session.execute(statement, chunk)


def _summarize_error_codes(errors: list[ImportErrorItem]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in errors:
        result[item.error_code] = result.get(item.error_code, 0) + 1
    return result


def _count_error_rows(errors: list[ImportErrorItem]) -> int:
    return len(
        {
            (
                item.source_table,
                item.source_record_id
                or hashlib.sha256(
                    json.dumps(
                        item.raw_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest(),
            )
            for item in errors
            if item.raw_payload
        }
    )


def _result_from_batch(batch: DataLoadBatch, *, skipped: bool) -> ImportResult:
    return ImportResult(
        batch_id=batch.batch_id,
        status=batch.status,
        total_rows=batch.total_rows,
        valid_rows=batch.valid_rows,
        error_rows=batch.error_rows,
        error_summary=batch.error_summary,
        skipped=skipped,
    )


def _chunks(values: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(values), UPSERT_BATCH_SIZE):
        yield values[index : index + UPSERT_BATCH_SIZE]
