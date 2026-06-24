"""Structured tool-call logging.

One JSON line per tool call to stdout (Railway captures stdout): which tenant /
user called which tool, ok/error, latency. Implemented as a FastMCP middleware so
tool signatures — and therefore their input schemas — are never touched. Tokens
and file CONTENT are never logged; the path/query argument is logged truncated
(a path is not a secret, content is).
"""

from __future__ import annotations

import json
import logging
import time

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from .config import Settings
from .identity import current_context

logger = logging.getLogger("kai.toolcall")

# Argument keys whose VALUE may be logged (truncated). Everything else (content,
# old_string, new_string, ...) is omitted so file data never reaches the logs.
_LOGGABLE_ARGS = ("path", "folder", "query")
_ARG_MAX = 200


def _safe_arg(arguments: dict | None) -> str:
    if not arguments:
        return ""
    for key in _LOGGABLE_ARGS:
        val = arguments.get(key)
        if isinstance(val, str) and val:
            return val[:_ARG_MAX]
    return ""


def log_call(
    *, tenant: str | None, user: str | None, tool: str, ok: bool, ms: int,
    err: str | None = None, arg: str = "",
) -> None:
    payload = {
        "event": "tool_call",
        "tenant": tenant,
        "user": user,
        "tool": tool,
        "ok": ok,
        "ms": ms,
    }
    if arg:
        payload["arg"] = arg
    if err:
        payload["err"] = err
    logger.info(json.dumps(payload, ensure_ascii=False))


class ToolCallLogger(Middleware):
    """Emit one structured log line per tool call."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        start = time.perf_counter()
        ok = True
        err: str | None = None
        try:
            return await call_next(context)
        except Exception as exc:  # noqa: BLE001 — re-raised after logging
            ok = False
            err = type(exc).__name__
            raise
        finally:
            ms = int((time.perf_counter() - start) * 1000)
            tenant = user = None
            try:
                ctx = current_context(self.settings)
                tenant, user = ctx.tenant, ctx.user
            except Exception:  # noqa: BLE001 — identity is best-effort for logs
                pass
            msg = context.message
            log_call(
                tenant=tenant,
                user=user,
                tool=getattr(msg, "name", "?"),
                ok=ok,
                ms=ms,
                err=err,
                arg=_safe_arg(getattr(msg, "arguments", None)),
            )
