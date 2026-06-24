# kai-mcp-empresa

**Kai — Layer 2: per-tenant company MCP server (files-over-app).**

The MCP server that exposes a company's brain — a folder of **markdown files** — as
read/write tools, scoped hard per tenant. Each person on the team connects it from
their AI of choice (Claude / ChatGPT) and reads & writes the company's context,
to-dos, meeting notes and transcripts **in natural language**. No database, no
vendor lock-in: the brain is just files on disk (a local folder, a Drive-synced
folder, or a persistent volume).

> This is the "empresa" layer of Kai's 3-layer architecture. The heavy
> Postgres+pgvector+Clerk variant lives in git history (`feat: bootstrap Kai
> layer-2 company MCP server`) and is parked; this is the lightweight,
> file-based v1 the team actually connects to.

## Stack

- **FastMCP** (Python) — Streamable HTTP transport
- **Markdown files on disk** — one folder per tenant under `KAI_DATA_ROOT`
- **Static bearer tokens** — a tokens file is the whitelist (who has a token is in)
- **uv** — strict lockfile, exact pins. No DB, no embeddings.
- **Git** — per-tenant history via the system `git` (subprocess); still no DB, no embeddings.

## Tools

Universal `kai_*` verbs over the company brain. The tenant is derived from the
token and is **never** a tool argument.

| Tool | What |
|---|---|
| `kai_read(path, offset, limit)` | Read a file — whole, or a line window (`offset`/`limit`) for long files |
| `kai_write(path, content, mode)` | Create/update a file; `mode` = `overwrite` or `append` |
| `kai_edit(path, old_string, new_string, replace_all)` | Replace exact text in place — targeted edit, doesn't rewrite the rest of the file |
| `kai_delete(path)` | Delete a file (or an empty folder); non-empty folders are refused |
| `kai_list(folder)` | List files and subfolders inside a folder |
| `kai_search(query, limit)` | Scored, frontmatter-aware substring search (terms AND-ed; title/tags/filename rank above body) — ordered by relevance |
| `kai_history(path, limit)` | The commit history of a file — who changed it, when, with each `sha` |
| `kai_revert(path, commit)` | Restore a file to a previous `commit`, recorded as a new commit (history is never rewritten) |
| `who_am_i()` | Caller's tenant, user, role + their `_identity/<user>.md` profile |

## Quick start (local dev)

```bash
uv sync --extra dev

# No auth, runs as the dev tenant on a local data folder (loopback only):
KAI_AUTH_DISABLED=true KAI_DATA_ROOT=./data uv run kai-mcp-empresa
#   → Streamable HTTP on http://127.0.0.1:8080/mcp

uv run pytest          # 33 tests
uv run ruff check .
```

## Provisioning a tenant (the whitelist)

