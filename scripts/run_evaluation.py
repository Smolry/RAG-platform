"""Repeatable evaluation. Writes results/<system>.csv (per question) + results/<system>_summary.json (config + aggregates).

  python -m scripts.run_evaluation --system baseline
  python -m scripts.run_evaluation --system project2 --tenant acme --user eval@acme --roles employee

The LLM judge is a *proxy*, not ground truth: judge model + prompt are recorded in the summary.
"""
import argparse
import csv
import hashlib
import json
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.metrics import JUDGE_PROMPT, citation_correct, parse_judge, percentile, retrieval_hit
from app.security.auth import Principal
from app.services.factory import build_service


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["baseline", "project2"])
    ap.add_argument("--eval-set", type=Path, default=Path("data/eval/eval_set.jsonl"))
    ap.add_argument("--only-validated", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--user", default="eval-user")
    ap.add_argument("--roles", default="employee")
    ap.add_argument("--out-dir", type=Path, default=Path("results"))
    a = ap.parse_args()

    s = get_settings().model_copy(update={"system": a.system})
    svc = build_service(s)
    principal = Principal(a.user, a.tenant, tuple(a.roles.split(","))) if a.system == "project2" else None

    raw = a.eval_set.read_bytes()
    items = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    if a.only_validated:
        items = [i for i in items if i.get("validated")]
    if a.limit:
        items = items[: a.limit]
    unvalidated = sum(1 for i in items if not i.get("validated"))

    rows = []
    for n, it in enumerate(items, 1):
        r = svc.answer(it["question"], principal=principal)
        retrieved_docs = [x["document_id"] for x in r["sources"]]
        cited_docs = [c["document_id"] for c in r["citations"]]
        correct, reason = float("nan"), "judge skipped"
        if not a.no_judge:
            j = svc.llm.complete([{"role": "user", "content": JUDGE_PROMPT.format(
                question=it["question"], reference=it["reference_answer"], answer=r["answer"])}], s.judge_model)
            correct, reason = parse_judge(j.text)
            svc.db.set_quality(r["request_id"], correct)
        rows.append({"request_id": r["request_id"], "question": it["question"], "reference_answer": it["reference_answer"],
                     "source_document": it["source_document"], "answer": r["answer"],
                     "retrieval_hit": retrieval_hit(it["source_document"], retrieved_docs),
                     "citation_correct": citation_correct(it["source_document"], cited_docs),
                     "answer_correct": correct, "judge_reason": reason, "model": r["model"],
                     "latency_ms": r["latency_ms"]["total"], "retrieval_ms": r["latency_ms"]["retrieval"],
                     "generation_ms": r["latency_ms"]["generation"], "input_tokens": r["usage"]["input_tokens"],
                     "output_tokens": r["usage"]["output_tokens"], "cost_usd": r["usage"]["cost_usd"],
                     "retrieved_chunk_ids": "|".join(x["chunk_id"] for x in r["sources"]),
                     "retrieval_scores": "|".join(f"{x['score']:.4f}" for x in r["sources"])})
        print(f"[{n}/{len(items)}] hit={rows[-1]['retrieval_hit']:.0f} correct={correct}")

    a.out_dir.mkdir(exist_ok=True)
    csv_path = a.out_dir / f"{a.system}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    def mean(k):
        v = [r[k] for r in rows if r[k] == r[k]]
        return st.mean(v) if v else None

    lat = [r["latency_ms"] for r in rows]
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(), "system": a.system, "n_questions": len(rows),
        "n_unvalidated_questions": unvalidated, "eval_set_sha256": hashlib.sha256(raw).hexdigest()[:16],
        "config": {"llm_backend": s.llm_backend, "generation_model": s.llm_model,
                   "judge_model": None if a.no_judge else s.judge_model, "embedder_backend": s.embedder_backend,
                   "embedding_model": s.embedding_model, "chunk_tokens": s.chunk_tokens,
                   "chunk_overlap_tokens": s.chunk_overlap_tokens, "top_k": s.top_k, "qdrant_url": s.qdrant_url},
        "metrics": {"retrieval_hit_rate": mean("retrieval_hit"), "citation_correctness": mean("citation_correct"),
                    "answer_correctness_llm_judge": mean("answer_correct"), "p50_latency_ms": percentile(lat, 50),
                    "p95_latency_ms": percentile(lat, 95), "mean_cost_usd_per_request": mean("cost_usd"),
                    "total_cost_usd": sum(r["cost_usd"] for r in rows)},
    }
    (a.out_dir / f"{a.system}_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["metrics"], indent=2), f"\nwrote {csv_path}")


if __name__ == "__main__":
    main()
