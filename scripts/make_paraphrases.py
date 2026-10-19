"""Add a 'paraphrase' field to each eval item (LLM-written, meaning-preserving). REVIEW the output by hand:
a paraphrase that changes the question's meaning would inflate 'false cache hit' counts.
  python -m scripts.make_paraphrases --eval-set data/eval/eval_set.jsonl"""
import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.generation.llm import LiteLLMClient

P = ("Rewrite the question so it asks EXACTLY the same thing with different wording and word order. "
     "Do not add or drop any constraint, entity, or number. Reply with only the rewritten question.\nQuestion: {q}")
ap = argparse.ArgumentParser()
ap.add_argument("--eval-set", type=Path, default=Path("data/eval/eval_set.jsonl"))
a = ap.parse_args()
s, llm = get_settings(), LiteLLMClient()
items = [json.loads(l) for l in a.eval_set.read_text().splitlines() if l.strip()]
for it in items:
    if not it.get("paraphrase"):
        it["paraphrase"] = llm.complete([{"role": "user", "content": P.format(q=it["question"])}], s.judge_model).text.strip()
a.eval_set.write_text("\n".join(json.dumps(i) for i in items) + "\n")
print(f"paraphrased {len(items)} questions -> {a.eval_set}; now review them by hand")
