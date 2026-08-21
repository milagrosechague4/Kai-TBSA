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


def resolve_caller(settings: Settings, request):
    """Verify the bearer token from an HTTP request and return caller context.

    Used by REST endpoints which bypass FastMCP's auth middleware.
    Returns (CallerContext, tenant_root_path, None) on success, or
    (None, None, JSONResponse) on auth failure.

    Also works with the TokenInPathMiddleware — if the middleware already moved
    the token from the URL path into the Authorization header, this reads it there.
    """
    from starlette.responses import JSONResponse

    from .identity import ForbiddenError, CallerContext, context_from_claims, tenant_root

    if settings.auth_disabled:
        ctx = CallerContext(
            tenant=settings.dev_tenant,
            user=settings.dev_user,
            role="dev",
        )
        root = (settings.data_root / ctx.tenant).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return ctx, root, None

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None, None, JSONResponse({"error": "missing bearer token"}, status_code=401)

    token = auth_header[7:].strip()
    tokens = _tokens_for(settings)

    if token not in tokens:
        return None, None, JSONResponse({"error": "invalid token"}, status_code=401)

    try:
        ctx = context_from_claims(tokens[token])
    except ForbiddenError as exc:
        return None, None, JSONResponse({"error": str(exc)}, status_code=403)

    root = tenant_root(settings, ctx)
    return ctx, root, None
