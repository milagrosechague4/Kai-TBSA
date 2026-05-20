"""Tenant context derived ONLY from the verified token (or dev mode).

The tenant is never an input to a tool — that is the core defense against
cross-tenant access. A caller cannot ask for another tenant's data because the
tenant is read from the token, not from arguments.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastmcp.exceptions import ToolError

from .config import Settings

_TENANT_NS = uuid.NAMESPACE_URL


def tenant_id_for(slug: str) -> str:
    """Deterministic tenant_id from a company slug (uuid5). Stable across runs."""
    return str(uuid.uuid5(_TENANT_NS, "kai-tenant:" + slug))


class ForbiddenError(ToolError):
    """Authenticated but not authorized for this tenant (HTTP 403 semantics)."""


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str
    role: str | None = None
    acl_tags: tuple[str, ...] = ()


def context_from_claims(settings: Settings, claims: dict) -> TenantContext:
    """Pure mapping from verified token claims to a TenantContext. Testable in isolation."""
    token_tenant = claims.get(settings.claim_tenant)
    if not token_tenant:
        raise ForbiddenError("token is missing the tenant claim")
    if str(token_tenant) != str(settings.tenant_id):
        raise ForbiddenError("token tenant does not match this instance")

    sub = claims.get("sub")
    if not sub:
        raise ForbiddenError("token is missing the subject (sub) claim")

    acl = claims.get(settings.claim_acl) or []
    if isinstance(acl, str):
        acl = [acl]

    return TenantContext(
        tenant_id=str(settings.tenant_id),
        user_id=str(sub),
        role=claims.get(settings.claim_role),
        acl_tags=tuple(str(t) for t in acl),
    )


def current_context(settings: Settings) -> TenantContext:
    """Resolve the caller's TenantContext for the active request."""
    if settings.auth_disabled:
        return TenantContext(
            tenant_id=str(settings.tenant_id),
            user_id=settings.dev_user_id,
            role="dev",
        )
    # Imported lazily so unit tests of context_from_claims need no request context.
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    return context_from_claims(settings, token.claims or {})
