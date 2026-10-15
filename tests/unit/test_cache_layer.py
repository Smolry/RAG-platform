"""Tests the cache's OWN isolation (layer 1), bypassing the orchestrator's live-ACL re-check (layer 2).
Found by mutation testing: orchestrator-level tests alone passed even with the tenant/permission filters removed."""
import pytest

from app.caching.semantic_cache import SemanticCache
from app.retrieval.embedder import HashEmbedder
from app.retrieval.store import make_client
from app.security.auth import Principal

NOW = 1_000_000.0


@pytest.fixture
def cache():
    emb = HashEmbedder()
    c = SemanticCache(make_client(":memory:"), "sc", threshold=0.9, ttl_seconds=100, clock=lambda: NOW)
    c.ensure(emb.dim)
    return c, emb.embed(["what is the salary band"])[0]


def put(c, vec, p, version="v1", user_specific=False):
    return c.store(vec, p, version, "q", "ans [1]", [{"chunk_id": "c1", "document_id": "d"}], [], user_specific, "m")


HR = Principal("hr1", "t1", ("hr",))


def test_same_context_hits(cache):
    c, v = cache
    put(c, v, HR)
    assert c.lookup(v, Principal("hr2", "t1", ("hr",)), "v1") is not None  # same tenant + same role set => shareable


def test_other_tenant_never_hits(cache):
    c, v = cache
    put(c, v, HR)
    assert c.lookup(v, Principal("hr1", "t2", ("hr",)), "v1") is None


def test_other_roles_same_tenant_never_hit(cache):
    c, v = cache
    put(c, v, HR)
    assert c.lookup(v, Principal("x", "t1", ("employee",)), "v1") is None
    assert c.lookup(v, Principal("x", "t1", ("hr", "employee")), "v1") is None  # superset of roles is a DIFFERENT context


def test_user_specific_entry_only_for_that_user(cache):
    c, v = cache
    emp = Principal("alice", "t1", ("employee",))
    put(c, v, emp, user_specific=True)
    assert c.lookup(v, emp, "v1") is not None
    assert c.lookup(v, Principal("bob", "t1", ("employee",)), "v1") is None


def test_non_user_specific_entry_shared_across_users_with_same_roles(cache):
    c, v = cache
    put(c, v, Principal("alice", "t1", ("employee",)), user_specific=False)
    assert c.lookup(v, Principal("bob", "t1", ("employee",)), "v1") is not None


def test_version_and_ttl(cache):
    c, v = cache
    put(c, v, HR)
    assert c.lookup(v, HR, "v2") is None
    c.clock = lambda: NOW + 101
    assert c.lookup(v, HR, "v1") is None


def test_orchestrator_marks_per_user_grant_entries_user_specific(p1, corpus):
    """Guards the user_specific computation in RAGService (mutation M7)."""
    from tests.conftest import principal_for
    _, users = corpus
    q = "CONFIDENTIAL Helios Robotics incident report vendor portal breach customer records CANARY-HELIOS-INCIDENT"
    p1.answer(q, principal_for(users, "employee@helios"))
    payloads = [pt.payload for pt in p1.cache.client.scroll("semantic_cache", limit=10, with_payload=True)[0]]
    assert len(payloads) == 1 and payloads[0]["user_specific"] is True
    # top_k=1 retrieves only the role-visible handbook chunk -> shareable (user_specific must be False)
    p1.answer("Helios Robotics employee handbook working hours remote work paid leave", principal_for(users, "employee@helios"), top_k=1)
    flags = sorted(pt.payload["user_specific"] for pt in p1.cache.client.scroll("semantic_cache", limit=10, with_payload=True)[0])
    assert flags == [False, True]
