"""Source readers. Each reader yields Documents; the pipeline does the rest.
Connectors (Notion/Drive/Slack) implement the same shape and live here later."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field


@dataclass
class Document:
    title: str
    content: str
    collection: str = "default"
    acl_tags: list[str] = field(default_factory=list)


def _title_from(text: str, path: pathlib.Path) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def read_markdown_dir(
    path: str | pathlib.Path,
    collection: str | None = None,
    acl_tags: list[str] | None = None,
) -> list[Document]:
    """Read every *.md under `path` into Documents.

    collection defaults to the file's parent folder name. acl_tags default empty
    (= visible to everyone in the tenant)."""
    root = pathlib.Path(path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"source path not found: {root}")

    docs: list[Document] = []
    for f in sorted(root.rglob("*.md")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        docs.append(
            Document(
                title=_title_from(text, f),
                content=text,
                collection=collection or (f.parent.name if f.parent != root else "default"),
                acl_tags=list(acl_tags or []),
            )
        )
    return docs
