from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.config import get_database_pool_settings, get_database_url
from app.settings import reset_settings


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(get_database_url(), **get_database_pool_settings())


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def reset_engine() -> None:
    get_session_factory.cache_clear()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    reset_settings()
