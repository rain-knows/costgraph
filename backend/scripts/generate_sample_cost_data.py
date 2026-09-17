from __future__ import annotations

import json
from calendar import monthrange
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"
PERIODS = tuple(
    f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 13)
)
FINISHED_PRODUCT_COUNT = 100
CORE_PRODUCT_COUNT = 10
GENERATED_PREFIX = "GEN-"


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _write(name: str, rows: list[dict[str, Any]]) -> None:
    (SAMPLES / name).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _timestamp(period: str, day: int, hour: int = 18) -> str:
    year, month = (int(value) for value in period.split("-"))
    valid_day = min(day, monthrange(year, month)[1])
    return f"{period}-{valid_day:02d}T{hour:02d}:00:00+08:00"


def _part_rows() -> list[dict[str, Any]]:
    families = (
        "汽车门板内饰",
        "汽车中控内饰",
        "汽车仪表板内饰",
        "汽车座舱装饰",
        "汽车立柱内饰",
    )
    rows: list[dict[str, Any]] = []
    for index in range(4, FINISHED_PRODUCT_COUNT + 1):
        rows.append(
            {
                "part_id": f"GEN-P-FG-{index:03d}",
                "part_number": f"FG-{index:03d}",
                "part_description": f"座舱装饰件系列 {index:03d}",
                "part_type": "finished_good",
                "product_family": families[(index - 1) % len(families)],
                "unit": "pcs",
                "source_record_id": f"GEN-SRC-P-FG-{index:03d}",
            }
        )

    common_parts = (
        ("RAW-01", "RM-PP-01", "通用改性 PP 料包", "raw_material"),
        ("RAW-02", "RM-ABS-01", "通用 ABS 料包", "raw_material"),
        ("RAW-03", "RM-TPO-01", "通用 TPO 表皮料包", "raw_material"),
        ("RAW-04", "RM-PCABS-01", "通用 PC/ABS 料包", "raw_material"),
        ("RAW-05", "RM-PA66-01", "通用 PA66 增强料包", "raw_material"),
        ("RAW-06", "RM-COAT-01", "通用涂层材料包", "raw_material"),
        ("BUY-01", "SF-CLIP-02", "通用金属卡扣套件", "purchased_semi_finished"),
        ("BUY-02", "SF-FOAM-01", "通用吸音棉套件", "purchased_semi_finished"),
        ("BUY-03", "SF-TRIM-01", "通用装饰条套件", "purchased_semi_finished"),
        ("BUY-04", "SF-FASTENER-01", "通用紧固件套件", "purchased_semi_finished"),
        ("WIP-01", "WIP-CABIN-BASE-01", "通用座舱注塑基材一", "work_in_progress"),
        ("WIP-02", "WIP-CABIN-BASE-02", "通用座舱注塑基材二", "work_in_progress"),
        (
            "WIP-03",
            "WIP-CABIN-SURFACE-01",
            "通用座舱表面处理半成品一",
            "work_in_progress",
        ),
        (
            "WIP-04",
            "WIP-CABIN-SURFACE-02",
            "通用座舱表面处理半成品二",
            "work_in_progress",
        ),
    )
    for suffix, number, description, part_type in common_parts:
        rows.append(
            {
                "part_id": f"GEN-P-COMMON-{suffix}",
                "part_number": number,
                "part_description": description,
                "part_type": part_type,
                "product_family": "汽车座舱公共部件",
                "unit": "pcs",
                "source_record_id": f"GEN-SRC-P-COMMON-{suffix}",
            }
        )
    return rows


def _scheduled_products(period: str) -> list[int]:
    month = int(period[-2:])
    rotating = [
        index
        for index in range(11, FINISHED_PRODUCT_COUNT + 1)
        if (index - 11) % 12 == month - 1
    ]
    return list(range(1, CORE_PRODUCT_COUNT + 1)) + rotating


def _product_part_id(index: int) -> str:
    return f"P-FG-{index:03d}" if index <= 3 else f"GEN-P-FG-{index:03d}"


def _add_cost(
    records: list[dict[str, Any]],
    event_id: str,
    period: str,
    code: str,
    amount: Decimal,
    line: str,
) -> None:
    record_id = f"GEN-CR-{event_id.removeprefix('GEN-E-')}-{line}"
    records.append(
        {
            "cost_record_id": record_id,
            "event_id": event_id,
            "cost_code": code,
            "amount": _money(amount),
            "currency": "CNY",
            "incurred_at": _timestamp(period, 26),
            "source_document_no": f"DOC-{event_id.removeprefix('GEN-E-')}",
            "source_document_line": line,
            "source_record_id": f"GEN-SRC-{record_id}",
        }
    )


