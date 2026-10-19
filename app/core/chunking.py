"""Word-window chunker with overlap.

IMPLEMENTATION DECISION (not in the PDF): the PDF says "about 500 tokens". Exact token counts
need a model-specific tokenizer, so we approximate 1 token ~= 0.75 words (common English
heuristic). `chunk_tokens` / `chunk_overlap_tokens` are config; swap in a real tokenizer later.
"""
from __future__ import annotations

import uuid

WORDS_PER_TOKEN = 0.75


def chunk_text(
    text: str,
    chunk_tokens: int = 500,
    overlap_tokens: int = 50,
    words_per_token: float = WORDS_PER_TOKEN,
) -> list[str]:
    if overlap_tokens >= chunk_tokens:
        raise ValueError("overlap must be smaller than chunk size")
    words = text.split()
    if not words:
        return []
    size = max(1, int(chunk_tokens * words_per_token))
    overlap = int(overlap_tokens * words_per_token)
    step = max(1, size - overlap)
    chunks: list[str] = []
    start = 0
    while start < len(words):
        chunks.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
        start += step
    return chunks


def make_chunk_id(document_id: str, index: int) -> str:
    """Deterministic UUID (Qdrant point ids must be UUID/int). Stable across re-ingestion."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}#{index}"))
