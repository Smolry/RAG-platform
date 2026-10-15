from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.core.chunking import chunk_text, make_chunk_id
from app.core.config import Settings
from app.core.models import ChunkRecord
from app.retrieval.embedder import Embedder
from app.retrieval.store import VectorStore


@dataclass
class DocumentInput:
    document_id: str
    text: str
    tenant_id: str
    classification: str = "public"
    allowed_roles: list[str] = field(default_factory=list)
    allowed_users: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    documents: int
    chunks: int
    corpus_version: str


def compute_corpus_version(docs: list[DocumentInput]) -> str:
    """Content hash of the corpus. Changes iff any document text/id changes (used by Project 1's cache)."""
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda d: d.document_id):
        h.update(d.document_id.encode())
        h.update(hashlib.sha256(d.text.encode()).digest())
    return h.hexdigest()[:12]


def ingest_documents(docs: list[DocumentInput], store: VectorStore, embedder: Embedder, settings: Settings,
                     batch_size: int = 64, db=None) -> IngestReport:
    """`corpus_version` is computed PER TENANT from that tenant's documents in this batch (a batch = the tenant's corpus
    snapshot). If `db` is given the new version is recorded, which invalidates that tenant's cached answers."""
    by_tenant: dict[str, list[DocumentInput]] = {}
    for d in docs:
        by_tenant.setdefault(d.tenant_id, []).append(d)
    versions = {t: compute_corpus_version(ds) for t, ds in by_tenant.items()}
    version = compute_corpus_version(docs)
    records: list[ChunkRecord] = []
    for d in docs:
        for i, piece in enumerate(chunk_text(d.text, settings.chunk_tokens, settings.chunk_overlap_tokens)):
            # ChunkRecord validation raises if tenant_id is blank -> an unprotected chunk can never exist.
            records.append(ChunkRecord(
                chunk_id=make_chunk_id(d.document_id, i), document_id=d.document_id, chunk_index=i, text=piece,
                tenant_id=d.tenant_id, classification=d.classification, allowed_roles=d.allowed_roles,
                allowed_users=d.allowed_users, corpus_version=versions[d.tenant_id],
            ))
    store.ensure_collection(embedder.dim)
    for s in range(0, len(records), batch_size):
        batch = records[s : s + batch_size]
        store.upsert(batch, embedder.embed([r.text for r in batch]))
    if db is not None:
        for t, v in versions.items():
            db.set_corpus_version(t, v)
    return IngestReport(documents=len(docs), chunks=len(records), corpus_version=version)
