"""Runtime settings. Fail-closed on auth by design.

The company brain is a folder of markdown files on disk. A bearer token maps a
caller to a tenant (a subfolder under KAI_DATA_ROOT) plus an identity. The token
file is the whitelist: who has a token is who gets in. Auth runs at the transport
edge; a tenant is NEVER an argument to a tool.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

HISTORY_DEFAULT_LIMIT = 20


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=True,
    )

    # Server
    host: str = Field("127.0.0.1", alias="KAI_MCP_HOST")
    port: int = Field(8080, alias="KAI_MCP_PORT")
    path: str = Field("/mcp", alias="KAI_MCP_PATH")

    # Data root: the parent dir holding one folder per tenant (the company brain).
    data_root: Path = Field(Path("./data"), alias="KAI_DATA_ROOT")

    # Auth — bearer tokens. JSON map:
    #   { "<token>": { "tenant": "koi", "user": "mili", "role": "cfo" }, ... }
    # Whitelist == the set of tokens present. Revoke == delete an entry.
    tokens_json: str | None = Field(None, alias="KAI_TOKENS_JSON")
    tokens_file: Path = Field(Path("./tokens.json"), alias="KAI_TOKENS_FILE")

    # Dev escape hatch (never reachable off loopback — see validate_fail_closed).
    auth_disabled: bool = Field(False, alias="KAI_AUTH_DISABLED")
    dev_tenant: str = Field("dev", alias="KAI_DEV_TENANT")
    dev_user: str = Field("dev", alias="KAI_DEV_USER")

    # Write guardrails.
    max_file_bytes: int = Field(1_000_000, alias="KAI_MAX_FILE_BYTES")
    allowed_suffixes: tuple[str, ...] = (
        ".md",
        ".markdown",
        ".txt",
        ".json",
        ".csv",
        ".yaml",
        ".yml",
    )

    # History / audit (per-tenant git repo). Off → mutating tools skip git.
    git_enabled: bool = Field(True, alias="KAI_GIT_ENABLED")
    git_timeout_s: float = Field(10.0, alias="KAI_GIT_TIMEOUT_S")

    # Google Drive integration (optional).
    google_sa_json: str | None = Field(None, alias="KAI_GOOGLE_SA_JSON")

    # Airtable integration (optional). Set to a Personal Access Token with
    # data.records:read scope on base appA00Nc1qVXa1lar.
    airtable_token: str | None = Field(None, alias="KAI_AIRTABLE_TOKEN")

    # Google Calendar OAuth (per-user). Create an OAuth 2.0 Client ID in Google
    # Cloud Console (type: Web application). Add the callback URL below as an
    # authorized redirect URI. Set both env vars on Railway to enable kai_calendar.
    google_oauth_client_id: str | None = Field(None, alias="KAI_GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str | None = Field(None, alias="KAI_GOOGLE_OAUTH_CLIENT_SECRET")
    # Public base URL of this server — used to build the OAuth redirect URI.
    # Example: https://stunning-spontaneity-production-4cbd.up.railway.app
    oauth_base_url: str = Field("http://localhost:8080", alias="KAI_OAUTH_BASE_URL")
    # If set, only Google accounts with this domain can connect their calendar.
    # Example: tbsa.ar — rejects gmail.com or any other domain at the callback.
    oauth_allowed_domain: str | None = Field(None, alias="KAI_OAUTH_ALLOWED_DOMAIN")

    # Observability: one JSON line per tool call to stdout (Railway captures it).
    log_toolcalls: bool = Field(True, alias="KAI_LOG_TOOLCALLS")

    def validate_fail_closed(self) -> None:
        """Refuse to start in an unsafe configuration."""
        if self.auth_disabled:
            if self.host not in ("127.0.0.1", "localhost", "::1"):
                raise SystemExit(
                    f"FATAL: KAI_AUTH_DISABLED=true is dev-only and refuses to bind to a "
                    f"non-loopback host ({self.host!r}). Provide a tokens file for any "
                    f"exposed deployment."
                )
            return
        if self.tokens_json:
            return
        if not self.tokens_file.exists():
            raise SystemExit(
                f"FATAL: auth not configured. Set KAI_TOKENS_JSON, or point "
                f"KAI_TOKENS_FILE at an existing file (current: {self.tokens_file}); "
                f"see .env.example / scripts/install_tenant.py. Or set "
                f"KAI_AUTH_DISABLED=true for local dev only."
            )


def get_settings() -> Settings:
    return Settings()
