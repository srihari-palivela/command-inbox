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

    # Scripted telephony (calls from a ticket) is a prototype, not in v1: off unless explicitly switched on.
    feature_telephony: bool = False

    # Identity: Keycloak (OIDC). The app keeps its own server-side session after the code exchange.
    oidc_issuer: str | None = None  # e.g. http://localhost:8081/realms/command-inbox
    oidc_client_id: str = "command-inbox"
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid email profile organization"

    # Platform console (operators). Its own origin, cookie and Keycloak realm; no tenant user can reach it.
    console_origin: str = "http://localhost:5174"
    platform_oidc_issuer: str | None = None  # e.g. http://localhost:8081/realms/operators
    platform_oidc_client_id: str = "command-inbox-console"
    platform_oidc_client_secret: str | None = None
    platform_session_idle_minutes: int = 20
    platform_session_ttl_hours: int = 8

    # Keycloak admin API (service account with realm-management rights on the tenant realm). Provisioning
    # creates each tenant's Keycloak Organization with it; unset, that step is recorded as skipped.
    keycloak_admin_url: str | None = None  # e.g. http://localhost:8081
    keycloak_realm: str = "command-inbox"
    keycloak_admin_client_id: str = "command-inbox-provisioner"
    keycloak_admin_client_secret: str | None = None

    # Transactional email (invitations). Unset SMTP_HOST in development: messages are logged, not sent.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    mail_from: str = "Command Inbox <no-reply@localhost>"

    # Key management: tenant data keys are wrapped by a key-encryption key. "local" derives the KEK from
    # ENCRYPTION_KEY (development and single-host installs); cloud KMS adapters plug in behind the same interface.
    kms_provider: Literal["local"] = "local"

    # Mailbox connectors (decision D3: delegated OAuth for one account; the app registrations belong to the bank).
    # Microsoft: a single-tenant app in the bank's Entra (MS_TENANT = its directory ID; "organizations" for dev).
    ms_client_id: str | None = None
    ms_client_secret: str | None = None
    ms_tenant: str = "organizations"
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    # Google: an Internal OAuth client in the bank's Cloud project, plus a Pub/Sub topic and push subscription.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_pubsub_topic: str | None = None  # projects/<project>/topics/<topic>
    google_push_audience: str | None = None  # the audience set on the push subscription
    google_push_service_account: str | None = None  # the service account the push subscription signs as
    # Where providers deliver change notifications (must be public HTTPS). Unset: mailboxes are polled.
    mail_webhook_base_url: str | None = None
    mail_poll_seconds: int = 60
    mail_sweep_minutes: int = 10

    # Knowledge. Embeddings from a self-hosted model (bge-m3 / e5-large behind an OpenAI-compatible
    # /v1/embeddings endpoint) or a provider's; "hash" is lexical and for development only.
    embedding_provider: Literal["hash", "openai_compatible"] = "hash"
    embedding_url: str | None = None
    embedding_model: str = "bge-m3"
    embedding_api_key: str | None = None
    embedding_send_dimensions: bool = False  # OpenAI v3 models: ask for 1024 dimensions
    # Optional cross-encoder reranker (Text Embeddings Inference `/rerank`); unset: fused ranking only.
    rerank_url: str | None = None
    # Below this cosine similarity (and with no full-text match) there is no source: raise a gap instead.
    retrieval_min_similarity: float = 0.2
    # ClamAV (clamd). Required in production: uploads and attachments are scanned before parsing.
    clamav_host: str | None = None
    clamav_port: int = 3310
    knowledge_max_upload_mb: int = 20
    # Per-workspace API requests per minute per API process (a tenant's limits.apiPerMinute overrides it);
    # 0 disables the limit.
    api_rate_per_minute: int = 3000
    invitation_ttl_hours: int = 72

    encryption_key: str = "dev-only-key-change-me-dev-only-key-change-me"
    intake_webhook_secret: str = "dev-intake-secret"  # noqa: S105 - development default, rejected in production

    # Models. Generation (drafts, extraction, summaries, copilot) uses Claude when a key is set; the
    # categorisation decision engine runs an open-weight model we host (llama.cpp or vLLM).
    anthropic_api_key: str | None = None
    # The platform default System 2 provider; deployments may pick another per node (tenant policy allowing).
    # "claude" is the older name for "anthropic".
    llm_provider: Literal["auto", "anthropic", "claude", "openai", "heuristic"] = "auto"
    copilot_model: str = "claude-opus-5"  # the default Anthropic model
    openai_api_key: str | None = None
    openai_base_url: str | None = None  # regional endpoint or an approved proxy
    openai_model: str = "gpt-5"
    # Model price list for budgets: model id → minor units of the tenant currency per 1,000 tokens (JSON).
    model_prices: dict[str, int] = {}
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
    def configured_providers(self) -> list[str]:
        """System 2 providers this installation can call (a key is set)."""
        return [
            p for p, key in (("anthropic", self.anthropic_api_key), ("openai", self.openai_api_key)) if key
        ]

    @property
    def default_provider(self) -> Literal["anthropic", "openai", "heuristic"]:
        if self.llm_provider in ("anthropic", "claude"):
            return "anthropic"
        if self.llm_provider in ("openai", "heuristic"):
            return self.llm_provider
        return "anthropic" if self.anthropic_api_key else "openai" if self.openai_api_key else "heuristic"

    @property
    def use_claude(self) -> bool:
        return self.default_provider == "anthropic"

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
            if not self.smtp_host:
                problems.append("SMTP_HOST must be set (invitations are emailed)")
            if not self.clamav_host:
                problems.append("CLAMAV_HOST must be set (uploads are scanned before parsing)")
            if self.embedding_provider == "hash":
                problems.append("EMBEDDING_PROVIDER must be a real embedding model, not the lexical hash")
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
