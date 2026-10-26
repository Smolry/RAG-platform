import json

from app.devtrace.retrieval import RetrievalConfig, SearchFilters, build_filter, caveat_for, redact_for_audit
from app.devtrace.schemas import EvidenceItem
from tests.devtrace_env import Env

Q = "validation error request body"


def versions(res):
    return sorted(i.library_version for i in res.items if i.version_kind == "introduced_in")


def test_results_carry_full_provenance():
    env = Env()
    res = env.retriever.search(env.user, Q, SearchFilters(library="fastapi", source_types=("docs",)), top_k=5)
    assert res.items
    for i in res.items:
        assert i.library == "fastapi" and i.source_type == "docs" and i.snapshot_id == "snap-test"
        assert i.provenance_url.startswith("https://raw.githubusercontent.com/") and i.source_url.startswith("https://fastapi.tiangolo.com/")
        assert i.content_hash and i.section_path and i.chunk_id


def test_release_note_version_range_is_exclusive_lower_inclusive_upper():
    env = Env()
    f = SearchFilters(library="fastapi", source_types=("release_notes",), version_gt="0.100.0", version_lte="0.110.0")
    res = env.retriever.search(env.user, Q, f, top_k=10)
    assert versions(res) == ["0.101.0", "0.105.0", "0.110.0"]
    assert res.debug["filters"]["version_range"] == "(0.100.0, 0.110.0]"


def test_open_ended_ranges():
    env = Env()
    up = env.retriever.search(env.user, Q, SearchFilters(library="fastapi", source_types=("release_notes",), version_gt="0.105.0"), top_k=10)
    assert versions(up) == ["0.110.0", "0.112.0"]
    down = env.retriever.search(env.user, Q, SearchFilters(library="fastapi", source_types=("release_notes",), version_lte="0.100.0"), top_k=10)
    assert set(versions(down)) == {"0.100.0", "0.99.1"}


def test_version_ranges_never_mix_libraries():
    """pydantic 2.5.0 would numerically sit inside a fastapi-style range, so the library filter must always apply."""
    env = Env()
    f = SearchFilters(library="pydantic", source_types=("release_notes",), version_gt="2.0.0", version_lte="2.9.0")
    res = env.retriever.search(env.user, Q, f, top_k=10)
    assert versions(res) == ["2.5.0", "2.9.0"] and {i.library for i in res.items} == {"pydantic"}


def test_library_filter_excludes_other_library_even_if_it_matches_better():
    env = Env()
    res = env.retriever.search(env.user, "pydantic v2 migration field_validator", SearchFilters(library="fastapi"), top_k=10)
    assert res.items and {i.library for i in res.items} == {"fastapi"}


def test_range_filter_never_returns_docs_pages():
    env = Env()
    res = env.retriever.search(env.user, Q, SearchFilters(library="fastapi", version_gt="0.0.1"), top_k=10)
    assert res.items and all(i.version_kind == "introduced_in" for i in res.items)


def test_empty_result_is_empty_not_an_error():
    env = Env()
    res = env.retriever.search(env.user, Q, SearchFilters(library="fastapi", source_types=("release_notes",), version_gt="9.0.0"))
    assert res.items == [] and res.debug["n_candidates"] == 0


def test_build_filter_keeps_tenant_and_roles_and_only_adds_conditions():
    env = Env()
    flt = build_filter(env.user, SearchFilters(library="fastapi", source_types=("docs",), version_gt="0.1.0", version_lte="0.2.0"))
    keys = [c.key for c in flt.must]
    assert keys[0] == "tenant_id" and {"library", "source_type", "version_key"} <= set(keys) and flt.should
    bare = build_filter(env.user, None)
    assert [c.key for c in bare.must] == ["tenant_id"] and bare.should


def test_cross_tenant_and_role_isolation():
    env = Env()
    q = "CANARY-DEVTRACE-ONLY internal note"
    mine = env.retriever.search(env.user, q, None, top_k=10)
    assert any("CANARY-DEVTRACE-ONLY" in i.text for i in mine.items)
    theirs = env.retriever.search(env.other_tenant, q, None, top_k=10)
    assert not any("CANARY" in i.text for i in theirs.items) and theirs.items          # they still see their own copy of public docs
    assert {i.chunk_id for i in mine.items}.isdisjoint({i.chunk_id for i in theirs.items})
    assert env.retriever.search(env.contractor, q, None, top_k=10).items == []


