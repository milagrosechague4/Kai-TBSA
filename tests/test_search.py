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


def test_frontmatter_hit_not_double_counted_as_body(tmp_path):
    # A term only in the frontmatter scores as a title hit (5), NOT title + body.
    settings = make_settings()
    root = _root(tmp_path)
    (root / "doc.md").write_text(
        "---\ntitle: Pricing\n---\nunrelated body\n", encoding="utf-8"
    )
    hits = fs.search(settings, root, "pricing", 20)
    assert hits[0]["path"] == "doc.md"
    assert hits[0]["score"] == 5  # _W_TITLE only — frontmatter not re-counted as body