1. Write a tenant spec (see `installs/koi.json`): the tenant slug + its users.
2. Generate the brain folder + bearer tokens:

   ```bash
   uv run python scripts/install_tenant.py installs/koi.json
   ```

   This prints **one bearer token per user** (printed once). Hand each person
   their line. The tokens land in `tokens.json` (gitignored — it's the whitelist).
   Revoke someone by deleting their entry.

3. Run the server pointing at the tokens file:

   ```bash
   KAI_TOKENS_FILE=./tokens.json KAI_DATA_ROOT=./data uv run kai-mcp-empresa
   ```

## Connecting from Claude / ChatGPT

Claude.ai / ChatGPT **web** custom connectors only do OAuth (there's no header
field), so each person connects with a **personal URL that carries their token in
the path**:

```
https://<host>/c/<token>/mcp
```

In Claude: **Settings → Connectors → Add custom connector** → paste that URL →
leave the OAuth fields empty → **Add**. No separate token, no login. A pure-ASGI
middleware (`url_auth.py`) moves the token from the path into an
`Authorization: Bearer` header, so the static whitelist verifies it as usual —
no OAuth, no DB. Generate the per-person URLs from `tokens.json`.

Header-capable clients (Claude Desktop config, our own scripts) can still use the
plain `https://<host>/mcp` endpoint with an `Authorization: Bearer <token>` header.
Each squad member uses **their own** token from `tokens.json`.

To expose a locally-running server quickly (Mat's laptop must stay on):

```bash
cloudflared tunnel --url http://127.0.0.1:8080   # gives a public https URL
```

## Deploy to Railway (permanent)

Railway gives an always-on host with a **persistent volume** for the brain — the
right home for this (Vercel-style serverless has an ephemeral filesystem). The
image is the committed `Dockerfile`; `railway.toml` wires the healthcheck.

1. **New project → Deploy from repo** (or `railway up`). Railway builds the Dockerfile.
2. **Add a Volume** mounted at **`/data`** (Service → Settings → Volumes). This is
   the company brain; it survives restarts and redeploys. `KAI_DATA_ROOT=/data`
   is already set in the image.
3. **Set the whitelist** as a service variable (Variables tab):
   - `KAI_TOKENS_JSON` = the contents of your `tokens.json`
     (`{"<token>": {"tenant":"koi","user":"mili","role":"cfo"}, ...}`).
   Store it as a secret. Add/remove a person by editing this var (redeploys).
4. **Deploy.** Railway gives a public HTTPS URL; the MCP is at `https://<app>.up.railway.app/mcp`,
   liveness at `/healthz`.
5. **Seed identity (optional, once):** open a shell on the service and run
   `uv run python scripts/install_tenant.py installs/koi.json` to create the
   tenant folder + `_identity/*.md` profiles on the volume. Everything else the
   squad writes through the tools (`kai_write`).

Health: `GET /healthz` → `{"status":"ok"}` (unauthenticated). The MCP endpoint
itself (`/mcp`) requires a bearer token.

For any other host, the same image works: mount a persistent volume (or a
Drive-synced folder) at `KAI_DATA_ROOT` and provide `KAI_TOKENS_JSON`, behind HTTPS.

## Auth

Fail-closed. The server **refuses to start** unless either:
- `KAI_TOKENS_FILE` points to an existing tokens file (production path), or
- `KAI_AUTH_DISABLED=true` is set explicitly (local dev only — binds loopback
  only, every request runs as `KAI_DEV_TENANT` / `KAI_DEV_USER`).

Each token entry carries `{ tenant, user, role }`. Those become the request's
identity; the tenant scopes every file path. Tokens are never logged or echoed
into tool output.

## Threat model

### Cross-tenant / path escape (the core defense)
- Every path a tool receives is resolved through `fs.resolve_within`, which
  rejects `..` traversal and confirms the fully-resolved real path (symlinks
  included) stays inside the tenant root. The tenant comes from the token, never
  from arguments — a caller cannot name another tenant's folder.
- **Tested:** `tests/test_fs_scoping.py` (traversal, absolute paths, symlink escape)
  and `tests/test_tools_e2e.py` (traversal blocked through the live client).

### Token leakage
- Tokens are the dict keys in the tokens file; they are never logged, returned in
  tool output, or used as a `client_id` (that's `tenant:user`).

### Tool poisoning (silent change of a tool's contract)
- CI runs a snapshot test of the tool catalog (`tests/test_tool_catalog_snapshot.py`
  vs `tool_catalog.snapshot.json`). Any change to a tool's name, description, or
  input schema fails CI with a diff for human review. Deps pinned in `uv.lock`.

### Write blast radius
- Writes are confined to the tenant root, restricted to text suffixes
  (`.md/.txt/.json/.csv/.yaml`), and capped at `KAI_MAX_FILE_BYTES`.

## History & audit

Each tenant's brain is its own **git repo** (`/data/<tenant>/.git`), initialized
on first write. Every `kai_write`/`kai_edit`/`kai_delete`/`kai_revert` makes a
commit **attributed to the caller** (`user <user@tenant.kai>`), so the company can
see who changed what and when (`kai_history`) and restore any prior version
(`kai_revert`, which records the rollback as a new commit — history is never
rewritten). The brain stays a plain, exportable git repo — no lock-in. Disable
with `KAI_GIT_ENABLED=false` (then `kai_history`/`kai_revert` are inert).

History starts at the first write *after* this feature lands: files that already
existed in a brain before it was git-tracked show empty `kai_history` (and can't be
reverted) until they're next written through a tool. No pre-git content is
retroactively attributed.

**Concurrency:** mutating tools serialize per tenant with an in-process
`asyncio.Lock` across the read-modify-write + commit, so two teammates editing the
same brain never lose each other's change. This assumes a **single process** (one
container) — the production invariant on Railway. Scaling to multiple workers would
require filesystem-level locking (out of scope today).

**Observability:** every tool call emits one JSON line to stdout
(`tenant`, `user`, `tool`, `ok`, `ms`) — captured by Railway logs. Tokens and file
content are never logged. Toggle with `KAI_LOG_TOOLCALLS`.

## Layout

```
src/kai_mcp_empresa/
  config.py        # env settings (pydantic-settings), fail-closed, case-sensitive
  auth.py          # StaticTokenVerifier from the tokens file
  identity.py      # CallerContext from token claims + tenant_root resolution
  fs.py            # path-safe read/write/list/search scoped to a tenant root
  server.py        # FastMCP instance + tool registration
  __main__.py      # entrypoint
  tools/files.py   # kai_read, kai_write, kai_edit, kai_delete, kai_list, kai_search, who_am_i
scripts/
  install_tenant.py  # provision a tenant: brain folder + tokens + identity
  dump_catalog.py    # regenerate the tool-catalog snapshot
tests/               # auth, path scoping, e2e roundtrip, catalog snapshot
```
