"""Tenant- and permission-aware semantic cache (Project 1), stored in Qdrant.

SECURITY MODEL - an entry is reusable only if ALL hold:
  1. same tenant_id                         (hard `must` filter)
  2. same permission_context                (hash of tenant + exact role set; adds user_id if the answer used a chunk
                                             visible to the user ONLY through a per-user grant)
  3. corpus_version == tenant's CURRENT version   (re-ingestion invalidates)
  4. not expired (TTL)
  5. similarity >= threshold
Plus (in RAGService): cached source chunks are re-checked against the LIVE ACL on every hit, and any ACL change purges
the tenant's cache. Never cached: errors / empty / "I don't know" / source-less answers.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from qdrant_client import QdrantClient, models

from app.security.auth import Principal

_NON_WORD = re.compile(r"[^\w\s]")


def normalize_query(q: str) -> str:
    return " ".join(_NON_WORD.sub(" ", q.lower()).split())


def permission_context(p: Principal, user_specific: bool = False) -> str:
    base = f"{p.tenant_id}|{','.join(sorted(set(p.roles)))}"
    if user_specific:
        base += f"|user:{p.user_id}"
    return hashlib.sha256(base.encode()).hexdigest()[:24]


@dataclass
class CacheHit:
    point_id: str
    score: float
    payload: dict


def is_cacheable(answer: str, model: str, sources: list, no_answer_text: str) -> bool:
    return bool(answer and answer.strip() and answer.strip() != no_answer_text and model != "none" and sources)


class SemanticCache:
    def __init__(self, client: QdrantClient, collection: str, threshold: float, ttl_seconds: int,
                 clock: Callable[[], float] = time.time):
        self.client, self.collection, self.threshold, self.ttl, self.clock = client, collection, threshold, ttl_seconds, clock

    def ensure(self, dim: int) -> None:
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE))
        for f in ("tenant_id", "permission_context", "corpus_version"):
            self.client.create_payload_index(self.collection, field_name=f, field_schema=models.PayloadSchemaType.KEYWORD)

    def lookup(self, vec: list[float], p: Principal, corpus_version: str) -> CacheHit | None:
        flt = models.Filter(
            must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=p.tenant_id)),
                  models.FieldCondition(key="corpus_version", match=models.MatchValue(value=corpus_version)),
                  models.FieldCondition(key="expires_at", range=models.Range(gt=self.clock()))],
            should=[models.FieldCondition(key="permission_context", match=models.MatchAny(
                any=[permission_context(p, False), permission_context(p, True)]))])
        res = self.client.query_points(self.collection, query=vec, query_filter=flt, limit=1, with_payload=True)
        if not res.points or res.points[0].score < self.threshold:
            return None
        pt = res.points[0]
        return CacheHit(str(pt.id), float(pt.score), pt.payload or {})

    def store(self, vec: list[float], p: Principal, corpus_version: str, normalized_query: str, answer: str,
              sources: list[dict], citations: list[dict], user_specific: bool, model: str,
              route: str | None = None) -> str:
        now, pid = self.clock(), str(uuid.uuid4())
        payload = {"normalized_query": normalized_query, "answer": answer, "sources": sources, "citations": citations,
                   "timestamp": now, "expires_at": now + self.ttl, "corpus_version": corpus_version,
                   "tenant_id": p.tenant_id, "permission_context": permission_context(p, user_specific),
                   "user_specific": user_specific, "model": model, "route": route}
        self.client.upsert(self.collection, points=[models.PointStruct(id=pid, vector=vec, payload=payload)], wait=True)
        return pid

    def delete(self, point_id: str) -> None:
        self.client.delete(self.collection, points_selector=models.PointIdsList(points=[point_id]), wait=True)

    def invalidate_tenant(self, tenant_id: str) -> None:
        self.client.delete(self.collection, wait=True, points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))])))

    def clear(self) -> None:
        self.client.delete(self.collection, wait=True, points_selector=models.FilterSelector(filter=models.Filter()))

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count
