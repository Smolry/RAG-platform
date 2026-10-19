"""Hybrid graph + vector retrieval (PDF Project 3 step 5).

 question -> (a) vector top-N (ACL pre-filtered, same Qdrant leg as the baseline)
          -> (b) question entities -> link to graph nodes -> expand 1-2 hops -> neighbour chunks (ACL-filtered in the graph)
          -> (c) merge by chunk id -> (d) cross-encoder rerank -> top_k -> (e) 2nd app-level ACL check -> (f) relation triples
Vector retrieval is NEVER replaced. `use_graph=False` gives the 'vector + rerank' ablation, which separates the gain from the
graph from the gain from reranking.
"""
from __future__ import annotations

import time

from app.core.config import Settings
from app.core.models import RetrievedChunk
from app.graph.extraction import ExtractStats
from app.graph.linking import EntityLinker, extract_question_entities
from app.graph.rerank import Reranker
from app.graph.store import GraphStore, Triple
from app.retrieval.embedder import Embedder
from app.retrieval.store import VectorStore
from app.security.acl import acl_filter, is_authorized
from app.security.auth import Principal


class HybridRetriever:
    def __init__(self, settings: Settings, vstore: VectorStore, embedder: Embedder, graph: GraphStore | None,
                 reranker: Reranker | None, llm=None, prices=None):
        self.s, self.vstore, self.embedder, self.graph, self.reranker, self.llm, self.prices = (
            settings, vstore, embedder, graph, reranker, llm, prices)
        self._linkers: dict[str, EntityLinker] = {}

    def refresh_linker(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._linkers.clear()
        else:
            self._linkers.pop(tenant_id, None)

    def _linker(self, tenant_id: str) -> EntityLinker:
        if tenant_id not in self._linkers:
            self._linkers[tenant_id] = EntityLinker(self.graph.all_entities(tenant_id))
        return self._linkers[tenant_id]

    def retrieve(self, question: str, principal: Principal, top_k: int | None = None) -> dict:
        s, k = self.s, top_k or self.s.top_k
        t0 = time.perf_counter()
        vec = self.embedder.embed([question])[0]
        t1 = time.perf_counter()
        vhits = self.vstore.search(vec, s.vector_k, acl_filter(principal))  # ACL PRE-filter, same as the baseline
        cands: dict[str, RetrievedChunk] = {h.chunk_id: h for h in vhits}
        origin: dict[str, set[str]] = {h.chunk_id: {"vector"} for h in vhits}
        paths: dict[str, int] = {}
        extra, seeds, links, graph_ms = {"in": 0, "out": 0, "cost": 0.0, "ms": 0.0}, [], [], 0.0
        if s.graph_enabled and self.graph is not None:
            tg = time.perf_counter()
            linker = self._linker(principal.tenant_id)
            if s.question_entities == "llm":
                st, te = ExtractStats(), time.perf_counter()
                names = extract_question_entities(self.llm, s.extraction_model, self.prices, question, st)
                extra = {"in": st.input_tokens, "out": st.output_tokens, "cost": st.cost_usd,
                         "ms": (time.perf_counter() - te) * 1000}
                links = linker.link_names(names)
            else:
                links = linker.link_ngrams(question)
            seeds = list(dict.fromkeys(l.entity_id for l in links))
            if seeds:
                for x in self.graph.expand(seeds, s.graph_hops, principal, s.graph_limit, s.graph_hub_degree_cap):
                    origin.setdefault(x.chunk_id, set()).add("graph")
                    paths[x.chunk_id] = x.paths
                    cands.setdefault(x.chunk_id, RetrievedChunk(chunk_id=x.chunk_id, score=0.0, payload=x.payload))
            graph_ms = (time.perf_counter() - tg) * 1000
        ids = list(cands)
        tr = time.perf_counter()
        if self.reranker is not None and s.rerank_enabled and ids:
            scores = self.reranker.score(question, [cands[i].text for i in ids])
            order = sorted(zip(ids, scores), key=lambda kv: -kv[1])
        else:  # ablation only: vector hits by score, then graph-only chunks by path count
            order = sorted(((i, cands[i].score if "vector" in origin[i] else -1.0 + paths.get(i, 0) * 1e-6) for i in ids),
                           key=lambda kv: -kv[1])
        rerank_ms = (time.perf_counter() - tr) * 1000
        top = [RetrievedChunk(chunk_id=i, score=float(sc), payload=cands[i].payload) for i, sc in order[:k]]
        # Graph-only chunks carry the GRAPH's copy of the ACL - the very data the graph filter just used. The 2nd check must not
        # trust it, so re-read those chunks from the vector store (source of truth). Missing there => empty payload => denied.
        graph_only = [c.chunk_id for c in top if "vector" not in origin[c.chunk_id]]
        if graph_only:
            truth = {str(p.id): (p.payload or {}) for p in
                     self.vstore.client.retrieve(self.vstore.collection, graph_only, with_payload=True)}
            top = [RetrievedChunk(chunk_id=c.chunk_id, score=c.score, payload=truth.get(c.chunk_id, {}))
                   if c.chunk_id in graph_only else c for c in top]
        allowed = [c for c in top if is_authorized(principal, c.payload)]  # 2nd, application-level, fail-closed check
        dropped = [c for c in top if not is_authorized(principal, c.payload)]
        triples: list[Triple] = []
        if s.graph_enabled and self.graph is not None and allowed:
            triples = self.graph.triples([c.chunk_id for c in allowed], principal)
        total_ms = (time.perf_counter() - t0) * 1000
        doc = lambda cid: cands[cid].document_id
        debug = {"seeds": seeds, "links": [(l.mention, l.entity_id, l.method) for l in links],
                 "vector_doc_ids": [h.document_id for h in vhits],
                 "graph_doc_ids": sorted({doc(c) for c, o in origin.items() if "graph" in o}),
                 "candidate_doc_ids": sorted({doc(c) for c in ids}),
                 "final_origin": {c.chunk_id: sorted(origin[c.chunk_id]) for c in allowed},
                 "graph_ms": graph_ms, "rerank_ms": rerank_ms, "n_candidates": len(ids)}
        return {"allowed": allowed, "dropped": dropped, "triples": triples, "extra_usage": extra, "debug": debug,
                "embed_ms": (t1 - t0) * 1000, "search_ms": total_ms - (t1 - t0) * 1000}
