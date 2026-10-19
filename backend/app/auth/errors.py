from __future__ import annotations

from fastapi import HTTPException

MISSING_BEARER_TOKEN = "missing_bearer_token"
INVALID_TOKEN = "invalid_token"
PRINCIPAL_NOT_IN_TENANT = "principal_not_in_tenant"
PRINCIPAL_HAS_NO_ROLE = "principal_has_no_role"
PRINCIPAL_NOT_FOUND = "principal_not_found"
PERMISSION_DENIED = "permission_denied"

_AUTH_MESSAGES: dict[str, str] = {
    MISSING_BEARER_TOKEN: "请求必须携带 Bearer 访问令牌。",
    INVALID_TOKEN: "访问令牌无效或已过期。",
    PRINCIPAL_NOT_IN_TENANT: "当前主体不是该租户的成员。",
    PRINCIPAL_HAS_NO_ROLE: "当前主体未获分配任何角色。",
    PRINCIPAL_NOT_FOUND: "该租户下不存在指定的主体。",
    PERMISSION_DENIED: "当前主体无权执行该操作。",
}


def auth_http_error(status_code: int, code: str) -> HTTPException:
    """构造统一错误体形状的鉴权异常；401 一律带上 Bearer 挑战头。"""
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": _AUTH_MESSAGES[code], "details": {}},
        headers=headers,
    )
