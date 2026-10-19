"""Create a SINGLE-HOP question set from the same corpus, so GraphRAG can be checked for harm on easy questions (PDF step 7).
HotpotQA has no single-hop questions, so these are LLM-written from ONE paragraph each and start validated=false: REVIEW BY HAND.
  python -m scripts.make_singlehop_set --n 60  ->  data/hotpot/questions_single.jsonl
"""
import argparse
import json
import random
import re
from pathlib import Path

from app.core.config import get_settings
from app.generation.llm import LiteLLMClient

P = ("Write ONE question that can be answered using ONLY the passage below, and its SHORT answer (a name, date, number or "
     "yes/no - at most 5 words). The question must contain enough context to be unambiguous without the passage. "
     'Reply ONLY with JSON: {{"question": "...", "answer": "..."}}\nPassage:\n{text}')

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=Path("data/hotpot/manifest.json"))
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", type=Path, default=Path("data/hotpot/questions_single.jsonl"))
    a = ap.parse_args()
    s, llm = get_settings(), LiteLLMClient()
    docs = json.loads(a.manifest.read_text())
    random.Random(a.seed).shuffle(docs)
    out = []
    for d in docs:
        if len(out) >= a.n:
            break
        text = Path(d["path"]).read_text(encoding="utf-8").strip()
        if len(text.split()) < 25:
            continue
        m = re.search(r"\{.*\}", llm.complete([{"role": "user", "content": P.format(text=text)}], s.judge_model).text, re.S)
        try:
            j = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            j = None
        if j and j.get("question") and j.get("answer"):
            out.append({"id": f"single-{len(out):03d}", "question": j["question"], "answer": str(j["answer"]), "type": "single",
                        "level": "single", "supporting_titles": [d["document_id"]], "gold_document_ids": [d["document_id"]],
                        "validated": False})
    a.out.write_text("\n".join(json.dumps(i) for i in out) + "\n")
    print(f"wrote {len(out)} DRAFT single-hop questions to {a.out} - review them, then set validated=true")
