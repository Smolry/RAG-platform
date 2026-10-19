"""Project 3: build pipeline, entity resolution effect, hybrid retrieval, graph security, orchestrator end-to-end.
LLM extraction is SCRIPTED (offline): these tests prove the pipeline logic, not extraction quality."""
import json

import pytest

from app.core.config import Settings
from app.generation.llm import LLMResult, PriceTable, RecordingLLM
from app.graph.build import build_graph, report_dict
from app.graph.hybrid import HybridRetriever
from app.graph.rerank import LexicalReranker
from app.graph.store import GChunk, InMemoryGraphStore
from app.observability.db import Database
from app.retrieval.embedder import HashEmbedder
from app.retrieval.store import VectorStore, make_client
from app.security.auth import Principal
from app.services.ingestion import DocumentInput, ingest_documents
from app.services.rag import RAGService

T = "hotpot"
READER = Principal("u1", T, ("reader",))

DOCS = {
    "film": "Film Orbit is a 2019 science fiction film. Film Orbit was directed by Dana Reed.",
    "dana": "D. Reed is a Portuguese filmmaker. D. Reed was born in Lisbon.",
    "lisbon": "Lisbon is the capital of Portugal.",
    "comet": "Film Comet was directed by Omar Vale. Omar Vale was born in Cairo.",
    "holt": "Dana Holt is an American painter.",
}
# scripted extraction: substring of the chunk text -> what the "LLM" returns
EXTRACT = {
    "Film Orbit is": {"entities": [{"name": "Film Orbit", "type": "WORK"}, {"name": "Dana Reed", "type": "PERSON"}],
                      "relations": [{"source": "Film Orbit", "type": "DIRECTED_BY", "target": "Dana Reed"}]},
    "D. Reed is": {"entities": [{"name": "D. Reed", "type": "PERSON"}, {"name": "Lisbon", "type": "LOCATION"}],
                   "relations": [{"source": "D. Reed", "type": "BORN_IN", "target": "Lisbon"}]},
    "Lisbon is the capital": {"entities": [{"name": "Lisbon", "type": "LOCATION"}, {"name": "Portugal", "type": "LOCATION"}],
                              "relations": [{"source": "Lisbon", "type": "CAPITAL_OF", "target": "Portugal"}]},
    "Film Comet": {"entities": [{"name": "Film Comet", "type": "WORK"}, {"name": "Omar Vale", "type": "PERSON"},
                                {"name": "Cairo", "type": "LOCATION"}],
                   "relations": [{"source": "Film Comet", "type": "DIRECTED_BY", "target": "Omar Vale"},
                                 {"source": "Omar Vale", "type": "BORN_IN", "target": "Cairo"}]},
    "Dana Holt": {"entities": [{"name": "Dana Holt", "type": "PERSON"}], "relations": []},
}


class ScriptedLLM:
    """Handles extraction prompts, question-entity prompts, and answers; counts calls."""

    def __init__(self, q_entities=("Film Orbit",)):
        self.calls, self.q_entities, self.prompts = [], list(q_entities), []

    def complete(self, messages, model, temperature=0.0):
        txt = messages[-1]["content"]
        self.prompts.append(txt)
        if txt.startswith("Extract a knowledge graph"):
            self.calls.append("extract")
            passage = txt.split("Passage:\n", 1)[1]
            for k, v in EXTRACT.items():
                if k in passage:
                    return LLMResult(json.dumps(v), 200, 100, model)
            return LLMResult('{"entities": [], "relations": []}', 200, 10, model)
        if txt.startswith("List the named entities"):
            self.calls.append("qent")
            return LLMResult(json.dumps({"entities": self.q_entities}), 50, 10, model)
        self.calls.append("answer")
        return LLMResult("Lisbon [1]", 300, 5, model)


PRICES = PriceTable({"gpt-4o-mini": {"input_per_1m": 1.0, "output_per_1m": 2.0}})


