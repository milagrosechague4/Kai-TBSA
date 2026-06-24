# kai-mcp-empresa v0.2 — Audit, Concurrency, Search, Observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-tenant git audit history (with undo), race-safe concurrent writes, frontmatter-aware scored search, and structured tool-call logging to the file-based company-brain MCP.

**Architecture:** Each tenant folder (`/data/<tenant>/`) becomes its own git repo, lazily initialized; every mutating tool commits the change attributed to the caller, serialized by a per-tenant `asyncio.Lock` and run off the event loop via `asyncio.to_thread`. Search is rewritten as scored grep with shallow frontmatter parsing. Observability is a FastMCP middleware that emits one JSON line per tool call — so tool signatures (and their schemas) are untouched.

**Tech Stack:** Python 3.11+, FastMCP 3.3.1 (Streamable HTTP + middleware), `subprocess` → the system `git` binary (no new Python dependency), `uv`, pytest (`asyncio_mode = auto`).

## Global Constraints

- Python `>=3.11`; line-length 100; ruff must pass (`uv run ruff check .`).
- No new Python runtime dependency — git history uses `subprocess` against the system `git`. (Only dev deps may change.)
- The tenant is NEVER a tool argument; it comes from the token via `identity.current_context` / `identity.tenant_root`. New code receives the already-resolved `tenant_root: Path` and never re-derives a tenant.
- Every untrusted path goes through `fs.resolve_within(root, relpath)` before any filesystem or git operation; git invocations additionally use a `--` separator so a path can never be parsed as a flag.
- Tokens and file *content* are never logged. Paths/queries may be logged, truncated.
- Errors raised to the client are `ToolError` subclasses (`PathError`, `GitError`) with sanitized messages (no absolute paths, no tokens).
- Keep the 56 existing tests green. TDD: failing test first, minimal code, green, commit.
- Single-process invariant: locking is in-process (`asyncio.Lock`). Documented; not for multi-worker.
- Commit messages: conventional commits with a scope, e.g. `feat(git): ...`.

---

## File structure (created / modified)

- **Create** `src/kai_mcp_empresa/git.py` — per-tenant git repo lifecycle + commit/history/blob-read. Pure git, no FastMCP imports.
- **Create** `src/kai_mcp_empresa/locks.py` — lazy per-tenant `asyncio.Lock` registry.
- **Create** `src/kai_mcp_empresa/obs.py` — structured JSON logger + `ToolCallLogger` FastMCP middleware.
- **Modify** `src/kai_mcp_empresa/config.py` — new settings (`git_enabled`, `git_timeout_s`, `log_toolcalls`, `history_default_limit` constant).
- **Modify** `src/kai_mcp_empresa/fs.py` — mutating ops accept a `CommitContext` and commit after writing; add `revert_file`, `file_history`, `_maybe_commit`; rewrite `search`.
- **Modify** `src/kai_mcp_empresa/tools/files.py` — mutating tools acquire the tenant lock + run in a thread; add `kai_history`, `kai_revert`.
- **Modify** `src/kai_mcp_empresa/server.py` — register the logging middleware.
- **Modify** `Dockerfile` — install `git`.
- **Modify** `.env.example` — document new settings.
- **Modify** `README.md` — tools table, History & audit section, concurrency note.
- **Regenerate** `tool_catalog.snapshot.json` via `scripts/dump_catalog.py`.
- **Create** tests: `tests/test_git.py`, `tests/test_concurrency.py`, `tests/test_search.py`, `tests/test_obs.py`; extend `tests/test_tools_e2e.py`.

---

## Task 1: `git.py` — repo lifecycle + attributed commit

**Files:**
- Create: `src/kai_mcp_empresa/git.py`
- Modify: `src/kai_mcp_empresa/config.py`
- Modify: `Dockerfile`
- Test: `tests/test_git.py`

**Interfaces:**
- Consumes: nothing (leaf module). Reads `root.name` as the tenant slug for the commit email domain.
- Produces:
  - `class GitError(ToolError)`
  - `@dataclass(frozen=True) class CommitContext: user: str; role: str | None; tool: str`
  - `ensure_repo(root: Path, *, timeout: float) -> None`
  - `commit_change(root: Path, relpath: str, ctx: CommitContext, *, timeout: float) -> str | None` (returns the new short sha, or `None` if the path had no change to commit)
  - `read_blob_at(root: Path, relpath: str, commit: str, *, timeout: float) -> str`
  - `history(root: Path, relpath: str, limit: int, *, timeout: float) -> list[dict]` (each: `{sha, author, date_relative, date_iso, message}`)

- [ ] **Step 1: Add settings to `config.py`**

In `class Settings`, after the write-guardrail block (`allowed_suffixes`), add:

```python
    # History / audit (per-tenant git repo). Off → mutating tools skip git.
    git_enabled: bool = Field(True, alias="KAI_GIT_ENABLED")
    git_timeout_s: float = Field(10.0, alias="KAI_GIT_TIMEOUT_S")

    # Observability: one JSON line per tool call to stdout (Railway captures it).
    log_toolcalls: bool = Field(True, alias="KAI_LOG_TOOLCALLS")
```

And at module level (after the imports, before `class Settings`) add the history default:

```python
HISTORY_DEFAULT_LIMIT = 20
```

- [ ] **Step 2: Install git in the Docker image**

In `Dockerfile`, immediately after `WORKDIR /app`, add:

