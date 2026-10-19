"""Evaluate the rule router on the labeled set (data/eval/router_set.jsonl, 50 simple + 50 complex, dev/test split).
  python -m scripts.eval_router --split dev      # use while tuning configs/routing.yaml
  python -m scripts.eval_router --split test     # report ONCE, after tuning is frozen
Query-only signals are evaluated here (retrieval-score signals need a live index and are excluded; stated in output).
Labels are author-assigned: simple = single-fact lookup; complex = comparison / explanation / multi-part synthesis.
"""
import argparse
import json
from pathlib import Path

from app.routing.router import RuleRouter


def evaluate(router: RuleRouter, items: list[dict]) -> dict:
    tp = fp = tn = fn = 0
    errors = []
    for it in items:
        pred = router.classify(it["query"]).route
        gold = it["label"]
        tp += pred == "complex" and gold == "complex"
        tn += pred == "simple" and gold == "simple"
        fp += pred == "complex" and gold == "simple"   # costs money (large model used needlessly)
        fn += pred == "simple" and gold == "complex"   # risks quality (small model on a hard query) -> escalation backstop
        if pred != gold:
            errors.append({"query": it["query"], "gold": gold, "pred": pred})
    n = len(items)
    return {"n": n, "accuracy": (tp + tn) / n, "confusion": {"tp": tp, "tn": tn, "fp_wasteful": fp, "fn_risky": fn},
            "complex_recall": tp / (tp + fn) if tp + fn else None, "complex_precision": tp / (tp + fp) if tp + fp else None,
            "errors": errors}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "stress", "all"], default="dev")
    ap.add_argument("--set", type=Path, default=Path("data/eval/router_set.jsonl"))
    ap.add_argument("--config", default="configs/routing.yaml")
    ap.add_argument("--out", type=Path, default=Path("results/router_eval.json"))
    a = ap.parse_args()
    items = [json.loads(l) for l in a.set.read_text().splitlines() if l.strip()]
    if a.split != "all":
        items = [i for i in items if i["split"] == a.split]
    res = evaluate(RuleRouter.from_yaml(a.config), items)
    res.update(split=a.split, signals="query-only (no retrieval scores)", config=a.config)
    a.out.parent.mkdir(exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != "errors"}, indent=2))
    for e in res["errors"]:
        print(f"  [{e['gold']}->{e['pred']}] {e['query']}")
