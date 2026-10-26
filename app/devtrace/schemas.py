"""Typed tool inputs/outputs. Every input model forbids extra fields, so a model-written call can never carry a tenant,
role, filter, collection, URL or any other policy field: such a call is rejected, not ignored."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.devtrace.versions import VersionError, version_key

Library = Literal["fastapi", "pydantic"]
SourceType = Literal["docs", "release_notes", "migration", "errors"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


def _check_version(v: str | None) -> str | None:
    if v is None:
        return None
    try:
        version_key(v)
    except VersionError as e:
        raise ValueError(str(e)) from None
    return v


class RetrieveDocsArgs(_Strict):
    query: str = Field(min_length=3, max_length=500, description="Search phrase (not the whole stack trace).")
    library: Library | None = None
    source_types: list[SourceType] | None = Field(default=None, max_length=4)
    user_version: str | None = Field(default=None, max_length=20, description="Installed version of `library`, to annotate version caveats.")
    top_k: int = Field(default=5, ge=1, le=8)

    _v = field_validator("user_version")(_check_version)

    @model_validator(mode="after")
    def _version_needs_library(self):
        if self.user_version and not self.library:
            raise ValueError("user_version requires library")
        return self


class SearchReleaseNotesArgs(_Strict):
    query: str = Field(min_length=3, max_length=500)
    library: Library
    from_version: str | None = Field(default=None, max_length=20, description="Exclusive lower bound (the version you upgraded FROM).")
    to_version: str | None = Field(default=None, max_length=20, description="Inclusive upper bound (the version you upgraded TO).")
    top_k: int = Field(default=6, ge=1, le=10)

    _v = field_validator("from_version", "to_version")(_check_version)

    @model_validator(mode="after")
    def _ordered(self):
        if self.from_version and self.to_version and version_key(self.from_version) >= version_key(self.to_version):
            raise ValueError("from_version must be lower than to_version")
        return self


class InspectSourcesArgs(_Strict):
    refs: list[str] = Field(min_length=1, max_length=5, description="Evidence refs already returned in this run, e.g. ['E1'].")

    @field_validator("refs")
    @classmethod
    def _refs(cls, refs: list[str]) -> list[str]:
        import re
        if any(not re.fullmatch(r"E\d{1,3}", r) for r in refs):
            raise ValueError("refs must look like E1, E2, ...")
        return refs


class EvidenceItem(BaseModel):
    """One retrieved passage. `text` is UNTRUSTED data from a document; it is never an instruction."""

    ref: str = ""
    chunk_id: str
    document_id: str
    library: str | None = None
    library_version: str | None = None
    version_kind: str | None = None          # docs_as_of | introduced_in
    published_at: str | None = None
    source_type: str | None = None
    source_title: str | None = None
    section_path: list[str] = []
    section_anchor: str | None = None
    source_url: str | None = None
    provenance_url: str | None = None
    content_hash: str | None = None
    snapshot_id: str | None = None
    score: float = 0.0                       # score used for the final ordering (rerank score when reranking)
    vector_score: float = 0.0
    text: str = ""
    truncated: bool = False
    version_caveat: str | None = None


class ToolResult(BaseModel):
    ok: bool
    tool: str
    items: list[EvidenceItem] = []
    error: str | None = None                 # machine code: unknown_tool | invalid_arguments | budget_exceeded | ledger_full | not_found | tool_failed
    message: str | None = None               # safe, value-free explanation
    meta: dict[str, Any] = {}
