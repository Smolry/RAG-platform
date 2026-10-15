"""Run the adversarial leakage suite against the CONFIGURED stack and write results/project2_security.json.
Exit code 1 if any leak -> CI-friendly.   python -m scripts.run_security_suite
Prereq: tenant corpus ingested (python -m scripts.ingest --manifest data/synthetic/manifest.json)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.adversarial import run_suite
from app.evaluation.synthetic_tenants import build_corpus
from app.generation.llm import RecordingLLM
from app.security.auth import Principal
from app.services.factory import build_service


def main() -> int:
    s = get_settings()
    docs, users = build_corpus()
    svc = build_service(s, llm=RecordingLLM())  # retrieval-level suite: no LLM call is made
    res = run_suite(svc, docs, [Principal(u["user_id"], u["tenant_id"], tuple(u["roles"])) for u in users])
    out = {"timestamp": datetime.now(timezone.utc).isoformat(), "embedder": s.embedder_backend,
           "embedding_model": s.embedding_model, "qdrant_url": s.qdrant_url, "chunk_tokens": s.chunk_tokens,
           "total_queries": res.total_queries, "authorized_chunks_returned": res.authorized_chunks_returned, "leaks": res.leaks, "cross_tenant_leaks": res.cross_tenant_leaks,
           "adversarial_pass_rate": res.pass_rate, "leak_details": res.leak_details[:20]}
    Path("results").mkdir(exist_ok=True)
    Path("results/project2_security.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "leak_details"}, indent=2))
    return 1 if res.leaks else 0


if __name__ == "__main__":
    sys.exit(main())
