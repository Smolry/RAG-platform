"""Load a built snapshot (chunks.jsonl) into the vector store under a tenant with server-chosen ACLs.

The documentation is public, but this platform authorises per tenant, so the public corpus is ingested INTO a tenant
(default "devtrace") with `allowed_roles`. ACLs come from this call's arguments, never from the chunk file.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from qdrant_client import models as qm

from app.core.models import ChunkRecord
from app.devtrace.versions import version_key
from app.retrieval.embedder import Embedder
from app.retrieval.store import VectorStore


def load_chunks(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def ingest_snapshot(chunks: list[dict], snapshot_id: str, store: VectorStore, embedder: Embedder, *, tenant_id: str = "devtrace",
                    allowed_roles: list[str] | None = None, db=None, batch_size: int = 64, replace: bool = True) -> int:
    if not chunks:
        raise ValueError("refusing to ingest an empty snapshot")
    roles = allowed_roles if allowed_roles is not None else ["user", "admin"]
    # Fields the SERVER owns. Anything of these names inside the chunk file is ignored, never trusted.
    server_owned = {"version_key", "tenant_id", "classification", "allowed_roles", "allowed_users", "corpus_version", "chunk_id", "source_chunk_id"}
    known = set(ChunkRecord.model_fields) - server_owned
    records = []
    for c in chunks:
        if c.get("snapshot_id") != snapshot_id:
            raise ValueError("chunk does not belong to the given snapshot")
        d = {k: v for k, v in c.items() if k in known}      # drops build-only fields such as `oversize`
        # Qdrant point ids are global to the collection: scope them by tenant so two tenants can hold the same public docs
        # without overwriting each other (the build-time id stays in `source_chunk_id`).
        scoped = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{c['chunk_id']}"))
        vk = version_key(c["library_version"]) if c.get("version_kind") == "introduced_in" and c.get("library_version") else None
        records.append(ChunkRecord(**d, version_key=vk, chunk_id=scoped, source_chunk_id=c["chunk_id"], tenant_id=tenant_id,
                                   classification="public", allowed_roles=roles, allowed_users=[], corpus_version=snapshot_id))
    store.ensure_collection(embedder.dim)
    for s in range(0, len(records), batch_size):
        b = records[s:s + batch_size]
        store.upsert(b, embedder.embed([r.text for r in b]))
    if replace:   # drop this tenant's chunks from OTHER snapshots so retrieval never mixes versions of the corpus
        store.client.delete(store.collection, wait=True, points_selector=qm.FilterSelector(filter=qm.Filter(
            must=[qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id))],
            must_not=[qm.FieldCondition(key="snapshot_id", match=qm.MatchValue(value=snapshot_id))])))
    if db is not None:
        db.set_corpus_version(tenant_id, snapshot_id)
    return len(records)
