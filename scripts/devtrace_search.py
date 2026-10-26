"""Dev-only retrieval spot check for the DevTrace corpus (Arm B building block). Needs Qdrant + an ingested snapshot.

  python -m scripts.devtrace_search "RequestValidationError 422 body" --library fastapi --user-version 0.100.0
  python -m scripts.devtrace_search "what changed" --release-notes --library fastapi --from 0.100.0 --to 0.110.0

Builds a LOCAL principal (tenant devtrace, role user); it never reads or prints credentials and makes no LLM call.
This is a developer tool, not an API: real callers get their principal from a verified JWT.
"""
from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.devtrace.retrieval import DevTraceRetriever, SearchFilters
from app.retrieval.embedder import build_embedder
from app.retrieval.store import VectorStore, make_client
from app.security.auth import Principal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--library", choices=["fastapi", "pydantic"])
    ap.add_argument("--user-version")
    ap.add_argument("--release-notes", action="store_true")
    ap.add_argument("--from", dest="from_version")
    ap.add_argument("--to", dest="to_version")
    ap.add_argument("--top-k", type=int, default=5)
    a = ap.parse_args()
    s = get_settings()
    r = DevTraceRetriever(VectorStore(make_client(s.qdrant_url), s.qdrant_collection), build_embedder(s))
    f = SearchFilters(library=a.library, source_types=("release_notes",) if a.release_notes else None,
                      version_gt=a.from_version, version_lte=a.to_version)
    res = r.search(Principal("local-dev", "devtrace", ("user",)), a.query, f, top_k=a.top_k, user_version=a.user_version)
    for n, i in enumerate(res.items, 1):
        print(f"{n}. [{i.library} {i.library_version or '-'}] {' > '.join(i.section_path)}\n   {i.provenance_url}")
        print(f"   {i.text[:160].replace(chr(10), ' ')}...")
        if i.version_caveat:
            print(f"   caveat: {i.version_caveat}")


if __name__ == "__main__":
    main()
