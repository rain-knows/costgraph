from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["demo", "test", "staging", "production"] = "demo"
    app_version: str = "dev"
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    cost_database_url: str | None = None
    cost_database_pool_size: int = Field(default=5, ge=1)
    cost_database_max_overflow: int = Field(default=10, ge=0)

    agent_principal_id: str = "local-costgraph-user"
    agent_tenant_id: str = "local-costgraph-tenant"
    agent_principal_roles: str = "cost_analyst"
    agent_server_allowed_capabilities: str = (
        "system_help,cost_calculation,report_generation"
    )
    agent_allowed_part_ids: str = ""
    agent_allowed_period_start: str | None = None
    agent_allowed_period_end: str | None = None

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_thinking: str = "disabled"
    deepseek_timeout_seconds: float = Field(default=30, gt=0)

    agent_run_timeout_seconds: int = Field(default=180, ge=1)
    agent_max_model_calls: int = Field(default=3, ge=0)
    agent_max_tool_calls: int = Field(default=12, ge=0)
    agent_tool_timeout_seconds: float = Field(default=15, gt=0)
    agent_run_lease_seconds: int = Field(default=90, ge=15)
    agent_run_heartbeat_seconds: int = Field(default=15, ge=1)
    agent_run_max_attempts: int = Field(default=3, ge=1, le=10)
    agent_worker_concurrency: int = Field(default=1, ge=1, le=32)
    agent_checkpoint_retention_hours: int = Field(default=24, ge=1)
    audit_trace_mode: Literal["metadata", "full"] = "metadata"
    audit_trace_retention_days: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def validate_environment_contract(self) -> AppSettings:
        if self.agent_run_heartbeat_seconds >= self.agent_run_lease_seconds:
            raise ValueError("AGENT_RUN_HEARTBEAT_SECONDS 必须小于租约时长")
        if self.app_env in {"staging", "production"}:
            errors: list[str] = []
            if not self.resolved_database_url:
                errors.append("必须配置 COST_DATABASE_URL")
            if not self.app_version.strip() or self.app_version.strip() == "dev":
                errors.append("APP_VERSION 必须是非 dev 的发布版本")
            if self.audit_trace_mode != "metadata":
                errors.append("AUDIT_TRACE_MODE 必须为 metadata")
            origins = self.cors_origins
            if (
                "cors_allowed_origins" not in self.model_fields_set
                or not origins
                or "*" in origins
            ):
                errors.append("CORS_ALLOWED_ORIGINS 必须显式配置且不能包含 *")
            if errors:
                raise ValueError("；".join(errors))
        return self

    @property
    def resolved_database_url(self) -> str | None:
        value = (self.cost_database_url or "").strip()
        if not value:
            return None
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("COST_DATABASE_URL 必须使用 PostgreSQL psycopg 驱动")
        return value

    @property
    def psycopg_database_url(self) -> str:
        value = self.resolved_database_url
        if not value:
            raise RuntimeError("未配置 COST_DATABASE_URL，无法使用 PostgreSQL。")
        return value.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def cors_origins(self) -> list[str]:
        return [
            item.strip()
            for item in self.cors_allowed_origins.split(",")
            if item.strip()
        ]

    def csv_values(self, field_name: str) -> list[str]:
        value = str(getattr(self, field_name))
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


def reset_settings() -> None:
    get_settings.cache_clear()
