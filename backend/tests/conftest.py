import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "")


@pytest.fixture(autouse=True)
def reset_cached_dependencies():
    from app.db.engine import reset_engine
    from app.settings import reset_settings

    reset_engine()
    reset_settings()
    yield
    reset_engine()
    reset_settings()
