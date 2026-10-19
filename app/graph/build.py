"""Graph build pipeline (Project 3): chunks -> LLM extraction (cached, resumable, cost-tracked) -> per-tenant entity
resolution -> store.load.  Every number needed for the 'indexing cost' claim is produced here."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from app.graph.extraction import PROMPT, ExtractStats, Rejections, extract_chunk, fold, validate_extraction
from app.graph.resolution import EntityNode, Mention, ResolutionResult, norm_key, resolve_entities
from app.graph.schema import ChunkExtraction
from app.graph.store import GChunk, GEdge, GEntity, GraphStore

EXTRACTION_VERSION = "v1"


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{EXTRACTION_VERSION}|{model}|{PROMPT}|{text}".encode()).hexdigest()


class ExtractionCache:
    """Append-only JSONL of RAW LLM output + usage. Re-validation on load means schema/grounding fixes cost nothing."""

    def __init__(self, path: str | Path | None):
        self.path, self.lock, self.data = (Path(path) if path else None), threading.Lock(), {}
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    self.data[d["key"]] = d

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, raw: str, usage: dict) -> None:
        d = {"key": key, "raw": raw, "usage": usage}
        with self.lock:
            self.data[key] = d
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps(d) + "\n")


class _Capture:
    def __init__(self, llm):
        self.llm, self.last = llm, None

    def complete(self, messages, model, temperature=0.0):
        self.last = self.llm.complete(messages, model, temperature)
        return self.last


def identity_resolution(mentions: list[Mention]) -> ResolutionResult:
    """NO entity resolution (ablation only): every distinct (type, normalised name) is its own node."""
    seen: dict[tuple[str, str], dict] = {}
    for m in mentions:
        k = norm_key(m.name)
        if k:
            d = seen.setdefault((m.type, k), {"chunks": set(), "name": " ".join(m.name.split())})
            d["chunks"].add(m.chunk_id)
    ents = [EntityNode(f"{t}:{k}", d["name"], t, [k], len(d["chunks"])) for (t, k), d in seen.items()]
    return ResolutionResult(ents, {(t, k): f"{t}:{k}" for (t, k) in seen}, [], [], len(seen))


@dataclass
class BuildReport:
    n_chunks: int = 0
    tenants: list[str] = field(default_factory=list)
    llm_calls_total: int = 0           # calls needed to build this graph (cached + fresh)
    llm_calls_fresh: int = 0           # calls made in THIS run
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    indexing_cost_usd: float = 0.0     # total indexing cost incl. cached calls (what a from-scratch build costs)
    fresh_cost_usd: float = 0.0
    rejections: dict = field(default_factory=dict)
    entities_distinct_before_resolution: int = 0
    entities_after_resolution: int = 0
    merges: int = 0
    ambiguous: int = 0
    relations: int = 0
    relations_dropped_self_after_merge: int = 0
    mentions: int = 0
    seconds: float = 0.0
    resolution_enabled: bool = True
    extraction_model: str = ""


def build_graph(chunks: list[GChunk], llm, model: str, prices, store: GraphStore, embed: Callable | None = None,
                cache_path: str | Path | None = None, workers: int = 4, resolve: bool = True, fuzzy: int = 92,
                embed_threshold: float = 0.80, max_retries: int = 1, progress: Callable[[int, int], None] | None = None):
    t0 = time.perf_counter()
    cache, lock = ExtractionCache(cache_path), threading.Lock()
    rep = BuildReport(n_chunks=len(chunks), resolution_enabled=resolve, extraction_model=model,
                      tenants=sorted({c.tenant_id for c in chunks}))
    rej_total, done = Rejections(), [0]
    extractions: dict[str, ChunkExtraction] = {}

    def work(c: GChunk) -> None:
        key = cache_key(model, c.text)
        rej, stats = Rejections(), ExtractStats()
        hit = cache.get(key)
        if hit:
            ext, u, fresh = validate_extraction(hit["raw"], c.chunk_id, c.text, rej), hit["usage"], False
        else:
            cap = _Capture(llm)
            ext = extract_chunk(cap, model, prices, c.chunk_id, c.text, stats, rej, max_retries)
            u = {"calls": stats.calls, "input_tokens": stats.input_tokens, "output_tokens": stats.output_tokens,
                 "cost_usd": stats.cost_usd, "retries": stats.retries}
            cache.put(key, cap.last.text if cap.last else "", u)
            fresh = True
        with lock:
            extractions[c.chunk_id] = ext
            rep.llm_calls_total += u["calls"]; rep.retries += u["retries"]
            rep.input_tokens += u["input_tokens"]; rep.output_tokens += u["output_tokens"]; rep.indexing_cost_usd += u["cost_usd"]
            if fresh:
                rep.llm_calls_fresh += u["calls"]; rep.fresh_cost_usd += u["cost_usd"]
            rej_total.counts.update(rej.counts)
            for ex in rej.examples:
                if len(rej_total.examples) < 50:
                    rej_total.examples.append(ex)
            done[0] += 1
            if progress:
                progress(done[0], len(chunks))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, chunks))
    rep.rejections = dict(rej_total.counts)

    audit = {"merges": [], "ambiguous": [], "rejection_examples": rej_total.examples}
    by_tenant: dict[str, list[GChunk]] = defaultdict(list)
    for c in chunks:
        by_tenant[c.tenant_id].append(c)
    for tenant, tchunks in by_tenant.items():
        mentions = [Mention(e.name, e.type, c.chunk_id) for c in tchunks for e in extractions[c.chunk_id].entities]
        res = resolve_entities(mentions, embed, fuzzy, embed_threshold) if resolve else identity_resolution(mentions)
        rep.entities_distinct_before_resolution += res.n_raw
        rep.entities_after_resolution += len(res.entities)
        rep.merges += len(res.merges); rep.ambiguous += len(res.ambiguous)
        audit["merges"] += [{"tenant": tenant, **m} for m in res.merges]
        audit["ambiguous"] += [{"tenant": tenant, **a} for a in res.ambiguous]
        eid = lambda e_id: f"{tenant}|{e_id}"
        gents = [GEntity(eid(e.id), e.name, e.type, tenant, tuple(e.aliases), e.mention_count) for e in res.entities]
        evidence: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        men: set[tuple[str, str]] = set()
        for c in tchunks:
            ext = extractions[c.chunk_id]
            ctype = {fold(e.name): e.type for e in ext.entities}
            for e in ext.entities:
                rid = res.resolve(e.name, e.type)
                if rid:
                    men.add((c.chunk_id, eid(rid)))
            for r in ext.relations:
                s, t = res.resolve(r.source, ctype[fold(r.source)]), res.resolve(r.target, ctype[fold(r.target)])
                if not s or not t:
                    continue
                if s == t:
                    rep.relations_dropped_self_after_merge += 1
                    continue
                evidence[(eid(s), r.type, eid(t))].add(c.chunk_id)
        edges = [GEdge(s, t, o, tenant, tuple(sorted(ev))) for (s, t, o), ev in evidence.items()]
        rep.relations += len(edges); rep.mentions += len(men)
        store.clear_tenant(tenant)
        store.load(tchunks, gents, edges, sorted(men))
    rep.seconds = time.perf_counter() - t0
    return rep, audit


def report_dict(rep: BuildReport) -> dict:
    d = asdict(rep)
    d["indexing_cost_usd_per_chunk"] = rep.indexing_cost_usd / rep.n_chunks if rep.n_chunks else None
    return d