```dockerfile
# git is required at runtime for the per-tenant audit history (subprocess).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: Write the failing test for ensure_repo + attributed commit + history**

Create `tests/test_git.py`:

```python
"""Per-tenant git audit: init, attributed commits, history, blob read."""

from __future__ import annotations

from pathlib import Path

import pytest

from kai_mcp_empresa import git
from kai_mcp_empresa.git import CommitContext, GitError

TIMEOUT = 10.0


def _tenant_root(tmp_path: Path) -> Path:
    root = tmp_path / "koi"  # root.name == tenant slug
    root.mkdir()
    return root


def test_ensure_repo_is_idempotent_and_creates_head(tmp_path):
    root = _tenant_root(tmp_path)
    git.ensure_repo(root, timeout=TIMEOUT)
    assert (root / ".git").is_dir()
    assert (root / ".gitignore").exists()
    # Second call must not raise or re-init.
    git.ensure_repo(root, timeout=TIMEOUT)


def test_commit_attributes_the_caller(tmp_path):
    root = _tenant_root(tmp_path)
    git.ensure_repo(root, timeout=TIMEOUT)
    (root / "todos.md").write_text("- [ ] uno\n", encoding="utf-8")
    sha = git.commit_change(
        root, "todos.md", CommitContext("mili", "cfo", "kai_write"), timeout=TIMEOUT
    )
    assert sha
    hist = git.history(root, "todos.md", 10, timeout=TIMEOUT)
    assert len(hist) == 1
    assert hist[0]["author"] == "mili"
    assert "todos.md" in hist[0]["message"]
    assert hist[0]["sha"] == sha


def test_commit_with_no_change_returns_none(tmp_path):
    root = _tenant_root(tmp_path)
    git.ensure_repo(root, timeout=TIMEOUT)
    (root / "a.md").write_text("x\n", encoding="utf-8")
    git.commit_change(root, "a.md", CommitContext("mat", "ceo", "kai_write"), timeout=TIMEOUT)
    # Nothing changed on disk → nothing to commit.
    assert git.commit_change(
        root, "a.md", CommitContext("mat", "ceo", "kai_write"), timeout=TIMEOUT
    ) is None


def test_read_blob_at_returns_old_content(tmp_path):
    root = _tenant_root(tmp_path)
    git.ensure_repo(root, timeout=TIMEOUT)
    (root / "a.md").write_text("v1\n", encoding="utf-8")
    sha = git.commit_change(root, "a.md", CommitContext("mat", None, "kai_write"), timeout=TIMEOUT)
    (root / "a.md").write_text("v2\n", encoding="utf-8")
    git.commit_change(root, "a.md", CommitContext("mat", None, "kai_edit"), timeout=TIMEOUT)
    assert git.read_blob_at(root, "a.md", sha, timeout=TIMEOUT) == "v1\n"


def test_read_blob_at_unknown_commit_raises(tmp_path):
    root = _tenant_root(tmp_path)
    git.ensure_repo(root, timeout=TIMEOUT)
    with pytest.raises(GitError):
        git.read_blob_at(root, "a.md", "deadbeef", timeout=TIMEOUT)


def test_history_of_unknown_file_is_empty(tmp_path):
    root = _tenant_root(tmp_path)
    git.ensure_repo(root, timeout=TIMEOUT)
    assert git.history(root, "nope.md", 10, timeout=TIMEOUT) == []
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_git.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kai_mcp_empresa.git'`.

- [ ] **Step 5: Implement `git.py`**

Create `src/kai_mcp_empresa/git.py`:

