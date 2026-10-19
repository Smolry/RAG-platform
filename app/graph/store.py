"""Graph store (Project 3): contract + in-memory implementation + Neo4j implementation.

SECURITY MODEL (a graph is an easy way to bypass ACLs, so it is enforced here, not by callers):
  * Entities are tenant-namespaced; traversal never leaves the caller's tenant.
  * (:Chunk) carries tenant_id / allowed_roles / allowed_users (copied from the vector payload).
  * (:Entity)-[:REL]->(:Entity) carries the UNION of its evidence chunks' ACLs. An edge is traversable only if the caller
    could read at least one evidence chunk (union-of-roles intersect  <=>  some chunk's roles intersect).
    Without this, a restricted chunk would still "connect" entities and leak reachability.
  * Returned chunks are ACL-filtered again; the hybrid retriever runs the app-level second check on top.
  * `update_document_acl` propagates a revocation to chunk nodes AND recomputes every affected edge's ACL.
Embeddings are deliberately NOT stored here: the vector leg stays in Qdrant (pre-filtered ACL search, and the baseline
comparison stays controlled). See docs/DECISIONS.md.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from app.security.acl import is_authorized
from app.security.auth import Principal


@dataclass(frozen=True)
class GChunk:
    chunk_id: str
    document_id: str
    text: str
    tenant_id: str
    classification: str = "public"
    allowed_roles: tuple[str, ...] = ()
    allowed_users: tuple[str, ...] = ()

    def payload(self) -> dict:
        return {"chunk_id": self.chunk_id, "document_id": self.document_id, "text": self.text, "tenant_id": self.tenant_id,
                "classification": self.classification, "allowed_roles": list(self.allowed_roles),
                "allowed_users": list(self.allowed_users)}


@dataclass(frozen=True)
class GEntity:
    id: str
    name: str
    type: str
    tenant_id: str
    aliases: tuple[str, ...] = ()
    mention_count: int = 0


@dataclass(frozen=True)
class GEdge:
    source_id: str
    type: str
    target_id: str
    tenant_id: str
    evidence: tuple[str, ...]  # chunk ids; ACL is derived from them

    @property
    def key(self) -> str:
        return f"{self.source_id}|{self.type}|{self.target_id}"


@dataclass
class Expanded:
    chunk_id: str
    document_id: str
    text: str
    payload: dict
    paths: int
    min_hops: int


@dataclass
class Triple:
    source: str
    type: str
    target: str
    evidence_chunk_ids: list[str] = field(default_factory=list)


def edge_acl(edge: GEdge, chunk_acl: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> tuple[list[str], list[str]]:
    roles: set[str] = set()
    users: set[str] = set()
    for ev in edge.evidence:
        r, u = chunk_acl.get(ev, ((), ()))
        roles.update(r)
        users.update(u)
    return sorted(roles), sorted(users)


def _ok(p: Principal, tenant_id: str, roles, users) -> bool:
    return is_authorized(p, {"tenant_id": tenant_id, "allowed_roles": list(roles), "allowed_users": list(users)})


def validate_tenants(chunks: list[GChunk], entities: list[GEntity], edges: list[GEdge], mentions: list[tuple[str, str]],
                     known_chunks: dict[str, str] | None = None, known_entities: dict[str, str] | None = None) -> None:
    """Refuse to load a graph that crosses tenants (edge endpoints, mentions, evidence). Prevention beats detection."""
    ct = {c.chunk_id: c.tenant_id for c in chunks}
    et = {e.id: e.tenant_id for e in entities}
    for chunk_id, tenant_id in (known_chunks or {}).items():
        if chunk_id in ct and ct[chunk_id] != tenant_id:
            raise ValueError(f"chunk {chunk_id} crosses tenants")
    for entity_id, tenant_id in (known_entities or {}).items():
        if entity_id in et and et[entity_id] != tenant_id:
            raise ValueError(f"entity {entity_id} crosses tenants")
    ct.update(known_chunks or {})
    et.update(known_entities or {})
    for e in edges:
        if et.get(e.source_id) != e.tenant_id or et.get(e.target_id) != e.tenant_id:
            raise ValueError(f"edge {e.key} crosses tenants")
        if any(ct.get(ev) != e.tenant_id for ev in e.evidence):
            raise ValueError(f"edge {e.key} has evidence from another tenant (or unknown chunk)")
    for cid, eid in mentions:
        if cid not in ct or eid not in et:
            raise ValueError(f"mention ({cid}, {eid}) references an unknown node")
        if ct[cid] != et[eid]:
            raise ValueError(f"mention ({cid}, {eid}) crosses tenants")


class GraphStore(Protocol):
    def ensure_schema(self) -> None: ...
    def clear_tenant(self, tenant_id: str) -> None: ...
    def load(self, chunks: list[GChunk], entities: list[GEntity], edges: list[GEdge],
             mentions: list[tuple[str, str]]) -> None: ...
    def all_entities(self, tenant_id: str) -> list[GEntity]: ...
    def expand(self, seed_ids: list[str], hops: int, principal: Principal, limit: int,
               hub_degree_cap: int) -> list[Expanded]: ...
    def triples(self, chunk_ids: list[str], principal: Principal) -> list[Triple]: ...
    def update_document_acl(self, document_id: str, tenant_id: str, allowed_roles: list[str],
                            allowed_users: list[str]) -> int: ...
    def stats(self) -> dict: ...


# ======================================================================================================================
class InMemoryGraphStore:
    """Reference implementation: tests and tiny local runs. Semantics mirror the Cypher in Neo4jGraphStore."""

    def __init__(self) -> None:
        self.chunks: dict[str, GChunk] = {}
        self.entities: dict[str, GEntity] = {}
        self.edges: dict[str, GEdge] = {}
        self.edge_acl: dict[str, tuple[list[str], list[str]]] = {}
        self.entity_chunks: dict[str, set[str]] = defaultdict(set)
        self.degree: dict[str, int] = Counter()

    def ensure_schema(self) -> None:
        pass

    def clear_tenant(self, tenant_id: str) -> None:
        self.chunks = {k: v for k, v in self.chunks.items() if v.tenant_id != tenant_id}
        gone = {k for k, v in self.entities.items() if v.tenant_id == tenant_id}
        self.entities = {k: v for k, v in self.entities.items() if k not in gone}
        self.edges = {k: v for k, v in self.edges.items() if v.tenant_id != tenant_id}
        self.edge_acl = {k: v for k, v in self.edge_acl.items() if k in self.edges}
        self.entity_chunks = defaultdict(set, {k: v for k, v in self.entity_chunks.items() if k not in gone})
        self.degree = Counter({k: v for k, v in self.degree.items() if k not in gone})

    def load(self, chunks, entities, edges, mentions) -> None:
        validate_tenants(chunks, entities, edges, mentions, {k: v.tenant_id for k, v in self.chunks.items()},
                         {k: v.tenant_id for k, v in self.entities.items()})
        for c in chunks:
            self.chunks[c.chunk_id] = c
        for e in entities:
            self.entities[e.id] = e
        acl = {c.chunk_id: (c.allowed_roles, c.allowed_users) for c in self.chunks.values()}
        for e in edges:
            if e.source_id not in self.entities or e.target_id not in self.entities:
                raise ValueError(f"edge {e.key} references an unknown entity")
            self.edges[e.key] = e
            self.edge_acl[e.key] = edge_acl(e, acl)
            self.degree[e.source_id] += 1
            self.degree[e.target_id] += 1
        for chunk_id, entity_id in mentions:
            if chunk_id not in self.chunks or entity_id not in self.entities:
                raise ValueError(f"mention ({chunk_id}, {entity_id}) references an unknown node")
            self.entity_chunks[entity_id].add(chunk_id)

    def all_entities(self, tenant_id: str) -> list[GEntity]:
        return [e for e in self.entities.values() if e.tenant_id == tenant_id]

    def _edge_ok(self, p: Principal, key: str) -> bool:
        roles, users = self.edge_acl[key]
        return _ok(p, self.edges[key].tenant_id, roles, users)

    def expand(self, seed_ids, hops, principal, limit, hub_degree_cap=10**9) -> list[Expanded]:
        hops = int(hops)
        adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key, e in self.edges.items():
            if e.tenant_id == principal.tenant_id and self._edge_ok(principal, key):
                adj[e.source_id].append((key, e.target_id))
                adj[e.target_id].append((key, e.source_id))
        paths: Counter = Counter()
        min_hops: dict[str, int] = {}

        def visit(node: str, used: frozenset, depth: int) -> None:
            for cid in self.entity_chunks.get(node, ()):
                c = self.chunks[cid]
                if _ok(principal, c.tenant_id, c.allowed_roles, c.allowed_users):
                    paths[cid] += 1
                    min_hops[cid] = min(min_hops.get(cid, depth), depth)
            if depth == hops:
                return
            # hub pruning: never walk THROUGH a high-degree node (it would connect everything to everything)
            if depth > 0 and self.degree[node] > hub_degree_cap:
                return
            for key, nb in adj.get(node, ()):
                if key not in used:  # Cypher trail semantics: a relationship is used at most once per path
                    visit(nb, used | {key}, depth + 1)

        for s in dict.fromkeys(seed_ids):
            ent = self.entities.get(s)
            if ent and ent.tenant_id == principal.tenant_id:
                visit(s, frozenset(), 0)
        out = []
        for cid, n in sorted(paths.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
            c = self.chunks[cid]
            out.append(Expanded(cid, c.document_id, c.text, c.payload(), n, min_hops[cid]))
        return out

    def triples(self, chunk_ids, principal) -> list[Triple]:
        want = set(chunk_ids)
        out = []
        for key, e in self.edges.items():
            ev = [x for x in e.evidence if x in want]
            if ev and e.tenant_id == principal.tenant_id and self._edge_ok(principal, key):
                out.append(Triple(self.entities[e.source_id].name, e.type, self.entities[e.target_id].name, ev))
        return sorted(out, key=lambda t: (t.source, t.type, t.target))

    def update_document_acl(self, document_id, tenant_id, allowed_roles, allowed_users) -> int:
        touched = {cid for cid, c in self.chunks.items() if c.document_id == document_id and c.tenant_id == tenant_id}
        for cid in touched:
            c = self.chunks[cid]
            self.chunks[cid] = GChunk(c.chunk_id, c.document_id, c.text, c.tenant_id, c.classification,
                                      tuple(allowed_roles), tuple(allowed_users))
        acl = {cid: (c.allowed_roles, c.allowed_users) for cid, c in self.chunks.items()}
        for key, e in self.edges.items():
            if touched & set(e.evidence):
                self.edge_acl[key] = edge_acl(e, acl)
        return len(touched)

    def stats(self) -> dict:
        return {"chunks": len(self.chunks), "entities": len(self.entities), "relations": len(self.edges),
                "mentions": sum(len(v) for v in self.entity_chunks.values())}


# ======================================================================================================================
_CHUNK_ACL = ("c.tenant_id = $tenant AND (any(x IN c.allowed_roles WHERE x IN $roles) OR $user IN c.allowed_users)")
_EDGE_ACL = ("r.tenant_id = $tenant AND (any(x IN r.allowed_roles WHERE x IN $roles) OR $user IN r.allowed_users)")


class Neo4jGraphStore:
    """Neo4j implementation (Cypher mirrors the PDF's expansion query + ACL predicates).

    !! NOT executed in the build sandbox (no Neo4j server reachable). It is covered by (a) tests/unit/test_graph_neo4j_queries.py
    with a fake driver (parameter + predicate checks) and (b) tests/integration/test_graph_store_contract.py, which runs the SAME
    contract tests against a live server when NEO4J_URI is set (`make test-neo4j`)."""

    def __init__(self, uri: str, user: str, password: str, database: str | None = None, driver=None):
        if driver is None:
            from neo4j import GraphDatabase  # lazy
            driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver, self.database = driver, database

    def _run(self, query: str, **params):
        with self.driver.session(database=self.database) as s:
            return list(s.run(query, **params))

    def close(self) -> None:
        self.driver.close()

    def ensure_schema(self) -> None:
        for q in ("CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
                  "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
                  "CREATE INDEX chunk_doc IF NOT EXISTS FOR (c:Chunk) ON (c.document_id)",
                  "CREATE INDEX entity_tenant IF NOT EXISTS FOR (e:Entity) ON (e.tenant_id)",
                  "CREATE INDEX rel_key IF NOT EXISTS FOR ()-[r:REL]-() ON (r.key)"):
            self._run(q)

    def clear_tenant(self, tenant_id: str) -> None:
        self._run("MATCH (c:Chunk {tenant_id: $t}) DETACH DELETE c", t=tenant_id)
        self._run("MATCH (e:Entity {tenant_id: $t}) DETACH DELETE e", t=tenant_id)

    def load(self, chunks, entities, edges, mentions) -> None:
        chunk_ids = {c.chunk_id for c in chunks}
        chunk_ids.update(ev for edge in edges for ev in edge.evidence)
        chunk_ids.update(chunk_id for chunk_id, _ in mentions)
        entity_ids = {e.id for e in entities}
        entity_ids.update(edge.source_id for edge in edges)
        entity_ids.update(edge.target_id for edge in edges)
        entity_ids.update(entity_id for _, entity_id in mentions)

        known_chunks = {
            row["id"]: row["tenant_id"]
            for row in self._run(
                "UNWIND $ids AS id OPTIONAL MATCH (c:Chunk {id: id}) "
                "RETURN c.id AS id, c.tenant_id AS tenant_id",
                ids=sorted(chunk_ids),
            )
            if row["id"] is not None
        }
        known_entities = {
            row["id"]: row["tenant_id"]
            for row in self._run(
                "UNWIND $ids AS id OPTIONAL MATCH (e:Entity {id: id}) "
                "RETURN e.id AS id, e.tenant_id AS tenant_id",
                ids=sorted(entity_ids),
            )
            if row["id"] is not None
        }
        validate_tenants(chunks, entities, edges, mentions, known_chunks, known_entities)
        acl = {c.chunk_id: (c.allowed_roles, c.allowed_users) for c in chunks}
        self._run("UNWIND $rows AS row MERGE (c:Chunk {id: row.id}) SET c.document_id = row.document_id, c.text = row.text, "
                  "c.tenant_id = row.tenant_id, c.classification = row.classification, c.allowed_roles = row.allowed_roles, "
                  "c.allowed_users = row.allowed_users",
                  rows=[{"id": c.chunk_id, "document_id": c.document_id, "text": c.text, "tenant_id": c.tenant_id,
                         "classification": c.classification, "allowed_roles": list(c.allowed_roles),
                         "allowed_users": list(c.allowed_users)} for c in chunks])
        deg: Counter = Counter()
        for e in edges:
            deg[e.source_id] += 1
            deg[e.target_id] += 1
        self._run("UNWIND $rows AS row MERGE (e:Entity {id: row.id}) SET e.name = row.name, e.type = row.type, "
                  "e.tenant_id = row.tenant_id, e.aliases = row.aliases, e.mention_count = row.mention_count, e.degree = row.degree",
                  rows=[{"id": e.id, "name": e.name, "type": e.type, "tenant_id": e.tenant_id, "aliases": list(e.aliases),
                         "mention_count": e.mention_count, "degree": deg[e.id]} for e in entities])
        rel_rows = []
        for e in edges:
            roles, users = edge_acl(e, acl)
            rel_rows.append({"s": e.source_id, "t": e.target_id, "key": e.key, "type": e.type, "tenant_id": e.tenant_id,
                             "evidence": list(e.evidence), "allowed_roles": roles, "allowed_users": users})
        self._run("UNWIND $rows AS row MATCH (a:Entity {id: row.s}) MATCH (b:Entity {id: row.t}) "
                  "MERGE (a)-[r:REL {key: row.key}]->(b) SET r.type = row.type, r.tenant_id = row.tenant_id, "
                  "r.evidence = row.evidence, r.allowed_roles = row.allowed_roles, r.allowed_users = row.allowed_users",
                  rows=rel_rows)
        self._run("UNWIND $rows AS row MATCH (c:Chunk {id: row.c}) MATCH (e:Entity {id: row.e}) MERGE (c)-[:MENTIONS]->(e)",
                  rows=[{"c": c, "e": e} for c, e in mentions])

    def all_entities(self, tenant_id: str) -> list[GEntity]:
        rows = self._run("MATCH (e:Entity {tenant_id: $t}) RETURN e.id AS id, e.name AS name, e.type AS type, "
                         "e.aliases AS aliases, e.mention_count AS mc", t=tenant_id)
        return [GEntity(r["id"], r["name"], r["type"], tenant_id, tuple(r["aliases"] or ()), int(r["mc"] or 0)) for r in rows]

    def expand(self, seed_ids, hops, principal, limit, hub_degree_cap=10**9) -> list[Expanded]:
        hops = int(hops)  # variable-length bounds cannot be parameters in Cypher: interpolate a validated int ONLY
        if not 0 <= hops <= 3:
            raise ValueError("hops must be in 0..3")
        q = (f"MATCH (e:Entity) WHERE e.id IN $seeds AND e.tenant_id = $tenant "
             f"MATCH p = (e)-[:REL*0..{hops}]-(n:Entity) "
             f"WHERE ALL(r IN relationships(p) WHERE {_EDGE_ACL}) "
             f"AND ALL(m IN nodes(p)[1..-1] WHERE m.degree <= $hub_cap) "
             f"MATCH (n)<-[:MENTIONS]-(c:Chunk) WHERE {_CHUNK_ACL} "
             f"RETURN c.id AS chunk_id, c.document_id AS document_id, c.text AS text, c.tenant_id AS tenant_id, "
             f"c.classification AS classification, c.allowed_roles AS allowed_roles, c.allowed_users AS allowed_users, "
             f"count(*) AS paths, min(length(p)) AS min_hops ORDER BY paths DESC, chunk_id LIMIT $limit")
        rows = self._run(q, seeds=list(seed_ids), tenant=principal.tenant_id, roles=list(principal.roles),
                         user=principal.user_id, hub_cap=int(hub_degree_cap), limit=int(limit))
        out = []
        for r in rows:
            payload = {k: r[k] for k in ("tenant_id", "classification", "allowed_roles", "allowed_users")}
            payload.update(chunk_id=r["chunk_id"], document_id=r["document_id"], text=r["text"])
            out.append(Expanded(r["chunk_id"], r["document_id"], r["text"], payload, int(r["paths"]), int(r["min_hops"])))
        return out

    def triples(self, chunk_ids, principal) -> list[Triple]:
        q = (f"MATCH (a:Entity)-[r:REL]->(b:Entity) WHERE any(ev IN r.evidence WHERE ev IN $chunks) AND {_EDGE_ACL} "
             f"RETURN a.name AS s, r.type AS t, b.name AS o, [ev IN r.evidence WHERE ev IN $chunks] AS evidence "
             f"ORDER BY s, t, o")
        rows = self._run(q, chunks=list(chunk_ids), tenant=principal.tenant_id, roles=list(principal.roles),
                         user=principal.user_id)
        return [Triple(r["s"], r["t"], r["o"], list(r["evidence"])) for r in rows]

    def update_document_acl(self, document_id, tenant_id, allowed_roles, allowed_users) -> int:
        rows = self._run("MATCH (c:Chunk {document_id: $d, tenant_id: $t}) SET c.allowed_roles = $roles, "
                         "c.allowed_users = $users RETURN c.id AS id", d=document_id, t=tenant_id,
                         roles=list(allowed_roles), users=list(allowed_users))
        touched = [r["id"] for r in rows]
        if not touched:
            return 0
        edges = self._run("MATCH ()-[r:REL {tenant_id: $t}]->() WHERE any(ev IN r.evidence WHERE ev IN $touched) "
                          "RETURN r.key AS key, r.evidence AS evidence", t=tenant_id, touched=touched)
        for er in edges:
            acl = self._run("MATCH (c:Chunk) WHERE c.id IN $ev RETURN c.allowed_roles AS roles, c.allowed_users AS users",
                            ev=list(er["evidence"]))
            roles = sorted({x for a in acl for x in (a["roles"] or [])})
            users = sorted({x for a in acl for x in (a["users"] or [])})
            self._run("MATCH ()-[r:REL {key: $k}]->() SET r.allowed_roles = $roles, r.allowed_users = $users",
                      k=er["key"], roles=roles, users=users)
        return len(touched)

    def stats(self) -> dict:
        one = lambda q: int(self._run(q)[0]["n"])
        return {"chunks": one("MATCH (c:Chunk) RETURN count(c) AS n"), "entities": one("MATCH (e:Entity) RETURN count(e) AS n"),
                "relations": one("MATCH ()-[r:REL]->() RETURN count(r) AS n")}
