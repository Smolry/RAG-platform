"""Lightweight rule-based router + escalation check (PDF Project 1). Deliberately simple; no DAGs."""
from __future__ import annotations

import re
from dataclasses import dataclass

import yaml


@dataclass
class RouteDecision:
    route: str  # "simple" | "complex"
    score: float
    signals: dict


class RuleRouter:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        kw = sorted((re.escape(k) for k in cfg["keywords"]), key=len, reverse=True)
        self._kw = re.compile(r"(?<![\w-])(" + "|".join(kw) + r")(?![\w-])", re.I)

    @classmethod
    def from_yaml(cls, path: str) -> "RuleRouter":
        with open(path) as f:
            return cls(yaml.safe_load(f))

    @staticmethod
    def count_entities(query: str) -> int:
        """Cheap proxy: capitalised tokens after the first word, ALL-CAPS tokens, tokens with digits, quoted spans."""
        toks = re.findall(r"[A-Za-z0-9][\w.+-]*", query)
        n = 0
        for i, t in enumerate(toks):
            if i > 0 and (t[0].isupper() or (t.isupper() and len(t) > 1)):
                n += 1
            elif any(c.isdigit() for c in t):
                n += 1
        return n + len(re.findall(r'"[^"]+"', query))

    def classify(self, query: str, scores: list[float] | None = None) -> RouteDecision:
        c, w = self.cfg, self.cfg["weights"]
        words = len(query.split())
        kws = sorted({m.lower() for m in self._kw.findall(query)})
        entities = self.count_entities(query)
        multi = query.count("?") > 1 or len(re.findall(r"\b(and|then|also)\b|;", query, re.I)) >= 2
        sig = {"n_words": words, "keywords": kws, "n_entities": entities, "multi_part": multi}
        score = 0.0
        if words >= c["long_query_words"]: score += w["long_query"]
        if words >= c["very_long_query_words"]: score += w["very_long_query"]
        if kws: score += w["keyword"]
        if entities >= c["entity_count_threshold"]: score += w["many_entities"]
        if multi: score += w["multi_part"]
        if scores:
            sig["top_score"], sig["spread"] = scores[0], scores[0] - scores[-1]
            if scores[0] < c["low_top_score"]: score += w["low_top_score"]
            if len(scores) >= 3 and sig["spread"] < c["flat_spread"]: score += w["flat_spread"]
        return RouteDecision("complex" if score >= c["complex_threshold"] else "simple", score, sig)

    def needs_escalation(self, answer: str, n_citations: int, scores: list[float], min_top_score: float) -> tuple[bool, str]:
        """Weak small-model answer? -> rerun on the large model. Returns (escalate, reason)."""
        low = answer.lower()
        if any(p in low for p in self.cfg["escalation_phrases"]): return True, "idk_phrase"
        if n_citations == 0: return True, "no_citation"
        if not scores or scores[0] < min_top_score: return True, "low_retrieval_score"
        return False, ""
