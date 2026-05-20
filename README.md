# kai-mcp-empresa

**Kai — Layer 2: per-tenant company MCP server.**

The MCP server that exposes a company's brain (knowledge base + identity + audit)
as MCP tools, scoped hard per tenant. This is the "empresa" layer of Kai's
3-layer architecture (persona filesystem → **empresa MCP** → cross-tenant MCP).

> Architecture source of truth:
> `kairos-vault/proyectos/koi-copilot/installs/koi-ventures/architecture-koi-mcp.md`
> (§ Componente 3 — MCP de empresa; § Decisiones técnicas cerradas; § Lo que SÍ es sagrado).
> This repo implements the **v1 read+audit subset** of that spec.

## Stack

- **FastMCP** (Python) — Streamable HTTP transport (no SSE)
- **Postgres + pgvector** — knowledge base with vector similarity
- **OAuth 2.1 / Clerk** — JWT verification via JWKS, `aud` claim binds the token to this resource
- **uv** — strict lockfile, exact pins
- Embeddings: OpenAI `text-embedding-3-small` for dev; bge-large local in prod (privacy invariant)

## Tools (v1)

Six-Tool Pattern — universal `kai_*` verbs, never per-connector tools.

| Tool | Kind | What |
|---|---|---|
| `kai_search` | read | Vector similarity over the knowledge base (pgvector), tenant + ACL filtered |
| `kai_fetch` | read | Get a single knowledge entry by id |
| `kai_list_collections` | read | List collections (groupings) in the tenant |
| `kai_list_objects` | read | List objects within a collection |
| `who_am_i` | identity | Caller profile (Yamel pattern): role, peers, tools, tone |
| `log_interaction` | audit | Append a tool-call record to the audit log |

Out of v1 (→ Phase 2): mutating tools (`kai_upsert`, `kai_delete`), two-tier
gating (`register_outbound_action` / `confirm_action`), connectors, `get_rules`.

## Quick start

```bash
# 1. DB
docker compose up -d                 # Postgres + pgvector on :5433
atlas migrate apply --env local      # apply schema

# 2. Env
cp .env.example .env                 # fill keys (or set KAI_AUTH_DISABLED=true for local dev)

# 3. Run
uv run kai-mcp-empresa               # Streamable HTTP on http://127.0.0.1:8080/mcp

# 4. Inspect
npx @modelcontextprotocol/inspector http://127.0.0.1:8080/mcp

# 5. Test
uv run pytest
```

## Auth

Fail-closed. The server **refuses to start** unless either:
- `CLERK_JWKS_URI` + `KAI_MCP_AUDIENCE` are set (production path), or
- `KAI_AUTH_DISABLED=true` is set explicitly (local dev only — every request runs as `KAI_DEV_TENANT_ID`).

In production every request carries a Clerk-issued JWT. `JWTVerifier` checks
signature (JWKS), issuer, expiry, and audience. The tenant id, user id, role and
ACL tags are read from token claims and used to scope every query.

**Known limitation (v1):** a valid token for the wrong tenant is rejected at the
tool layer (`ForbiddenError`) rather than mapped to an HTTP 403 status. Access is
blocked either way; surfacing it as a proper 403 needs FastMCP middleware and is a
follow-up.

## Threat Model

This server defends explicitly against the following (per spec § seguridad
supply-chain + tool integrity). Anything not listed is assumed mitigated upstream.

### Tenant escape (cross-tenant data leak)
- **Defense:** every DB query is parameterized by `tenant_id` derived **only**
  from the verified token — never from tool arguments. A tool cannot request
  another tenant's data because the tenant is not an input. ACL tags filter
  further within a tenant.
- **Tested:** `tests/test_tools_scoping.py` asserts a tool call bound to tenant A
  cannot read tenant B's rows.

### Token leakage
- **Defense:** tokens are NEVER logged, returned in tool output, or written to
  the audit log / traces. The audit log stores `user_id` + `tenant_id` (opaque
  UUIDs), never the bearer token. Verification happens at the transport edge.

### Tool poisoning (silent change of a tool's contract)
- **Defense:** CI runs a snapshot test of the tool catalog
  (`tests/test_tool_catalog_snapshot.py` vs `tool_catalog.snapshot.json`). Any
  change to a tool's name, description, or input schema fails CI with a diff for
  human review. MCP packages are pinned to exact versions in `uv.lock`.

### Prompt injection (via knowledge content)
- **Defense:** tools return data, never instructions. Knowledge content is
  returned as structured payloads (typed fields), and the audit log records what
  was accessed. The server performs no autonomous outbound action in v1 — all
  mutating/outbound tools (with their two-tier confirmation gates) are out of
  scope until Phase 2, so there is no injectable action surface yet.

### Auth bypass
- **Defense:** fail-closed startup (see Auth). The dev bypass requires an
  explicit env flag and is documented as dev-only; it is never the default.

## Layout

```
src/kai_mcp_empresa/
  config.py        # env settings (pydantic-settings), fail-closed validation
  db.py            # asyncpg pool + pgvector registration
  identity.py      # TenantContext + extraction from token claims / dev mode
  auth.py          # JWTVerifier (Clerk) wiring
  embeddings.py    # Embedder protocol + OpenAI + deterministic fake (tests)
  server.py        # FastMCP instance + tool registration + run()
  __main__.py      # entrypoint
  tools/read.py    # kai_search, kai_fetch, kai_list_collections, kai_list_objects
  tools/special.py # who_am_i, log_interaction
tests/             # auth, tenant scoping, tool catalog snapshot
```
