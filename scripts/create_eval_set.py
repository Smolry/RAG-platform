"""Generate a DRAFT eval set from your corpus with an LLM, then hand-review it.
python -m scripts.create_eval_set --path data/raw --n 80
python -m scripts.create_eval_set --review data/eval/eval_set.jsonl     # interactive keep/edit/drop
The PDF: "Check the generated ones by hand. This file is the most valuable thing in your repo."
Items start with validated=false; run_evaluation --only-validated uses reviewed items only."""
import argparse
import json
import random
import re
from pathlib import Path

from app.core.config import get_settings
from app.generation.llm import LiteLLMClient

PROMPT = """From the passage below write ONE specific question that can be answered ONLY from this passage,
and a short reference answer. Reply ONLY with JSON: {{"question": "...", "reference_answer": "..."}}
Passage:
{passage}"""


def generate(path: Path, n: int, out: Path, seed: int, prefix: str) -> None:
    s, llm = get_settings(), LiteLLMClient()
    files = [f for f in sorted(path.rglob("*")) if f.suffix.lower() in {".txt", ".md"} and f.is_file()]
    random.Random(seed).shuffle(files)
    items = []
    for f in files:
        if len(items) >= n:
            break
        words = f.read_text(errors="ignore").split()
        if len(words) < 80:
            continue
        start = random.Random(seed + len(items)).randint(0, max(0, len(words) - 300))
        passage = " ".join(words[start : start + 300])
        m = re.search(r"\{.*\}", llm.complete([{"role": "user", "content": PROMPT.format(passage=passage)}],
                                              s.judge_model).text, re.S)
        if not m:
            continue
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        items.append({"question": d["question"], "reference_answer": d["reference_answer"],
                      "source_document": f"{prefix}/{f.relative_to(path).with_suffix('').as_posix()}",
                      "validated": False})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(i) for i in items) + "\n")
    print(f"wrote {len(items)} DRAFT items to {out} - now run --review")


def review(p: Path) -> None:
    items = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    kept = []
    for i, it in enumerate(items, 1):
        if it.get("validated"):
            kept.append(it); continue
        print(f"\n[{i}/{len(items)}] source: {it['source_document']}\nQ: {it['question']}\nA: {it['reference_answer']}")
        c = input("(k)eep / (e)dit answer / (d)rop > ").strip().lower()
        if c == "e":
            it["reference_answer"] = input("new answer: ").strip(); c = "k"
        if c == "k":
            it["validated"] = True; kept.append(it)
    p.write_text("\n".join(json.dumps(i) for i in kept) + "\n")
    print(f"kept {len(kept)} validated items")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path)
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--out", type=Path, default=Path("data/eval/eval_set.jsonl"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--review", type=Path)
    ap.add_argument("--doc-prefix", default="default", help="must equal the --tenant used at ingest (document ids are tenant/path)")
    a = ap.parse_args()
    review(a.review) if a.review else generate(a.path, a.n, a.out, a.seed, a.doc_prefix)
