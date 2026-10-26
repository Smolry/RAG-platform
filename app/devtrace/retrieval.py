"""Version- and source-aware retrieval for DevTrace (Arm B). Same security contract as the baseline:

  * the authorization filter (tenant + roles) is built from the SERVER-verified Principal, never from tool arguments;
    extra conditions (library, source type, version range) can only be ADDED to `must`, and a guard asserts the tenant
    condition survived;
  * every hit is re-checked in application code (`is_authorized`), fail-closed, and violations are audited;
  * the semantic cache is NOT used (no demonstrated safe threshold; see docs/SECURITY.md S5);
  * audit rows store a hash + length of the query, not its text (stack traces/code may contain secrets; S4).
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

from qdrant_client import models as qm

from app.core.models import RetrievedChunk
from app.devtrace.schemas import EvidenceItem
from app.devtrace.versions import version_key
from app.retrieval.embedder import Embedder
from app.retrieval.store import VectorStore
from app.security.acl import acl_filter, is_authorized
from app.security.auth import Principal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalConfig:
    candidate_k: int = 20          # fetched from the vector index before reranking
    top_k: int = 5
    rerank: bool = True            # ablation switch (Phase 4)
    use_filters: bool = True       # ablation switch: False ignores library/source/version filters
    max_text_chars: int = 1500


@dataclass(frozen=True)
class SearchFilters:
    library: str | None = None
    source_types: tuple[str, ...] | None = None
    version_gt: str | None = None      # exclusive
    version_lte: str | None = None     # inclusive

    def describe(self) -> dict:
        return {k: v for k, v in {"library": self.library, "source_types": list(self.source_types) if self.source_types else None,
                                  "version_range": self._range()}.items() if v}

    def _range(self) -> str | None:
        if not (self.version_gt or self.version_lte):
            return None
        return f"({self.version_gt or '-inf'}, {self.version_lte or '+inf'}]"


def build_filter(principal: Principal, f: SearchFilters | None) -> qm.Filter:
    base = acl_filter(principal)
    must = list(base.must or [])
    if f:
        if f.library:
            must.append(qm.FieldCondition(key="library", match=qm.MatchValue(value=f.library)))
        if f.source_types:
            must.append(qm.FieldCondition(key="source_type", match=qm.MatchAny(any=list(f.source_types))))
        if f.version_gt or f.version_lte:
            must.append(qm.FieldCondition(key="version_key", range=qm.Range(
                gt=version_key(f.version_gt) if f.version_gt else None,
                lte=version_key(f.version_lte) if f.version_lte else None)))
    tenant_ok = any(getattr(c, "key", None) == "tenant_id" and getattr(c.match, "value", None) == principal.tenant_id for c in must)
    if not tenant_ok or not base.should:   # defence in depth: never return a filter that lost the tenant or role condition
        raise RuntimeError("authorization filter lost its tenant/role condition")
    return qm.Filter(must=must, should=base.should)


def redact_for_audit(query: str) -> str:
    return f"[redacted sha256={hashlib.sha256(query.encode()).hexdigest()[:16]} len={len(query)}]"


def caveat_for(item: EvidenceItem, library: str | None, user_version: str | None) -> str | None:
    """Deterministic, source-grounded version note. Never guesses: only compares the user's version with the version the
    SOURCE itself states (page's pinned release, or the release-note heading)."""
    if not user_version or not library or item.library != library or not item.library_version:
        return None
    try:
        u, v = version_key(user_version), version_key(item.library_version)
    except ValueError:
        return None
    if item.version_kind == "introduced_in":
        if v <= u:
            return f"Release note for {library} {item.library_version}: already included in your {user_version}."
        return f"Release note for {library} {item.library_version}: newer than your {user_version}; it affects you only after upgrading."
    if item.version_kind == "docs_as_of":
        if v == u:
            return None
        return (f"Page text reflects {library} {item.library_version}; your {user_version} is "
                f"{'older' if u < v else 'newer'}, so behaviour may differ.")
    return None


@dataclass
class RetrievalResult:
    items: list[EvidenceItem]
    debug: dict = field(default_factory=dict)


def to_item(c: RetrievedChunk, max_chars: int) -> EvidenceItem:
    p = c.payload
    text = p.get("text", "") or ""
    return EvidenceItem(
        chunk_id=c.chunk_id, document_id=p.get("document_id", ""), library=p.get("library"), library_version=p.get("library_version"),
        version_kind=p.get("version_kind"), published_at=p.get("published_at"), source_type=p.get("source_type"),
        source_title=p.get("source_title"), section_path=list(p.get("section_path") or []), section_anchor=p.get("section_anchor"),
        source_url=p.get("source_url"), provenance_url=p.get("provenance_url"), content_hash=p.get("content_hash"),
        snapshot_id=p.get("snapshot_id"), vector_score=c.score, score=c.score, text=text[:max_chars], truncated=len(text) > max_chars)


class DevTraceRetriever:
    def __init__(self, store: VectorStore, embedder: Embedder, reranker=None, db=None, config: RetrievalConfig | None = None):
        self.store, self.embedder, self.reranker, self.db = store, embedder, reranker, db
        self.config = config or RetrievalConfig()

    def search(self, principal: Principal, query: str, filters: SearchFilters | None = None, *, top_k: int | None = None,
               user_version: str | None = None, request_id: str = "devtrace") -> RetrievalResult:
        cfg = self.config
        k = top_k or cfg.top_k
        flt = filters if cfg.use_filters else None
        t0 = time.perf_counter()
        vec = self.embedder.embed([query])[0]
        t1 = time.perf_counter()
        hits = self.store.search(vec, max(cfg.candidate_k, k), build_filter(principal, flt))
        t2 = time.perf_counter()
        allowed = [h for h in hits if is_authorized(principal, h.payload)]
        ok_ids = {h.chunk_id for h in allowed}
        dropped = [h for h in hits if h.chunk_id not in ok_ids]
        if dropped:
            log.error("ACL VIOLATION in devtrace retrieval: %d chunk(s) failed 2nd check request=%s", len(dropped), request_id)
            self._audit(request_id, principal, "acl_violation", query, dropped)
        seen: set[str] = set()
        uniq = []
        for h in allowed:                      # identical passages (same content hash) add no information
            key = h.payload.get("content_hash") or h.chunk_id
            if key not in seen:
                seen.add(key)
                uniq.append(h)
        order = uniq
        rerank_ms = 0.0
        if cfg.rerank and self.reranker is not None and uniq:
            tr = time.perf_counter()
            scores = self.reranker.score(query, [h.text for h in uniq])
            order = [h for h, _ in sorted(zip(uniq, scores), key=lambda x: -x[1])]
            rerank = {h.chunk_id: s for h, s in zip(uniq, scores)}
            rerank_ms = (time.perf_counter() - tr) * 1000
        items = []
        for h in order[:k]:
            it = to_item(h, cfg.max_text_chars)
            if cfg.rerank and self.reranker is not None:
                it.score = float(rerank[h.chunk_id])
            it.version_caveat = caveat_for(it, flt.library if flt else None, user_version)
            items.append(it)
        self._audit(request_id, principal, "retrieval", query, [h for h in order[:k]])
        return RetrievalResult(items, {"filters": flt.describe() if flt else {}, "n_candidates": len(hits), "n_dropped_by_acl": len(dropped),
                                       "n_after_dedupe": len(uniq), "embed_ms": (t1 - t0) * 1000, "search_ms": (t2 - t1) * 1000,
                                       "rerank_ms": rerank_ms, "reranked": bool(cfg.rerank and self.reranker is not None)})

    def fetch(self, principal: Principal, chunk_ids: list[str]) -> list[RetrievedChunk]:
        """Re-read chunks from the store by id and re-authorize against the CURRENT ACL (revocation applies mid-run)."""
        pts = self.store.client.retrieve(self.store.collection, chunk_ids, with_payload=True) if chunk_ids else []
        return [RetrievedChunk(chunk_id=str(p.id), score=0.0, payload=p.payload or {}) for p in pts
                if is_authorized(principal, p.payload or {})]

    def _audit(self, rid: str, principal: Principal, event: str, query: str, hits) -> None:
        if self.db is None:
            return
        self.db.audit(request_id=rid, event_type=event, user_id=principal.user_id, tenant_id=principal.tenant_id,
                      query=redact_for_audit(query), chunk_ids=[h.chunk_id for h in hits],
                      document_ids=[h.document_id for h in hits], model="devtrace-retrieval")
