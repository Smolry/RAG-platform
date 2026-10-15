# Decision log
Format: decision - why - alternatives. "PDF" = RAG_Build_Guides.pdf; "IMPL" = my choice where the PDF is silent.

| # | Decision | Why | Alternatives |
|---|---|---|---|
| 1 | Qdrant for vectors (PDF: Qdrant *or* pgvector+RLS) | Payload filters run **inside** the ANN search; same store later hosts the semantic cache | pgvector + row-level security |
| 2 | Postgres (SQLite in tests) for logs/audit | PDF allows either for P1, requires Postgres for P2 audit; one SQLAlchemy path | separate stores per project |
| 3 | `app/observability/` instead of `app/logging/` (IMPL) | avoids confusion with stdlib `logging` | - |
| 4 | Token ~= 0.75 words chunker (IMPL) | no tokenizer download/dependency; size is config | tiktoken / model tokenizer |
| 5 | Access rule = tenant AND (role OR user) (PDF snippet); classification derives default roles at ingest (IMPL) | single enforcement point, matches PDF code | numeric clearance levels |
| 6 | `corpus_version` = content hash of the corpus (IMPL) | deterministic: unchanged re-ingest keeps cache valid, any change invalidates | timestamp / manual counter |
| 7 | Admin audit/ACL endpoints scoped to caller's tenant; foreign docs return 404 (IMPL) | cross-tenant admin must not learn existence | global super-admin |
| 8 | Suite refuses to run on an empty/mis-ingested store (IMPL) | found by my own smoke run: it reported "0 leaks" on an empty DB | - |
| 9 | Hash embedder + stub LLM exist for offline tests only; stamped into every result file | tests run with no downloads, and nobody can mistake them for results | mocks per test |
| 10 | DAG-based routing intentionally **not used** | keeps scope on RAG engineering per the source spec | - |

Pending (written with their phase): rule-based routing first, tenant-aware cache context, 1-2 graph hops, cross-encoder reranking, threshold selection rule (PDF: lowest with zero false hits).

## Known gap: ACLs for derived data
PDF warns summaries, whole-document embeddings and chat history also need ACLs. This system has none of them today
(no chat history, no summaries, chunk-level embeddings only). If added, they MUST inherit `tenant_id/allowed_*` from the source documents
and be revoked via the same `update_acl` path.

## Project 1 decisions
| # | Decision | Why | Alternatives |
|---|---|---|---|
| 11 | Semantic cache lives in Qdrant (separate collection) | PDF allows it; reuses ANN + payload filters, so tenant/version/TTL are *filters*, not post-checks | Redis + RediSearch, in-process LRU |
| 12 | Cache validity = tenant ∧ permission_context ∧ corpus_version ∧ TTL ∧ sim≥τ | A cache hit skips retrieval, so it must carry the authorization context itself (PDF: "never leak cached answers across tenants") | tenant-only key (unsafe: HR answer served to contractor) |
| 13 | `permission_context` = hash(tenant, sorted roles); per-user-grant answers keyed by user | exact role-set equality is the conservative sharing rule | per-user cache (safe but ~no sharing) |
| 14 | Live-ACL re-check on hit + purge on ACL change | revocation must beat the cache; two independent layers | rely on TTL only (stale window = leak window) |
| 15 | `corpus_version` is **per tenant**, content-hash of that tenant's batch | another tenant's re-ingest must not flush my cache; unchanged re-ingest keeps entries | global version |
| 16 | Threshold chosen as lowest value with **zero false hits** (PDF); default 0.97 until measured | a false hit serves a wrong answer; a miss only costs money | F1-optimal threshold |
| 17 | Rule router first, escalation as backstop (PDF) | no training data, explainable, microsecond cost; E1 shows its weakness, escalation limits the damage | LLM/learned router (extra cost + latency, needs labels) |
| 18 | Escalation reuses retrieval and re-generates on LARGE; both calls are billed | honest cost accounting | silently re-route everything |
| 19 | "No quality drop" is a statistical gate (paired bootstrap CI vs −0.05 margin) | small eval sets cannot prove non-inferiority; the report must say so | compare raw means |
| 20 | DAG-based routing **intentionally excluded** | keeps the project centered on RAG engineering and the scope of the source spec | — |
