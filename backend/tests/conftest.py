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


def postgres_unavailable(reason: str) -> None:
    """Local offline runs may skip DB tests; the dedicated CI gate must fail closed."""
    if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """The dedicated PostgreSQL gate must fail if any integration test skips."""
    if os.getenv("REQUIRE_POSTGRES_TESTS") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None and reporter.stats.get("skipped"):
        reporter.write_line(
            "PostgreSQL 集成门禁禁止 skip；请检查数据库、角色和测试前置条件。",
            red=True,
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def reset_cached_dependencies():
    from app.db.engine import reset_engine
    from app.settings import reset_settings

    reset_engine()
    reset_settings()
    yield
    reset_engine()
    reset_settings()
