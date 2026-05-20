"""Paragraph-aware text chunking with overlap. Deliberately simple — good enough
for markdown notes; swap for a smarter splitter if retrieval quality needs it."""

from __future__ import annotations


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            step = max(1, max_chars - overlap)
            for i in range(0, len(para), step):
                chunks.append(para[i : i + max_chars])
            continue

        if not current:
            current = para
        elif len(current) + len(para) + 2 <= max_chars:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = (tail + "\n\n" + para) if tail else para

    if current:
        chunks.append(current)
    return chunks
