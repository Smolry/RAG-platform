"""Measure Project 2 numbers: filtering latency overhead + permission-revoke propagation time.
python -m scripts.measure_project2 [--n 200] [--revoke-trials 20]
Overhead = per-query search latency WITH the ACL pre-filter minus WITHOUT it (same vectors, same k)."""
import argparse
import json
import statistics as st
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.adversarial import attack_queries
from app.evaluation.metrics import percentile
from app.evaluation.synthetic_tenants import build_corpus
from app.generation.llm import RecordingLLM
from app.security.acl import acl_filter
from app.security.auth import Principal
from app.services.factory import build_service

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=200)
ap.add_argument("--revoke-trials", type=int, default=20)
a = ap.parse_args()

s = get_settings()
_, users = build_corpus()
svc = build_service(s, llm=RecordingLLM())
persona = Principal("hr@helios", "helios", ("hr",))
queries = [q for _, q in attack_queries()]
queries = (queries * (a.n // len(queries) + 1))[: a.n]
vecs = svc.embedder.embed(queries)
for v in vecs[:10]:  # warm-up
    svc.store.search(v, 5, None)
    svc.store.search(v, 5, acl_filter(persona))

def timed(f):
    t = time.perf_counter(); f(); return (time.perf_counter() - t) * 1000

plain = [timed(lambda v=v: svc.store.search(v, 5, None)) for v in vecs]
filt = [timed(lambda v=v: svc.store.search(v, 5, acl_filter(persona))) for v in vecs]

admin = Principal("admin@helios", "helios", ("admin",))
doc = "helios/salary-sheet"
q = "CONFIDENTIAL Helios Robotics salary sheet CANARY-HELIOS-SALARY"
update_ms, effective_ms = [], []
for _ in range(a.revoke_trials):
    svc.store.update_acl(doc, "helios", ["hr", "admin"], [])
    assert doc in {c.document_id for c in svc.retrieve(q, persona, 10)["allowed"]}, "grant precondition failed"
    t0 = time.perf_counter()
    svc.store.update_acl(doc, admin.tenant_id, ["admin"], [])
    t_upd = (time.perf_counter() - t0) * 1000
    while doc in {c.document_id for c in svc.retrieve(q, persona, 10)["allowed"]}:
        if time.perf_counter() - t0 > 30:
            raise SystemExit("revocation did not take effect within 30s")
    update_ms.append(t_upd); effective_ms.append((time.perf_counter() - t0) * 1000)
svc.store.update_acl(doc, "helios", ["hr", "admin"], [])  # restore

out = {"timestamp": datetime.now(timezone.utc).isoformat(), "embedder": s.embedder_backend, "qdrant_url": s.qdrant_url,
       "n_queries": len(queries),
       "search_ms_no_filter": {"p50": percentile(plain, 50), "p95": percentile(plain, 95)},
       "search_ms_acl_filter": {"p50": percentile(filt, 50), "p95": percentile(filt, 95)},
       "filter_overhead_ms": {"p50": percentile(filt, 50) - percentile(plain, 50),
                              "p95": percentile(filt, 95) - percentile(plain, 95),
                              "mean": st.mean(filt) - st.mean(plain)},
       "revoke_trials": a.revoke_trials,
       "revoke_update_ms": {"p50": percentile(update_ms, 50), "max": max(update_ms)},
       "revoke_to_effective_ms": {"p50": percentile(effective_ms, 50), "max": max(effective_ms)}}
Path("results").mkdir(exist_ok=True)
Path("results/project2_measurements.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
