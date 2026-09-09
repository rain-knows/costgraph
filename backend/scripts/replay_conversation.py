from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用固定 Provider/Tool Fixture 重新执行 Runtime 图。"
    )
    parser.add_argument("fixture", type=Path, help="Replay Fixture JSON 文件")
    args = parser.parse_args()

    from app.agent.replay import replay_fixture

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    result = replay_fixture(fixture)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
