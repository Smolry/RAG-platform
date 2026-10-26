"""Embed and index a built DevTrace snapshot into Qdrant.

  python -m scripts.ingest_devtrace --snapshot data/devtrace/snapshots/<id> [--tenant devtrace]

Needs Qdrant running (docker compose up -d) and the embedding model (first run downloads ~90 MB from Hugging Face).
Replaces the tenant's chunks from any other snapshot. No credentials involved.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import get_settings
from app.devtrace.corpus.ingest import ingest_snapshot, load_chunks
from app.observability.db import Database
from app.retrieval.embedder import build_embedder
from app.retrieval.store import VectorStore, make_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--tenant", default="devtrace")
    ap.add_argument("--roles", default="user,admin")
    a = ap.parse_args()
    chunks = load_chunks(a.snapshot / "chunks.jsonl")
    s = get_settings()
    n = ingest_snapshot(chunks, a.snapshot.name, VectorStore(make_client(s.qdrant_url), s.qdrant_collection), build_embedder(s),
                        tenant_id=a.tenant, allowed_roles=[r for r in a.roles.split(",") if r], db=Database(s.database_url))
    print(f"ingested {n} chunks for tenant {a.tenant!r}, snapshot {a.snapshot.name}")


if __name__ == "__main__":
    main()
