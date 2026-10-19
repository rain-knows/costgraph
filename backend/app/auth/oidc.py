from __future__ import annotations

from functools import lru_cache

import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError
from pydantic import BaseModel, ConfigDict

from app.settings import get_settings

SIGNING_ALGORITHMS: tuple[str, ...] = ("RS256", "RS384", "RS512", "ES256", "ES384")
REQUIRED_CLAIMS: tuple[str, ...] = ("exp", "iat", "iss", "aud", "sub")


class OIDCError(Exception):
    """OIDC 校验失败的基类；异常消息永远不包含令牌原文。"""


class InvalidTokenError(OIDCError):
    """签名、issuer、audience、有效期或必需 claim 不合法。"""


class OIDCUnavailableError(OIDCError):
    """JWKS 拉取失败，或服务端缺少 OIDC 配置。"""


class TokenClaims(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    tenant_id: str
    roles: tuple[str, ...] = ()


class OIDCVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str,
        tenant_claim: str,
        roles_claim: str,
        leeway_seconds: int,
        cache_seconds: int,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._leeway = leeway_seconds
        self._jwks_client = PyJWKClient(
            jwks_uri, cache_jwk_set=True, lifespan=cache_seconds
        )

    def verify(self, token: str) -> TokenClaims:
        """校验访问令牌并返回受信 claim；失败只抛 ``OIDCError`` 子类。"""
        if not token.strip():
            raise InvalidTokenError("访问令牌为空")
        signing_key = self._signing_key(token)
        payload = self._decode(token, signing_key)
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise InvalidTokenError("访问令牌缺少有效的 sub")
        tenant_id = payload.get(self._tenant_claim)
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise InvalidTokenError("访问令牌缺少有效的租户 claim")
        raw_roles = payload.get(self._roles_claim, [])
        if not isinstance(raw_roles, list) or any(
            not isinstance(role, str) or not role.strip() for role in raw_roles
        ):
            raise InvalidTokenError("访问令牌的 roles claim 必须是字符串列表")
        return TokenClaims(subject=subject, tenant_id=tenant_id, roles=tuple(raw_roles))

    def _signing_key(self, token: str):
        try:
            return self._jwks_client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError as exc:
            raise OIDCUnavailableError("无法获取 OIDC 签名密钥") from exc
        except jwt.PyJWKClientError as exc:
            raise InvalidTokenError("访问令牌的签名密钥不可解析") from exc

    def _decode(self, token: str, signing_key) -> dict[str, object]:
        try:
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=list(SIGNING_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": list(REQUIRED_CLAIMS)},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("访问令牌校验失败") from exc


@lru_cache(maxsize=8)
def _cached_verifier(issuer: str, audience: str, jwks_uri: str) -> OIDCVerifier:
    settings = get_settings()
    return OIDCVerifier(
        issuer=issuer,
        audience=audience,
        jwks_uri=jwks_uri,
        tenant_claim=settings.oidc_tenant_claim,
        roles_claim=settings.oidc_roles_claim,
        leeway_seconds=settings.oidc_leeway_seconds,
        cache_seconds=settings.oidc_jwks_cache_seconds,
    )


def get_oidc_verifier() -> OIDCVerifier:
    settings = get_settings()
    issuer = settings.oidc_issuer.strip()
    audience = settings.oidc_audience.strip()
    jwks_uri = settings.oidc_jwks_uri.strip()
    if not issuer or not audience or not jwks_uri:
        raise OIDCUnavailableError("服务端未配置 OIDC 校验参数")
    return _cached_verifier(issuer, audience, jwks_uri)
