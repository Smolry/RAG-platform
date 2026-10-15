# Architecture

One service that gains capability phase by phase. Nothing is forked; each phase is a layer on `RAGService`.

```mermaid
flowchart TB
  C[Client] -->|Bearer JWT| API[FastAPI]
  API --> AUTH[verify_token -> Principal]
  AUTH --> ORCH[RAGService]
  subgraph P1["Project 1 (implemented)"]
    CACHE[Tenant-aware semantic cache] --- ROUTER[Rule router + escalation]
  end
  subgraph P3["Project 3 (PLANNED)"]
    KG[(Neo4j KG)] --- RERANK[Cross-encoder rerank]
  end
  ORCH --> EMB[sentence-transformers]
  ORCH -->|ACL pre-filter inside search| QD[(Qdrant)]
  ORCH --> ACL2[2nd app-level ACL check]
  ACL2 --> LLM[LiteLLM]
  ORCH --> PG[(Postgres: request_log + append-only audit_log)]
```

## Status by phase
| Phase | State |
|---|---|
| Baseline (ingest, chunk, embed, top-5, cite, FastAPI, eval, instrumentation) | **implemented, unit/integration tested offline** |
| Project 2 (JWT, ACL payload, pre-filter, 2nd check, audit, revocation, adversarial suite) | **implemented, tests pass offline**; must be re-run on real Qdrant + real embedder before quoting numbers |
| Project 1 (tenant-aware cache, router, escalation, dashboard, benchmark) | **implemented, 78+ tests pass offline**; threshold sweep + benchmark **not yet run on real stack** |
| Project 3 (GraphRAG) | not yet built |

## Secure request flow (Project 2)
```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  participant Q as Qdrant
  participant L as LLM
  participant D as Postgres
  U->>A: POST /query {query} + JWT
  A->>A: verify JWT -> (user_id, tenant_id, roles)  [body identity ignored]
  A->>Q: vector search WITH ACL filter (tenant must; role/user should)
  Q-->>A: authorized candidates only
  A->>A: 2nd check per chunk (fail-closed); violations -> audit + alert
  A->>L: prompt = only chunks that passed both checks
  A->>D: request_log + audit_log(+audit_document)
  A-->>U: answer + citations + sources
```
Failure paths: bad/expired token -> 401; non-admin on admin route -> 403; foreign/unknown doc on admin route -> 404
(existence never revealed); zero authorized chunks -> fixed "I don't know" with **no LLM call**.

## Data stores
- **Qdrant** `chunks`: vector + payload (`tenant_id, document_id, chunk_index, text, classification, allowed_roles, allowed_users, corpus_version`), keyword payload indexes on the ACL fields.
- **Postgres/SQLite**: `request_log` (instrumentation), `audit_log` + `audit_document` (append-only via DB triggers).

## Project 1 flow
See [docs/project1/README.md](project1/README.md) for the cache / routing / escalation flowchart and the security reasoning.
Cache hits skip retrieval, so validity carries the authorization context (tenant, role-set hash, corpus_version, TTL) and a live ACL re-check runs on every hit.
