"""Entity resolution (Project 3, PDF step 3: "the most underrated step").

WHY: extraction runs per chunk, so one real-world entity arrives as many surface forms ("J. Smith", "John Smith", "Smith, John",
"Acme", "Acme Inc."). If they stay separate nodes, a relation extracted from chunk A and a mention in chunk B never meet and
traversal finds nothing. The opposite error (merging DIFFERENT entities, e.g. two people called "John Smith" or "Paris" the
person vs the city) silently creates false paths. This module is deliberately PRECISION-first:

  * only entities of the SAME TYPE can merge;
  * exact normalised match merges (cheap, safe);
  * otherwise a candidate needs a STRING rule (initial-compatible person names | near-identical fuzzy ratio | corporate-suffix
    variants) AND an EMBEDDING-similarity gate;
  * a name compatible with >1 existing cluster is left UNMERGED and logged as ambiguous (never guess);
  * clusters are matched to their REPRESENTATIVE only (no transitive chaining J.Smith~John~... ).
Every decision is recorded in `merges` / `ambiguous` so errors can be audited (see docs/LIMITATIONS.md).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

from app.graph.extraction import fold

_ARTICLES = {"the", "a", "an"}
_CORP_SUFFIX = {"inc", "incorporated", "ltd", "limited", "llc", "corp", "corporation", "co", "company", "plc", "gmbh", "sa", "ag"}


def norm_key(name: str) -> str:
    toks = fold(name).split()
    while toks and toks[0] in _ARTICLES:
        toks = toks[1:]
    return " ".join(toks)


def _person_compatible(a: list[str], b: list[str]) -> bool:
    """Same last token; every token of the shorter name matches a token of the longer one (equal, or initial of it), in order."""
    if len(a) > len(b):
        a, b = b, a
    if not a or a[-1] != b[-1] or len(a) == len(b) and a == b:
        return a == b and bool(a)
    j = 0
    for tok in a[:-1]:
        while j < len(b) - 1 and not (b[j] == tok or (len(tok) == 1 and b[j].startswith(tok))):
            j += 1
        if j >= len(b) - 1:
            return False
        j += 1
    return True


def string_rule(a: str, b: str, etype: str, fuzzy: int) -> str | None:
    """Name of the string rule that fires, or None. `a`,`b` are norm_keys (different strings)."""
    ta, tb = a.split(), b.split()
    if etype == "PERSON" and min(len(ta), len(tb)) >= 1 and _person_compatible(ta, tb) and len(ta) != len(tb) or \
            (etype == "PERSON" and len(ta) == len(tb) and _person_compatible(ta, tb) and a != b):
        # a bare surname ("Reed") must NOT merge into a full name by this rule: demand >=2 tokens on the shorter side
        if min(len(ta), len(tb)) >= 2:
            return "person_initials"
    strip = lambda t: [x for x in t if x not in _CORP_SUFFIX]
    if etype in ("ORGANIZATION", "PRODUCT", "OTHER") and strip(ta) == strip(tb) and strip(ta):
        return "corporate_suffix"
    if fuzz.ratio(a, b) >= fuzzy and min(len(a), len(b)) >= 5:
        return "fuzzy_ratio"
    return None


@dataclass
class Mention:
    name: str
    type: str
    chunk_id: str


@dataclass
class EntityNode:
    id: str
    name: str
    type: str
    aliases: list[str]
    mention_count: int  # distinct chunks


@dataclass
class ResolutionResult:
    entities: list[EntityNode]
    key_to_id: dict[tuple[str, str], str]  # (type, norm_key) -> entity id
    merges: list[dict] = field(default_factory=list)
    ambiguous: list[dict] = field(default_factory=list)
    n_raw: int = 0

    def resolve(self, name: str, etype: str) -> str | None:
        return self.key_to_id.get((etype, norm_key(name)))


def resolve_entities(mentions: list[Mention], embed: Callable[[list[str]], list[list[float]]] | None = None,
                     fuzzy: int = 92, embed_threshold: float = 0.80) -> ResolutionResult:
    by_type: dict[str, dict[str, dict]] = defaultdict(dict)  # type -> key -> {surfaces:Counter, chunks:set}
    for m in mentions:
        k = norm_key(m.name)
        if not k:
            continue
        d = by_type[m.type].setdefault(k, {"surfaces": Counter(), "chunks": set()})
        d["surfaces"][" ".join(m.name.split())] += 1
        d["chunks"].add(m.chunk_id)
    raw_keys = sum(len(v) for v in by_type.values())
    entities: list[EntityNode] = []
    key_to_id: dict[tuple[str, str], str] = {}
    merges: list[dict] = []
    ambiguous: list[dict] = []

    def sim(a: str, b: str, cache: dict) -> float:
        if embed is None:
            return 1.0  # no embedder supplied: string rule alone decides (documented; weaker)
        for x in (a, b):
            if x not in cache:
                cache[x] = embed([x])[0]
        va, vb = cache[a], cache[b]
        na = sum(x * x for x in va) ** 0.5 or 1.0
        nb = sum(x * x for x in vb) ** 0.5 or 1.0
        return sum(x * y for x, y in zip(va, vb)) / (na * nb)

    cache: dict[str, list[float]] = {}
    for etype, keys in by_type.items():
        # most-mentioned (then longest) first => representatives are the fuller, more common forms
        order = sorted(keys, key=lambda k: (-len(keys[k]["chunks"]), -len(k), k))
        clusters: list[dict] = []  # {rep, members:[keys]}
        for k in order:
            cands = []
            for c in clusters:
                rule = string_rule(k, c["rep"], etype, fuzzy)
                if rule:
                    s = sim(f"{k} ({etype})", f"{c['rep']} ({etype})", cache)
                    if s >= embed_threshold:
                        cands.append((c, rule, s))
            if len(cands) == 1:
                c, rule, s = cands[0]
                c["members"].append(k)
                merges.append({"type": etype, "merged": k, "into": c["rep"], "rule": rule, "embed_sim": round(s, 3)})
            else:
                if len(cands) > 1:
                    ambiguous.append({"type": etype, "name": k, "candidates": [c["rep"] for c, _, _ in cands]})
                clusters.append({"rep": k, "members": [k]})
        for c in clusters:
            members = c["members"]
            chunks = set().union(*(keys[m]["chunks"] for m in members))
            surfaces = Counter()
            for m in members:
                surfaces.update(keys[m]["surfaces"])
            canonical = max(keys[c["rep"]]["surfaces"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
            eid = f"{etype}:{c['rep']}"
            aliases = sorted({m for m in members} | {norm_key(s) for s in surfaces})
            entities.append(EntityNode(eid, canonical, etype, aliases, len(chunks)))
            for m in members:
                key_to_id[(etype, m)] = eid
    return ResolutionResult(entities, key_to_id, merges, ambiguous, raw_keys)
