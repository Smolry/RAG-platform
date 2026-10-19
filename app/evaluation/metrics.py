"""Metric definitions (see docs/EVALUATION.md). Pure functions, no I/O."""
from __future__ import annotations

import json
import math
import re
import string
from collections import Counter


def normalize_answer(s: str) -> str:
    """SQuAD/HotpotQA-style normalization: lowercase, strip punctuation + articles + extra whitespace."""
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def strip_citations(s: str) -> str:
    """Remove citation markers like [1] / [2][3] BEFORE scoring: normalize_answer would otherwise keep the digit as a token."""
    return " ".join(re.sub(r"\[\d+\]", " ", s).split())


def all_supporting_found(gold_docs: set[str], retrieved_docs: set[str]) -> float:
    """1.0 only if EVERY gold supporting document was retrieved (what a 2-hop question actually needs)."""
    return float(bool(gold_docs) and gold_docs <= retrieved_docs)


def exact_match(pred: str, gold: str) -> float:
    return float(normalize_answer(pred) == normalize_answer(gold))


def f1_score(pred: str, gold: str) -> float:
    p, g = normalize_answer(pred).split(), normalize_answer(gold).split()
    common = Counter(p) & Counter(g)
    overlap = sum(common.values())
    if not p or not g or overlap == 0:
        return 0.0
    prec, rec = overlap / len(p), overlap / len(g)
    return 2 * prec * rec / (prec + rec)


def retrieval_hit(source_document: str, retrieved_document_ids: list[str]) -> float:
    """1.0 if the gold source document appears among the retrieved chunks' documents."""
    return float(source_document in set(retrieved_document_ids))


def citation_correct(source_document: str, cited_document_ids: list[str]) -> float:
    return float(source_document in set(cited_document_ids))


def supporting_fact_recall(gold_docs: set[str], retrieved_docs: set[str]) -> float:
    """Fraction of gold supporting documents/paragraphs that retrieval returned (multi-hop, Project 3)."""
    return len(gold_docs & retrieved_docs) / len(gold_docs) if gold_docs else 0.0


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0,100]."""
    if not values:
        return float("nan")
    xs = sorted(values)
    k = (len(xs) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


JUDGE_PROMPT = """You are grading a question-answering system.
Question: {question}
Reference answer: {reference}
System answer: {answer}

Is the system answer factually consistent with the reference answer and does it answer the question?
Reply with ONLY JSON: {{"correct": true|false, "reason": "<one short sentence>"}}"""


def parse_judge(text: str) -> tuple[float, str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return 0.0, f"unparseable judge output: {text[:80]}"
    try:
        d = json.loads(m.group(0))
        return float(bool(d.get("correct"))), str(d.get("reason", ""))
    except (json.JSONDecodeError, ValueError):
        return 0.0, f"unparseable judge output: {text[:80]}"
