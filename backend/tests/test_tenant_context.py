"""租户上下文的清理契约。

``release_session()`` 失败时必须作废物理连接：租户用会话级
``set_config(..., false)`` 写入，``rollback()`` 不会清除它，若把仍带旧租户的
连接还回连接池，下一个请求就会串租户。这些用例用假 session 固定该行为，
不需要真实 PostgreSQL。

同时固定调用顺序：必须先 ``expunge_all()`` 再 ``rollback()``。``rollback()``
会把身份映射里的实例全部标记为 expired，而仓储习惯在会话关闭后把 ORM 行
映射成 dict，后置的属性访问会触发刷新并抛 ``DetachedInstanceError``。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.db.tenant import release_session


class _FakeSession:
    def __init__(self, *, fail_on_reset: bool) -> None:
        self.fail_on_reset = fail_on_reset
        self.rolled_back = False
        self.expunged = False
        self.invalidated = False
        self.closed = False
        self.executed: list[str] = []
        self.calls: list[str] = []

    def expunge_all(self) -> None:
        self.expunged = True
        self.calls.append("expunge_all")

    def rollback(self) -> None:
        self.rolled_back = True
        self.calls.append("rollback")

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> None:
        sql = str(getattr(statement, "text", statement))
        self.executed.append(sql)
        if self.fail_on_reset and "RESET" in sql.upper():
            raise OperationalError("RESET app.tenant_id", {}, Exception("boom"))

    def invalidate(self) -> None:
        self.invalidated = True

    def close(self) -> None:
        self.closed = True


def test_release_session_resets_tenant_and_closes() -> None:
    session = _FakeSession(fail_on_reset=False)

    release_session(session)  # type: ignore[arg-type]

    assert session.rolled_back is True
    assert session.expunged is True
    assert any("RESET" in sql.upper() for sql in session.executed)
    assert session.invalidated is False
    assert session.closed is True


def test_release_session_invalidates_connection_when_reset_fails() -> None:
    """RESET 失败时不能把可能仍带旧租户的连接还给池子。"""
    session = _FakeSession(fail_on_reset=True)

    release_session(session)  # type: ignore[arg-type]

    assert session.rolled_back is True
    assert session.expunged is True
    assert session.invalidated is True
    assert session.closed is True


def test_release_session_expunges_before_rollback() -> None:
    """顺序不能调换：rollback 先执行会让仓储读到的 ORM 行变成 detached。"""
    session = _FakeSession(fail_on_reset=False)

    release_session(session)  # type: ignore[arg-type]

    assert session.calls[:2] == ["expunge_all", "rollback"]


def test_release_session_never_raises_on_cleanup_failure() -> None:
    """清理失败不得掩盖调用方正在抛出的业务异常。"""
    session = _FakeSession(fail_on_reset=True)

    release_session(session)  # type: ignore[arg-type]

    assert session.closed is True


@pytest.mark.parametrize("tenant", ["", "   ", None])
def test_set_tenant_rejects_blank_values(tenant: Any) -> None:
    from app.db.tenant import set_tenant

    with pytest.raises(RuntimeError):
        set_tenant(_FakeSession(fail_on_reset=False), tenant)  # type: ignore[arg-type]