def settings(**kw):
    base = dict(system="project3", embedder_backend="hash", qdrant_url=":memory:", database_url="sqlite://", chunk_tokens=200,
                chunk_overlap_tokens=10, top_k=2, vector_k=1, graph_limit=20, graph_hops=2, graph_backend="memory",
                reranker_backend="lexical", question_entities="llm", extraction_model="gpt-4o-mini", llm_model="gpt-4o-mini",
                answer_style="short", jwt_secret="x" * 40)
    base.update(kw)
    return Settings(**base)


def make(s=None, roles=("reader",), restricted=None, resolve=True, llm=None, cache_path=None):
    s = s or settings()
    emb, vs, graph, llm = HashEmbedder(), VectorStore(make_client(":memory:"), "chunks"), InMemoryGraphStore(), llm or ScriptedLLM()
    docs = [DocumentInput(f"{T}/{k}", v, T, "public", list(roles) if k != restricted else ["admin"]) for k, v in DOCS.items()]
    ingest_documents(docs, vs, emb, s)
    chunks = [GChunk(str(p.id), p.payload["document_id"], p.payload["text"], T, p.payload["classification"],
                     tuple(p.payload["allowed_roles"]), tuple(p.payload["allowed_users"]))
              for p in vs.client.scroll("chunks", limit=100, with_payload=True)[0]]
    rep, audit = build_graph(chunks, llm, "gpt-4o-mini", PRICES, graph, embed=None, resolve=resolve, workers=1, cache_path=cache_path)
    hy = HybridRetriever(s, vs, emb, graph, LexicalReranker(), llm, PRICES)
    svc = RAGService(s, vs, emb, llm, Database("sqlite://"), PRICES, hybrid=hy, graph=graph)
    return svc, hy, graph, rep, audit, llm


Q = "Where was the director of Film Orbit born?"


# ---------------- build ----------------
def test_build_report_counts_cost_and_resolution():
    _, _, graph, rep, audit, llm = make()
    assert rep.n_chunks == 5 and rep.llm_calls_total == rep.llm_calls_fresh == 5 and llm.calls.count("extract") == 5
    assert rep.indexing_cost_usd == pytest.approx(5 * (200 * 1 + 100 * 2) / 1e6)  # 5 calls x (200 in, 100 out)
    assert rep.entities_distinct_before_resolution == rep.entities_after_resolution + 1  # exactly one merge: D. Reed -> Dana Reed
    assert audit["merges"][0]["merged"] == "d reed" and audit["merges"][0]["rule"] == "person_initials"
    assert rep.relations == 5 and rep.mentions == 10 and rep.relations_dropped_self_after_merge == 0  # 1+1+1+2+0 relations; 2+2+2+3+1 mentions and report_dict(rep)["indexing_cost_usd_per_chunk"] > 0
    assert graph.stats()["chunks"] == 5


def test_extraction_cache_makes_rebuild_free_but_reports_true_cost(tmp_path):
    cp = tmp_path / "cache.jsonl"
    _, _, _, r1, _, _ = make(cache_path=cp)
    llm2 = ScriptedLLM()
    _, _, _, r2, _, _ = make(llm=llm2, cache_path=cp)
    assert llm2.calls.count("extract") == 0 and r2.llm_calls_fresh == 0 and r2.fresh_cost_usd == 0
    assert r2.indexing_cost_usd == pytest.approx(r1.indexing_cost_usd)  # "from scratch" cost stays reported


def test_bad_llm_output_is_rejected_and_counted_not_loaded():
    class Bad(ScriptedLLM):
        def complete(self, messages, model, temperature=0.0):
            if messages[-1]["content"].startswith("Extract"):
                self.calls.append("extract")
                return LLMResult('{"entities": [{"name": "Atlantis", "type": "WIZARD"}, {"name": "Lisbon", "type": "LOCATION"}], '
                                 '"relations": [{"source": "Lisbon", "type": "TELEPORTS_TO", "target": "Atlantis"}]}', 10, 10, model)
            return super().complete(messages, model, temperature)
    _, _, graph, rep, _, _ = make(llm=Bad())
    assert rep.rejections.get("bad_entity_type", 0) >= 1 and rep.rejections.get("bad_relation_type", 0) >= 1
    assert not any(e.name == "Atlantis" for e in graph.all_entities(T))


