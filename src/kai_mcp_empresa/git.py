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
        # Name the subcommand, not a leading `-c key=val` pair, in the message.
        subcmd = next((a for a in args if not a.startswith("-") and "=" not in a), args[0])
        # stderr can name relpaths but never the absolute root or a token.
        raise GitError(f"git {subcmd} failed: {proc.stderr.strip()[:300]}")
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
