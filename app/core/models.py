from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Classification = Literal["public", "internal", "confidential"]


class ChunkRecord(BaseModel):
    """What is stored in the vector DB payload. Ingestion CANNOT create a chunk without a tenant."""

    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    tenant_id: str
    classification: Classification
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    corpus_version: str = "unversioned"

    @field_validator("tenant_id", "document_id")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("tenant_id/document_id must be a non-blank string")
        return v.strip()


class RetrievedChunk(BaseModel):
    chunk_id: str
    score: float
    payload: dict

    @property
    def document_id(self) -> str:
        return self.payload.get("document_id", "")

    @property
    def text(self) -> str:
        return self.payload.get("text", "")