# ---------------- the multi-hop proof ----------------
def docs_of(r):
    return {c.document_id for c in r["allowed"]}


def test_vector_only_misses_the_bridge_document_graph_finds_it():
    svc, hy, *_ = make()
    vec = HybridRetriever(settings(graph_enabled=False), hy.vstore, hy.embedder, None, LexicalReranker())
    assert docs_of(vec.retrieve(Q, READER, 2)) == {f"{T}/film"}            # vector leg (k=1) finds the film paragraph only
    v_only = {h.document_id for h in hy.vstore.search(hy.embedder.embed([Q])[0], 1)}
    assert v_only == {f"{T}/film"}
    r = hy.retrieve(Q, READER, 2)
    assert docs_of(r) == {f"{T}/film", f"{T}/dana"}                        # graph pulled in the director's paragraph
    assert f"{T}/dana" in r["debug"]["graph_doc_ids"] and r["debug"]["final_origin"][next(c.chunk_id for c in r["allowed"] if c.document_id == f"{T}/dana")] == ["graph"]
    assert any(t.type == "BORN_IN" and t.target == "Lisbon" for t in r["triples"])  # relation triple with provenance
    assert all(t.evidence_chunk_ids for t in r["triples"])


def test_without_entity_resolution_the_bridge_is_not_found():
    """The PDF's 'most underrated step': 'D. Reed' and 'Dana Reed' stay separate nodes, so traversal cannot cross."""
    _, hy, graph, rep, _, _ = make(resolve=False)
    assert rep.merges == 0
    r = hy.retrieve(Q, READER, 2)
    assert f"{T}/dana" not in {c.document_id for c in r["allowed"]} and f"{T}/dana" not in r["debug"]["graph_doc_ids"]


def test_graph_disabled_equals_vector_plus_rerank_ablation():
    _, hy, *_ = make(s=settings(graph_enabled=False))
    r = hy.retrieve(Q, READER, 2)
    assert r["debug"]["graph_doc_ids"] == [] and r["triples"] == [] and r["extra_usage"]["cost"] == 0.0


def test_ngram_linking_needs_no_llm():
    llm = ScriptedLLM()
    _, hy, *_ = make(s=settings(question_entities="ngram"), llm=llm)
    n = llm.calls.count("qent")
    r = hy.retrieve(Q, READER, 2)
    assert llm.calls.count("qent") == n == 0 and f"{T}/dana" in docs_of(r)


def test_hallucinated_question_entity_is_dropped():
    _, hy, *_ = make(llm=ScriptedLLM(q_entities=("Atlantis",)))
    assert hy.retrieve(Q, READER, 2)["debug"]["seeds"] == []


def test_candidate_pool_is_a_superset_of_the_vector_hits():
    """Graph candidates are ADDED to the vector candidates, never substituted for them (PDF: hybrid, not replacement)."""
    _, hy, *_ = make(s=settings(top_k=3, vector_k=3))
    r = hy.retrieve(Q, READER, 3)
    assert set(r["debug"]["vector_doc_ids"]) <= set(r["debug"]["candidate_doc_ids"])
    assert set(r["debug"]["graph_doc_ids"]) - set(r["debug"]["vector_doc_ids"])  # and the graph really contributed something new


# ---------------- security: the graph must not become an ACL bypass ----------------
def test_restricted_bridge_document_unreachable_for_reader():
    _, hy, graph, *_ = make(restricted="dana")
    r = hy.retrieve(Q, READER, 5)
    assert f"{T}/dana" not in {c.document_id for c in r["allowed"]} | {c.document_id for c in r["dropped"]}
    assert f"{T}/dana" not in r["debug"]["candidate_doc_ids"]
    assert all(t.target != "Lisbon" or t.type != "BORN_IN" for t in r["triples"])      # restricted edge not shown either
    admin = hy.retrieve(Q, Principal("a", T, ("admin",)), 5)
    assert f"{T}/dana" in {c.document_id for c in admin["allowed"]} or f"{T}/dana" in admin["debug"]["candidate_doc_ids"]


