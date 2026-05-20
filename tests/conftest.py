"""Shared test helpers."""

from __future__ import annotations

import os

from kai_mcp_empresa.config import Settings

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
DEV_USER = "00000000-0000-0000-0000-0000000000aa"


def make_settings(**over) -> Settings:
    base = dict(
        auth_disabled=True,
        tenant_id=TENANT_A,
        dev_user_id=DEV_USER,
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://kai:kai@localhost:5433/kai_empresa"
        ),
        openai_api_key=None,
        embedding_dim=1536,
    )
    base.update(over)
    return Settings(_env_file=None, **base)
