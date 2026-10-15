"""Project 1 benchmark core (testable, no CLI). See scripts/run_benchmark.py.

Arms (same service, ablation flags):
  always_large : cache off, routing off  -> every request on the LARGE model   (the cost/quality reference)
  routing_only : cache off, routing on   (+ escalation)
  cache_only   : cache on,  routing off
  full         : cache on,  routing on
Workload per arm (fresh cache each arm): pass 1 = original questions (cold), pass 2 = paraphrases, pass 3 = repeats.
"""
from __future__ import annotations

import random
import statistics as st
from dataclasses import dataclass

from app.evaluation.metrics import JUDGE_PROMPT, parse_judge, percentile, retrieval_hit

ARMS = {
    "always_large": dict(cache_enabled=False, routing_enabled=False),
    "routing_only": dict(cache_enabled=False, routing_enabled=True),
    "cache_only": dict(cache_enabled=True, routing_enabled=False),
    "full": dict(cache_enabled=True, routing_enabled=True),
}
PASSES = ("original", "paraphrase", "repeat")


@dataclass
class Row:
    arm: str
    pass_name: str
    idx: int
    question: str
    answer: str
    retrieval_hit: float
    correct: float
    cache_hit: bool
    route: str | None
    escalated: bool
    model: str
    cost_usd: float
    latency_ms: float


def workload(items: list[dict]) -> list[tuple[str, int, str]]:
    """(pass, idx, question). Items without a 'paraphrase' are skipped for the paraphrase pass."""
    w = [("original", i, it["question"]) for i, it in enumerate(items)]
    w += [("paraphrase", i, it["paraphrase"]) for i, it in enumerate(items) if it.get("paraphrase")]
    w += [("repeat", i, it["question"]) for i, it in enumerate(items)]
    return w


def run_arm(arm: str, svc, principal, items: list[dict], judge_llm, judge_model: str) -> list[Row]:
    if svc.cache is not None:
        svc.cache.clear()
    rows: list[Row] = []
    for pass_name, i, q in workload(items):
        it = items[i]
        r = svc.answer(q, principal=principal)
        j = judge_llm.complete([{"role": "user", "content": JUDGE_PROMPT.format(
            question=it["question"], reference=it["reference_answer"], answer=r["answer"])}], judge_model)
        correct, _ = parse_judge(j.text)
        svc.db.set_quality(r["request_id"], correct)
        rows.append(Row(arm, pass_name, i, q, r["answer"],
                        retrieval_hit(it["source_document"], [s["document_id"] for s in r["sources"]]), correct,
                        bool(r["cache_hit"]), r["route"], bool(r["escalated"]), r["model"], r["usage"]["cost_usd"],
                        r["latency_ms"]["total"]))
    return rows


def paired_bootstrap(diffs: list[float], n_boot: int = 5000, seed: int = 0) -> tuple[float, float, float]:
    """Mean paired difference with a 95% percentile-bootstrap CI (resampling items)."""
    if not diffs:
        return float("nan"), float("nan"), float("nan")
    rng, n = random.Random(seed), len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return sum(diffs) / n, means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def summarize_arm(rows: list[Row]) -> dict:
    def block(rs: list[Row]) -> dict:
        if not rs:
            return {}
        lat = [r.latency_ms for r in rs]
        routed = [r for r in rs if r.route]
        return {"n": len(rs), "cost_per_request_usd": st.mean(r.cost_usd for r in rs), "total_cost_usd": sum(r.cost_usd for r in rs),
                "p50_latency_ms": percentile(lat, 50), "p95_latency_ms": percentile(lat, 95),
                "cache_hit_rate": st.mean(r.cache_hit for r in rs), "quality": st.mean(r.correct for r in rs),
                "retrieval_hit_rate": st.mean(r.retrieval_hit for r in rs),
                "route_split": {k: sum(r.route == k for r in routed) / len(routed) for k in ("simple", "complex")} if routed else None,
                "escalation_rate_of_simple": (sum(r.escalated for r in routed if r.route == "simple") /
                                              max(1, sum(r.route == "simple" for r in routed))) if routed else None,
                "quality_by_route": {k: st.mean(r.correct for r in routed if r.route == k)
                                     for k in ("simple", "complex") if any(r.route == k for r in routed)} or None,
                "false_cache_hits_judged_wrong": sum(1 for r in rs if r.cache_hit and r.correct == 0.0)}
    return {"overall": block(rows), **{p: block([r for r in rows if r.pass_name == p]) for p in PASSES}}


def compare(ref_rows: list[Row], rows: list[Row], margin: float = 0.05) -> dict:
    """Arm vs reference (always_large). Quality delta is PAIRED per question (cluster = original item)."""
    ref_s, s = summarize_arm(ref_rows)["overall"], summarize_arm(rows)["overall"]
    by_item_ref: dict[int, list[float]] = {}
    by_item: dict[int, list[float]] = {}
    for r in ref_rows: by_item_ref.setdefault(r.idx, []).append(r.correct)
    for r in rows: by_item.setdefault(r.idx, []).append(r.correct)
    diffs = [st.mean(by_item[i]) - st.mean(by_item_ref[i]) for i in by_item if i in by_item_ref]
    mean, lo, hi = paired_bootstrap(diffs)
    cost_ref = ref_s["cost_per_request_usd"]
    return {"cost_reduction_pct": 100 * (1 - s["cost_per_request_usd"] / cost_ref) if cost_ref else None,
            "p95_latency_change_pct": 100 * (s["p95_latency_ms"] / ref_s["p95_latency_ms"] - 1),
            "p50_latency_change_pct": 100 * (s["p50_latency_ms"] / ref_s["p50_latency_ms"] - 1),
            "quality_delta": {"mean": mean, "ci95": [lo, hi], "n_items": len(diffs), "noninferiority_margin": margin,
                              "claim": ("no quality drop SUPPORTED (CI lower bound above -margin)" if lo > -margin else
                                        "no quality drop NOT supported by this data (CI lower bound at/below -margin)")}}
