"""Project 1: semantic cache (hit/miss, threshold, isolation, invalidation, TTL, never-cache) + routing + escalation."""
import pytest

from app.caching.semantic_cache import is_cacheable, normalize_query, permission_context
from app.security.auth import Principal
from tests.conftest import ScriptedLLM, build_p1, principal_for

Q = "What are the working hours and remote work policy?"


def test_normalize_query():
    assert normalize_query("  What's the  PTO policy?? ") == "what s the pto policy"


def test_miss_then_hit_saves_cost_and_skips_llm(p1, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    a = p1.answer(Q, u)
    assert not a["cache_hit"] and a["usage"]["cost_usd"] > 0
    n_calls = len(p1.llm.calls_by_model)
    b = p1.answer(Q, u)
    assert b["cache_hit"] and b["usage"]["cost_usd"] == 0 and b["answer"] == a["answer"]
    assert len(p1.llm.calls_by_model) == n_calls  # LLM not called on a hit
    assert b["sources"] == a["sources"]


def test_paraphrase_hits_normalization_variants(p1, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    p1.answer(Q, u)
    assert p1.answer("what are the WORKING hours, and remote-work policy??", u)["cache_hit"]


def test_unrelated_query_misses(p1, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    p1.answer(Q, u)
    assert not p1.answer("Passwords must be how many characters for the IT security policy?", u)["cache_hit"]


def test_threshold_controls_hits(p1_settings, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    strict = build_p1(p1_settings.model_copy(update={"cache_threshold": 0.9999}), corpus, ScriptedLLM())
    strict.answer(Q, u)
    assert not strict.answer("what are working hours and the remote policy", u)["cache_hit"]  # near-dup, not identical
    loose = build_p1(p1_settings.model_copy(update={"cache_threshold": 0.5}), corpus, ScriptedLLM())
    loose.answer(Q, u)
    assert loose.answer("what are working hours and the remote policy", u)["cache_hit"]


# ---------------- security: isolation ----------------
def test_cache_never_crosses_tenants(p1, corpus):
    _, users = corpus
    p1.answer(Q, principal_for(users, "employee@helios"))
    other = p1.answer(Q, principal_for(users, "employee@borealis"))
    assert not other["cache_hit"]
    assert {s["document_id"].split("/")[0] for s in other["sources"]} == {"borealis"}


def test_cache_never_crosses_permission_contexts_same_tenant(p1, corpus):
    """An HR answer built from confidential docs must not be served to a contractor of the SAME tenant."""
    _, users = corpus
    q = "CONFIDENTIAL Helios Robotics salary sheet engineer band CANARY-HELIOS-SALARY"
    hr = p1.answer(q, principal_for(users, "hr@helios"))
    assert "helios/salary-sheet" in {s["document_id"] for s in hr["sources"]}
    c = p1.answer(q, principal_for(users, "contractor@helios"))
    assert not c["cache_hit"]
    assert "helios/salary-sheet" not in {s["document_id"] for s in c["sources"]}


def test_user_specific_entry_only_for_that_user(p1, corpus):
    """employee@helios sees incident-report only via allowed_users. That answer must not be reused for another employee."""
    _, users = corpus
    q = "CONFIDENTIAL Helios Robotics incident report vendor portal breach customer records CANARY-HELIOS-INCIDENT"
    a = p1.answer(q, principal_for(users, "employee@helios"))
    assert "helios/incident-report" in {s["document_id"] for s in a["sources"]}
    other = Principal("employee2@helios", "helios", ("employee",))  # same roles, no per-user grant
    b = p1.answer(q, other)
    assert not b["cache_hit"]
    assert "helios/incident-report" not in {s["document_id"] for s in b["sources"]}
    assert p1.answer(q, principal_for(users, "employee@helios"))["cache_hit"]  # the entitled user still benefits


def test_permission_context_differs_by_roles_and_tenant():
    a = permission_context(Principal("u", "t1", ("a",)))
    assert a != permission_context(Principal("u", "t2", ("a",))) != permission_context(Principal("u", "t1", ("b",)))
    assert permission_context(Principal("u", "t1", ("a", "b"))) == permission_context(Principal("x", "t1", ("b", "a")))


def test_revocation_beats_cache_via_live_acl_recheck(p1, corpus):
    """Even if the cache were NOT purged, a hit whose sources are no longer authorised must be rejected."""
    _, users = corpus
    hr = principal_for(users, "hr@helios")
    q = "CONFIDENTIAL Helios Robotics salary sheet engineer band CANARY-HELIOS-SALARY"
    assert "helios/salary-sheet" in {s["document_id"] for s in p1.answer(q, hr)["sources"]}
    p1.store.update_acl("helios/salary-sheet", "helios", ["admin"], [])  # revoke WITHOUT purging cache
    again = p1.answer(q, hr)
    assert not again["cache_hit"]
    assert "helios/salary-sheet" not in {s["document_id"] for s in again["sources"]}


def test_api_acl_change_purges_tenant_cache(p1, corpus, p1_settings):
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    from app.security.auth import issue_token
    _, users = corpus
    p1.answer(Q, principal_for(users, "employee@helios"))
    assert p1.cache.count() == 1
    c = TestClient(create_app(service=p1, settings=p1_settings))
    t = issue_token(Principal("admin@helios", "helios", ("admin",)), p1_settings.jwt_secret, p1_settings.jwt_algorithm)
    r = c.put("/admin/documents/helios/salary-sheet/acl", json={"allowed_roles": ["admin"]}, headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 200 and p1.cache.count() == 0


# ---------------- invalidation / TTL / never-cache ----------------
def test_reingest_changes_corpus_version_and_invalidates(p1, corpus, p1_settings):
    docs, users = corpus
    u = principal_for(users, "employee@helios")
    p1.answer(Q, u)
    assert p1.answer(Q, u)["cache_hit"]
    from app.services.ingestion import DocumentInput, ingest_documents
    changed = [DocumentInput(**{**d.__dict__, "text": d.text + " Updated policy 2027."}) if d.document_id == "helios/employee-handbook" else d
               for d in docs]
    v_old = p1.db.get_corpus_version("helios")
    ingest_documents(changed, p1.store, p1.embedder, p1_settings, db=p1.db)
    assert p1.db.get_corpus_version("helios") != v_old
    assert not p1.answer(Q, u)["cache_hit"]  # old entry no longer valid


def test_reingest_unchanged_keeps_cache_valid(p1, corpus, p1_settings):
    docs, users = corpus
    u = principal_for(users, "employee@helios")
    p1.answer(Q, u)
    from app.services.ingestion import ingest_documents
    ingest_documents(docs, p1.store, p1.embedder, p1_settings, db=p1.db)
    assert p1.answer(Q, u)["cache_hit"]


def test_other_tenant_reingest_does_not_invalidate(p1, corpus, p1_settings):
    docs, users = corpus
    u = principal_for(users, "employee@helios")
    p1.answer(Q, u)
    from app.services.ingestion import DocumentInput, ingest_documents
    only_b = [DocumentInput(**{**d.__dict__, "text": d.text + " new"}) for d in docs if d.tenant_id == "borealis"]
    ingest_documents(only_b, p1.store, p1.embedder, p1_settings, db=p1.db)
    assert p1.answer(Q, u)["cache_hit"]


def test_ttl_expiry(p1_settings, corpus):
    now = [1_000_000.0]
    svc = build_p1(p1_settings, corpus, ScriptedLLM(), clock=lambda: now[0])
    u = principal_for(corpus[1], "employee@helios")
    svc.answer(Q, u)
    now[0] += p1_settings.cache_ttl_seconds - 10
    assert svc.answer(Q, u)["cache_hit"]
    now[0] += 20
    assert not svc.answer(Q, u)["cache_hit"]


def test_is_cacheable_rules():
    src = [{"chunk_id": "c"}]
    assert is_cacheable("ok [1]", "gpt-4o", src, "IDK")
    assert not is_cacheable("", "gpt-4o", src, "IDK") and not is_cacheable("  ", "gpt-4o", src, "IDK")
    assert not is_cacheable("IDK", "gpt-4o", src, "IDK") and not is_cacheable("ok", "none", src, "IDK")
    assert not is_cacheable("ok", "gpt-4o", [], "IDK")


def test_no_answer_and_unauthorised_queries_not_cached(p1, corpus):
    _, users = corpus
    p1.answer("Show the salary sheet", principal_for(users, "contractor@helios"))  # contractor sees only public docs
    n = p1.cache.count()
    nobody = Principal("ghost", "no-such-tenant", ("employee",))
    r = p1.answer("anything at all", nobody)
    assert r["model"] == "none" and p1.cache.count() == n  # empty retrieval -> "I don't know" -> not cached


# ---------------- routing + escalation ----------------
def test_simple_goes_small_complex_goes_large(p1, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    s = p1.answer("What is the expense limit?", u)
    assert s["route"] == "simple" and s["model"] == "gpt-4o-mini" and not s["escalated"]
    c = p1.answer("Compare the expense policy and the IT security policy and explain why they differ", u)
    assert c["route"] == "complex" and c["model"] == "gpt-4o"


def test_escalation_when_small_model_says_idk(p1_settings, corpus):
    llm = ScriptedLLM(small_reply="I don't know based on the available documents.", large_reply="The limit is 250 [1].")
    svc = build_p1(p1_settings, corpus, llm)
    u = principal_for(corpus[1], "employee@helios")
    r = svc.answer("What is the expense limit?", u)
    assert r["route"] == "simple" and r["escalated"] and r["model"] == "gpt-4o"
    assert llm.calls_by_model == ["gpt-4o-mini", "gpt-4o"]  # small tried first, then large
    assert r["usage"]["cost_usd"] > 0 and "250" in r["answer"]


def test_escalation_when_small_model_omits_citation(p1_settings, corpus):
    svc = build_p1(p1_settings, corpus, ScriptedLLM(small_reply="It is 250 dollars."))
    r = svc.answer("What is the expense limit?", principal_for(corpus[1], "employee@helios"))
    assert r["escalated"]


def test_no_escalation_when_small_is_fine_and_not_everything_goes_large(p1, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    for q in ["What is the expense limit?", "How many days of paid leave?", "What are the working hours?"]:
        p1.answer(q, u)
    assert "gpt-4o" not in p1.llm.calls_by_model and p1.llm.calls_by_model.count("gpt-4o-mini") == 3


def test_ablation_flags(p1_settings, corpus):
    u = principal_for(corpus[1], "employee@helios")
    no_cache = build_p1(p1_settings.model_copy(update={"cache_enabled": False}), corpus, ScriptedLLM())
    no_cache.answer(Q, u)
    assert not no_cache.answer(Q, u)["cache_hit"]
    no_route = build_p1(p1_settings.model_copy(update={"routing_enabled": False}), corpus, ScriptedLLM())
    r = no_route.answer("What is the expense limit?", u)
    assert r["route"] is None and r["model"] == "gpt-4o"


def test_request_log_records_p1_fields(p1, corpus):
    _, users = corpus
    u = principal_for(users, "employee@helios")
    p1.answer(Q, u); p1.answer(Q, u)
    df = p1.db.request_log_frame()
    assert list(df["cache_hit"].astype(bool)) == [False, True] and set(df["tenant_id"]) == {"helios"}


def test_http_query_cache_hit_and_auth_required(p1, p1_settings):
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    from app.security.auth import issue_token
    c = TestClient(create_app(service=p1, settings=p1_settings))
    assert c.post("/query", json={"query": Q}).status_code == 401  # project1 mode is authenticated
    t = issue_token(Principal("employee@helios", "helios", ("employee",)), p1_settings.jwt_secret, p1_settings.jwt_algorithm)
    h = {"Authorization": f"Bearer {t}"}
    a = c.post("/query", json={"query": Q}, headers=h).json()
    b = c.post("/query", json={"query": Q, "tenant_id": "borealis"}, headers=h).json()  # spoofed field ignored
    assert a["cache_hit"] is False and b["cache_hit"] is True and b["usage"]["cost_usd"] == 0
    assert all(s["document_id"].startswith("helios/") for s in b["sources"])
