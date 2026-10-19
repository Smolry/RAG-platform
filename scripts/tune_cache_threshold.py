"""Sweep the semantic-cache similarity threshold 0.85 -> 0.97 over labeled pairs and report false hits / misses.

  python -m scripts.tune_cache_threshold            # uses configured embedder (EMBEDDER_BACKEND / EMBEDDING_MODEL)

Pairs: data/eval/cache_pairs.jsonl  (50 paraphrases that SHOULD hit, 50 near-misses that MUST NOT hit).
  false hit  = near-miss pair with cosine >= t   (the dangerous error: wrong cached answer served)
  false miss = paraphrase pair with cosine <  t   (safe but wasteful: pays for an LLM call)
Selection rule (PDF): the LOWEST threshold with ZERO false hits; if none exists in the sweep, the one with the fewest
false hits (ties -> fewest misses) and the result is flagged. Writes results/cache_threshold_sweep.{csv,json}.
Caveat: pairs are author-written; 100 pairs is a small sample, so re-check on traffic before trusting the 3rd decimal.
"""
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from app.caching.semantic_cache import normalize_query
from app.core.config import get_settings
from app.retrieval.embedder import build_embedder


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


def sweep(pairs: list[dict], sims: list[float], lo=0.85, hi=0.97, step=0.01) -> list[dict]:
    pos = [s for p, s in zip(pairs, sims) if p["should_hit"]]
    neg = [s for p, s in zip(pairs, sims) if not p["should_hit"]]
    rows, t = [], lo
    while t <= hi + 1e-9:
        fh, fm = sum(s >= t for s in neg), sum(s < t for s in pos)
        rows.append({"threshold": round(t, 2), "false_hits": fh, "false_hit_rate": fh / len(neg),
                     "false_misses": fm, "false_miss_rate": fm / len(pos), "hit_rate_on_paraphrases": 1 - fm / len(pos)})
        t += step
    return rows


def select(rows: list[dict]) -> tuple[dict, str]:
    safe = [r for r in rows if r["false_hits"] == 0]
    if safe:
        return min(safe, key=lambda r: r["threshold"]), "lowest threshold with zero false hits"
    best = min(rows, key=lambda r: (r["false_hits"], r["false_misses"]))
    return best, "NO threshold in the sweep has zero false hits; picked fewest false hits - treat as UNSAFE and investigate"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, default=Path("data/eval/cache_pairs.jsonl"))
    ap.add_argument("--out-dir", type=Path, default=Path("results"))
    a = ap.parse_args()
    s, emb = get_settings(), None
    emb = build_embedder(s)
    pairs = [json.loads(l) for l in a.pairs.read_text().splitlines() if l.strip()]
    v1 = emb.embed([normalize_query(p["q1"]) for p in pairs])
    v2 = emb.embed([normalize_query(p["q2"]) for p in pairs])
    sims = [cos(x, y) for x, y in zip(v1, v2)]
    rows = sweep(pairs, sims)
    chosen, rule = select(rows)
    pos = sorted(sim for p, sim in zip(pairs, sims) if p["should_hit"])
    neg = sorted((sim, p["q1"], p["q2"]) for p, sim in zip(pairs, sims) if not p["should_hit"])
    out = {"timestamp": datetime.now(timezone.utc).isoformat(), "embedder_backend": s.embedder_backend,
           "embedding_model": s.embedding_model, "n_paraphrase": len(pos), "n_near_miss": len(neg),
           "NOT_FOR_REPORTING": s.embedder_backend == "hash", "selection_rule": rule, "chosen": chosen,
           "paraphrase_sim_min_median_max": [pos[0], pos[len(pos) // 2], pos[-1]],
           "hardest_near_misses": [{"sim": round(x, 4), "q1": q1, "q2": q2} for x, q1, q2 in reversed(neg[-5:])],
           "sweep": rows}
    a.out_dir.mkdir(exist_ok=True)
    (a.out_dir / "cache_threshold_sweep.json").write_text(json.dumps(out, indent=2))
    with (a.out_dir / "cache_threshold_sweep.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"{'t':>5} {'false_hits':>10} {'false_miss':>10}")
    for r in rows:
        print(f"{r['threshold']:>5} {r['false_hits']:>10} {r['false_misses']:>10}" + ("   <- chosen" if r is chosen else ""))
    print(f"\nrule: {rule}")
    if chosen["false_hits"] == 0:
        print(f"Set CACHE_THRESHOLD={chosen['threshold']} in .env")
    else:
        print("!! No safe threshold in 0.85-0.97: do NOT lower CACHE_THRESHOLD; keep the conservative default (0.97) or fix the embedder/pairs.")
    if out["NOT_FOR_REPORTING"]:
        print("!! hash embedder: plumbing check only, NOT a reportable result")


if __name__ == "__main__":
    main()
