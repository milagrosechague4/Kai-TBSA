"""Auth wiring: Clerk-issued JWTs verified via JWKS. Fail-closed."""

from __future__ import annotations

from .config import Settings


def build_auth(settings: Settings):
    """Return a JWTVerifier for Clerk, or None when auth is disabled (dev only)."""
    if settings.auth_disabled:
        return None

    from fastmcp.server.auth.providers.jwt import JWTVerifier

    return JWTVerifier(
        jwks_uri=settings.clerk_jwks_uri,
        issuer=settings.clerk_issuer,
        audience=settings.audience,
    )