def _generated_facts(
    existing_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    existing_finished = {
        (row["part_id"], row["period"])
        for row in existing_events
        if row["part_id"].startswith("P-FG-")
    }
    events: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    for period in PERIODS:
        year, month = (int(value) for value in period.split("-"))
        for product in _scheduled_products(period):
            part_id = _product_part_id(product)
            if (part_id, period) in existing_finished:
                continue

            key = f"{period.replace('-', '')}-P{product:03d}"
            quantity = 760 + product * 4 + month * 9
            defective = (product + month + year) % 17
            qualified = quantity - defective
            raw_a = (product - 1) % 6 + 1
            raw_b = (product + month - 1) % 6 + 1
            if raw_b == raw_a:
                raw_b = raw_b % 6 + 1
            bought = (product + month - 2) % 4 + 1
            wip_base = (product - 1) % 2 + 1
            wip_surface = (product + month - 1) % 2 + 3
            event_ids = {
                "raw_a": f"GEN-E-RAW-A-{key}",
                "raw_b": f"GEN-E-RAW-B-{key}",
                "bought": f"GEN-E-BUY-{key}",
                "molding": f"GEN-E-MOLD-{key}",
                "surface": f"GEN-E-SURF-{key}",
                "finished": f"GEN-E-FG-{key}",
            }
            event_specs = (
                (
                    "raw_a",
                    "purchase",
                    f"GEN-P-COMMON-RAW-{raw_a:02d}",
                    3,
                    "PURCHASE",
                    "公共原料购置",
                    quantity * 2,
                    0,
                    "",
                    "",
                ),
                (
                    "raw_b",
                    "purchase",
                    f"GEN-P-COMMON-RAW-{raw_b:02d}",
                    4,
                    "PURCHASE",
                    "公共辅料购置",
                    quantity,
                    0,
                    "",
                    "",
                ),
                (
                    "bought",
                    "purchase",
                    f"GEN-P-COMMON-BUY-{bought:02d}",
                    5,
                    "PURCHASE",
                    "公共外购件购置",
                    quantity,
                    0,
                    "",
                    "",
                ),
                (
                    "molding",
                    "process",
                    f"GEN-P-COMMON-WIP-{wip_base:02d}",
                    12,
                    "INJECTION_MOLDING",
                    "注塑成型",
                    quantity,
                    8,
                    "CC-INJECTION",
                    "注塑车间",
                ),
                (
                    "surface",
                    "process",
                    f"GEN-P-COMMON-WIP-{wip_surface:02d}",
                    20,
                    "SURFACE_FINISHING",
                    "表面处理",
                    quantity,
                    5,
                    "CC-SURFACE",
                    "表面处理车间",
                ),
                (
                    "finished",
                    "process",
                    part_id,
                    26,
                    "CABIN_ASSEMBLY",
                    "座舱装饰件总成装配",
                    qualified,
                    defective,
                    "CC-ASSEMBLY",
                    "总成装配车间",
                ),
            )
            for (
                role,
                event_type,
                output_part,
                day,
                process_code,
                process_name,
                good,
                bad,
                center_code,
                center_name,
            ) in event_specs:
                event_id = event_ids[role]
                events.append(
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "output_batch_id": f"GEN-B-{role.upper()}-{key}",
                        "part_id": output_part,
                        "period": period,
                        "completion_time": _timestamp(period, day),
                        "cost_center_code": center_code,
                        "cost_center_name": center_name,
                        "work_order_number": ""
                        if event_type == "purchase"
                        else f"WO-{role.upper()}-{key}",
                        "lot_number": f"LOT-{role.upper()}-{key}",
                        "process_code": process_code,
                        "process_name": process_name,
                        "qualified_quantity": str(good),
                        "defective_quantity": str(bad),
                        "unit": "pcs",
                        "machine_hours": "0"
                        if event_type == "purchase"
                        else _money(Decimal(good) / Decimal(18)),
                        "labor_hours": "0"
                        if event_type == "purchase"
                        else _money(Decimal(good) / Decimal(12)),
                        "source_record_id": f"GEN-SRC-{event_id}",
                    }
                )

            edges = (
                ("RAW-A-MOLD", "molding", "raw_a", quantity),
                ("RAW-B-MOLD", "molding", "raw_b", quantity),
                ("MOLD-SURF", "surface", "molding", quantity),
                ("SURF-FG", "finished", "surface", quantity),
                ("BUY-FG", "finished", "bought", quantity),
            )
            for label, target, source, consumed in edges:
                input_id = f"GEN-I-{label}-{key}"
                inputs.append(
                    {
                        "input_id": input_id,
                        "event_id": event_ids[target],
                        "source_event_id": event_ids[source],
                        "consumed_quantity": str(consumed),
                        "unit": "pcs",
                        "source_record_id": f"GEN-SRC-{input_id}",
                    }
                )

            seasonal = (
                Decimal(1)
                + Decimal(month - 1) * Decimal("0.006")
                + Decimal(year - 2025) * Decimal("0.035")
            )
            product_factor = Decimal(1) + Decimal(product % 13) * Decimal("0.012")
            base = Decimal(quantity) * seasonal * product_factor
            _add_cost(
                records,
                event_ids["raw_a"],
                period,
                "raw_material",
                base * Decimal("5.20"),
                "MAT",
            )
            _add_cost(
                records,
                event_ids["raw_b"],
                period,
                "raw_material",
                base * Decimal("1.35"),
                "AUX",
            )
            _add_cost(
                records,
                event_ids["bought"],
                period,
                "purchased_semi_finished",
                base * Decimal("1.10"),
                "BUY",
            )
            for role, factor in (
                ("molding", Decimal("1.00")),
                ("surface", Decimal("0.82")),
                ("finished", Decimal("1.18")),
            ):
                _add_cost(
                    records,
                    event_ids[role],
                    period,
                    "direct_wages",
                    base * factor * Decimal("0.70"),
                    f"{role[:2].upper()}-LAB",
                )
                _add_cost(
                    records,
                    event_ids[role],
                    period,
                    "electricity",
                    base * factor * Decimal("0.22"),
                    f"{role[:2].upper()}-ENE",
                )
                _add_cost(
                    records,
                    event_ids[role],
                    period,
                    "equipment_depreciation",
                    base * factor * Decimal("0.16"),
                    f"{role[:2].upper()}-DEP",
                )
                _add_cost(
                    records,
                    event_ids[role],
                    period,
                    "testing_inspection",
                    base * factor * Decimal("0.11"),
                    f"{role[:2].upper()}-OH",
                )
            _add_cost(
                records,
                event_ids["finished"],
                period,
                "transportation",
                base * Decimal("0.18"),
                "POST-TR",
            )
            _add_cost(
                records,
                event_ids["finished"],
                period,
                "post_manufacturing_storage_fee",
                base * Decimal("0.05"),
                "POST-ST",
            )

    return events, inputs, records