```python
"""Per-tenant git audit history.

Each tenant root (/data/<tenant>/) is its own git repo. Every mutating tool
commits its change, attributed to the caller, so the company can see who changed
what and restore any prior version — and the brain stays an exportable git repo
(data ownership). Implemented with subprocess against the system `git`: no extra
Python dependency. The tenant root is always passed in already-resolved; paths
are tenant-relative and validated upstream by fs.resolve_within. Every git call
uses `--` so a path can never be read as a flag.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from fastmcp.exceptions import ToolError


class GitError(ToolError):
    """A git operation failed; message is sanitized for the client."""


@dataclass(frozen=True)
class CommitContext:
    """Who made the change and through which tool — becomes the commit author."""

    user: str
    role: str | None
    tool: str


# ASCII unit separator: a field delimiter that never appears in git metadata.
_FS = "\x1f"
_LOG_FORMAT = _FS.join(["%h", "%an", "%ar", "%aI", "%s"])


def _run(
    args: list[str], cwd: Path, timeout: float, *, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a git command in `cwd`. On failure (check=True) raise a sanitized GitError."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # git not installed
        raise GitError("git is not available on this server") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        # stderr can name relpaths but never the absolute root or a token.
        raise GitError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc


def _email(root: Path, user: str) -> str:
    return f"{user}@{root.name}.kai"


def ensure_repo(root: Path, *, timeout: float) -> None:
    """Initialize the tenant repo if absent. Idempotent. HEAD always exists after."""
    if (root / ".git").is_dir():
        return
    _run(["init", "-q"], root, timeout)
    _run(["config", "user.name", "kai"], root, timeout)
    _run(["config", "user.email", f"kai@{root.name}.kai"], root, timeout)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".*.tmp\n", encoding="utf-8")
    _run(["add", "--", ".gitignore"], root, timeout)
    _run(["commit", "-q", "-m", "chore: init brain"], root, timeout)


def commit_change(
    root: Path, relpath: str, ctx: CommitContext, *, timeout: float
) -> str | None:
    """Stage and commit changes to one path, attributed to the caller.

    Returns the new short sha, or None when the path had nothing to commit.
    """
    _run(["add", "-A", "--", relpath], root, timeout)
    staged = _run(["diff", "--cached", "--quiet", "--", relpath], root, timeout, check=False)
    if staged.returncode == 0:  # 0 = no staged changes for this path
        return None
    subject = f"{ctx.tool} {relpath}"
    body = f"by {ctx.user}" + (f" ({ctx.role})" if ctx.role else "")
    _run(
        [
            "-c", f"user.name={ctx.user}",
            "-c", f"user.email={_email(root, ctx.user)}",
            "commit", "-q", "-m", subject, "-m", body, "--", relpath,
        ],
        root,
        timeout,
    )
    return _run(["rev-parse", "--short", "HEAD"], root, timeout).stdout.strip()


def read_blob_at(root: Path, relpath: str, commit: str, *, timeout: float) -> str:
    """Return the file's content as of `commit`. Raises GitError if absent there."""
    return _run(["show", f"{commit}:{relpath}"], root, timeout).stdout


def history(root: Path, relpath: str, limit: int, *, timeout: float) -> list[dict]:
    """Commits touching `relpath`, newest first. Empty if the file has no history."""
    out = _run(
        ["log", f"-n{limit}", "--follow", f"--format={_LOG_FORMAT}", "--", relpath],
        root,
        timeout,
        check=False,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return []
    rows: list[dict] = []
    for line in out.stdout.splitlines():
        sha, author, rel, iso, msg = (line.split(_FS) + ["", "", "", "", ""])[:5]
        rows.append(
            {"sha": sha, "author": author, "date_relative": rel, "date_iso": iso, "message": msg}
        )
    return rows
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_git.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Lint and commit**

```bash
cd ~/Documents/GitHub/kai-mcp-empresa
uv run ruff check src/kai_mcp_empresa/git.py tests/test_git.py
git add src/kai_mcp_empresa/git.py tests/test_git.py src/kai_mcp_empresa/config.py Dockerfile
git commit -m "feat(git): per-tenant git audit — ensure_repo, attributed commit, history

Each tenant brain becomes its own git repo (lazy init); commit_change
attributes every change to the caller via -c user.name/email; history and
read_blob_at back the upcoming kai_history/kai_revert tools. subprocess
against system git (now installed in the Dockerfile) — no new dependency.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `locks.py` + wire mutating fs ops to commit under the lock

**Files:**
- Create: `src/kai_mcp_empresa/locks.py`
- Modify: `src/kai_mcp_empresa/fs.py`
- Modify: `src/kai_mcp_empresa/tools/files.py`
- Test: `tests/test_concurrency.py`

**Interfaces:**
- Consumes: `git.CommitContext`, `git.ensure_repo`, `git.commit_change` (Task 1); `identity.current_context`, `identity.tenant_root`.
- Produces:
  - `locks.tenant_lock(root: Path) -> asyncio.Lock`
  - `fs.write_file(settings, root, relpath, content, mode="overwrite", commit: git.CommitContext | None = None) -> dict` (commit param added)
  - `fs.edit_file(settings, root, relpath, old_string, new_string, replace_all=False, commit: git.CommitContext | None = None) -> dict` (commit param added)
  - `fs.delete_file(settings, root, relpath, commit: git.CommitContext | None = None) -> dict` (commit param added)
  - `kai_write` / `kai_edit` / `kai_delete` now acquire `tenant_lock` and run the sync fs call via `asyncio.to_thread`.

- [ ] **Step 1: Write the failing concurrency test**

Create `tests/test_concurrency.py`:

```python
"""Concurrent mutations of the same file must not lose an update."""

from __future__ import annotations

import asyncio

from conftest import make_settings
from fastmcp import Client

from kai_mcp_empresa.server import build_server


async def test_concurrent_edits_no_lost_update(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool(
            "kai_write", {"path": "board.md", "content": "- [ ] a\n- [ ] b\n"}
        )
        await asyncio.gather(
            client.call_tool(
                "kai_edit",
                {"path": "board.md", "old_string": "- [ ] a", "new_string": "- [x] a"},
            ),
            client.call_tool(
                "kai_edit",
                {"path": "board.md", "old_string": "- [ ] b", "new_string": "- [x] b"},
            ),
        )
        read = (await client.call_tool("kai_read", {"path": "board.md"})).data
        # Both edits survived — neither overwrote the other's change.
        assert read["content"] == "- [x] a\n- [x] b\n"


async def test_each_mutation_makes_a_commit(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "t.md", "content": "uno\n"})
        await client.call_tool(
            "kai_edit", {"path": "t.md", "old_string": "uno", "new_string": "dos"}
        )
        hist = (await client.call_tool("kai_history", {"path": "t.md"})).data
        # kai_history is added in Task 3; until then this assertion is the driver.
        assert len(hist) == 2
```

> Note: `test_each_mutation_makes_a_commit` depends on `kai_history` (Task 3). Mark it expected-to-fail until Task 3, or run only `test_concurrent_edits_no_lost_update` in this task's verify step. Keep both in the file — Task 3 turns the second green.

