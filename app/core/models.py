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
    # --- optional source provenance (DevTrace). All default to None so pre-existing payloads stay valid. ---
    # Never invent these: leave None when the source does not state them.
    source_chunk_id: str | None = None     # tenant-independent id from the corpus build (chunk_id is tenant-scoped)
    source_url: str | None = None          # public documentation URL (human link; "latest" docs can drift)
    provenance_url: str | None = None      # immutable raw URL at the pinned commit (exact bytes we ingested)
    source_title: str | None = None
    section_heading: str | None = None
    section_path: list[str] | None = None
    section_anchor: str | None = None      # only when the source declares an explicit anchor id
    source_type: str | None = None         # docs | release_notes | migration | errors
    library: str | None = None
    library_version: str | None = None     # see version_kind
    version_kind: str | None = None        # "docs_as_of" (page text as of this release) | "introduced_in" (release-note entry)
    published_at: str | None = None        # ISO date taken from the source heading, if present
    fetched_at: str | None = None
    content_hash: str | None = None        # sha256 of `text`
    snapshot_id: str | None = None
    license: str | None = None

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
