from __future__ import annotations

from typing import Protocol

from app.graph.extraction import fold


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder (PDF: rerank merged vector+graph candidates with a cross-encoder)."""

    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder  # lazy: heavy import
        self.model = CrossEncoder(model_name)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(x) for x in self.model.predict([(query, t) for t in texts], show_progress_bar=False)]


_STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "is", "was", "were", "are", "and", "or", "for", "by", "with", "where",
         "what", "which", "who", "when", "how", "did", "does", "do", "that", "this", "it", "as", "from"}


class LexicalReranker:
    """Stopword-filtered token-overlap scorer. TEST/OFFLINE ONLY - not a cross-encoder, never use for reported results."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        q = set(fold(query).split()) - _STOP
        out = []
        for t in texts:
            toks = fold(t).split()
            out.append(len(q & (set(toks) - _STOP)) / (len(toks) ** 0.5 or 1.0))
        return out


def build_reranker(settings) -> Reranker:
    return LexicalReranker() if settings.reranker_backend == "lexical" else CrossEncoderReranker(settings.rerank_model)
