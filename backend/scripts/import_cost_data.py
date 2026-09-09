from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="将成本 v2 JSON/CSV 导入 PostgreSQL。")
    parser.add_argument(
        "--parts", default=str(PROJECT_ROOT / "data/samples/parts.json")
    )
    parser.add_argument(
        "--events", default=str(PROJECT_ROOT / "data/samples/cost_events.json")
    )
    parser.add_argument(
        "--inputs", default=str(PROJECT_ROOT / "data/samples/cost_event_inputs.json")
    )
    parser.add_argument(
        "--records", default=str(PROJECT_ROOT / "data/samples/cost_records.json")
    )
    parser.add_argument("--tenant-id", default="local-costgraph-tenant")
    parser.add_argument("--source-system", default="costgraph_samples")
    args = parser.parse_args()
    from app.services.cost_data_import_service import CostDataImporter, load_records

    result = CostDataImporter().import_records(
        tenant_id=args.tenant_id,
        source_system=args.source_system,
        parts=load_records(args.parts),
        events=load_records(args.events),
        inputs=load_records(args.inputs),
        records=load_records(args.records),
        source_file=f"{args.parts}, {args.events}, {args.inputs}, {args.records}",
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "published" else 1


if __name__ == "__main__":
    raise SystemExit(main())