- [ ] **Step 2: Run the concurrency test to verify it fails**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_concurrency.py::test_concurrent_edits_no_lost_update -v`
Expected: FAIL — `kai_edit` does not yet accept being driven this way / commit param absent (or the two edits race). (If it happens to pass because bodies are still fully synchronous, proceed — the lock makes the guarantee explicit and survives the `to_thread` change in Step 5.)

- [ ] **Step 3: Implement `locks.py`**

Create `src/kai_mcp_empresa/locks.py`:

```python
"""Per-tenant write serialization.

A mutating tool (write/edit/delete/revert) holds its tenant's lock across the
read-modify-write + git commit, so two callers editing the same brain can't lose
each other's change. One lock per tenant root, created on first use. In-process
only — the server runs as a single process (Railway: one container). A second
worker would need filesystem locking; out of scope.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Keyed by the resolved tenant root. Mutated only from the event loop thread
# (no await between get and set), so a plain dict is safe.
_locks: dict[Path, asyncio.Lock] = {}


def tenant_lock(root: Path) -> asyncio.Lock:
    key = root.resolve()
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
```

- [ ] **Step 4: Add commit wiring to `fs.py`**

In `src/kai_mcp_empresa/fs.py`, add the import near the top (after `from .config import Settings`):

```python
from . import git
```

Add a private helper after `_atomic_write_text` (before `sweep_stale_temps`):

```python
def _maybe_commit(
    settings: Settings, root: Path, relpath: str, commit: "git.CommitContext | None"
) -> None:
    """Record a mutation in the tenant's git history, if enabled. Best-effort by
    contract of the caller: the file write already succeeded, so a commit failure
    surfaces as a GitError but never loses content."""
    if commit is None or not settings.git_enabled:
        return
    git.ensure_repo(root, timeout=settings.git_timeout_s)
    git.commit_change(root, relpath, commit, timeout=settings.git_timeout_s)
```

Update the three mutating signatures and add a `_maybe_commit` call at the end of each, just before the `return`:

`write_file` — change the signature to:

```python
def write_file(
    settings: Settings,
    root: Path,
    relpath: str,
    content: str,
    mode: str = "overwrite",
    commit: "git.CommitContext | None" = None,
) -> dict:
```

and before its `return {...}` add:

```python
    rel = str(path.relative_to(root))
    _maybe_commit(settings, root, rel, commit)
```

(use `rel` in the returned `"path"` too, or keep the existing `str(path.relative_to(root))`.)

`edit_file` — add `commit: "git.CommitContext | None" = None` as the last parameter, and before its `return {...}` add:

```python
    _maybe_commit(settings, root, str(path.relative_to(root)), commit)
```

`delete_file` — add `commit: "git.CommitContext | None" = None` as the last parameter. For deletes, compute the relpath BEFORE removing, and commit AFTER removal. Replace the file/dir removal tail so it reads:

```python
    rel = str(path.relative_to(root))
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as exc:
            raise PathError(
                f"{relpath!r} is a non-empty folder; delete its files first"
            ) from exc
        return {"path": rel, "deleted": True, "type": "dir"}
    path.unlink()
    _maybe_commit(settings, root, rel, commit)
    return {"path": rel, "deleted": True, "type": "file"}
```

(Directory removal is not tracked by git — an empty dir has no git content — so only the file branch commits.)

- [ ] **Step 5: Make the mutating tools lock + thread in `tools/files.py`**

In `src/kai_mcp_empresa/tools/files.py`, add imports after the existing ones:

```python
import asyncio

from ..git import CommitContext
from ..locks import tenant_lock
```

Replace the bodies of `kai_write`, `kai_edit`, `kai_delete` so each resolves the caller, builds a `CommitContext`, and runs the sync fs call under the tenant lock in a worker thread. Example for `kai_write` (apply the same shape to the others, changing the tool name and fs function):

```python
    @mcp.tool
    async def kai_write(path: str, content: str, mode: str = "overwrite") -> dict:
        """<keep the existing docstring unchanged>"""
        ctx, root = _root()
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_write")
        async with tenant_lock(root):
            return await asyncio.to_thread(
                fs.write_file, settings, root, path, content, mode, cc
            )
```

`kai_edit`:

```python
        ctx, root = _root()
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_edit")
        async with tenant_lock(root):
            return await asyncio.to_thread(
                fs.edit_file, settings, root, path, old_string, new_string, replace_all, cc
            )
```

`kai_delete`:

```python
        ctx, root = _root()
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_delete")
        async with tenant_lock(root):
            return await asyncio.to_thread(fs.delete_file, settings, root, path, cc)
```

(Leave `kai_read`, `kai_list`, `kai_search`, `who_am_i` unchanged — reads need no lock.)

- [ ] **Step 6: Run the concurrency test (first case) + full suite**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_concurrency.py::test_concurrent_edits_no_lost_update tests/test_git.py tests/test_tools_e2e.py -v`
Expected: `test_concurrent_edits_no_lost_update` PASS, Task 1 + existing e2e still PASS. (`test_each_mutation_makes_a_commit` still fails — `kai_history` lands in Task 3.)

- [ ] **Step 7: Lint and commit**

```bash
cd ~/Documents/GitHub/kai-mcp-empresa
uv run ruff check src tests
git add src/kai_mcp_empresa/locks.py src/kai_mcp_empresa/fs.py src/kai_mcp_empresa/tools/files.py tests/test_concurrency.py
git commit -m "feat(concurrency): per-tenant lock + commit-on-write

Mutating tools now hold an asyncio.Lock per tenant across the
read-modify-write and run it off the event loop via to_thread; fs
write/edit/delete commit the change to the tenant's git history. Two
people editing the same brain no longer lose each other's update.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `kai_history` + `kai_revert` tools

**Files:**
- Modify: `src/kai_mcp_empresa/fs.py`
- Modify: `src/kai_mcp_empresa/tools/files.py`
- Test: `tests/test_tools_e2e.py` (extend), `tests/test_concurrency.py` (second case turns green)

**Interfaces:**
- Consumes: `git.history`, `git.read_blob_at`, `git.commit_change` (Task 1); `fs._atomic_write_text`, `fs.resolve_within`, `fs._check_suffix`; `tenant_lock`, `CommitContext` (Task 2).
- Produces:
  - `fs.file_history(settings, root, relpath, limit) -> list[dict]`
  - `fs.revert_file(settings, root, relpath, commit_sha, ctx: git.CommitContext) -> dict` (returns `{path, reverted_to, sha}`)
  - tools `kai_history(path, limit=20) -> list[dict]`, `kai_revert(path, commit) -> dict`

- [ ] **Step 1: Write the failing e2e test for history + revert**

Append to `tests/test_tools_e2e.py`:

```python
async def test_history_and_revert_roundtrip(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "plan.md", "content": "v1\n"})
        await client.call_tool(
            "kai_edit", {"path": "plan.md", "old_string": "v1", "new_string": "v2"}
        )
        hist = (await client.call_tool("kai_history", {"path": "plan.md"})).data
        assert len(hist) == 2
        assert hist[0]["author"] == "tester"  # newest first (the edit)

        first_sha = hist[-1]["sha"]  # the original write
        res = (
            await client.call_tool(
                "kai_revert", {"path": "plan.md", "commit": first_sha}
            )
        ).data
        assert res["reverted_to"] == first_sha
        read = (await client.call_tool("kai_read", {"path": "plan.md"})).data
        assert read["content"] == "v1\n"
        # Revert is a NEW commit on top — history grew, nothing was rewritten.
        hist2 = (await client.call_tool("kai_history", {"path": "plan.md"})).data
        assert len(hist2) == 3


async def test_revert_unknown_commit_is_error(tmp_path):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    async with Client(mcp) as client:
        await client.call_tool("kai_write", {"path": "x.md", "content": "hi\n"})
        result = await client.call_tool(
            "kai_revert", {"path": "x.md", "commit": "deadbeef"}, raise_on_error=False
        )
        assert result.is_error
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_tools_e2e.py::test_history_and_revert_roundtrip -v`
Expected: FAIL — `Unknown tool: kai_history`.

- [ ] **Step 3: Add `file_history` + `revert_file` to `fs.py`**

In `src/kai_mcp_empresa/fs.py`, add after `delete_file`:

```python
def file_history(settings: Settings, root: Path, relpath: str, limit: int) -> list[dict]:
    """Commit history for one file (newest first). Empty if git is off or no history."""
    if not settings.git_enabled:
        return []
    path = resolve_within(root, relpath)  # validates the path; file need not exist
    return git.history(root, str(path.relative_to(root)), limit, timeout=settings.git_timeout_s)


def revert_file(
    settings: Settings,
    root: Path,
    relpath: str,
    commit_sha: str,
    ctx: "git.CommitContext",
) -> dict:
    """Restore a file to its content at `commit_sha`, recorded as a new commit.

    Never rewrites history: it reads the old blob, writes it atomically, and
    commits on top. Errors if git is off or the file did not exist at that commit.
    """
    if not settings.git_enabled:
        raise git.GitError("history/revert is disabled (KAI_GIT_ENABLED=false)")
    path = resolve_within(root, relpath)
    if path == root:
        raise PathError(f"{relpath!r} is not a file path")
    _check_suffix(settings, path)
    rel = str(path.relative_to(root))
    content = git.read_blob_at(root, rel, commit_sha, timeout=settings.git_timeout_s)
    if len(content.encode("utf-8")) > settings.max_file_bytes:
        raise PathError(f"reverted content would exceed the {settings.max_file_bytes}-byte limit")
    _atomic_write_text(path, content)
    sha = git.commit_change(root, rel, ctx, timeout=settings.git_timeout_s)
    return {"path": rel, "reverted_to": commit_sha, "sha": sha}
```

- [ ] **Step 4: Add the two tools to `tools/files.py`**

In `src/kai_mcp_empresa/tools/files.py`, add after `kai_search` (and before `who_am_i`):

```python
    @mcp.tool
    async def kai_history(path: str, limit: int = 20) -> list[dict]:
        """Show the change history of a file in the company brain.

        Returns the commits that touched `path`, newest first — each with a short
        `sha`, the `author` (the teammate who made the change), a relative and ISO
        date, and the message. Use the `sha` with kai_revert to restore a previous
        version. Empty if the file has no recorded history yet.
        """
        _, root = _root()
        limit = max(1, min(limit, 100))
        return fs.file_history(settings, root, path, limit)

    @mcp.tool
    async def kai_revert(path: str, commit: str) -> dict:
        """Restore a file to how it was at a previous commit.

        `commit` is a `sha` from kai_history. The file's content is rolled back to
        that version and the rollback is saved as a NEW commit on top — history is
        never rewritten, so every change (including this one) stays auditable.
        Returns the path, the commit reverted to, and the new commit sha.
        """
        ctx, root = _root()
        cc = CommitContext(user=ctx.user, role=ctx.role, tool="kai_revert")
        async with tenant_lock(root):
            return await asyncio.to_thread(fs.revert_file, settings, root, path, commit, cc)
```

- [ ] **Step 5: Run the new e2e + the deferred concurrency case + full suite**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_tools_e2e.py tests/test_concurrency.py tests/test_git.py -v`
Expected: all PASS, including `test_each_mutation_makes_a_commit` and `test_history_and_revert_roundtrip`.

- [ ] **Step 6: Lint and commit**

```bash
cd ~/Documents/GitHub/kai-mcp-empresa
uv run ruff check src tests
git add src/kai_mcp_empresa/fs.py src/kai_mcp_empresa/tools/files.py tests/test_tools_e2e.py tests/test_concurrency.py
git commit -m "feat(tools): add kai_history + kai_revert

kai_history lists a file's commits (attributed, newest first);
kai_revert restores a file to a prior commit as a NEW commit — history
is never rewritten, so the undo is itself auditable. Backs the data
ownership pitch with a demonstrable per-tenant audit trail.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: scored, frontmatter-aware `fs.search`

**Files:**
- Modify: `src/kai_mcp_empresa/fs.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `Settings.allowed_suffixes`.
- Produces: `fs.search(settings, root, query, limit) -> list[dict]` — each hit `{path, score, matches: [{line, text}]}`, sorted by `score` desc then `path` asc. Signature unchanged; output gains `score` and ordering.

- [ ] **Step 1: Write the failing search test**

Create `tests/test_search.py`:

```python
"""Scored, frontmatter-aware search."""

from __future__ import annotations

from pathlib import Path

from conftest import make_settings

from kai_mcp_empresa import fs


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "co"
    root.mkdir()
    return root


def test_multi_term_is_and(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    (root / "both.md").write_text("revenue and pricing notes\n", encoding="utf-8")
    (root / "one.md").write_text("revenue only\n", encoding="utf-8")
    hits = fs.search(settings, root, "revenue pricing", 20)
    paths = [h["path"] for h in hits]
    assert "both.md" in paths
    assert "one.md" not in paths  # missing "pricing" → excluded


def test_frontmatter_title_ranks_above_body(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    (root / "title-hit.md").write_text(
        "---\ntitle: Pricing strategy\ntags: [gtm]\n---\nbody\n", encoding="utf-8"
    )
    (root / "body-hit.md").write_text(
        "a long note mentioning pricing once in the body\n", encoding="utf-8"
    )
    hits = fs.search(settings, root, "pricing", 20)
    assert hits[0]["path"] == "title-hit.md"
    assert hits[0]["score"] > hits[1]["score"]


def test_tag_match_counts(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    (root / "tagged.md").write_text("---\ntags: [koi, finance]\n---\nx\n", encoding="utf-8")
    hits = fs.search(settings, root, "finance", 20)
    assert hits and hits[0]["path"] == "tagged.md"


def test_score_present_and_sorted(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    (root / "a.md").write_text("kai kai kai\n", encoding="utf-8")
    (root / "b.md").write_text("kai once\n", encoding="utf-8")
    hits = fs.search(settings, root, "kai", 20)
    assert all("score" in h for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_malformed_frontmatter_does_not_raise(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    (root / "weird.md").write_text("---\nnot: [closed\nstill pricing here\n", encoding="utf-8")
    hits = fs.search(settings, root, "pricing", 20)
    assert any(h["path"] == "weird.md" for h in hits)


def test_empty_query_returns_empty(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    (root / "a.md").write_text("x\n", encoding="utf-8")
    assert fs.search(settings, root, "   ", 20) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_search.py -v`
Expected: FAIL — current `search` has no `score`, no multi-term AND, no ranking.

- [ ] **Step 3: Rewrite `fs.search` (and add helpers)**

In `src/kai_mcp_empresa/fs.py`, replace the entire `search` function with the following, and add the two helpers above it:

```python
# Scoring weights: a term in the title/frontmatter is worth far more than a body hit.
_W_TITLE = 5
_W_FILENAME = 3
_W_BODY = 1
_BODY_CAP = 10  # cap one file's body contribution so a huge file can't dominate


def _frontmatter_text(text: str) -> str:
    """Return the raw YAML frontmatter block (between leading --- fences), or "".

    Shallow and forgiving: if there's no closing fence we just take the rest of
    the file as frontmatter-ish text for matching. Never raises — search must not
    fail on a malformed block.
    """
    if not text.startswith("---"):
        return ""
    rest = text[3:]
    end = rest.find("\n---")
    return rest if end == -1 else rest[:end]


def _score_file(relpath: str, text: str, terms: list[str]) -> int:
    """Sum the weighted score of `terms` in a file. 0 if any term is absent."""
    low = text.lower()
    if not all(t in low for t in terms):
        return 0
    fm = _frontmatter_text(text).lower()
    name = relpath.lower()
    score = 0
    for t in terms:
        if t in fm:
            score += _W_TITLE
        if t in name:
            score += _W_FILENAME
        score += min(_BODY_CAP, low.count(t)) * _W_BODY
    return score


def search(settings: Settings, root: Path, query: str, limit: int = 20) -> list[dict]:
    """Scored, frontmatter-aware substring search across the tenant's text files.

    The query is split into terms; a file matches only if EVERY term appears
    (case-insensitive). Each file gets a score — hits in the YAML frontmatter
    (title/tags) and the filename weigh more than body hits — and results come
    back sorted by score (desc), then path. Plain grep, no index: the brain is
    small and the win is transparency. Returns one entry per matching file with
    its score and up to 5 matched lines.
    """
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return []
    scored: list[tuple[int, dict]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in settings.allowed_suffixes:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(root))
        score = _score_file(rel, text, terms)
        if score == 0:
            continue
        matches = []
        for n, line in enumerate(text.splitlines(), start=1):
            low_line = line.lower()
            if any(t in low_line for t in terms):
                matches.append({"line": n, "text": line.strip()[:200]})
                if len(matches) >= 5:
                    break
        scored.append((score, {"path": rel, "score": score, "matches": matches}))
    scored.sort(key=lambda s: (-s[0], s[1]["path"]))
    return [hit for _, hit in scored[:limit]]
```

- [ ] **Step 4: Run the search tests + the existing e2e (search assertion)**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_search.py tests/test_tools_e2e.py -v`
Expected: PASS. (The existing `test_write_list_read_search_who_am_i` still finds `reuniones/2026-06-17.md` for query "ERP".)

- [ ] **Step 5: Lint and commit**

```bash
cd ~/Documents/GitHub/kai-mcp-empresa
uv run ruff check src tests
git add src/kai_mcp_empresa/fs.py tests/test_search.py
git commit -m "feat(search): scored, frontmatter-aware multi-term search

Query terms are AND-ed; frontmatter (title/tags) and filename hits
outweigh body hits; results carry a score and sort by it. Still pure
grep — no index, no dependency. Output gains a 'score' field (catalog
snapshot updated in the maintenance task).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: observability — `obs.py` middleware

**Files:**
- Create: `src/kai_mcp_empresa/obs.py`
- Modify: `src/kai_mcp_empresa/server.py`
- Test: `tests/test_obs.py`

**Interfaces:**
- Consumes: `identity.current_context` (best-effort, for tenant/user); FastMCP `Middleware`, `MiddlewareContext`, `CallNext`.
- Produces:
  - `obs.log_call(*, logger, tenant, user, tool, ok, ms, err=None, arg=None) -> None`
  - `class ToolCallLogger(Middleware)` with `__init__(self, settings)` and `async def on_call_tool(self, context, call_next)`.
  - `server.build_server` registers the middleware when `settings.log_toolcalls`.

- [ ] **Step 1: Write the failing observability test**

Create `tests/test_obs.py`:

```python
"""Structured tool-call logging: one JSON line per call, no secrets."""

from __future__ import annotations

import json

from conftest import make_settings
from fastmcp import Client

from kai_mcp_empresa.server import build_server


async def test_tool_call_emits_one_json_line(tmp_path, caplog):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    with caplog.at_level("INFO", logger="kai.toolcall"):
        async with Client(mcp) as client:
            await client.call_tool("kai_write", {"path": "a.md", "content": "hi\n"})

    lines = [r.getMessage() for r in caplog.records if r.name == "kai.toolcall"]
    assert lines, "expected at least one tool-call log line"
    payloads = [json.loads(m) for m in lines]
    write = next(p for p in payloads if p["tool"] == "kai_write")
    assert write["event"] == "tool_call"
    assert write["tenant"] == "testco"
    assert write["user"] == "tester"
    assert write["ok"] is True
    assert isinstance(write["ms"], int)
    # No content, no token ever in the log payload.
    assert "hi" not in write.get("arg", "")
    assert "content" not in json.dumps(write)


async def test_failed_tool_call_logs_error(tmp_path, caplog):
    settings = make_settings(data_root=tmp_path)
    mcp, _ = build_server(settings)
    with caplog.at_level("INFO", logger="kai.toolcall"):
        async with Client(mcp) as client:
            await client.call_tool(
                "kai_read", {"path": "missing.md"}, raise_on_error=False
            )
    payloads = [json.loads(r.getMessage()) for r in caplog.records if r.name == "kai.toolcall"]
    read = next(p for p in payloads if p["tool"] == "kai_read")
    assert read["ok"] is False
    assert read["err"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_obs.py -v`
Expected: FAIL — no `kai.toolcall` log records (no middleware yet).

- [ ] **Step 3: Implement `obs.py`**

Create `src/kai_mcp_empresa/obs.py`:

```python
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
        "arg": arg,
    }
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
```

- [ ] **Step 4: Register the middleware in `server.py`**

In `src/kai_mcp_empresa/server.py`, add the import:

```python
from .obs import ToolCallLogger
```

and after `register_tools(mcp, settings)` add:

```python
    if settings.log_toolcalls:
        mcp.add_middleware(ToolCallLogger(settings))
```

- [ ] **Step 5: Run the obs tests + full suite**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_obs.py -v && uv run pytest -q`
Expected: obs tests PASS; whole suite PASS except the catalog snapshot (fixed in Task 6, which adds the new tools to the snapshot).

- [ ] **Step 6: Lint and commit**

```bash
cd ~/Documents/GitHub/kai-mcp-empresa
uv run ruff check src tests
git add src/kai_mcp_empresa/obs.py src/kai_mcp_empresa/server.py tests/test_obs.py
git commit -m "feat(obs): structured tool-call logging via middleware

One JSON line per tool call to stdout (tenant/user/tool/ok/ms), as a
FastMCP middleware so tool schemas are untouched. Tokens and file
content are never logged; path/query is truncated. Toggle with
KAI_LOG_TOOLCALLS.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: regenerate snapshot + docs + Dockerfile/env, full green

**Files:**
- Regenerate: `tool_catalog.snapshot.json`
- Modify: `README.md`, `.env.example`
- (Dockerfile already updated in Task 1 — verify.)

**Interfaces:**
- Consumes: everything above. No new code interfaces.

- [ ] **Step 1: Regenerate the tool catalog snapshot**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run python scripts/dump_catalog.py`
Expected output: `wrote 9 tools to .../tool_catalog.snapshot.json` (was 7 → now includes `kai_history`, `kai_revert`; `kai_search` description updated).

- [ ] **Step 2: Verify the snapshot test passes against the regenerated file**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest tests/test_tool_catalog_snapshot.py -v`
Expected: PASS.

- [ ] **Step 3: Update `.env.example`**

Add these lines to `.env.example` (under a new `# History / observability` heading):

```bash
# History / audit — per-tenant git repo. Off disables kai_history/kai_revert.
KAI_GIT_ENABLED=true
KAI_GIT_TIMEOUT_S=10
# Observability — one JSON line per tool call to stdout.
KAI_LOG_TOOLCALLS=true
```

- [ ] **Step 4: Update `README.md`**

In the **Tools** table, add two rows and update the search row:

```markdown
| `kai_search(query, limit)` | Scored, frontmatter-aware substring search (terms AND-ed; title/tags/filename rank above body) — ordered by relevance |
| `kai_history(path, limit)` | The commit history of a file — who changed it, when, with each `sha` |
| `kai_revert(path, commit)` | Restore a file to a previous `commit`, recorded as a new commit (history is never rewritten) |
```

Add a new section after **Threat model** (before **Layout**):

```markdown
## History & audit

Each tenant's brain is its own **git repo** (`/data/<tenant>/.git`), initialized
on first write. Every `kai_write`/`kai_edit`/`kai_delete`/`kai_revert` makes a
commit **attributed to the caller** (`user <user@tenant.kai>`), so the company can
see who changed what and when (`kai_history`) and restore any prior version
(`kai_revert`, which records the rollback as a new commit — history is never
rewritten). The brain stays a plain, exportable git repo — no lock-in. Disable
with `KAI_GIT_ENABLED=false` (then `kai_history`/`kai_revert` are inert).

**Concurrency:** mutating tools serialize per tenant with an in-process
`asyncio.Lock` across the read-modify-write + commit, so two teammates editing the
same brain never lose each other's change. This assumes a **single process** (one
container) — the production invariant on Railway. Scaling to multiple workers would
require filesystem-level locking (out of scope today).

**Observability:** every tool call emits one JSON line to stdout
(`tenant`, `user`, `tool`, `ok`, `ms`) — captured by Railway logs. Tokens and file
content are never logged. Toggle with `KAI_LOG_TOOLCALLS`.
```

In the **Stack** list, update the no-DB line to note git, e.g. add: "Per-tenant git history via the system `git` (subprocess) — still no DB, no embeddings."

- [ ] **Step 5: Verify the Dockerfile git install (from Task 1) is present**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && grep -n "install -y --no-install-recommends git" Dockerfile`
Expected: one match. (If missing, add the `apt-get` block from Task 1 Step 2.)

- [ ] **Step 6: Full suite + lint, green**

Run: `cd ~/Documents/GitHub/kai-mcp-empresa && uv run pytest -q && uv run ruff check .`
Expected: all tests PASS (56 original + new git/concurrency/search/obs + e2e additions), ruff clean.

- [ ] **Step 7: Commit**

```bash
cd ~/Documents/GitHub/kai-mcp-empresa
git add tool_catalog.snapshot.json README.md .env.example Dockerfile
git commit -m "docs(v0.2): catalog snapshot + README/env for audit, search, obs

Regenerate the tool-catalog snapshot (9 tools: +kai_history, +kai_revert,
updated kai_search), document the per-tenant git history, concurrency
invariant, and structured logging; add the new env settings.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage:**
- History + audit (kai_history/kai_revert, per-tenant git, attributed, exportable) → Tasks 1, 3. ✓
- Concurrency (per-tenant asyncio lock, RMW+commit atomic) → Task 2. ✓
- Search (multi-term AND, frontmatter/title/tags weighting, score, sorted) → Task 4. ✓
- Observability (structured stdout JSON, no token/content, toggle) → Task 5. ✓
- Config additions (git_enabled, git_timeout_s, log_toolcalls, HISTORY_DEFAULT_LIMIT) → Task 1 Step 1. ✓
- Error handling (write-then-commit ordering, GitError sanitized, revert unknown commit, search never raises) → Tasks 1/3/4. ✓
- Maintenance (snapshot, README, .env.example, Dockerfile git) → Tasks 1 + 6. ✓
- Tests per axis → Tasks 1–5; e2e roundtrip → Task 3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type consistency:** `CommitContext(user, role, tool)` defined in Task 1, used identically in Tasks 2/3. `commit_change(...) -> str | None`, `history(...) -> list[dict]` with keys `{sha, author, date_relative, date_iso, message}` consumed unchanged by `file_history`/tools. `revert_file(...) -> {path, reverted_to, sha}` matches the e2e assertions. `search(...)` hit shape `{path, score, matches}` matches `test_search.py`. ✓

**Note for the implementer:** `HISTORY_DEFAULT_LIMIT` is defined for documentation/clarity; the `kai_history` tool hardcodes the default `limit=20` in its signature (FastMCP reads the literal default into the schema — a module constant can't be the schema default). Keep both at 20.
