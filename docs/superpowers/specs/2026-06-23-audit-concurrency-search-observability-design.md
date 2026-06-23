# Design — kai-mcp-empresa v0.2: audit, concurrency, search, observability

> Date: 2026-06-23
> Status: approved (brainstorming) → ready for implementation plan
> Scope: four interacting improvements to the file-based company-brain MCP server.

## Context

`kai-mcp-empresa` is Kai's Layer-2: a per-tenant MCP server that exposes a
company's brain — a folder of markdown files under `KAI_DATA_ROOT` — as
read/write `kai_*` tools, scoped hard per tenant. It runs on Railway behind a
persistent volume mounted at `/data`. The tenant is derived from the bearer
token, never a tool argument; `fs.resolve_within` is the single path-safety gate.

The repo is healthy (56 tests green, ruff clean, threat model documented). This
work adds capability, it does not fix a fire. Four axes, chosen by the product
owner:

1. **History + audit** — who changed what, when, and undo.
2. **Concurrency safety** — multiple squad members writing in parallel.
3. **Better search** — ranking + frontmatter awareness.
4. **Observability** — structured logging of tool calls.

### Why these, why now

The brain holds a company's real operating data (Koi's finances among it) and is
written by 4+ people concurrently through their own AI clients. Today there is no
record of *who changed what*, no undo, and `kai_edit` is a lock-free
read-modify-write that two concurrent callers can clobber. Kai's #1 external
pitch is **data ownership / trust** ("your data lives in your system"). A git
history per tenant turns that pitch into something demonstrable: every change is
attributed, reversible, and the brain is an exportable git repo.

## Non-goals (YAGNI)

- **No BM25 / embedding index.** A company brain is tens-to-hundreds of files;
  grep with scoring is enough. BM25 stays a clean future upgrade.
- **No queryable in-volume audit log / `kai_activity` tool.** Git already audits
  writes; stdout logging covers reads/errors/latency. `kai_activity` is a future
  product feature, not this cycle.
- **No multi-process / multi-worker coordination.** Railway runs a single
  container/process; an in-process `asyncio.Lock` is sufficient and is documented
  as an invariant. If the service ever scales to N workers, locking must move to
  the filesystem (e.g. `flock`) — called out so it is not forgotten.
- **No history rewriting.** Revert is always a new commit; history is append-only.

## Architecture

New modules and where they plug into the existing flow:

```
tool call (FastMCP)
  └─ register_tools wrapper  ──────────────► obs.py        (structured stdout log: tenant/user/tool/ok/ms)
       └─ tool fn (tools/files.py)
            └─ per-tenant asyncio.Lock      (locks.py)     (mutating ops only)
                 └─ fs.py  (write/edit/delete, atomic)
                      └─ git.py             (commit / history / revert via subprocess)
            └─ fs.search  (scoring + frontmatter)           (read path, no lock)
```

- `git.py` — **new.** Per-tenant git repo lifecycle + commit/history/revert.
- `locks.py` — **new.** Lazy `asyncio.Lock` per tenant root.
- `obs.py` — **new.** Structured JSON logger + the tool-wrapping helper.
- `fs.py` — **modified.** `search` rewritten; mutating ops call `git.py` under lock.
- `tools/files.py` — **modified.** Two new tools; mutating tools acquire the lock
  and trigger a commit; all tools wrapped for logging.
- `config.py` — **modified.** A few new settings (below).

### Component: `git.py` (history + audit)

Implemented with **`subprocess` calls to the `git` binary** — no new Python
dependency. The plan's first step verifies `git` is present in the Docker base
image (and adds it to the `Dockerfile` if not).

Each tenant root (`/data/<tenant>/`) is its own git repo, initialized **lazily**
on the first mutating call:

- `ensure_repo(tenant_root)` — if no `.git`, run `git init`, write a `.gitignore`
  (`.*.tmp` so stranded atomic-write temps never get committed), set a default
  `user.name`/`user.email` at repo level as a fallback, and make an initial empty
  commit so `HEAD` always exists. Idempotent.
- `commit_change(tenant_root, relpath, *, user, role, tool)` — `git add <relpath>`
  then `git commit` with the **caller as author**:
  `-c user.name="<user>" -c user.email="<user>@<tenant>.kai"`. Message:
  `"{tool} {relpath}"`, body line `"by {user} ({role})"`. If the working tree has
  no change for that path (e.g. an edit produced identical bytes — should not
  happen, but defensively), it is a no-op, not an error.
