"""Filesystem operations scoped to a single tenant root.

Every path a tool receives is untrusted. `resolve_within` is the one gate: it
rejects absolute paths, parent traversal, and symlink escapes, guaranteeing the
returned path lives inside the tenant root. No tool touches the filesystem
without going through here.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from fastmcp.exceptions import ToolError

from . import git
from .config import Settings


class PathError(ToolError):
    """The requested path is outside the tenant root or otherwise not allowed."""


def resolve_within(root: Path, relpath: str) -> Path:
    """Resolve `relpath` against `root`, guaranteeing the result stays inside it.

    A leading "/" is treated as sugar for "from the company root" (agents often
    pass "/todos.md"). `..` traversal is rejected, and the fully resolved real
    path (symlinks included) is confirmed to be the root or below — so even an
    absolute-looking input can never escape the tenant folder.
    """
    root = root.resolve()
    rel = (relpath or "").strip().lstrip("/")
    if not rel or rel == ".":
        return root
    candidate = Path(rel)
    if candidate.is_absolute():
        raise PathError(f"absolute paths are not allowed: {relpath!r}")
    if ".." in candidate.parts:
        raise PathError(f"path traversal is not allowed: {relpath!r}")

    resolved = (root / candidate).resolve()
    # The resolved path (after following any symlinks) must be the root or below.
    if resolved != root and root not in resolved.parents:
        raise PathError(f"path escapes the tenant root: {relpath!r}")
    return resolved


def _check_suffix(settings: Settings, path: Path) -> None:
    if path.suffix.lower() not in settings.allowed_suffixes:
        raise PathError(
            f"file type {path.suffix or '(none)'!r} not allowed; "
            f"allowed: {', '.join(settings.allowed_suffixes)}"
        )


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace `path`'s contents in one atomic step.

    Write to a temp file in the same directory, then os.replace — a concurrent
    reader (or a crash mid-write) never sees a half-written file, and a failed
    edit leaves the original untouched. The temp lives in the same dir so the
    replace stays on one filesystem (a cross-device rename is not atomic). The
    dot-prefixed temp name is also hidden from list/search, which skip dotfiles.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


def sweep_stale_temps(data_root: Path, max_age_seconds: float = 3600) -> int:
    """Reap orphaned atomic-write temp files left by a hard crash.

    `_atomic_write_text` only unlinks its temp on a Python-level exception — a
    SIGKILL/OOM/container redeploy in the window between mkstemp and os.replace
    strands a `.<name>.<rand>.tmp` file. Those are invisible (list/search skip
    dotfiles) and never reaped, so they'd accumulate on the volume forever. The
    server reboots often (Railway restart policy), so a startup sweep keeps the
    brain clean. Only our own pattern, older than `max_age_seconds`, is removed —
    an in-flight write is never touched. Best-effort: errors are swallowed.
    """
    if not data_root.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for tmp in data_root.rglob(".*.tmp"):
        try:
            if tmp.is_file() and tmp.stat().st_mtime < cutoff:
                tmp.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def read_file(
    settings: Settings, root: Path, relpath: str, offset: int = 0, limit: int = 0
) -> dict:
    """Read a file, optionally a line-addressed window of it.

    `offset` is the 1-based line to start at (0 or 1 = from the top); `limit`
    caps how many lines come back (0 = to the end). Both must be >= 0 — negative
    indices (Python-slice "from the end") are rejected rather than silently
    treated as "the whole file", which would defeat the point of windowing. The
    result always carries `total_lines` plus the `start_line`/`end_line` actually
    returned, so a caller can page through a long transcript or ledger — or locate
    an anchor for an edit — without ever loading the whole file into context.
    """
    if offset < 0 or limit < 0:
        raise PathError(
            f"offset and limit must be >= 0 (offset is 1-based, 0 = from the top); "
            f"got offset={offset}, limit={limit}"
        )
    path = resolve_within(root, relpath)
    if not path.exists():
        raise PathError(f"file not found: {relpath!r}")
    if path.is_dir():
        raise PathError(f"{relpath!r} is a directory; use kai_list")
    _check_suffix(settings, path)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    total = len(lines)
    start = max(0, offset - 1) if offset > 0 else 0
    end = total if limit <= 0 else min(total, start + limit)
    window = lines[start:end] if start < total else []
    content = "".join(window)

    return {
        "path": str(path.relative_to(root)),
        "bytes": len(content.encode("utf-8")),
        "content": content,
        "total_lines": total,
        "start_line": start + 1 if window else 0,
        "end_line": start + len(window) if window else 0,
        "truncated": len(window) < total,
    }


def write_file(
    settings: Settings,
    root: Path,
    relpath: str,
    content: str,
    mode: str = "overwrite",
    commit: "git.CommitContext | None" = None,
) -> dict:
    if mode not in ("overwrite", "append"):
        raise PathError(f"mode must be 'overwrite' or 'append', got {mode!r}")
    path = resolve_within(root, relpath)
    if path == root or path.is_dir():
        raise PathError(f"{relpath!r} is not a writable file path")
    _check_suffix(settings, path)

    new_bytes = len(content.encode("utf-8"))
    existing = 0
    if mode == "append" and path.exists():
        existing = path.stat().st_size
    if existing + new_bytes > settings.max_file_bytes:
        raise PathError(
            f"file would exceed the {settings.max_file_bytes}-byte limit "
            f"({existing + new_bytes} bytes)"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if mode == "append":
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content)
    else:
        _atomic_write_text(path, content)

    rel = str(path.relative_to(root))
    _maybe_commit(settings, root, rel, commit)
    return {
        "path": rel,
        "mode": mode,
        "created": created,
        "bytes": path.stat().st_size,
    }


def edit_file(
    settings: Settings,
    root: Path,
    relpath: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    commit: "git.CommitContext | None" = None,
) -> dict:
    """Replace an exact substring in an existing file, in place.

    The targeted-edit alternative to overwriting the whole file: only the bytes
    matching `old_string` change, so concurrent edits to *different* parts of the
    same file (two people closing different loops in loops-abiertos.md) don't
    clobber each other the way a full overwrite would. `old_string` must occur
    exactly once unless `replace_all` is set. The replace + write is atomic.
    """
    if old_string == new_string:
        raise PathError("old_string and new_string are identical; nothing to change")
    if old_string == "":
        raise PathError("old_string is empty; use kai_write to create or replace a file")

    path = resolve_within(root, relpath)
    if not path.exists():
        raise PathError(f"file not found: {relpath!r}")
    if path.is_dir():
        raise PathError(f"{relpath!r} is a directory; use kai_list")
    _check_suffix(settings, path)

    text = path.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise PathError(f"old_string not found in {relpath!r}")
    if count > 1 and not replace_all:
        raise PathError(
            f"old_string is not unique in {relpath!r} ({count} matches); add "
            f"surrounding context to make it unique, or pass replace_all=true"
        )

    new_text = (
        text.replace(old_string, new_string)
        if replace_all
        else text.replace(old_string, new_string, 1)
    )
    new_bytes = len(new_text.encode("utf-8"))
    if new_bytes > settings.max_file_bytes:
        raise PathError(
            f"edit would exceed the {settings.max_file_bytes}-byte limit "
            f"({new_bytes} bytes)"
        )

    _atomic_write_text(path, new_text)
    _maybe_commit(settings, root, str(path.relative_to(root)), commit)
    return {
        "path": str(path.relative_to(root)),
        "replacements": count if replace_all else 1,
        "bytes": new_bytes,
    }


def delete_file(
    settings: Settings,
    root: Path,
    relpath: str,
    commit: "git.CommitContext | None" = None,
) -> dict:
    """Delete a single file, or an empty folder. Goes through the same path gate.

    Non-empty folders are refused (no recursive delete — delete the files first).
    Irreversible.
    """
    path = resolve_within(root, relpath)
    if path == root:
        raise PathError("refusing to delete the company root")
    if not path.exists():
        raise PathError(f"path not found: {relpath!r}")
    rel = str(path.relative_to(root))
    if path.is_dir():
        try:
            path.rmdir()  # succeeds only if empty
        except OSError as exc:
            raise PathError(
                f"{relpath!r} is a non-empty folder; delete its files first"
            ) from exc
        return {"path": rel, "deleted": True, "type": "dir"}
    path.unlink()
    _maybe_commit(settings, root, rel, commit)
    return {"path": rel, "deleted": True, "type": "file"}


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
        raise PathError(
            f"reverted content would exceed the {settings.max_file_bytes}-byte limit"
        )
    _atomic_write_text(path, content)
    sha = git.commit_change(root, rel, ctx, timeout=settings.git_timeout_s)
    return {"path": rel, "reverted_to": commit_sha, "sha": sha}


def list_tree(settings: Settings, root: Path, folder: str = ".") -> dict:
    base = resolve_within(root, folder)
    if not base.exists():
        raise PathError(f"folder not found: {folder!r}")
    if base.is_file():
        raise PathError(f"{folder!r} is a file; use kai_read")

    entries: list[dict] = []
    for child in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        entries.append(
            {
                "name": child.name,
                "path": str(child.relative_to(root)),
                "type": "dir" if is_dir else "file",
                "bytes": None if is_dir else child.stat().st_size,
            }
        )
    return {"folder": str(base.relative_to(root)) or ".", "entries": entries}


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
