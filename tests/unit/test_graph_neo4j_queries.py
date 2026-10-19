"""Neo4jGraphStore against a FAKE driver: checks parameters + ACL predicates without a server. (Live parity: the contract tests.)"""
import pytest

from app.graph.store import GChunk, GEdge, GEntity, Neo4jGraphStore
from app.security.auth import Principal

P = Principal("u1", "t1", ("reader", "hr"))


class FakeSession:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        self.log.append((" ".join(query.split()), params))
        return self.rows.pop(0) if self.rows else []


class FakeDriver:
    def __init__(self, rows=None):
        self.log, self.rows, self.closed = [], rows or [], False

    def session(self, database=None):
        return FakeSession(self.log, self.rows)

    def close(self):
        self.closed = True


def store(rows=None):
    d = FakeDriver(rows)
    return Neo4jGraphStore("", "", "", driver=d), d


def test_expand_query_has_acl_on_edges_and_chunks_and_tenant_and_parameters():
    s, d = store()
    s.expand(["t1|PERSON:a"], 2, P, 7, 50)
    q, params = d.log[0]
    assert "[:REL*0..2]" in q and "e.tenant_id = $tenant" in q
    assert q.count("allowed_roles") >= 2 and "r.tenant_id = $tenant" in q and "c.tenant_id = $tenant" in q  # edge AND chunk ACL
    assert "nodes(p)[1..-1]" in q and "m.degree <= $hub_cap" in q
    assert params == {"seeds": ["t1|PERSON:a"], "tenant": "t1", "roles": ["reader", "hr"], "user": "u1", "hub_cap": 50, "limit": 7}


@pytest.mark.parametrize("bad", [-1, 4, 99])
def test_hops_validated_and_only_an_int_is_interpolated(bad):
    s, _ = store()
    with pytest.raises(ValueError):
        s.expand(["x"], bad, P, 1, 1)


def test_hops_cannot_be_used_for_injection():
    s, d = store()
    with pytest.raises((ValueError, TypeError)):
        s.expand(["x"], "2}) DETACH DELETE n //", P, 1, 1)
    assert not d.log


def test_user_data_never_interpolated_into_cypher():
    s, d = store()
    evil = "x' }) DETACH DELETE e //"
    s.expand([evil], 1, Principal(evil, evil, (evil,)), 1, 1)
    s.triples([evil], Principal(evil, evil, (evil,)))
    s.update_document_acl(evil, evil, [evil], [evil])
    s.clear_tenant(evil)
    for q, _ in d.log:
        assert "DETACH DELETE e //" not in q


def test_triples_query_is_acl_scoped_and_returns_only_requested_evidence():
    s, d = store()
    s.triples(["c1"], P)
    q, params = d.log[0]
    assert "r.tenant_id = $tenant" in q and "[ev IN r.evidence WHERE ev IN $chunks]" in q and params["chunks"] == ["c1"]


def test_update_acl_recomputes_edge_union_from_chunks():
    rows = [[{"id": "c2"}],                                                    # chunk update returns touched ids
            [{"key": "k1", "evidence": ["c2", "c3"]}],                         # affected edges
            [{"roles": ["hr"], "users": []}, {"roles": ["reader"], "users": ["u9"]}],  # ACL of the edge's evidence chunks
            []]
    s, d = store(rows)
    assert s.update_document_acl("d2", "t1", ["hr"], []) == 1
    last_q, last_p = d.log[-1]
    assert "SET r.allowed_roles" in last_q and last_p["roles"] == ["hr", "reader"] and last_p["users"] == ["u9"]


def test_update_acl_nothing_touched_stops_early():
    s, d = store([[]])
    assert s.update_document_acl("nope", "t1", [], []) == 0 and len(d.log) == 1


def test_load_writes_edge_acl_union_and_degree():
    s, d = store()
    ch = [GChunk("c1", "d1", "t", "t1", "public", ("a",), ()), GChunk("c2", "d2", "t", "t1", "public", ("b",), ("u",))]
    ents = [GEntity("t1|X:a", "A", "X", "t1"), GEntity("t1|X:b", "B", "X", "t1")]
    s.load(ch, ents, [GEdge("t1|X:a", "REL_T", "t1|X:b", "t1", ("c1", "c2"))], [("c1", "t1|X:a")])
    rel = next(p for q, p in d.log if "MERGE (a)-[r:REL" in q)["rows"][0]
    assert rel["allowed_roles"] == ["a", "b"] and rel["allowed_users"] == ["u"] and rel["evidence"] == ["c1", "c2"]
    ent_rows = next(p for q, p in d.log if "MERGE (e:Entity" in q)["rows"]
    assert {r["id"]: r["degree"] for r in ent_rows} == {"t1|X:a": 1, "t1|X:b": 1}


def test_load_rejects_cross_tenant_mentions_to_existing_nodes_before_writing():
    s, d = store([
        [{"id": "x1", "tenant_id": "t2"}],
        [{"id": "t1|PERSON:dana reed", "tenant_id": "t1"}],
    ])

    with pytest.raises(ValueError, match="crosses tenants"):
        s.load([], [], [], [("x1", "t1|PERSON:dana reed")])

    assert len(d.log) == 2
    assert all("MERGE" not in query for query, _ in d.log)


def test_schema_and_close():
    s, d = store()
    s.ensure_schema(); s.close()
    assert d.closed and any("CONSTRAINT chunk_id" in q for q, _ in d.log) and any("rel_key" in q for q, _ in d.log)
