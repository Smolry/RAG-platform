"""Project 1 benchmark: always-large vs routing / cache / full, on originals + paraphrases + repeats.

  python -m scripts.run_benchmark --tenant acme --user eval@acme --roles employee [--limit 50]

Prereqs: baseline corpus ingested with the SAME --tenant (so eval `source_document` ids match), eval set hand-validated and
paraphrased (scripts.make_paraphrases), real LLM configured (LLM_MODEL / SMALL_MODEL / LARGE_MODEL + provider key),
server Qdrant + Postgres (docker compose up -d) so latency/filter numbers are meaningful.
Writes results/project1.csv, results/project1_summary.json, results/project1_report.md (every number in the docs comes from here).
"""
import argparse
import csv
import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.benchmark import ARMS, compare, run_arm, summarize_arm
from app.generation.llm import LiteLLMClient, RecordingLLM
from app.security.auth import Principal
from app.services.factory import build_service


def pct(x):
    return "n/a" if x is None else f"{x:+.1f}%"


def report_md(summary: dict) -> str:
    L = ["# Project 1 benchmark report (generated - do not edit by hand)", "",
         f"- timestamp: {summary['timestamp']}  - n_questions: {summary['n_questions']}  - eval_set_sha256: `{summary['eval_set_sha256']}`",
         f"- LLM backend: **{summary['config']['llm_backend']}**, small=`{summary['config']['small_model']}`, large=`{summary['config']['large_model']}`, "
         f"judge=`{summary['config']['judge_model']}`, embedder=`{summary['config']['embedder_backend']}` / `{summary['config']['embedding_model']}`",
         f"- cache threshold={summary['config']['cache_threshold']} ttl={summary['config']['cache_ttl_seconds']}s", ""]
    if summary["config"]["llm_backend"] == "stub" or summary["config"]["embedder_backend"] == "hash":
        L += ["> **STUB/HASH RUN - plumbing check only. These numbers are NOT results.**", ""]
    L += ["| arm | cost/req | cost vs always_large | p50 ms | p95 ms | p95 vs ref | cache hit | quality | quality Δ vs ref (95% CI) | retrieval hit |",
          "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for arm, d in summary["arms"].items():
        o, c = d["overall"], d.get("vs_always_large")
        qd = f"{c['quality_delta']['mean']:+.3f} [{c['quality_delta']['ci95'][0]:+.3f}, {c['quality_delta']['ci95'][1]:+.3f}]" if c else "ref"
        L.append(f"| {arm} | ${o['cost_per_request_usd']:.5f} | {pct(-c['cost_reduction_pct']) if c and c['cost_reduction_pct'] is not None else 'ref'} | "
                 f"{o['p50_latency_ms']:.0f} | {o['p95_latency_ms']:.0f} | {pct(c['p95_latency_change_pct']) if c else 'ref'} | "
                 f"{o['cache_hit_rate']:.1%} | {o['quality']:.3f} | {qd} | {o['retrieval_hit_rate']:.1%} |")
    L += ["", "## Per-pass (cache hit rate / quality / cost per request)", "", "| arm | pass | n | cache hit | quality | cost/req |", "|---|---|--:|--:|--:|--:|"]
    for arm, d in summary["arms"].items():
        for p in ("original", "paraphrase", "repeat"):
            b = d.get(p)
            if b:
                L.append(f"| {arm} | {p} | {b['n']} | {b['cache_hit_rate']:.1%} | {b['quality']:.3f} | ${b['cost_per_request_usd']:.5f} |")
    L += ["", "## Claims the data supports", ""]
    for arm, d in summary["arms"].items():
        c = d.get("vs_always_large")
        if c:
            o = d["overall"]
            L.append(f"- **{arm}**: cost {pct(-c['cost_reduction_pct']) if c['cost_reduction_pct'] is not None else 'n/a'}, p95 {pct(c['p95_latency_change_pct'])}; "
                     f"{c['quality_delta']['claim']} (margin {c['quality_delta']['noninferiority_margin']}, n={c['quality_delta']['n_items']} items); "
                     f"false cache hits judged wrong: {o['false_cache_hits_judged_wrong']}; "
                     f"route split: {o['route_split']}; escalation rate of simple: {o['escalation_rate_of_simple']}")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", type=Path, default=Path("data/eval/eval_set.jsonl"))
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--user", default="eval-user")
    ap.add_argument("--roles", default="employee")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only-validated", action="store_true")
    ap.add_argument("--margin", type=float, default=0.05, help="non-inferiority margin for the quality claim")
    ap.add_argument("--out-dir", type=Path, default=Path("results"))
    a = ap.parse_args()

    base = get_settings().model_copy(update={"system": "project1"})
    raw = a.eval_set.read_bytes()
    items = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    if a.only_validated:
        items = [i for i in items if i.get("validated")]
    if a.limit:
        items = items[: a.limit]
    if not any(i.get("paraphrase") for i in items):
        print("WARNING: no 'paraphrase' fields - run scripts.make_paraphrases; the paraphrase pass will be empty")
    principal = Principal(a.user, a.tenant, tuple(a.roles.split(",")))
    judge = RecordingLLM() if base.llm_backend == "stub" else LiteLLMClient()

    all_rows, arms = {}, {}
    for arm in a.arms.split(","):
        s = base.model_copy(update={**ARMS[arm], "run_label": f"bench:{arm}"})
        svc = build_service(s)
        print(f"== arm {arm} ==")
        all_rows[arm] = run_arm(arm, svc, principal, items, judge, s.judge_model)
        arms[arm] = summarize_arm(all_rows[arm])
    if "always_large" in all_rows:
        for arm in arms:
            if arm != "always_large":
                arms[arm]["vs_always_large"] = compare(all_rows["always_large"], all_rows[arm], a.margin)

    summary = {"timestamp": datetime.now(timezone.utc).isoformat(), "n_questions": len(items),
               "eval_set_sha256": hashlib.sha256(raw).hexdigest()[:16],
               "config": {"llm_backend": base.llm_backend, "small_model": base.small_model, "large_model": base.large_model,
                          "judge_model": base.judge_model, "embedder_backend": base.embedder_backend,
                          "embedding_model": base.embedding_model, "cache_threshold": base.cache_threshold,
                          "cache_ttl_seconds": base.cache_ttl_seconds, "top_k": base.top_k, "qdrant_url": base.qdrant_url,
                          "escalation_min_top_score": base.escalation_min_top_score},
               "arms": arms}
    a.out_dir.mkdir(exist_ok=True)
    (a.out_dir / "project1_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (a.out_dir / "project1_report.md").write_text(report_md(summary), encoding="utf-8")
    flat = [dataclasses.asdict(r) for rs in all_rows.values() for r in rs]
    with (a.out_dir / "project1.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(flat[0])); w.writeheader(); w.writerows(flat)
    print((a.out_dir / "project1_report.md").read_text())


if __name__ == "__main__":
    main()
