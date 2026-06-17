"""Auth wiring: static bearer tokens. Fail-closed.

The whitelist comes from one of two sources (env wins): KAI_TOKENS_JSON (inline
JSON, used on cloud) or KAI_TOKENS_FILE (a file on disk, local dev). Each entry
carries the caller's tenant + identity as claims. FastMCP's StaticTokenVerifier
checks the bearer at the transport edge; the claims become AccessToken.claims,
read back in identity.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings


def shape_tokens(raw: dict) -> dict[str, dict]:
    """Turn the human-editable whitelist into the StaticTokenVerifier shape.

    Input entry:  { "tenant": "koi", "user": "mili", "role": "cfo" }
    Each entry is enriched with client_id ("tenant:user" — never the token) and
    a scope. The token string stays the dict key; it is never copied into values.
    """
    tokens: dict[str, dict] = {}
    for token, meta in raw.items():
        tenant = meta.get("tenant")
        user = meta.get("user", "unknown")
        if not tenant:
            raise SystemExit(
                f"FATAL: tokens entry for user {user!r} is missing 'tenant'."
            )
        tokens[token] = {
            "client_id": f"{tenant}:{user}",
            "scopes": meta.get("scopes", ["kai"]),
            "tenant": tenant,
            "user": user,
            "role": meta.get("role"),
        }
    return tokens


def load_tokens(tokens_file: Path) -> dict[str, dict]:
    """Load + shape the whitelist from a JSON file (used by the install script)."""
    return shape_tokens(json.loads(tokens_file.read_text(encoding="utf-8")))


def _tokens_for(settings: Settings) -> dict[str, dict]:
    if settings.tokens_json:
        return shape_tokens(json.loads(settings.tokens_json))
    return load_tokens(settings.tokens_file)


def build_auth(settings: Settings):
    """Return a StaticTokenVerifier, or None when auth is disabled (dev only)."""
    if settings.auth_disabled:
        return None

    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier(tokens=_tokens_for(settings))