def main() -> None:
    parts = [
        row
        for row in _load("parts.json")
        if not row["part_id"].startswith(GENERATED_PREFIX)
    ]
    events = [
        row
        for row in _load("cost_events.json")
        if not row["event_id"].startswith(GENERATED_PREFIX)
    ]
    inputs = [
        row
        for row in _load("cost_event_inputs.json")
        if not row["input_id"].startswith(GENERATED_PREFIX)
    ]
    records = [
        row
        for row in _load("cost_records.json")
        if not row["cost_record_id"].startswith(GENERATED_PREFIX)
    ]

    generated_events, generated_inputs, generated_records = _generated_facts(events)
    parts.extend(_part_rows())
    events.extend(generated_events)
    inputs.extend(generated_inputs)
    records.extend(generated_records)

    _write("parts.json", parts)
    _write("cost_events.json", events)
    _write("cost_event_inputs.json", inputs)
    _write("cost_records.json", records)

    finished_ids = {
        row["part_id"] for row in parts if row["part_type"] == "finished_good"
    }
    finished_events = [row for row in events if row["part_id"] in finished_ids]
    print(
        json.dumps(
            {
                "parts": len(parts),
                "finished_products": len(finished_ids),
                "finished_batches": len(finished_events),
                "events": len(events),
                "inputs": len(inputs),
                "cost_records": len(records),
                "periods": len({row["period"] for row in finished_events}),
                "minimum_finished_batches_per_period": min(
                    sum(row["period"] == period for row in finished_events)
                    for period in PERIODS
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
