"""Caller context derived ONLY from the verified token (or dev mode).

The tenant is never an input to a tool — that is the core defense against
cross-tenant access. A caller cannot ask for another tenant's data because the
tenant is read from the token, not from arguments. The tenant maps to one folder
under KAI_DATA_ROOT; every file path a tool touches is resolved inside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fastmcp.exceptions import ToolError

from .config import Settings

# A tenant slug becomes a directory name, so it must be filesystem-safe and can
# never contain separators or traversal. Defense in depth alongside path resolution.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ForbiddenError(ToolError):
    """Authenticated but not authorized for this resource (HTTP 403 semantics)."""


@dataclass(frozen=True)
class CallerContext:
    tenant: str
    user: str
    role: str | None = None

    @property
    def client_id(self) -> str:
        return f"{self.tenant}:{self.user}"


def _validate_slug(tenant: str) -> str:
    if not _SLUG_RE.match(tenant):
        raise ForbiddenError(f"invalid tenant slug: {tenant!r}")
    return tenant


def context_from_claims(claims: dict) -> CallerContext:
    """Pure mapping from verified token claims to a CallerContext. Testable alone."""
    tenant = claims.get("tenant")
    if not tenant:
        raise ForbiddenError("token is missing the tenant claim")
    return CallerContext(
        tenant=_validate_slug(str(tenant)),
        user=str(claims.get("user", "unknown")),
        role=claims.get("role"),
    )


def current_context(settings: Settings) -> CallerContext:
    """Resolve the caller's CallerContext for the active request."""
    if settings.auth_disabled:
        return CallerContext(
            tenant=_validate_slug(settings.dev_tenant),
            user=settings.dev_user,
            role="dev",
        )
    # Imported lazily so unit tests of context_from_claims need no request context.
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    return context_from_claims(token.claims or {})


def tenant_root(settings: Settings, ctx: CallerContext) -> Path:
    """The on-disk folder that IS this tenant's company brain. Created on demand."""
    root = (settings.data_root / ctx.tenant).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root
