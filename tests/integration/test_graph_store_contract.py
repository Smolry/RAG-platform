"""One contract, two implementations. InMemoryGraphStore always runs; Neo4jGraphStore runs only when NEO4J_URI is set
(`docker compose up -d neo4j`, then `make test-neo4j`).

Graph (tenant t1):  Film Orbit --DIRECTED_BY--> Dana Reed --BORN_IN--> Lisbon --LOCATED_IN--> Portugal
  c1 mentions Film Orbit, Dana Reed | c2 mentions Dana Reed, Lisbon | c3 mentions Lisbon, Portugal | c4 mentions Portugal
  c2 is restricted to role 'hr'. Tenant t2 has its own copy of 'Dana Reed' that must never be reachable from t1.
"""
import os

import pytest

from app.graph.store import GChunk, GEdge, GEntity, InMemoryGraphStore, Neo4jGraphStore
from app.security.auth import Principal

READER = Principal("u1", "t1", ("reader",))
HR = Principal("u2", "t1", ("reader", "hr"))
OTHER = Principal("u9", "t2", ("reader",))


def E(t, key, name, typ="PERSON"):
    return GEntity(f"{t}|{typ}:{key}", name, typ, t, (key,), 1)


def build():
    ch = [GChunk("c1", "d1", "Film Orbit was directed by Dana Reed.", "t1", "public", ("reader", "hr")),
          GChunk("c2", "d2", "Dana Reed was born in Lisbon.", "t1", "confidential", ("hr",)),
          GChunk("c3", "d3", "Lisbon is in Portugal.", "t1", "public", ("reader", "hr")),
          GChunk("c4", "d4", "Portugal is a country.", "t1", "public", ("reader", "hr")),
          GChunk("x1", "dx", "Dana Reed of the other tenant.", "t2", "public", ("reader",))]
    ents = [E("t1", "film orbit", "Film Orbit", "WORK"), E("t1", "dana reed", "Dana Reed"),
            E("t1", "lisbon", "Lisbon", "LOCATION"), E("t1", "portugal", "Portugal", "LOCATION"),
            E("t2", "dana reed", "Dana Reed")]
    eid = lambda t, k, ty: f"{t}|{ty}:{k}"
    edges = [GEdge(eid("t1", "film orbit", "WORK"), "DIRECTED_BY", eid("t1", "dana reed", "PERSON"), "t1", ("c1",)),
             GEdge(eid("t1", "dana reed", "PERSON"), "BORN_IN", eid("t1", "lisbon", "LOCATION"), "t1", ("c2",)),
             GEdge(eid("t1", "lisbon", "LOCATION"), "LOCATED_IN", eid("t1", "portugal", "LOCATION"), "t1", ("c3",))]
    men = [("c1", eid("t1", "film orbit", "WORK")), ("c1", eid("t1", "dana reed", "PERSON")),
           ("c2", eid("t1", "dana reed", "PERSON")), ("c2", eid("t1", "lisbon", "LOCATION")),
           ("c3", eid("t1", "lisbon", "LOCATION")), ("c3", eid("t1", "portugal", "LOCATION")),
           ("c4", eid("t1", "portugal", "LOCATION")), ("x1", eid("t2", "dana reed", "PERSON"))]
    return ch, ents, edges, men


FILM, DANA, LISBON, PORT = ("t1|WORK:film orbit", "t1|PERSON:dana reed", "t1|LOCATION:lisbon", "t1|LOCATION:portugal")


