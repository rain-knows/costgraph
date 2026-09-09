from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="查看成本数据导入批次和行级错误。")
    parser.add_argument("batch_id", type=UUID)
    args = parser.parse_args()

    from app.db.engine import get_session_factory
    from app.db.models import DataLoadBatch, DataLoadError

    session = get_session_factory()()
    try:
        batch = session.get(DataLoadBatch, args.batch_id)
        if batch is None:
            print(
                json.dumps(
                    {"error": "batch_not_found", "batch_id": str(args.batch_id)},
                    ensure_ascii=False,
                )
            )
            return 1
        errors = session.scalars(
            select(DataLoadError)
            .where(DataLoadError.batch_id == args.batch_id)
            .order_by(DataLoadError.error_id)
        ).all()
        result = {
            "batch": {
                "batch_id": str(batch.batch_id),
                "tenant_id": batch.tenant_id,
                "source_system": batch.source_system,
                "source_file": batch.source_file,
                "source_snapshot_hash": batch.source_snapshot_hash,
                "status": batch.status,
                "total_rows": batch.total_rows,
                "valid_rows": batch.valid_rows,
                "error_rows": batch.error_rows,
                "error_summary": batch.error_summary,
                "started_at": batch.started_at.isoformat(),
                "finished_at": batch.finished_at.isoformat()
                if batch.finished_at
                else None,
            },
            "errors": [
                {
                    "source_table": item.source_table,
                    "source_record_id": item.source_record_id,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for item in errors
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
