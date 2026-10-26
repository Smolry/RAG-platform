"""DevTrace tools: the ONLY way a model can touch evidence. Typed, validated, budgeted, authorization-bound.

Design rules (each has a test in tests/security/test_devtrace_tool_boundary.py):
  1. The model supplies search phrases and version numbers, nothing else. Policy fields (tenant, roles, filters, collection,
     URLs, limits) are not parameters; passing one is rejected (`extra="forbid"`), not silently ignored.
  2. Identity comes from the ToolContext built by the server from the verified JWT. Follow-up calls reuse the same context.
  3. Every call is counted against a budget enforced here in code; the model cannot raise it.
  4. Everything returned is recorded in an EvidenceLedger. `inspect_sources` can only open refs that retrieval already
     returned in THIS run, and it re-checks authorization live.
  5. Errors are value-free codes. No stack traces, paths, or argument values go back to the model or the client.
Retrieved text is untrusted: use `render_evidence_block` when putting it in a prompt.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.devtrace.retrieval import DevTraceRetriever, SearchFilters, to_item
from app.devtrace.schemas import (EvidenceItem, InspectSourcesArgs, RetrieveDocsArgs, SearchReleaseNotesArgs, ToolResult)
from app.security.auth import Principal

log = logging.getLogger(__name__)
INSPECT_MAX_CHARS = 6000


class LedgerFull(RuntimeError):
    pass


@dataclass
class ToolBudget:
    """Hard cap on tool calls per investigation. Enforced in application code, not by prompting."""
    max_calls: int = 8
    max_invalid: int = 4          # malformed/unknown calls are cheap but still bounded, so a model cannot loop on them forever
    used: int = 0
    invalid: int = 0
    by_tool: dict[str, int] = field(default_factory=dict)

    def note_invalid(self) -> bool:
        self.invalid += 1
        return self.invalid <= self.max_invalid

    def consume(self, tool: str) -> bool:
        if self.used >= self.max_calls:
            return False
        self.used += 1
        self.by_tool[tool] = self.by_tool.get(tool, 0) + 1
        return True

    @property
    def remaining(self) -> int:
        return self.max_calls - self.used


class EvidenceLedger:
    """Everything shown to the model in this run, keyed by stable refs (E1, E2, ...). Later citation validation checks
    against THIS set, so a fabricated or out-of-run source can never validate."""

    def __init__(self, max_items: int = 60):
        self.max_items, self._by_ref, self._by_chunk = max_items, {}, {}

    def add(self, item: EvidenceItem) -> EvidenceItem:
        if item.chunk_id in self._by_chunk:                       # same passage -> same ref
            existing = self._by_ref[self._by_chunk[item.chunk_id]]
            existing.version_caveat = item.version_caveat or existing.version_caveat
            return existing
        if len(self._by_ref) >= self.max_items:
            raise LedgerFull
        item.ref = f"E{len(self._by_ref) + 1}"
        self._by_ref[item.ref] = item
        self._by_chunk[item.chunk_id] = item.ref
        return item

    def get(self, ref: str) -> EvidenceItem | None:
        return self._by_ref.get(ref)

    def refs(self) -> list[str]:
        return list(self._by_ref)

    def __len__(self) -> int:
        return len(self._by_ref)


_DELIM = re.compile(r"[<>]{3,}")


def _flat(s: str | None) -> str:
    return _DELIM.sub(" ", re.sub(r"\s+", " ", s or "")).strip()


def render_evidence_block(item: EvidenceItem) -> str:
    """Delimited, escaped rendering for prompts. Document text cannot forge a closing delimiter or a header."""
    body = _DELIM.sub(lambda m: "·" * len(m.group()), item.text)
    head = f"<<<EVIDENCE {item.ref} | {_flat(item.library)} {_flat(item.library_version)} | {_flat(' > '.join(item.section_path))}>>>"
    return f"{head}\n{body}\n<<<END {item.ref}>>>"


class DevTraceToolbox:
    def __init__(self, principal: Principal, retriever: DevTraceRetriever, *, ledger: EvidenceLedger | None = None,
                 budget: ToolBudget | None = None, request_id: str = "devtrace"):
        self._principal, self._retriever, self._request_id = principal, retriever, request_id   # not exposed to the model
        # `is None`, not `or`: an empty EvidenceLedger is falsy (it has __len__), which would silently drop a caller's cap.
        self.ledger = ledger if ledger is not None else EvidenceLedger()
        self.budget = budget if budget is not None else ToolBudget()
        self._tools = {"retrieve_docs": (RetrieveDocsArgs, self._retrieve_docs),
                       "search_release_notes": (SearchReleaseNotesArgs, self._search_release_notes),
                       "inspect_sources": (InspectSourcesArgs, self._inspect_sources)}

    # ---- schemas for the LLM layer (Phase 5) ----
    def specs(self) -> list[dict]:
        doc = {"retrieve_docs": "Search official docs/release notes/migration guides for passages relevant to a phrase.",
               "search_release_notes": "Search release-note entries for one library, optionally only between two versions "
                                       "(from_version exclusive, to_version inclusive).",
               "inspect_sources": "Read the full text and metadata of passages already returned in this run, by ref (E1...)."}
        return [{"name": n, "description": doc[n], "parameters": m.model_json_schema()} for n, (m, _) in self._tools.items()]

    # ---- single entry point ----
    def dispatch(self, name: str, raw_args: object) -> ToolResult:
        spec = self._tools.get(name) if isinstance(name, str) else None
        if spec is None:
            if not self.budget.note_invalid():
                return self._err("?", "budget_exceeded", "too many invalid tool calls")
            return self._err("?", "unknown_tool", "no such tool")          # the unknown name is never echoed back
        model, fn = spec
        if not isinstance(raw_args, dict):
            return self._invalid(name, "arguments must be an object")
        try:
            args = model.model_validate(raw_args)
        except ValidationError as e:   # report WHERE and WHY, never the offending values
            msg = "; ".join(f"{'.'.join(str(x) for x in err['loc']) or 'arguments'}: {err['type']}" for err in e.errors()[:6])
            return self._invalid(name, msg)
        if not self.budget.consume(name):
            return self._err(name, "budget_exceeded", "tool-call budget exhausted")
        try:
            return fn(args)
        except LedgerFull:
            return self._err(name, "ledger_full", "evidence limit reached for this investigation")
        except Exception as e:  # noqa: BLE001 - never leak internals to the model/client
            log.error("tool %s failed: %s", name, type(e).__name__)
            return self._err(name, "tool_failed", "the tool failed; try a different query or stop")

    # ---- tools ----
    def _retrieve_docs(self, a: RetrieveDocsArgs) -> ToolResult:
        f = SearchFilters(library=a.library, source_types=tuple(a.source_types) if a.source_types else None)
        return self._run("retrieve_docs", a.query, f, a.top_k, a.user_version)

    def _search_release_notes(self, a: SearchReleaseNotesArgs) -> ToolResult:
        f = SearchFilters(library=a.library, source_types=("release_notes",), version_gt=a.from_version, version_lte=a.to_version)
        return self._run("search_release_notes", a.query, f, a.top_k, a.to_version)

    def _run(self, tool: str, query: str, f: SearchFilters, top_k: int, user_version: str | None) -> ToolResult:
        res = self._retriever.search(self._principal, query, f, top_k=top_k, user_version=user_version, request_id=self._request_id)
        items = [self.ledger.add(i) for i in res.items]
        meta = {"filters": res.debug["filters"], "returned": len(items), "calls_remaining": self.budget.remaining,
                "n_candidates": res.debug["n_candidates"]}
        if not items:
            meta["note"] = "no matching passages in the corpus for these filters"
        return ToolResult(ok=True, tool=tool, items=items, meta=meta)

    def _inspect_sources(self, a: InspectSourcesArgs) -> ToolResult:
        known = [(r, self.ledger.get(r)) for r in dict.fromkeys(a.refs)]
        missing = [r for r, it in known if it is None]
        if missing:
            return self._err("inspect_sources", "not_found", "unknown refs: " + ", ".join(missing))
        live = {c.chunk_id: c for c in self._retriever.fetch(self._principal, [it.chunk_id for _, it in known])}
        out, gone = [], []
        for r, it in known:
            c = live.get(it.chunk_id)
            if c is None:
                gone.append(r)          # deleted or authorization revoked since retrieval
                continue
            full = to_item(c, INSPECT_MAX_CHARS)
            full.ref, full.version_caveat = it.ref, it.version_caveat
            out.append(full)
        meta = {"returned": len(out), "calls_remaining": self.budget.remaining}
        if gone:
            meta["unavailable"] = gone
        return ToolResult(ok=True, tool="inspect_sources", items=out, meta=meta)

    def _invalid(self, tool: str, message: str) -> ToolResult:
        if not self.budget.note_invalid():
            return self._err(tool, "budget_exceeded", "too many invalid tool calls")
        return self._err(tool, "invalid_arguments", message)

    @staticmethod
    def _err(tool: str, code: str, message: str) -> ToolResult:
        return ToolResult(ok=False, tool=tool, error=code, message=message)
