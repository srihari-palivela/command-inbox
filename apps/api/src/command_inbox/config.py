"""Process configuration, read once from the environment (and `.env` in development).

Production refuses to start with development secrets or insecure cookies: a misconfigured deploy fails
fast instead of running unsafely.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["development", "test", "production"] = Field("production", alias="APP_ENV")
    host: str = "0.0.0.0"  # noqa: S104 - containers bind all interfaces; the ingress decides exposure
    port: int = 4000
    # Only these peers may set X-Forwarded-For/Proto (the reverse proxy). Never "*" in production.
    forwarded_allow_ips: str = "127.0.0.1"

    # Runtime connection: a non-owner role, so row-level security applies.
    database_url: str = "postgresql+asyncpg://ci_app:ci_app@localhost:5432/command_inbox"
    # Migration / seed connection: the schema owner.
    database_admin_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/command_inbox"
    db_pool_size: int = 10

    web_origin: str = "http://localhost:5173"
    public_api_url: str = "http://localhost:4000"
    cookie_secure: bool = False
    session_ttl_hours: int = 12  # absolute lifetime: a session can never outlive this
    session_idle_minutes: int = 30  # and it ends after this long without use

    # Demo mode: passwordless sign-in as a seeded person and the "view as" switch. Never in production.
    demo_mode: bool = False

    # Identity: Keycloak (OIDC). The app keeps its own server-side session after the code exchange.
    oidc_issuer: str | None = None  # e.g. http://localhost:8081/realms/command-inbox
    oidc_client_id: str = "command-inbox"
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid email profile organization"

    encryption_key: str = "dev-only-key-change-me-dev-only-key-change-me"
    intake_webhook_secret: str = "dev-intake-secret"  # noqa: S105 - development default, rejected in production

    # Models. Generation (drafts, extraction, summaries, copilot) uses Claude when a key is set; the
    # categorisation decision engine runs an open-weight model we host (llama.cpp or vLLM).
    anthropic_api_key: str | None = None
    llm_provider: Literal["auto", "claude", "heuristic"] = "auto"
    copilot_model: str = "claude-opus-5"
    decision_engine: Literal["auto", "heuristic", "llamacpp", "vllm"] = "auto"
    decision_engine_url: str | None = None  # e.g. http://localhost:8090 (llama.cpp) or http://vllm:8000
    decision_model: str = "qwen3-4b-instruct"

    # Observability
    log_level: str = "INFO"
    log_json: bool = True
    otel_exporter_otlp_endpoint: str | None = None  # e.g. http://otel-collector:4318
    otel_service_name: str = "command-inbox-api"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    embedded_worker: bool = True

    @property
    def is_prod(self) -> bool:
        return self.env == "production"

    @property
    def use_claude(self) -> bool:
        return self.llm_provider == "claude" or (self.llm_provider == "auto" and bool(self.anthropic_api_key))

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer)

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.env == "production":
            problems = []
            if self.encryption_key.startswith("dev-only"):
                problems.append("ENCRYPTION_KEY must be set")
            if self.intake_webhook_secret == "dev-intake-secret":  # noqa: S105
                problems.append("INTAKE_WEBHOOK_SECRET must be set")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true")
            if self.demo_mode:
                problems.append("DEMO_MODE must be false")
            if not self.oidc_enabled:
                problems.append("OIDC_ISSUER must be set")
            if not self.oidc_client_secret:
                problems.append("OIDC_CLIENT_SECRET must be set")
            if self.decision_engine in ("auto", "heuristic"):
                problems.append("DECISION_ENGINE must be llamacpp or vllm (no silent keyword fallback)")
            if self.llm_provider == "auto":
                problems.append("LLM_PROVIDER must be set explicitly")
            if self.forwarded_allow_ips.strip() == "*":
                problems.append("FORWARDED_ALLOW_IPS must list the proxy addresses, not *")
            if "postgres:postgres@" in self.database_admin_url or "ci_app:ci_app@" in self.database_url:
                problems.append("database credentials must not be the development defaults")
            if problems:
                raise ValueError("unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
