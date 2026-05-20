"""Identity + audit tools. who_am_i implements the Yamel pattern; log_interaction
is the day-1 audit hook (bearer tokens are never stored)."""

from __future__ import annotations

import json

from fastmcp import FastMCP

from .. import db
from ..config import Settings
from ..identity import current_context


def register_special_tools(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool
    async def who_am_i() -> dict:
        """Return the caller's identity profile (role, peers, tools, tone).

        Lets the agent resolve "my lead", "legal", "the standard metrics" without
        the user repeating context. Reads the latest who-am-I snapshot for the caller.
        """
        ctx = current_context(settings)
        pool = await db.get_pool(settings)
        row = await pool.fetchrow(
            """
            SELECT profile
            FROM who_am_i_snapshots
            WHERE tenant_id = $1::uuid AND user_id = $2
            ORDER BY version DESC
            LIMIT 1
            """,
            ctx.tenant_id,
            ctx.user_id,
        )
        if row and row["profile"]:
            prof = row["profile"]
            return json.loads(prof) if isinstance(prof, str) else dict(prof)
        return {
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
            "role": ctx.role,
            "note": "no profile snapshot yet",
        }

    @mcp.tool
    async def log_interaction(
        tool: str, outcome: str = "ok", cost_tokens: int = 0, latency_ms: int = 0
    ) -> dict:
        """Append a tool-call record to the tenant audit log.

        Stores only opaque ids + metrics — never bearer tokens or prompt content.
        """
        ctx = current_context(settings)
        pool = await db.get_pool(settings)
        rec_id = await pool.fetchval(
            """
            INSERT INTO interactions (tenant_id, user_id, tool, outcome, cost_tokens, latency_ms)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            ctx.tenant_id,
            ctx.user_id,
            tool,
            outcome,
            cost_tokens,
            latency_ms,
        )
        return {"logged": True, "id": str(rec_id)}
