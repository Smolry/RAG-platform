"""LLM extraction -> strictly validated ChunkExtraction.  Per-item validation: one bad item never poisons the chunk.

Rejection reasons (all counted; surfaced in the build report so extraction quality is visible, not hidden):
  invalid_json | bad_entity_schema | bad_entity_type | ungrounded_entity | bad_relation_schema | bad_relation_type |
  dangling_relation | self_relation
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.graph.schema import (ENTITY_ALIASES, ENTITY_TYPES, RELATION_ALIASES, RELATION_TYPES, ChunkExtraction, Entity,
                              GroundedRelation, Relation)

PROMPT = """Extract a knowledge graph from the passage. Reply with ONLY JSON:
{{"entities": [{{"name": "...", "type": "<ENTITY_TYPE>"}}], "relations": [{{"source": "<entity name>", "type": "<RELATION_TYPE>", "target": "<entity name>"}}]}}
ENTITY_TYPE must be one of: {etypes}
RELATION_TYPE must be one of: {rtypes}
Rules: only entities that literally appear in the passage; relation source/target must be names from your entity list;
use RELATED_TO only if no specific type fits; no commentary.
Passage:
{text}"""


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", s).split())


def grounded(name: str, chunk_text: str) -> bool:
    """Anti-hallucination: the entity name (accent/punctuation-insensitive) must occur in the chunk text."""
    n, t = fold(name), fold(chunk_text)
    return bool(n) and (f" {n} " in f" {t} ")


@dataclass
class Rejections:
    counts: Counter = field(default_factory=Counter)
    examples: list[dict] = field(default_factory=list)

    def add(self, reason: str, item) -> None:
        self.counts[reason] += 1
        if len(self.examples) < 20:
            self.examples.append({"reason": reason, "item": item})


def _json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no json object")
    return json.loads(m.group(0))


def validate_extraction(raw: str, chunk_id: str, chunk_text: str, rej: Rejections | None = None) -> ChunkExtraction:
    rej = rej if rej is not None else Rejections()
    try:
        data = _json(raw)
        assert isinstance(data, dict)
    except (ValueError, AssertionError, json.JSONDecodeError):
        rej.add("invalid_json", raw[:100])
        return ChunkExtraction(chunk_id=chunk_id)
    ents: dict[str, Entity] = {}
    for e in data.get("entities") or []:
        if not isinstance(e, dict):
            rej.add("bad_entity_schema", e); continue
        t = str(e.get("type", "")).strip().upper().replace(" ", "_")
        t = ENTITY_ALIASES.get(t, t)
        if t not in ENTITY_TYPES:
            rej.add("bad_entity_type", e); continue
        try:
            ent = Entity(name=str(e.get("name", "")), type=t)
        except ValidationError:
            rej.add("bad_entity_schema", e); continue
        if not grounded(ent.name, chunk_text):
            rej.add("ungrounded_entity", e); continue
        ents.setdefault(fold(ent.name), ent)
    rels: dict[tuple, GroundedRelation] = {}
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            rej.add("bad_relation_schema", r); continue
        t = str(r.get("type", "")).strip().upper().replace(" ", "_")
        t = RELATION_ALIASES.get(t, t)
        if t not in RELATION_TYPES:
            rej.add("bad_relation_type", r); continue
        try:
            rel = Relation(source=str(r.get("source", "")), type=t, target=str(r.get("target", "")))
        except ValidationError:
            rej.add("bad_relation_schema", r); continue
        s, tg = fold(rel.source), fold(rel.target)
        if s not in ents or tg not in ents:
            rej.add("dangling_relation", r); continue  # endpoints must be validated, grounded entities
        if s == tg:
            rej.add("self_relation", r); continue
        rels.setdefault((s, rel.type, tg), GroundedRelation(source=ents[s].name, type=rel.type, target=ents[tg].name,
                                                            evidence_chunk_id=chunk_id))
    return ChunkExtraction(chunk_id=chunk_id, entities=list(ents.values()), relations=list(rels.values()))


@dataclass
class ExtractStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    retries: int = 0


def extract_chunk(llm, model: str, prices, chunk_id: str, text: str, stats: ExtractStats, rej: Rejections,
                  max_retries: int = 1) -> ChunkExtraction:
    prompt = PROMPT.format(etypes=", ".join(ENTITY_TYPES), rtypes=", ".join(RELATION_TYPES), text=text)
    for attempt in range(max_retries + 1):
        res = llm.complete([{"role": "user", "content": prompt}], model)
        stats.calls += 1; stats.input_tokens += res.input_tokens; stats.output_tokens += res.output_tokens
        stats.cost_usd += prices.cost(res.model, res.input_tokens, res.output_tokens)
        try:
            _json(res.text)
        except (ValueError, json.JSONDecodeError):
            if attempt < max_retries:
                stats.retries += 1
                continue
        return validate_extraction(res.text, chunk_id, text, rej)
    return ChunkExtraction(chunk_id=chunk_id)