def test_ablation_switch_use_filters_false_still_enforces_authorization():
    env = Env(config=RetrievalConfig(rerank=False, use_filters=False))
    res = env.retriever.search(env.other_tenant, "CANARY-DEVTRACE-ONLY internal", SearchFilters(library="pydantic"), top_k=10)
    assert {i.library for i in res.items} - {"pydantic"}            # filters really were ignored...
    assert not any("CANARY" in i.text for i in res.items)           # ...but the tenant boundary was not
    assert res.debug["filters"] == {}


def test_second_check_catches_a_broken_prefilter_and_audits_it(monkeypatch):
    env = Env()
    orig = env.store.search
    monkeypatch.setattr(env.store, "search", lambda v, limit, query_filter=None: orig(v, 200, None))   # simulate a filter bug
    res = env.retriever.search(env.other_tenant, "CANARY-DEVTRACE-ONLY internal note", None, top_k=10)
    assert not any("CANARY" in i.text for i in res.items) and res.debug["n_dropped_by_acl"] > 0
    assert env.db.audit_events("acl_violation")


def test_audit_stores_a_hash_not_the_query():
    env = Env()
    secret = "Traceback ... AWS_SECRET_ACCESS_KEY=abc123SECRET in validation error request body"
    env.retriever.search(env.user, secret, None)
    rows = env.db.audit_events("retrieval")
    assert rows and all("abc123SECRET" not in r.query and "Traceback" not in r.query for r in rows)
    assert rows[0].query == redact_for_audit(secret) and "sha256=" in rows[0].query and rows[0].model == "devtrace-retrieval"


def test_duplicate_passages_are_collapsed():
    env = Env()
    res = env.retriever.search(env.user, Q, None, top_k=10)
    hashes = [i.content_hash for i in res.items]
    assert len(hashes) == len(set(hashes))


class _Rev:
    def score(self, q, texts):
        return [float(len(t)) for t in texts]          # longest text first: a deliberately different order


def test_reranker_controls_final_order_and_is_ablatable():
    flt = SearchFilters(library="fastapi")
    plain = Env(config=RetrievalConfig(rerank=False))
    env = Env(reranker=_Rev(), config=RetrievalConfig(rerank=True))
    no_rr = plain.retriever.search(plain.user, Q, flt, top_k=4)
    rr = env.retriever.search(env.user, Q, flt, top_k=4)
    assert rr.debug["reranked"] and not no_rr.debug["reranked"]
    lens = [len(i.text) for i in rr.items]
    assert len(lens) == 4 and lens == sorted(lens, reverse=True)           # rerank score (= text length here) decides the order
    assert [i.score for i in rr.items] == [float(n) for n in lens]          # score field reports the reranker's score...
    assert all(0.0 <= i.vector_score <= 1.0 + 1e-9 for i in rr.items)       # ...while the original vector score is preserved
    # candidates come from the same pool: reranking reorders, it never adds passages the vector search did not return
    pool = {i.chunk_id for i in plain.retriever.search(plain.user, Q, flt, top_k=20).items}
    assert {i.chunk_id for i in rr.items} <= pool

def _item(**kw):
    return EvidenceItem(chunk_id="c", document_id="d", library="fastapi", **kw)


def test_version_caveats_are_deterministic_and_source_grounded():
    docs = _item(library_version="0.162.0", version_kind="docs_as_of")
    assert "older" in caveat_for(docs, "fastapi", "0.99.1") and "0.162.0" in caveat_for(docs, "fastapi", "0.99.1")
    assert caveat_for(docs, "fastapi", "0.162.0") is None
    assert "newer" in caveat_for(docs, "fastapi", "0.200.0")
    rel_new = _item(library_version="0.110.0", version_kind="introduced_in")
    assert "after upgrading" in caveat_for(rel_new, "fastapi", "0.100.0")
    assert "already included" in caveat_for(rel_new, "fastapi", "0.110.0") and "already included" in caveat_for(rel_new, "fastapi", "0.150.0")
    assert caveat_for(docs, "pydantic", "2.0.0") is None and caveat_for(docs, "fastapi", None) is None
    assert caveat_for(docs, "fastapi", "not-a-version") is None


def test_caveat_attached_through_search():
    env = Env()
    res = env.retriever.search(env.user, Q, SearchFilters(library="fastapi", source_types=("docs",)), user_version="0.99.1")
    assert res.items and all(i.version_caveat and "older" in i.version_caveat for i in res.items)


def test_ingest_sets_version_key_for_release_notes_only():
    env = Env()
    rows = [p for _, p in env.store.iter_chunks("devtrace")]
    assert all((p["version_key"] is not None) == (p["version_kind"] == "introduced_in") for p in rows)
    assert json.dumps(rows[0])  # payload stays JSON-serialisable
