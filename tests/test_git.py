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
