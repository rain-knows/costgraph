from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from time import perf_counter

from sqlalchemy import event

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.engine import get_engine
from app.domain.authorization import get_server_principal
from app.services.cost_data_query_service import CostDataQueryService

ITERATIONS = 10
P95_TARGET_SECONDS = 1.0


def main() -> int:
    service = CostDataQueryService()
    principal = get_server_principal()
    calls = {
        "overview": lambda: service.overview("2026-06", principal),
        "list": lambda: service.list_finished_batches(
            period="2026-06",
            query=None,
            cost_center_code=None,
            sort="completion_time_desc",
            page=1,
            page_size=20,
            principal=principal,
        ),
        "unit_cost_sort": lambda: service.list_finished_batches(
            period="2026-06",
            query=None,
            cost_center_code=None,
            sort="unit_cost_desc",
            page=1,
            page_size=20,
            principal=principal,
        ),
        "filtered_list": lambda: service.list_finished_batches(
            period="2026-06",
            query="FG-001",
            cost_center_code=None,
            sort="completion_time_desc",
            page=1,
            page_size=20,
            principal=principal,
        ),
        "detail": lambda: service.finished_batch_detail("FG-A-2026-06", principal),
    }
    engine = get_engine()
    report: dict[str, dict[str, float | int]] = {}
    passed = True
    for name, call in calls.items():
        call()
        timings: list[float] = []
        query_counts: list[int] = []
        for _ in range(ITERATIONS):
            count = 0

            def before_cursor_execute(*_args) -> None:
                nonlocal count
                count += 1

            event.listen(engine, "before_cursor_execute", before_cursor_execute)
            started = perf_counter()
            try:
                call()
            finally:
                timings.append(perf_counter() - started)
                query_counts.append(count)
                event.remove(engine, "before_cursor_execute", before_cursor_execute)
        p95 = statistics.quantiles(timings, n=20)[18]
        maximum_queries = max(query_counts)
        report[name] = {
            "p95_seconds": round(p95, 3),
            "max_seconds": round(max(timings), 3),
            "max_queries": maximum_queries,
        }
        passed = passed and p95 < P95_TARGET_SECONDS and maximum_queries <= 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
