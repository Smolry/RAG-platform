"""Each isolation layer of InMemoryGraphStore.expand is tested ALONE by corrupting the in-memory graph on purpose
(load() refuses such graphs, so we edit the dicts directly). Found by mutation testing: layered checks were masking each other."""
from app.graph.store import GChunk, GEdge, GEntity, InMemoryGraphStore
from app.security.auth import Principal

OTHER = Principal("u9", "t2", ("reader",))


def corrupt_store():
    s = InMemoryGraphStore()
    a, b, x = (GEntity("t1|X:a", "A", "X", "t1"), GEntity("t1|X:b", "B", "X", "t1"), GEntity("t2|X:x", "X", "X", "t2"))
    s.entities = {e.id: e for e in (a, b, x)}
    s.chunks = {"ca": GChunk("ca", "da", "a", "t1", "public", ("reader",)), "cb": GChunk("cb", "db", "b", "t1", "public", ("reader",)),
                "cx": GChunk("cx", "dx", "x", "t2", "public", ("reader",))}
    s.entity_chunks = {"t1|X:a": {"ca", "cx"}, "t1|X:b": {"cb"}, "t2|X:x": {"cx"}}   # cx (tenant t2) wrongly mentions a t1 entity
    s.edges = {"k1": GEdge("t2|X:x", "RELATED_TO", "t1|X:b", "t1", ("cb",))}          # edge tagged t1, endpoint in t2
    s.edge_acl = {"k1": (["reader"], [])}
    s.degree = {"t1|X:a": 0, "t1|X:b": 1, "t2|X:x": 1}
    return s


def test_seed_from_another_tenant_is_refused_even_if_chunk_acl_would_pass():
    s = corrupt_store()
    # caller is t2; seed belongs to t1; the t2 chunk 'cx' passes the chunk ACL. Only the seed-tenant check stops this.
    assert s.expand(["t1|X:a"], 0, OTHER, 10, 100) == []


def test_edge_from_another_tenant_is_not_traversed_even_if_acl_would_pass():
    s = corrupt_store()
    # t2 caller, t2 seed 'x'; edge k1 is tagged t1 but touches x. Only the edge-tenant check stops the walk into t1.
    got = {r.chunk_id for r in s.expand(["t2|X:x"], 2, OTHER, 10, 100)}
    assert got == {"cx"}  # the seed's own chunk only; never cb (t1) via the bad edge
