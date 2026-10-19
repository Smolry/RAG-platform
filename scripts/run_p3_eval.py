"""Project 3 evaluation: vector baseline vs vector+rerank vs GraphRAG hybrid on HotpotQA (+ optional single-hop set).

  python -m scripts.run_p3_eval --tenant hotpot --single data/hotpot/questions_single.jsonl --only-validated
Prereqs: scripts.load_hotpotqa -> scripts.ingest --manifest data/hotpot/manifest.json -> scripts.build_graph; real LLM + embedder
+ cross-encoder; docker compose up (Qdrant + Neo4j). Writes results/project3.csv, project3_summary.json, project3_report.md.
"""
import argparse
import csv
import dataclasses
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.p3_eval import ARMS_P3, compare, run_arm, summarize
from app.security.auth import Principal
from app.services.factory import build_service


def pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}"


def report_md(sm: dict) -> str:
    c = sm["config"]
    L = ["# Project 3 report (generated - do not edit by hand)", "",
         f"- timestamp {sm['timestamp']} | questions: {sm['n_questions']} | questions_sha256 `{sm['questions_sha256']}`",
         f"- LLM backend **{c['llm_backend']}** (generation `{c['llm_model']}`, extraction `{c['extraction_model']}`) | embedder `{c['embedder_backend']}` / `{c['embedding_model']}` | "
         f"reranker `{c['reranker_backend']}` / `{c['rerank_model']}` | graph `{c['graph_backend']}` | top_k={c['top_k']} vector_k={c['vector_k']} hops={c['graph_hops']}", ""]
    if c["llm_backend"] == "stub" or c["embedder_backend"] == "hash" or c["reranker_backend"] == "lexical":
        L += ["> **STUB / HASH / LEXICAL RUN - plumbing check only. These numbers are NOT results.**", ""]
    for slc in ("multi_hop", "bridge", "comparison", "single", "all"):
        if not any(slc in a["by_slice"] for a in sm["arms"].values()):
            continue
        L += [f"## {slc}", "", "| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |", "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for arm, a in sm["arms"].items():
            s = a["by_slice"].get(slc)
            if not s:
                continue
            d = a.get("vs_vector", {}).get(slc)
            dtxt = "ref" if arm == "vector" else (f"{100 * d['f1']['delta']:+.1f} [{100 * d['f1']['ci95'][0]:+.1f}, {100 * d['f1']['ci95'][1]:+.1f}]" if d else "n/a")
            L.append(f"| {arm} | {s['n']} | {pct(s['em'])} | {pct(s['f1'])} | {dtxt} | {pct(s['sf_recall'])} | {pct(s['all_supporting_found'])} | "
                     f"{pct(s['candidate_recall'])} | ${s['cost_per_query_usd']:.5f} | {s['p95_latency_ms']:.0f} |")
        L.append("")
    L += ["## Claims the data supports", ""]
    for arm, a in sm["arms"].items():
        for slc, d in a.get("vs_vector", {}).items():
            if slc in ("multi_hop", "single"):
                L.append(f"- **{arm} / {slc}**: ΔF1 {100 * d['f1']['delta']:+.1f} pts, ΔSF-recall {100 * d['sf_recall']['delta']:+.1f} pts - {d['claim']}")
    ib = sm.get("indexing")
    L += ["", "## Indexing cost (one-time, paid before the first query)", ""]
    L += ([f"- {ib['n_chunks']} chunks, {ib['llm_calls_total']} extraction calls, {ib['input_tokens']} in / {ib['output_tokens']} out tokens, "
           f"**${ib['indexing_cost_usd']:.4f}** (${ib['indexing_cost_usd_per_chunk']:.5f}/chunk), {ib['seconds']:.0f}s wall, "
           f"entities {ib['entities_distinct_before_resolution']} → {ib['entities_after_resolution']} after resolution, {ib['relations']} relations, "
           f"rejections {ib['rejections']}"] if ib else ["- results/graph_build_report.json not found: run scripts.build_graph"])
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=Path("data/hotpot/questions.jsonl"))
    ap.add_argument("--single", type=Path)
    ap.add_argument("--tenant", default="hotpot")
    ap.add_argument("--arms", default="vector,vector_rerank,hybrid")
    ap.add_argument("--only-validated", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--out-dir", type=Path, default=Path("results"))
    a = ap.parse_args()
    base = get_settings()
    raw = a.questions.read_bytes()
    qs = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
    if a.single:
        raw += a.single.read_bytes()
        qs += [json.loads(l) for l in a.single.read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.only_validated:
        qs = [q for q in qs if q.get("validated", True)]
    if a.limit:
        qs = qs[: a.limit]
    unvalidated = sum(1 for q in qs if q.get("validated") is False)
    principal = Principal("p3-eval", a.tenant, ("reader",))
    all_rows, arms = {}, {}
    for arm in a.arms.split(","):
        s = base.model_copy(update={**ARMS_P3[arm], "run_label": f"p3:{arm}"})
        print(f"== arm {arm} ==")
        all_rows[arm] = run_arm(arm, build_service(s), principal, qs)
        arms[arm] = {"by_slice": summarize(all_rows[arm])}
    if "vector" in all_rows:
        for arm in arms:
            if arm != "vector":
                arms[arm]["vs_vector"] = compare(all_rows["vector"], all_rows[arm], a.margin)
    bp = a.out_dir / "graph_build_report.json"
    sm = {"timestamp": datetime.now(timezone.utc).isoformat(), "n_questions": len(qs), "n_unvalidated": unvalidated,
          "questions_sha256": hashlib.sha256(raw).hexdigest()[:16], "arms": arms,
          "indexing": json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else None,
          "config": {k: getattr(base, k) for k in ("llm_backend", "llm_model", "extraction_model", "embedder_backend", "embedding_model",
                                                   "reranker_backend", "rerank_model", "graph_backend", "top_k", "vector_k", "graph_hops",
                                                   "graph_limit", "graph_hub_degree_cap")}}
    a.out_dir.mkdir(exist_ok=True)
    (a.out_dir / "project3_summary.json").write_text(json.dumps(sm, indent=2), encoding="utf-8")
    (a.out_dir / "project3_report.md").write_text(report_md(sm), encoding="utf-8")
    flat = [dataclasses.asdict(r) for rs in all_rows.values() for r in rs]
    with (a.out_dir / "project3.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(flat[0])); w.writeheader(); w.writerows(flat)
    sys.stdout.reconfigure(encoding="utf-8")
    print((a.out_dir / "project3_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
