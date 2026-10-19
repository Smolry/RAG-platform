"""Build the knowledge graph in Neo4j from the chunks already in Qdrant (one source of truth for ids/text/ACLs).

  python -m scripts.build_graph --tenant hotpot --workers 8 [--limit 200] [--no-resolve]
Steps: LLM extraction (fixed Pydantic schema, validated, grounded; raw output cached -> resumable, re-runs are free) ->
per-tenant entity resolution -> load Neo4j. Writes results/graph_build_report.json (indexing cost!) and
results/graph_resolution_audit.json (every merge, ambiguous name, and rejection example - REVIEW these by hand).
Prereq: `docker compose up -d` + `python -m scripts.ingest --manifest data/hotpot/manifest.json` + an LLM key.
"""
import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.generation.llm import LiteLLMClient, PriceTable, RecordingLLM
from app.graph.build import build_graph, report_dict
from app.graph.store import GChunk, InMemoryGraphStore, Neo4jGraphStore
from app.retrieval.embedder import build_embedder
from app.retrieval.store import VectorStore, make_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="hotpot")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-resolve", action="store_true", help="ablation: skip entity resolution (the graph will be fragmented)")
    ap.add_argument("--no-embed-gate", action="store_true", help="string rules alone decide merges (weaker)")
    ap.add_argument("--out-dir", type=Path, default=Path("results"))
    a = ap.parse_args()
    s = get_settings()
    vs = VectorStore(make_client(s.qdrant_url), s.qdrant_collection)
    chunks = [GChunk(cid, p["document_id"], p["text"], p["tenant_id"], p.get("classification", "public"),
                     tuple(p.get("allowed_roles") or ()), tuple(p.get("allowed_users") or ())) for cid, p in vs.iter_chunks(a.tenant)]
    chunks.sort(key=lambda c: c.chunk_id)
    if a.limit:
        chunks = chunks[: a.limit]
    if not chunks:
        raise SystemExit(f"no chunks for tenant {a.tenant!r} in Qdrant - run scripts.ingest first")
    store = InMemoryGraphStore() if s.graph_backend == "memory" else Neo4jGraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    store.ensure_schema()
    llm = RecordingLLM() if s.llm_backend == "stub" else LiteLLMClient()
    embed = None if a.no_embed_gate else build_embedder(s).embed
    rep, audit = build_graph(chunks, llm, s.extraction_model, PriceTable.from_yaml(s.models_config_path), store, embed,
                             s.extraction_cache_path, a.workers, not a.no_resolve,
                             progress=lambda d, n: print(f"\rextracted {d}/{n}", end="", flush=True))
    print()
    out = report_dict(rep) | {"llm_backend": s.llm_backend, "embedder_backend": s.embedder_backend, "graph_backend": s.graph_backend,
                              "NOT_FOR_REPORTING": s.llm_backend == "stub" or s.embedder_backend == "hash",
                              "store_stats": store.stats()}
    a.out_dir.mkdir(exist_ok=True)
    (a.out_dir / "graph_build_report.json").write_text(json.dumps(out, indent=2))
    (a.out_dir / "graph_resolution_audit.json").write_text(json.dumps(audit, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
