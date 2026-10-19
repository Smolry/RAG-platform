"""Project 3 evaluation core (testable). CLI: scripts/run_p3_eval.py.

Arms (same service, same short-answer prompt, same top_k; ONLY retrieval differs):
  vector        : Project-2 path, plain vector top-k (the baseline)
  vector_rerank : vector top-N -> cross-encoder -> top-k           (isolates the reranker's contribution)
  hybrid        : vector top-N + graph expansion -> cross-encoder -> top-k + relation triples   (GraphRAG)
  hybrid_ngram  : as hybrid, but question entities found by dictionary match (no LLM call)
Metrics: Exact Match, token F1 (HotpotQA normalisation, citations stripped), supporting-fact recall (fraction of gold
paragraphs in the final context), all-supporting-found, candidate recall (pool before rerank), cost, latency.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass

from app.evaluation.benchmark import paired_bootstrap
from app.evaluation.metrics import (all_supporting_found, exact_match, f1_score, percentile, strip_citations,
                                    supporting_fact_recall)

ARMS_P3 = {
    "vector": dict(system="project2", answer_style="short"),
    "vector_rerank": dict(system="project3", graph_enabled=False, answer_style="short"),
    "hybrid": dict(system="project3", graph_enabled=True, question_entities="llm", answer_style="short"),
    "hybrid_ngram": dict(system="project3", graph_enabled=True, question_entities="ngram", answer_style="short"),
}
MULTI = ("bridge", "comparison")


@dataclass
class Row:
    arm: str
    qid: str
    qtype: str
    level: str
    question: str
    gold: str
    answer: str
    em: float
    f1: float
    sf_recall: float
    all_found: float
    cand_recall: float | None
    cost_usd: float
    latency_ms: float
    retrieval_ms: float
    n_seeds: int | None


def run_arm(arm: str, svc, principal, questions: list[dict]) -> list[Row]:
    rows = []
    for q in questions:
        r = svc.answer(q["question"], principal=principal)
        pred = strip_citations(r["answer"])
        gold_docs = set(q["gold_document_ids"])
        got = {s["document_id"] for s in r["sources"]}
        dbg = r.get("retrieval_debug")
        rows.append(Row(arm, q["id"], q["type"], q.get("level", ""), q["question"], q["answer"], r["answer"],
                        exact_match(pred, q["answer"]), f1_score(pred, q["answer"]), supporting_fact_recall(gold_docs, got),
                        all_supporting_found(gold_docs, got),
                        supporting_fact_recall(gold_docs, set(dbg["candidate_doc_ids"])) if dbg else None,
                        r["usage"]["cost_usd"], r["latency_ms"]["total"], r["latency_ms"]["retrieval"],
                        len(dbg["seeds"]) if dbg else None))
    return rows


def slice_rows(rows: list[Row]) -> dict[str, list[Row]]:
    s = {"all": rows, "multi_hop": [r for r in rows if r.qtype in MULTI]}
    for t in sorted({r.qtype for r in rows}):
        s[t] = [r for r in rows if r.qtype == t]
    for lv in ("easy", "medium", "hard"):
        if any(r.level == lv and r.qtype in MULTI for r in rows):
            s[f"multi_hop:{lv}"] = [r for r in rows if r.level == lv and r.qtype in MULTI]
    return {k: v for k, v in s.items() if v}


def summarize(rows: list[Row]) -> dict:
    out = {}
    for name, rs in slice_rows(rows).items():
        cands = [r.cand_recall for r in rs if r.cand_recall is not None]
        out[name] = {"n": len(rs), "em": st.mean(r.em for r in rs), "f1": st.mean(r.f1 for r in rs),
                     "sf_recall": st.mean(r.sf_recall for r in rs), "all_supporting_found": st.mean(r.all_found for r in rs),
                     "candidate_recall": st.mean(cands) if cands else None,
                     "cost_per_query_usd": st.mean(r.cost_usd for r in rs),
                     "p50_latency_ms": percentile([r.latency_ms for r in rs], 50),
                     "p95_latency_ms": percentile([r.latency_ms for r in rs], 95),
                     "mean_seeds": st.mean(r.n_seeds for r in rs if r.n_seeds is not None) if any(r.n_seeds is not None for r in rs) else None}
    return out


def compare(ref: list[Row], rows: list[Row], margin: float = 0.05) -> dict:
    """Paired (same question) deltas vs the reference arm with 95% bootstrap CIs, per slice and metric."""
    ref_by = {r.qid: r for r in ref}
    out = {}
    for name, rs in slice_rows(rows).items():
        res = {}
        for metric in ("em", "f1", "sf_recall", "all_found"):
            d = [getattr(r, metric) - getattr(ref_by[r.qid], metric) for r in rs if r.qid in ref_by]
            mean, lo, hi = paired_bootstrap(d)
            res[metric] = {"delta": mean, "ci95": [lo, hi], "n": len(d)}
        harm_slice = name == "single"
        f = res["f1"]
        res["claim"] = (("no F1 harm SUPPORTED (CI lower bound > -%.2f)" % margin if f["ci95"][0] > -margin else
                         "no F1 harm NOT supported by this data") if harm_slice else
                        ("F1 improvement SUPPORTED (CI lower bound > 0)" if f["ci95"][0] > 0 else
                         "F1 improvement NOT supported by this data (CI includes 0 or negative)"))
        out[name] = res
    return out
