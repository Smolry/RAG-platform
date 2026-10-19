"""Question entity extraction + entity linking (PDF Project 3 step 5b).

Two ways to find question entities (settings.question_entities):
  "llm"   : one cheap LLM call, strict JSON, every name must literally occur in the question (same grounding rule as build).
  "ngram" : dictionary match of question n-grams against the graph's known aliases. Zero LLM cost, deterministic.
Linking maps a name to graph entity ids: exact alias match first, then a high-cutoff fuzzy match. Ambiguity is kept
(<= max_candidates ids per name) because for RETRIEVAL a wrong extra seed only adds noise, while a missing seed loses the hop.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from app.graph.extraction import ExtractStats, _json, fold, grounded
from app.graph.resolution import norm_key
from app.graph.store import GEntity

Q_PROMPT = """List the named entities (people, places, organizations, works, events) that are mentioned in the question.
Reply with ONLY JSON: {{"entities": ["name", ...]}}. Each name must appear in the question text exactly as written.
Question: {question}"""

_STOP_SINGLE = {"what", "which", "who", "whom", "whose", "where", "when", "why", "how", "name", "does", "did", "was", "were",
                "are", "the", "film", "movie", "song", "band", "city", "country", "actor", "director", "author", "born", "company",
                "year", "person", "team", "album", "book", "show", "series", "both", "also", "same", "american", "british"}


@dataclass(frozen=True)
class Link:
    mention: str
    entity_id: str
    method: str  # exact | fuzzy | ngram
    score: float


class EntityLinker:
    def __init__(self, entities: list[GEntity], fuzzy: int = 92, max_candidates: int = 2):
        self.fuzzy, self.max_candidates = fuzzy, max_candidates
        self.entities = {e.id: e for e in entities}
        idx: dict[str, list[str]] = defaultdict(list)
        for e in entities:
            for k in {norm_key(e.name), *(norm_key(a) for a in e.aliases)}:
                if k and e.id not in idx[k]:
                    idx[k].append(e.id)
        for k in idx:
            idx[k].sort(key=lambda i: -self.entities[i].mention_count)
        self.index, self.keys = dict(idx), list(idx)

    def link_name(self, name: str) -> list[Link]:
        k = norm_key(name)
        if not k:
            return []
        if k in self.index:
            return [Link(name, i, "exact", 1.0) for i in self.index[k][: self.max_candidates]]
        if len(k) >= 5 and self.keys:
            m = process.extractOne(k, self.keys, scorer=fuzz.ratio, score_cutoff=self.fuzzy)
            if m:
                return [Link(name, i, "fuzzy", m[1] / 100) for i in self.index[m[0]][: self.max_candidates]]
        return []

    def link_names(self, names: list[str]) -> list[Link]:
        out, seen = [], set()
        for n in names:
            for l in self.link_name(n):
                if l.entity_id not in seen:
                    seen.add(l.entity_id)
                    out.append(l)
        return out

    def link_ngrams(self, question: str, max_n: int = 6) -> list[Link]:
        """Greedy longest-match, non-overlapping. Single-token matches must be capitalised and not a question word."""
        raw = re.findall(r"\w+", question)
        toks = [fold(t) for t in raw]
        covered, out, seen = [False] * len(toks), [], set()
        for n in range(min(max_n, len(toks)), 0, -1):
            for i in range(len(toks) - n + 1):
                if any(covered[i : i + n]) or not all(toks[i : i + n]):
                    continue
                key = norm_key(" ".join(toks[i : i + n]))
                if key not in self.index:
                    continue
                if n == 1 and (len(key) < 4 or key in _STOP_SINGLE or not raw[i][:1].isupper()):
                    continue
                for j in range(i, i + n):
                    covered[j] = True
                for eid in self.index[key][: self.max_candidates]:
                    if eid not in seen:
                        seen.add(eid)
                        out.append(Link(" ".join(raw[i : i + n]), eid, "ngram", 1.0))
        return out


def extract_question_entities(llm, model: str, prices, question: str, stats: ExtractStats) -> list[str]:
    res = llm.complete([{"role": "user", "content": Q_PROMPT.format(question=question)}], model)
    stats.calls += 1
    stats.input_tokens += res.input_tokens
    stats.output_tokens += res.output_tokens
    stats.cost_usd += prices.cost(res.model, res.input_tokens, res.output_tokens)
    try:
        raw = _json(res.text).get("entities") or []
    except (ValueError, AttributeError):
        return []
    names, seen = [], set()
    for n in raw:
        if isinstance(n, str) and n.strip() and grounded(n, question) and fold(n) not in seen:
            seen.add(fold(n))
            names.append(" ".join(n.split()))
    return names
