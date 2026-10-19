"""OIDC 校验与 IAM 接口的独立测试：签名、iss、aud、过期、角色与范围。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import oidc
from app.auth.context import get_current_principal
from app.auth.errors import PERMISSION_DENIED
from app.auth.oidc import InvalidTokenError, OIDCVerifier
from app.domain.authorization import ExecutionPrincipal
from app.main import create_app
from app.services.iam_service import IamService, get_iam_service

ISSUER = "https://idp.example.com"
AUDIENCE = "costgraph"
TENANT = "tenant-a"
SUBJECT = "user-1"


class _StaticKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class _StaticJwksClient:
    """用本地生成的密钥替代真实 JWKS 拉取，保持校验路径不变。"""

    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, _token: str) -> _StaticKey:
        return _StaticKey(self._key)


@pytest.fixture(scope="module")
def signing_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(signing_key: Any) -> OIDCVerifier:
    instance = OIDCVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_uri="https://idp.example.com/jwks",
        tenant_claim="tenant_id",
        roles_claim="roles",
        leeway_seconds=0,
        cache_seconds=600,
    )
    instance._jwks_client = _StaticJwksClient(  # type: ignore[assignment]
        signing_key.public_key()
    )
    return instance


def _token(signing_key: Any, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": SUBJECT,
        "tenant_id": TENANT,
        "roles": ["viewer"],
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256")


def test_valid_token_yields_trusted_claims(verifier, signing_key) -> None:
    claims = verifier.verify(_token(signing_key))
    assert claims.subject == SUBJECT
    assert claims.tenant_id == TENANT
    assert claims.roles == ("viewer",)


@pytest.mark.parametrize(
    "override",
    [
        {"iss": "https://attacker.example.com"},
        {"aud": "another-service"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"sub": ""},
        {"tenant_id": ""},
        {"roles": "viewer"},
    ],
)
def test_invalid_claims_are_rejected(verifier, signing_key, override) -> None:
    with pytest.raises(InvalidTokenError):
        verifier.verify(_token(signing_key, **override))


def test_token_signed_by_another_key_is_rejected(verifier) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(InvalidTokenError):
        verifier.verify(_token(other))


def test_missing_token_is_rejected(verifier) -> None:
    with pytest.raises(InvalidTokenError):
        verifier.verify("   ")


class FakeIamRepository:
    def __init__(self) -> None:
        self.memberships: dict[tuple[str, str], dict[str, Any]] = {}
        self.scopes: dict[tuple[str, str], dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []

    def get_membership(self, tenant_id: str, principal_id: str):
        row = self.memberships.get((tenant_id, principal_id))
        if row is None:
            return None
        return {**row, "data_scope": self.scopes.get((tenant_id, principal_id))}

    def list_memberships(self, tenant_id: str, *, limit: int, offset: int):
        rows = [
            {**row, "data_scope": self.scopes.get(key)}
            for key, row in sorted(self.memberships.items())
            if key[0] == tenant_id
        ]
        return rows[offset : offset + limit], len(rows)

    def set_roles(self, tenant_id, principal_id, roles, *, updated_by) -> None:
        self.memberships[(tenant_id, principal_id)] = {
            "principal_id": principal_id,
            "roles": list(roles),
            "updated_by": updated_by,
            "updated_at": datetime.now(UTC),
        }

    def get_data_scope(self, tenant_id: str, principal_id: str):
        return self.scopes.get((tenant_id, principal_id))

    def set_data_scope(
        self,
        tenant_id,
        principal_id,
        *,
        part_ids,
        period_start,
        period_end,
        updated_by,
    ) -> None:
        self.scopes[(tenant_id, principal_id)] = {
            "allowed_part_ids": list(part_ids),
            "allowed_period_start": period_start,
            "allowed_period_end": period_end,
        }

    def list_audit_events(
        self, tenant_id, *, limit, offset, subject_id=None, action=None
    ):
        rows = [event for event in self.audit_events if event["tenant_id"] == tenant_id]
        return rows[offset : offset + limit], len(rows)

    def append_audit_event(self, **kwargs) -> None:
        self.audit_events.append(kwargs)


def _principal(role: str, *, principal_id: str = "admin-1") -> ExecutionPrincipal:
    return ExecutionPrincipal(
        principal_id=principal_id, tenant_id=TENANT, roles=(role,)
    )


@pytest.fixture
def iam_api():
    repository = FakeIamRepository()
    repository.set_roles(TENANT, "target-1", ["viewer"], updated_by="bootstrap")
    app = create_app()
    app.dependency_overrides[get_iam_service] = lambda: IamService(repository)
    app.dependency_overrides[get_current_principal] = lambda: _principal("tenant_admin")
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, repository, app
    finally:
        app.dependency_overrides.clear()


def test_tenant_admin_lists_principals_with_scope(iam_api) -> None:
    client, _, _ = iam_api
    response = client.get("/api/iam/principals")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["principal_id"] == "target-1"
    assert body["items"][0]["data_scope"]["allowed_part_ids"] == []


@pytest.mark.parametrize("role", ["viewer", "data_operator", "data_approver"])
def test_non_admin_roles_cannot_manage_iam(iam_api, role: str) -> None:
    client, _, app = iam_api
    app.dependency_overrides[get_current_principal] = lambda: _principal(role)
    response = client.get("/api/iam/principals")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == PERMISSION_DENIED


def test_roles_and_scope_updates_are_audited(iam_api) -> None:
    client, repository, _ = iam_api
    updated = client.put(
        "/api/iam/principals/target-1/roles", json={"roles": ["data_approver"]}
    )
    assert updated.status_code == 200
    assert updated.json()["roles"] == ["data_approver"]

    scoped = client.put(
        "/api/iam/principals/target-1/scope",
        json={
            "allowed_part_ids": ["FG-001", "FG-001"],
            "allowed_period_start": "2026-01",
            "allowed_period_end": "2026-06",
        },
    )
    assert scoped.status_code == 200
    assert scoped.json()["data_scope"]["allowed_part_ids"] == ["FG-001"]

    actions = [event["action"] for event in repository.audit_events]
    assert actions == ["iam.roles_updated", "iam.scope_updated"]
    assert repository.audit_events[1]["detail"]["allowed_period_end"] == "2026-06"


def test_scope_update_for_unknown_principal_is_404(iam_api) -> None:
    client, _, _ = iam_api
    response = client.put(
        "/api/iam/principals/ghost/scope", json={"allowed_part_ids": ["FG-001"]}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "iam_principal_not_found"


def test_invalid_period_range_is_422(iam_api) -> None:
    client, _, _ = iam_api
    response = client.put(
        "/api/iam/principals/target-1/scope",
        json={
            "allowed_period_start": "2026-06",
            "allowed_period_end": "2026-01",
        },
    )
    assert response.status_code == 422


def test_audit_endpoint_requires_audit_read(iam_api) -> None:
    client, _, app = iam_api
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_operator"
    )
    denied = client.get("/api/audit/events")
    assert denied.status_code == 403

    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "data_approver"
    )
    allowed = client.get("/api/audit/events")
    assert allowed.status_code == 200
    assert allowed.json()["items"] == []


def test_missing_bearer_token_returns_401_with_challenge() -> None:
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "missing_bearer_token"


def test_invalid_bearer_token_returns_401(monkeypatch) -> None:
    def raise_invalid(_token: str):
        raise InvalidTokenError("访问令牌校验失败")

    monkeypatch.setattr(oidc, "get_oidc_verifier", lambda: _RejectingVerifier())
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_token"


class _RejectingVerifier:
    def verify(self, _token: str):
        raise InvalidTokenError("访问令牌校验失败")
