from __future__ import annotations

from app.settings import get_settings


def get_database_url() -> str:
    value = get_settings().resolved_database_url
    if value is None:
        raise RuntimeError("未配置 COST_DATABASE_URL，无法使用 PostgreSQL 数据服务。")
    return value


def get_database_pool_settings() -> dict[str, int | bool]:
    settings = get_settings()
    return {
        "pool_pre_ping": True,
        "pool_size": settings.cost_database_pool_size,
        "max_overflow": settings.cost_database_max_overflow,
    }