def _stores():
    out = [pytest.param(lambda: InMemoryGraphStore(), id="memory")]
    uri = os.environ.get("NEO4J_URI")
    if uri and not os.environ.get("NEO4J_PASSWORD"):
        out.append(pytest.param(None, id="neo4j", marks=pytest.mark.skip(reason="set NEO4J_PASSWORD (no default credential in source)")))
    elif uri:
        def mk():
            s = Neo4jGraphStore(uri, os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"])
            s.ensure_schema(); s.clear_tenant("t1"); s.clear_tenant("t2")
            return s
        out.append(pytest.param(mk, id="neo4j"))
    else:
        out.append(pytest.param(None, id="neo4j", marks=pytest.mark.skip(reason="set NEO4J_URI to run against a live Neo4j")))
    return out


@pytest.fixture(params=_stores())
def store(request):
    s = request.param()
    s.load(*build())
    return s


def ids(res):
    return {r.chunk_id for r in res}


def test_hops_zero_one_two(store):
    assert ids(store.expand([FILM], 0, HR, 50, 100)) == {"c1"}                  # only chunks mentioning the seed
    assert ids(store.expand([FILM], 1, HR, 50, 100)) == {"c1", "c2"}            # + chunks of the direct neighbour (Dana)
    assert ids(store.expand([FILM], 2, HR, 50, 100)) == {"c1", "c2", "c3"}      # + Lisbon's chunks; Portugal is 3 hops


def test_path_counts_and_min_hops_rank_chunks(store):
    r = {x.chunk_id: x for x in store.expand([FILM], 2, HR, 50, 100)}
    assert r["c1"].min_hops == 0 and r["c2"].min_hops == 1 and r["c3"].min_hops == 2
    assert r["c1"].paths >= r["c3"].paths  # closer chunks are reachable by at least as many trails
    assert store.expand([FILM], 2, HR, 1, 100)[0].chunk_id == r["c1"].chunk_id or r["c1"].paths == r["c2"].paths  # limit honoured


def test_restricted_chunk_never_returned_to_reader(store):
    assert "c2" not in ids(store.expand([FILM, DANA, LISBON], 2, READER, 50, 100))


def test_restricted_edge_is_not_traversable(store):
    """The Dana--BORN_IN-->Lisbon edge is evidenced ONLY by hr-only c2: a reader must not even reach Lisbon through it."""
    reader = ids(store.expand([FILM], 3, READER, 50, 100))
    assert reader == {"c1"}  # c3/c4 are readable, but reachable only THROUGH the restricted edge
    assert {"c3", "c4"} <= ids(store.expand([FILM], 3, HR, 50, 100))


def test_tenant_isolation(store):
    assert ids(store.expand([DANA], 2, OTHER, 50, 100)) == set()               # t1 seed, t2 caller
    assert "x1" not in ids(store.expand([DANA], 2, HR, 50, 100))               # t2 chunk never reachable from t1
    assert ids(store.expand(["t2|PERSON:dana reed"], 2, OTHER, 50, 100)) == {"x1"}


def test_hub_degree_cap_blocks_walking_through_hubs(store):
    assert ids(store.expand([FILM], 2, HR, 50, 0)) == {"c1", "c2"}  # Dana (degree 2) not walked through at cap 0


def test_triples_carry_provenance_and_respect_acl(store):
    t = store.triples(["c1", "c2", "c3"], HR)
    assert {(x.source, x.type, x.target) for x in t} == {("Film Orbit", "DIRECTED_BY", "Dana Reed"), ("Dana Reed", "BORN_IN", "Lisbon"),
                                                         ("Lisbon", "LOCATED_IN", "Portugal")}
    assert all(x.evidence_chunk_ids for x in t)
    r = store.triples(["c1", "c2", "c3"], READER)
    assert ("Dana Reed", "BORN_IN", "Lisbon") not in {(x.source, x.type, x.target) for x in r}
    assert store.triples(["c1"], OTHER) == []


def test_revocation_propagates_to_chunks_and_edges(store):
    assert store.update_document_acl("d2", "t1", ["reader", "hr"], []) == 1      # grant: readers can now use c2 + its edge
    assert {"c2", "c3"} <= ids(store.expand([FILM], 2, READER, 50, 100))
    assert store.update_document_acl("d2", "t1", ["hr"], []) == 1                # revoke
    assert ids(store.expand([FILM], 3, READER, 50, 100)) == {"c1"}
    assert ("Dana Reed", "BORN_IN", "Lisbon") not in {(x.source, x.type, x.target) for x in store.triples(["c2"], READER)}
    assert store.update_document_acl("d2", "t2", ["reader"], []) == 0            # other tenant's id: no effect


def test_all_entities_and_clear_tenant(store):
    assert {e.name for e in store.all_entities("t1")} == {"Film Orbit", "Dana Reed", "Lisbon", "Portugal"}
    assert {e.id for e in store.all_entities("t2")} == {"t2|PERSON:dana reed"}
    store.clear_tenant("t1")
    assert store.all_entities("t1") == [] and store.stats()["chunks"] == 1
    assert ids(store.expand(["t2|PERSON:dana reed"], 1, OTHER, 10, 100)) == {"x1"}  # t2 untouched


def test_stats(store):
    assert store.stats() == {"chunks": 5, "entities": 5, "relations": 3, **({"mentions": 8} if isinstance(store, InMemoryGraphStore) else {})}


def test_load_rejects_dangling_references():
    s = InMemoryGraphStore()
    ch, ents, edges, men = build()
    with pytest.raises(ValueError):
        s.load(ch, ents[:1], edges, men)


def test_triples_only_for_requested_evidence_chunks(store):
    assert {(x.source, x.type, x.target) for x in store.triples(["c1"], HR)} == {("Film Orbit", "DIRECTED_BY", "Dana Reed")}
    assert store.triples(["c4"], HR) == []                      # a chunk that evidences no edge
    t = store.triples(["c1", "c2"], HR)
    assert all(set(x.evidence_chunk_ids) <= {"c1", "c2"} for x in t)


def test_load_refuses_cross_tenant_edges_mentions_and_evidence(store):
    ch, ents, edges, men = build()
    with pytest.raises(ValueError, match="crosses tenants"):
        store.load([], [], [GEdge(FILM, "RELATED_TO", "t2|PERSON:dana reed", "t1", ("c1",))], [])
    with pytest.raises(ValueError, match="crosses tenants"):
        store.load([], [], [], [("x1", DANA)])                  # t2 chunk mentioning a t1 entity
    with pytest.raises(ValueError, match="evidence"):
        store.load([], [], [GEdge(FILM, "RELATED_TO", DANA, "t1", ("x1",))], [])
