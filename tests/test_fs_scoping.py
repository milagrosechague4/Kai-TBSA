"""The core security guard: no tool can touch a path outside its tenant root.

This is the file-based analog of cross-tenant escape. resolve_within is the one
gate, so we hammer it directly with traversal, absolute paths, and symlink escapes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from conftest import make_settings

from kai_mcp_empresa import fs
from kai_mcp_empresa.fs import PathError


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "koi"
    root.mkdir()
    return root


def test_resolve_plain_path_stays_inside(tmp_path):
    root = _root(tmp_path)
    p = fs.resolve_within(root, "reuniones/2026-06-17.md")
    assert root in p.parents


def test_resolve_empty_is_root(tmp_path):
    root = _root(tmp_path)
    assert fs.resolve_within(root, "") == root.resolve()
    assert fs.resolve_within(root, ".") == root.resolve()


@pytest.mark.parametrize(
    "bad",
    [
        "../secrets.md",
        "../../etc/passwd",
        "reuniones/../../escape.md",
    ],
)
def test_resolve_rejects_traversal(tmp_path, bad):
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.resolve_within(root, bad)


@pytest.mark.parametrize("abs_path", ["/etc/passwd", "/tmp/abs.md", "/todos.md"])
def test_leading_slash_is_root_relative_and_confined(tmp_path, abs_path):
    # A leading slash is sugar for "from the company root" — it never escapes.
    root = _root(tmp_path)
    resolved = fs.resolve_within(root, abs_path)
    assert resolved == root or root in resolved.parents


def test_symlink_escape_is_blocked(tmp_path):
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "loot.md").write_text("secret", encoding="utf-8")
    # Plant a symlink inside the root pointing out.
    (root / "link").symlink_to(outside)
    with pytest.raises(PathError):
        fs.resolve_within(root, "link/loot.md")


def test_write_then_read_roundtrip(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "todos.md", "- [ ] cargar la llamada\n")
    out = fs.read_file(settings, root, "todos.md")
    assert "cargar la llamada" in out["content"]
    assert out["path"] == "todos.md"


def test_append_mode_accumulates(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "log.md", "uno\n")
    fs.write_file(settings, root, "log.md", "dos\n", mode="append")
    assert fs.read_file(settings, root, "log.md")["content"] == "uno\ndos\n"


def test_disallowed_suffix_rejected(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.write_file(settings, root, "evil.sh", "rm -rf /")


def test_size_limit_enforced(tmp_path):
    settings = make_settings(max_file_bytes=10)
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.write_file(settings, root, "big.md", "x" * 11)


def test_delete_file_removes_it(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "drafts/stale.md", "viejo\n")
    res = fs.delete_file(settings, root, "drafts/stale.md")
    assert res == {"path": "drafts/stale.md", "deleted": True, "type": "file"}
    with pytest.raises(PathError):
        fs.read_file(settings, root, "drafts/stale.md")


def test_delete_empty_folder_ok_nonempty_refused(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "box/keep.md", "x\n")
    # non-empty folder is refused
    with pytest.raises(PathError):
        fs.delete_file(settings, root, "box")
    # after removing the file, the now-empty folder can go
    fs.delete_file(settings, root, "box/keep.md")
    assert fs.delete_file(settings, root, "box")["type"] == "dir"


def test_delete_missing_path_errors(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.delete_file(settings, root, "nope.md")


def test_delete_refuses_root(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.delete_file(settings, root, ".")


def test_delete_rejects_traversal(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.delete_file(settings, root, "../outside.md")


def test_edit_replaces_unique_substring(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "loops.md", "- [ ] uno\n- [ ] dos\n- [ ] tres\n")
    res = fs.edit_file(settings, root, "loops.md", "- [ ] dos", "- [x] dos")
    assert res == {"path": "loops.md", "replacements": 1, "bytes": res["bytes"]}
    # Only the targeted row changed; the siblings are untouched.
    assert fs.read_file(settings, root, "loops.md")["content"] == (
        "- [ ] uno\n- [x] dos\n- [ ] tres\n"
    )


def test_edit_missing_old_string_errors(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "hola\n")
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "f.md", "chau", "adios")


def test_edit_non_unique_without_replace_all_errors(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "x\nx\nx\n")
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "f.md", "x", "y")
    # File is left untouched when the edit is refused.
    assert fs.read_file(settings, root, "f.md")["content"] == "x\nx\nx\n"


def test_edit_replace_all(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "x\nx\nx\n")
    res = fs.edit_file(settings, root, "f.md", "x", "y", replace_all=True)
    assert res["replacements"] == 3
    assert fs.read_file(settings, root, "f.md")["content"] == "y\ny\ny\n"


def test_edit_identical_strings_refused(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "hola\n")
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "f.md", "hola", "hola")


def test_edit_empty_old_string_refused(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "hola\n")
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "f.md", "", "algo")


def test_edit_missing_file_errors(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "nope.md", "a", "b")


def test_edit_rejects_traversal(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "../outside.md", "a", "b")


def test_edit_enforces_size_limit(tmp_path):
    settings = make_settings(max_file_bytes=10)
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "ab\n")
    with pytest.raises(PathError):
        fs.edit_file(settings, root, "f.md", "ab", "a" * 20)


def test_read_offset_limit_window(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "big.md", "L1\nL2\nL3\nL4\nL5\n")

    full = fs.read_file(settings, root, "big.md")
    assert full["total_lines"] == 5
    assert full["truncated"] is False
    assert full["start_line"] == 1 and full["end_line"] == 5

    window = fs.read_file(settings, root, "big.md", offset=2, limit=2)
    assert window["content"] == "L2\nL3\n"
    assert window["start_line"] == 2 and window["end_line"] == 3
    assert window["total_lines"] == 5
    assert window["truncated"] is True


def test_read_rejects_negative_offset_or_limit(tmp_path):
    # A negative arg must fail loud, not silently fall back to "the whole file".
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "a\nb\nc\n")
    with pytest.raises(PathError):
        fs.read_file(settings, root, "f.md", offset=-1)
    with pytest.raises(PathError):
        fs.read_file(settings, root, "f.md", limit=-2)


def test_sweep_removes_only_stale_temps(tmp_path):
    root = _root(tmp_path)
    stale = root / ".loops.md.AAAA.tmp"
    fresh = root / ".loops.md.BBBB.tmp"
    stale.write_text("x", encoding="utf-8")
    fresh.write_text("y", encoding="utf-8")
    old = time.time() - 7200  # 2h ago, past the 1h cutoff
    os.utime(stale, (old, old))

    removed = fs.sweep_stale_temps(tmp_path)
    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()  # in-flight (recent) temp is left alone


def test_read_offset_past_eof_is_empty(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "f.md", "una\nlinea\n")
    out = fs.read_file(settings, root, "f.md", offset=99)
    assert out["content"] == ""
    assert out["start_line"] == 0 and out["end_line"] == 0
    assert out["total_lines"] == 2
    assert out["truncated"] is True


def test_list_and_search(tmp_path):
    settings = make_settings()
    root = _root(tmp_path)
    fs.write_file(settings, root, "todos.md", "- responsable: Mati\n")
    fs.write_file(settings, root, "reuniones/kickoff.md", "arrancamos el MCP\n")

    listing = fs.list_tree(settings, root, ".")
    names = {e["name"] for e in listing["entries"]}
    assert {"todos.md", "reuniones"} <= names

    hits = fs.search(settings, root, "mati")
    assert any(h["path"] == "todos.md" for h in hits)
    assert hits[0]["matches"][0]["line"] == 1
