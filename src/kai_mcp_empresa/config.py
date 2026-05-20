"""Runtime settings. Fail-closed on auth by design."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # Server
    host: str = Field("127.0.0.1", alias="KAI_MCP_HOST")
    port: int = Field(8080, alias="KAI_MCP_PORT")
    path: str = Field("/mcp", alias="KAI_MCP_PATH")

    # Database
    database_url: str = Field(
        "postgresql://kai:kai@localhost:5433/kai_empresa", alias="DATABASE_URL"
    )

    # Tenant this instance serves (token tenant claim must match → else 403)
    tenant_id: str = Field(
        "00000000-0000-0000-0000-000000000001", alias="KAI_TENANT_ID"
    )

    # Auth (OAuth 2.1 / Clerk)
    clerk_jwks_uri: str | None = Field(None, alias="CLERK_JWKS_URI")
    clerk_issuer: str | None = Field(None, alias="CLERK_ISSUER")
    audience: str = Field("kai-mcp-empresa", alias="KAI_MCP_AUDIENCE")
    claim_tenant: str = Field("org_id", alias="KAI_CLAIM_TENANT")
    claim_role: str = Field("role", alias="KAI_CLAIM_ROLE")
    claim_acl: str = Field("acl_tags", alias="KAI_CLAIM_ACL")

    # Dev escape hatch (never true in prod)
    auth_disabled: bool = Field(False, alias="KAI_AUTH_DISABLED")
    dev_user_id: str = Field(
        "00000000-0000-0000-0000-0000000000aa", alias="KAI_DEV_USER_ID"
    )

    # Embeddings
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    embedding_model: str = Field("text-embedding-3-small", alias="KAI_EMBEDDING_MODEL")
    embedding_dim: int = Field(1536, alias="KAI_EMBEDDING_DIM")

    def validate_fail_closed(self) -> None:
        """Refuse to start without auth unless explicitly disabled for local dev."""
        if self.auth_disabled:
            return
        if not (self.clerk_jwks_uri and self.audience):
            raise SystemExit(
                "FATAL: auth not configured. Set CLERK_JWKS_URI + KAI_MCP_AUDIENCE, "
                "or set KAI_AUTH_DISABLED=true for local dev only."
            )


def get_settings() -> Settings:
    return Settings()
