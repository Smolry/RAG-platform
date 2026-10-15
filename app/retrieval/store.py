"""Qdrant wrapper. Owns collection setup, payload indexes, filtered search, ACL updates."""
from __future__ import annotations

from qdrant_client import QdrantClient, models

from app.core.models import ChunkRecord, RetrievedChunk

KEYWORD_INDEXES = ["tenant_id", "document_id", "allowed_roles", "allowed_users", "classification", "corpus_version"]


def make_client(url: str) -> QdrantClient:
    return QdrantClient(location=":memory:") if url == ":memory:" else QdrantClient(url=url)


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str):
        self.client = client
        self.collection = collection

    def ensure_collection(self, dim: int) -> None:
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
        for field in KEYWORD_INDEXES:  # PDF: payload indexes on tenant_id / allowed_roles keep filtering fast
            self.client.create_payload_index(
                self.collection, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
            )

    def upsert(self, records: list[ChunkRecord], vectors: list[list[float]]) -> None:
        points = [
            models.PointStruct(id=r.chunk_id, vector=v, payload=r.model_dump())
            for r, v in zip(records, vectors, strict=True)
        ]
        self.client.upsert(self.collection, points=points, wait=True)

    def search(self, vector: list[float], limit: int, query_filter: models.Filter | None = None) -> list[RetrievedChunk]:
        res = self.client.query_points(
            self.collection, query=vector, query_filter=query_filter, limit=limit, with_payload=True
        )
        return [RetrievedChunk(chunk_id=str(p.id), score=float(p.score), payload=p.payload or {}) for p in res.points]

    def _doc_filter(self, document_id: str, tenant_id: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)),
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            ]
        )

    def count_document_chunks(self, document_id: str, tenant_id: str) -> int:
        return self.client.count(self.collection, count_filter=self._doc_filter(document_id, tenant_id), exact=True).count

    def update_acl(
        self,
        document_id: str,
        tenant_id: str,
        allowed_roles: list[str],
        allowed_users: list[str],
        classification: str | None = None,
    ) -> int:
        """Rewrite ACL payload on EVERY chunk of the document (scoped to the caller's tenant). Returns #chunks."""
        n = self.count_document_chunks(document_id, tenant_id)
        if n == 0:
            return 0
        payload: dict = {"allowed_roles": allowed_roles, "allowed_users": allowed_users}
        if classification:
            payload["classification"] = classification
        self.client.set_payload(
            self.collection, payload=payload, points=self._doc_filter(document_id, tenant_id), wait=True
        )
        return n
