"""Build the Project 3 corpus + question set from HotpotQA (distractor setting).

  python -m scripts.load_hotpotqa --n 120 --seed 7                       # downloads via Hugging Face `datasets`
  python -m scripts.load_hotpotqa --json hotpot_dev_distractor_v1.json   # or use the official JSON file
Outputs (data/hotpot/): docs/*.md (one paragraph = one document), manifest.json (same format as scripts.ingest --manifest),
questions.jsonl (id, question, answer, type[bridge|comparison], level, supporting_titles, gold_document_ids), stats.json.
Every sampled question brings its 10 paragraphs (2 gold + 8 distractors); the corpus is the UNION across questions, so retrieval
runs over a shared pool exactly like a real index. Dataset license: CC BY-SA 4.0 (keep attribution; see stats.json).
"""
import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path


def slug(title: str) -> str:
    return re.sub(r"[^\w\-\.\(\) ]+", "_", title).strip().replace(" ", "_")[:150]


def normalize_example(ex: dict) -> dict:
    """Accepts the Hugging Face schema (context/supporting_facts as dicts of lists) or the official JSON schema (lists of pairs)."""
    ctx, sf = ex["context"], ex["supporting_facts"]
    if isinstance(ctx, dict):
        paragraphs = dict(zip(ctx["title"], ctx["sentences"]))
        sup = list(dict.fromkeys(sf["title"]))
    else:
        paragraphs = {t: s for t, s in ctx}
        sup = list(dict.fromkeys(t for t, _ in sf))
    return {"id": ex.get("id") or ex.get("_id"), "question": ex["question"], "answer": ex["answer"], "type": ex["type"],
            "level": ex["level"], "supporting_titles": sup,
            "paragraphs": {t: " ".join(x.strip() for x in s).strip() for t, s in paragraphs.items()}}


def sample(examples: list[dict], n: int, seed: int) -> list[dict]:
    """Deterministic, stratified by question type so bridge/comparison keep their dataset proportions."""
    rng, by_type = random.Random(seed), {}
    for e in examples:
        by_type.setdefault(e["type"], []).append(e)
    total, out = len(examples), []
    for t, items in sorted(by_type.items()):
        rng.shuffle(items)
        out += items[: max(1, round(n * len(items) / total))]
    rng.shuffle(out)
    return out[:n]


def build(examples: list[dict], tenant: str, out: Path) -> dict:
    corpus: dict[str, str] = {}
    conflicts, used_slugs = 0, {}
    questions, skipped = [], 0
    for e in examples:
        if not all(t in e["paragraphs"] and e["paragraphs"][t] for t in e["supporting_titles"]):
            skipped += 1
            continue
        for t, text in e["paragraphs"].items():
            doc = f"{t}: {text}"
            if t in corpus and corpus[t] != doc:
                conflicts += 1  # same title, different text: keep the first (documented in stats)
            corpus.setdefault(t, doc)
        questions.append({"id": e["id"], "question": e["question"], "answer": e["answer"], "type": e["type"],
                          "level": e["level"], "supporting_titles": e["supporting_titles"],
                          "gold_document_ids": [f"{tenant}/{slug(t)}" for t in e["supporting_titles"]]})
    (out / "docs").mkdir(parents=True, exist_ok=True)
    manifest = []
    for t, text in sorted(corpus.items()):
        s = slug(t)
        if s in used_slugs and used_slugs[s] != t:
            raise SystemExit(f"slug collision between titles {used_slugs[s]!r} and {t!r}")
        used_slugs[s] = t
        p = out / "docs" / f"{s}.md"
        p.write_text(text + "\n", encoding="utf-8")
        manifest.append({"document_id": f"{tenant}/{s}", "path": str(p), "tenant_id": tenant, "classification": "public",
                         "allowed_roles": ["reader"], "allowed_users": []})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "questions.jsonl").write_text(
        "\n".join(json.dumps(q) for q in questions) + "\n",
        encoding="utf-8",
    )
    stats = {"dataset": "HotpotQA distractor/validation", "license": "CC BY-SA 4.0", "n_questions": len(questions),
             "n_skipped_missing_gold_paragraph": skipped, "n_documents": len(corpus), "title_text_conflicts": conflicts,
             "types": dict(Counter(q["type"] for q in questions)), "levels": dict(Counter(q["level"] for q in questions)),
             "tenant": tenant}
    (out / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


def load_examples(json_path: Path | None) -> list[dict]:
    if json_path:
        return [normalize_example(e) for e in json.loads(json_path.read_text(encoding="utf-8"))]
    from datasets import load_dataset  # pip install datasets
    return [normalize_example(e) for e in load_dataset("hotpotqa/hotpot_qa", "distractor", split="validation")]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120, help="questions (PDF: 100-200)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--tenant", default="hotpot")
    ap.add_argument("--out", type=Path, default=Path("data/hotpot"))
    a = ap.parse_args()
    print(json.dumps(build(sample(load_examples(a.json), a.n, a.seed), a.tenant, a.out), indent=2))
