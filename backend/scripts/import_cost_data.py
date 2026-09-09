from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="将成本 JSON/CSV 导入 PostgreSQL。")
    parser.add_argument(
        "--products", default=str(PROJECT_ROOT / "data/samples/products.json")
    )
    parser.add_argument(
        "--outputs", default=str(PROJECT_ROOT / "data/samples/production_outputs.json")
    )
    parser.add_argument(
        "--entries",
        default=str(PROJECT_ROOT / "data/samples/process_cost_entries.json"),
    )
    parser.add_argument("--tenant-id", default="local-costgraph-tenant")
    parser.add_argument("--source-system", default="costgraph_samples")
    args = parser.parse_args()

    from app.services.cost_data_import_service import CostDataImporter, load_records

    result = CostDataImporter().import_records(
        tenant_id=args.tenant_id,
        source_system=args.source_system,
        products=load_records(args.products),
        outputs=load_records(args.outputs),
        entries=load_records(args.entries),
        source_file=f"{args.products}, {args.outputs}, {args.entries}",
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "published" else 1


if __name__ == "__main__":
    raise SystemExit(main())