- `history(tenant_root, relpath, limit)` — `git log --follow -n <limit>
  --format=...` for that path → `[{sha, author, date_relative, date_iso,
  message}]`. Empty list if the file has no history yet.
- `revert(tenant_root, relpath, commit, *, user, role)` — read the file's blob at
  `<commit>` (`git show <commit>:<relpath>`), write it to the working tree via the
  normal atomic write, and `commit_change(... tool="kai_revert")`. This restores
  content as a **new commit on top** — never `git revert`/`reset` that could
  rewrite or move history. Errors clearly if `<commit>` is unknown or the file did
  not exist at that commit.

All git invocations:
- run with `cwd=tenant_root`, never accept a tenant as input (caller passes the
  already-resolved tenant root from `identity.tenant_root`).
- pass paths as `relpath` strings already validated by `resolve_within` upstream;
  `git.py` additionally uses `--` separators so a path can never be read as a flag.
- have a timeout; a git failure raises a `ToolError` subclass (`GitError`) with a
  sanitized message (never leak absolute paths or tokens).

**Path safety note:** tools resolve the path through `resolve_within` first (which
yields the absolute path and proves it is inside the tenant root), then pass the
**tenant-relative** path to `git.py`. `git.py` does not re-resolve; it trusts the
already-gated relpath and uses `--` to prevent flag injection.

### Component: `locks.py` (concurrency)

A module-level `dict[Path, asyncio.Lock]` keyed by resolved tenant root, with a
small accessor `tenant_lock(root) -> asyncio.Lock` that creates the lock on first
use. Mutating tools (`kai_write`, `kai_edit`, `kai_delete`, `kai_revert`) wrap
their **read-modify-write + git commit** in `async with tenant_lock(root):`, so
the whole sequence is atomic per tenant. Read tools (`kai_read`, `kai_list`,
`kai_search`, `kai_history`, `who_am_i`) take no lock — the atomic write in `fs.py`
already guarantees a reader never sees a half-written file.

Granularity is **per tenant**, not per file: it is the simplest correct choice,
git operations on a repo should be serialized anyway, and write volume is low. The
lock dict is process-local — documented invariant: single process. (A second
worker would need filesystem locking; out of scope, noted in README threat model.)

### Component: `fs.search` rewrite (search)

Stays pure grep — no index, no dependency. Changes:

- **Multi-term AND:** the query is split on whitespace into terms; a file matches
  only if **every** term appears somewhere in it (case-insensitive).
- **Frontmatter awareness:** if a file starts with a `---` YAML block, parse it
  shallowly (no YAML lib needed for the common case — read `key: value` and
  `tags: [a, b]` / list form). A term that hits `title:` or a `tags:` value scores
  higher than a body hit.
- **Scoring per file:**
  - title/frontmatter match: weight 5 per term
  - filename match: weight 3 per term
  - body match: weight 1 per occurrence (capped to keep one huge file from
    dominating, e.g. cap body contribution at 10)
  Total `score` is the sum across terms; files with score 0 are excluded.
- **Output:** same shape as today plus a `score` field, **sorted by score
  descending** (tie-break: path asc). Still returns matched line numbers +
  snippets (now: lines matching *any* term, up to 5). `limit` clamped 1..100 as
  today.

This is a contract change to `kai_search`'s output (new `score` field) → the tool
catalog snapshot is regenerated and the e2e search test updated.

### Component: `obs.py` + tool wrapping (observability)

- A `logging`-based structured logger that emits **one JSON line per tool call**
  to stdout (Railway captures stdout). Fields: `ts` (iso), `event="tool_call"`,
  `tenant`, `user`, `tool`, `ok` (bool), `ms` (int), and on failure `err` (the
  exception class name + sanitized message — never the token, never file content).
  The matched `path`/`folder`/`query` argument is included **truncated** (path is
  not secret; content is). `query` is logged truncated too.
- A helper `instrument(tool_name, fn)` (or a small decorator applied inside
  `register_tools`) wraps each registered tool: capture start time, resolve
  caller identity for the log, run, log success/failure, re-raise. This keeps the
  tool function bodies clean — logging is one wrapper, not per-tool boilerplate.
- Log level / on-off via a setting (`KAI_LOG_TOOLCALLS`, default on). Format is
  always JSON for machine-grep; a startup line records version + tenant count.

### Config additions (`config.py`)

