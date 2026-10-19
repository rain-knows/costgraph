"""租户上下文注入。

RLS 策略读取 ``current_setting('app.tenant_id', true)``。租户只能来自已认证
主体的 ``tenant_id``（API 侧）或已领取记录上固化的 ``tenant_id``（Worker 侧），
绝不来自请求参数、查询串或模型输出。缺失即调用方缺陷：显式失败，不回退到
默认租户，也不允许退化成跨租户可见。

隔离是双重的：Repository 的 ``WHERE tenant_id = ...`` 是业务定界，RLS 是
最后一道防线。两者都必须存在，任何一方单独存在都不足以成立。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TENANT_SETTING = "app.tenant_id"
_SET_TENANT_SQL = f"SELECT set_config('{TENANT_SETTING}', :tenant_id, false)"
_RESET_SQL = f"RESET {TENANT_SETTING}"


def set_tenant(session: Session, tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise RuntimeError("写入 RLS 租户上下文需要非空 tenant_id")
    session.execute(text(_SET_TENANT_SQL), {"tenant_id": tenant_id})


@contextmanager
def privileged_session(
    session_factory: Callable[[], Session],
    *,
    statement_timeout_ms: int | None = None,
) -> Iterator[Session]:
    """Worker 与迁移使用的受控连接：不写租户，依靠表所有者身份越过策略。

    只允许用于领取任务、推进租约、发布快照这类"租户尚未或不应由调用方
    决定"的路径。任何由请求主体发起的读写都必须走 :func:`scoped_session`。
    """
    session = session_factory()
    try:
        if statement_timeout_ms is not None:
            session.execute(
                text(
                    f"SET LOCAL statement_timeout = {max(1, int(statement_timeout_ms))}"
                )
            )
        yield session
    finally:
        session.close()


def release_session(session: Session) -> None:
    """回滚未提交的改动、清空租户上下文再关闭，避免连接复用串租户。"""
    try:
        session.rollback()
        session.execute(text(_RESET_SQL))
    except Exception:  # 清理失败不应掩盖业务异常
        logger.warning("清空租户上下文失败，连接将随 session 关闭而废弃", exc_info=True)
    finally:
        session.close()


@contextmanager
def scoped_session(
    session_factory: Callable[[], Session],
    tenant_id: str,
    *,
    statement_timeout_ms: int | None = None,
) -> Iterator[Session]:
    """打开租户受限 session；退出时先回滚再清空租户，避免连接复用串租户。"""
    session = session_factory()
    try:
        if statement_timeout_ms is not None:
            session.execute(
                text(
                    f"SET LOCAL statement_timeout = {max(1, int(statement_timeout_ms))}"
                )
            )
        set_tenant(session, tenant_id)
        yield session
    finally:
        release_session(session)
