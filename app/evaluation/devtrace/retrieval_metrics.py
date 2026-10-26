"""Retrieval metrics for DevTrace. Pure functions, no I/O. Definitions (see docs/EVALUATION.md):

Inputs are RANKED lists of keys (best first). A key is whatever granularity the dataset labels: a chunk's `source_chunk_id`
(strict) or its `document_id` (lenient: any section of the right page counts). Pick one per run and record it.

* Duplicate keys are collapsed to their first (best-ranked) occurrence, so several chunks of one page cannot inflate a
  document-level score or push precision above 1.
* Cases with NO relevant labels (unanswerable / abstention cases) return None for every ranking metric: "not applicable" is
  not the same as 0, and averaging a 0 in would punish a system for correctly finding nothing. Use `mean_defined`.
* precision@k divides by k even when fewer than k results exist (missing results are not free precision).
* These measure whether the labelled source was RETRIEVED, not whether an answer is correct or well-cited.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from app.devtrace.versions import in_range


def collapse(ranked: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ranked))


def hit_at_k(relevant: set[str], ranked: Sequence[str], k: int) -> float | None:
    _check_k(k)
    return None if not relevant else float(bool(relevant & set(collapse(ranked)[:k])))


def recall_at_k(relevant: set[str], ranked: Sequence[str], k: int) -> float | None:
    """Fraction of the relevant keys found in the top k."""
    _check_k(k)
    return None if not relevant else len(relevant & set(collapse(ranked)[:k])) / len(relevant)


def precision_at_k(relevant: set[str], ranked: Sequence[str], k: int) -> float | None:
    """Relevant keys in the top k, divided by k."""
    _check_k(k)
    return None if not relevant else len(relevant & set(collapse(ranked)[:k])) / k


def reciprocal_rank(relevant: set[str], ranked: Sequence[str], k: int | None = None) -> float | None:
    """1/rank of the first relevant key (0 if none within k). Mean over cases = MRR."""
    if not relevant:
        return None
    for i, key in enumerate(collapse(ranked)[:k] if k else collapse(ranked), 1):
        if key in relevant:
            return 1.0 / i
    return 0.0


def source_coverage(required: set[str], ranked: Sequence[str], k: int) -> float | None:
    """Multi-source cases: fraction of the REQUIRED sources present in the top k (same as recall; named for the report)."""
    return recall_at_k(required, ranked, k)


def all_required_found(required: set[str], ranked: Sequence[str], k: int) -> float | None:
    """1.0 only if EVERY required source is in the top k (what a multi-document answer actually needs)."""
    _check_k(k)
    return None if not required else float(required <= set(collapse(ranked)[:k]))


def version_correctness(items: Iterable[dict], library: str, gt: str | None, lte: str | None) -> float | None:
    """Of the retrieved RELEASE-NOTE items, the fraction that belong to `library` and lie in (gt, lte]. None if there were none.

    Items are dicts with library, library_version, version_kind (as in EvidenceItem)."""
    rel = [i for i in items if i.get("version_kind") == "introduced_in"]
    if not rel:
        return None
    ok = sum(1 for i in rel if i.get("library") == library and i.get("library_version") and in_range(i["library_version"], gt, lte))
    return ok / len(rel)


def mean_defined(values: Iterable[float | None]) -> tuple[float | None, int]:
    """(mean over applicable cases, number of applicable cases). Always report both."""
    v = [x for x in values if x is not None]
    return (sum(v) / len(v) if v else None), len(v)


def _check_k(k: int) -> None:
    if k < 1:
        raise ValueError("k must be >= 1")