| Setting | Alias | Default | Purpose |
|---|---|---|---|
| `git_enabled` | `KAI_GIT_ENABLED` | `true` | Master switch for the audit history. Off → mutating tools skip git (tests, degraded mode). |
| `git_timeout_s` | `KAI_GIT_TIMEOUT_S` | `10` | Timeout per git subprocess. |
| `log_toolcalls` | `KAI_LOG_TOOLCALLS` | `true` | Emit per-call structured logs. |
| `history_default_limit` | — (constant) | `20` | Default commits returned by `kai_history`. |

No change to auth, tenant resolution, or the path gate.

## Tool surface (after)

| Tool | Change |
|---|---|
| `kai_read` | unchanged |
| `kai_write` | now commits to git under the tenant lock |
| `kai_edit` | now commits to git under the tenant lock (RMW now race-safe) |
| `kai_delete` | now commits to git under the tenant lock |
| `kai_list` | unchanged |
| `kai_search` | **rewritten** — scoring + frontmatter; output gains `score`, sorted |
| `who_am_i` | unchanged |
| `kai_history(path, limit=20)` | **new** — commit log for one file |
| `kai_revert(path, commit)` | **new** — restore a file to `commit` as a new commit |

## Data flow examples

**Write with audit:** `kai_write("todos.md", ...)` → wrapper logs start →
`async with tenant_lock(root)` → `fs.write_file` (atomic) → `git.ensure_repo` (lazy)
→ `git.commit_change(... user=mili, role=cfo, tool=kai_write)` → wrapper logs
`{ok:true, ms:42}`. Result unchanged in shape (still `{path, mode, created, bytes}`).

**Undo:** `kai_history("todos.md")` → `[{sha:"a1b2", author:"rochi", date:"2h ago",
message:"kai_edit todos.md"}, ...]`. Then `kai_revert("todos.md", "a1b2")` → reads
the blob at `a1b2`, writes it, commits `"kai_revert todos.md"` → `{path, reverted_to:"a1b2", sha:"<new>"}`.

## Error handling

- Git failures → `GitError(ToolError)` with sanitized message; the underlying
  file write still happened (write-then-commit), so a commit failure is logged and
  surfaced but does not lose the user's content. **Ordering decision:** write
  first, then commit — content safety beats audit completeness. A failed commit
  leaves the change uncommitted (next successful mutation's `git add .` picks it
  up, or the startup is fine); this is logged at error level.
- `kai_revert` with unknown commit / file-absent-at-commit → clear `ToolError`.
- Search on a malformed frontmatter block → fall back to treating the file as
  plain text (never raise from search).
- Lock is always released (`async with`).
- All existing `PathError` behavior preserved.

## Testing

TDD throughout. Keep the 56 existing tests green; add:

- **`test_git.py`** — `ensure_repo` idempotent; commit attributes the right
  author; history returns commits newest-first; revert restores content as a new
  commit and never rewrites history; revert on unknown commit errors; git-disabled
  mode is a clean no-op.
- **`test_concurrency.py`** — two concurrent `kai_edit`s on different parts of the
  same file via the live client both land (no lost update); serialized commits.
- **`test_search.py`** — multi-term AND; frontmatter/title hit ranks above body
  hit; `score` present and sorted; malformed frontmatter doesn't raise.
- **`test_obs.py`** — a tool call emits one JSON line with the expected fields;
  token/content never present; failure path logs `err`.
- **Snapshot:** regenerate `tool_catalog.snapshot.json` (new tools + search schema)
  and keep `test_tool_catalog_snapshot.py` green.
- **e2e:** extend `test_tools_e2e.py` for history/revert roundtrip through the
  live client.

## Docs / maintenance that this drags in

- `README.md`: tools table (+ history/revert, search note), a new **History &
  audit** section (per-tenant git, exportable, attributed), and a threat-model
  note on the single-process locking invariant.
- `tool_catalog.snapshot.json` regenerated via `scripts/dump_catalog.py`.
- `.env.example`: the new settings.
- Confirm/instrument `git` in the `Dockerfile`.

## Build order

1. `git.py` + `test_git.py` (verify `git` in image first).
2. `locks.py`; wire mutating ops in `fs.py`/`tools` under the lock + commit; `test_concurrency.py`.
3. `kai_history` / `kai_revert` tools + e2e roundtrip.
4. `fs.search` rewrite + `test_search.py`.
5. `obs.py` + tool wrapping + `test_obs.py`.
6. Regenerate snapshot; update README / `.env.example` / Dockerfile; full suite green + ruff.

## Open questions

None blocking. Future upgrades explicitly deferred: BM25 index, `kai_activity`
in-volume audit tool, multi-worker filesystem locking.