def test_other_tenant_never_reaches_this_graph():
    _, hy, *_ = make()
    r = hy.retrieve(Q, Principal("evil", "other-tenant", ("reader",)), 5)
    assert r["allowed"] == [] and r["debug"]["candidate_doc_ids"] == [] and r["triples"] == []


def test_second_check_uses_vector_store_not_the_graphs_copy_of_the_acl():
    """Simulate a STALE graph: Qdrant says the document is now admin-only, the graph still says reader."""
    _, hy, graph, *_ = make()
    hy.vstore.update_acl(f"{T}/dana", T, ["admin"], [])           # revoke in Qdrant ONLY
    r = hy.retrieve(Q, READER, 5)
    assert f"{T}/dana" not in {c.document_id for c in r["allowed"]}
    assert f"{T}/dana" in {c.document_id for c in r["dropped"]}    # caught by the fail-closed re-check


def test_forbidden_text_never_reaches_the_llm_even_with_a_stale_graph():
    svc, hy, graph, _, _, llm = make()
    svc.store.update_acl(f"{T}/dana", T, ["admin"], [])
    svc.answer(Q, READER)
    answer_prompt = llm.prompts[-1]
    assert "Portuguese filmmaker" not in answer_prompt and "born in Lisbon" not in answer_prompt
    assert any(e.event_type == "acl_violation" for e in svc.db.audit_events())


def test_api_revocation_reaches_graph_and_vector(tmp_path):
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    from app.security.auth import issue_token
    svc, hy, graph, *_ = make(roles=("reader", "admin"))
    s = svc.settings
    c = TestClient(create_app(service=svc, settings=s))
    tok = issue_token(Principal("adm", T, ("admin",)), s.jwt_secret, s.jwt_algorithm)
    assert f"{T}/dana" in docs_of(hy.retrieve(Q, READER, 2))
    r = c.put(f"/admin/documents/{T}/dana/acl", json={"allowed_roles": ["admin"]}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    after = hy.retrieve(Q, READER, 5)
    assert f"{T}/dana" not in {x.document_id for x in after["allowed"]} and f"{T}/dana" not in after["debug"]["candidate_doc_ids"]


# ---------------- orchestrator ----------------
def test_orchestrator_prompt_has_triples_with_provenance_and_cost_includes_question_extraction():
    svc, hy, graph, _, _, llm = make()
    out = svc.answer(Q, READER)
    prompt = llm.prompts[-1]
    assert "Relations extracted from the passages" in prompt and "Dana Reed --BORN_IN--> Lisbon [" in prompt
    assert out["answer"].startswith("Lisbon") and out["citations"]
    # usage = answer call (300 in, 5 out) + question-entity call (50 in, 10 out)
    assert out["usage"]["input_tokens"] == 350 and out["usage"]["output_tokens"] == 15
    assert out["usage"]["cost_usd"] == pytest.approx((350 * 1 + 15 * 2) / 1e6)
    assert out["retrieval_debug"]["graph_doc_ids"] and out["retrieval_debug"]["seeds"]


def test_short_prompt_style_and_requires_principal():
    svc, *_, llm = make()
    svc.answer(Q, READER)
    assert llm.calls[-1] == "answer"
    with pytest.raises(PermissionError):
        svc.answer(Q, None)


def test_project2_mode_is_the_vector_baseline_and_ignores_the_graph():
    s2 = settings(system="project2", top_k=2)
    svc, hy, graph, _, _, llm = make(s=s2)
    svc.hybrid = None
    out = svc.answer(Q, READER)
    assert "Relations extracted" not in llm.prompts[-1] and "retrieval_debug" not in out
