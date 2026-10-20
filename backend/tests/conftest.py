import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("APP_ENV", "test")
os.environ["LOCAL_DEMO_AUTH_ENABLED"] = "false"
# 测试一律离线：非空占位值才能盖过 backend/.env 里的真实 Key
# （SettingsConfigDict 的 env_ignore_empty 会跳过空字符串），
# 而含 placeholder 的值会被确定性判定为不可用。
os.environ["DEEPSEEK_API_KEY"] = "offline-test-placeholder"
os.environ.setdefault(
    "AGENT_SERVER_ALLOWED_CAPABILITIES",
    "system_help,cost_calculation,report_generation",
)


@pytest.fixture(autouse=True)
def reset_cached_dependencies():
    from app.db.engine import reset_engine
    from app.settings import reset_settings

    reset_engine()
    reset_settings()
    yield
    reset_engine()
    reset_settings()
