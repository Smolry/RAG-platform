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

## Project 3 decisions
| # | Decision | Why | Alternatives / trade-off |
|---|---|---|---|
| 21 | **Neo4j holds the graph; Qdrant keeps ALL embeddings and vector search** (deviation from the PDF/spec's "chunk embeddings in a Neo4j vector index where practical") | one ACL pre-filter implementation (Qdrant payload filter), the vector leg stays *identical* to the baseline so the comparison is controlled, no duplicated embeddings | Neo4j vector index: single store, but a second ACL implementation and a changed baseline. Cost: two stores joined by chunk id |
| 22 | Fixed schema: 8 entity types, 19 relation types; synonyms aliased; unknown types rejected and counted | PDF: a constrained schema gives a much cleaner graph; rejection counts make extraction quality visible | open-vocabulary extraction (noisy, unmergeable) |
| 23 | Entity must literally occur in the chunk (grounding); `evidence_chunk_id` assigned by our code | LLM cannot fabricate entities or provenance | trust the LLM (hallucinated nodes; no reliable citations). Grounded-but-wrong relations remain possible |
| 24 | Entity resolution is **precision-first**: same type only; string rule AND embedding gate; ambiguity left unmerged; compare to representative only; full audit trail | a wrong merge silently creates false paths; a missed merge only loses recall | aggressive clustering (more recall, hidden errors); LLM-judged merges (cost) |
| 25 | Graph is tenant-namespaced; **edge ACL = union of its evidence chunks' ACLs**, traversable if the caller could read ≥1 | otherwise a restricted chunk would still connect entities and leak reachability; an edge also supported by a readable chunk is derivable from it, so no leak | per-edge single ACL; post-filter (unsafe) |
| 26 | Hub-degree cap (50): never walk *through* very high-degree entities | a hub like "United States" connects everything to everything and floods the candidate pool | no cap (noise), lower cap (loses legitimate paths through popular entities: a recall cost) |
| 27 | Expand 2 hops by default (PDF: 1–2) | bridge questions mostly need 1; chains need 2; candidates grow roughly with degree^hops | 1 hop (cheaper); 3+ (noise, cost) |
| 28 | Vector retrieval is never replaced; merge by chunk id | PDF: hybrid, not graph-only; the graph can only add candidates | graph-only retrieval |
| 29 | Second ACL check **re-reads graph-only chunks from Qdrant** | the graph's ACL copy is the same data the graph filter just used; an independent check must not share it | trust the graph payload (a stale graph would pass) |
| 30 | Cross-encoder (`ms-marco-MiniLM-L-6-v2`) reranks the merged pool (≤10 vector + ≤20 graph) to top 5; `vector_rerank` arm isolates it | candidates from different sources are not score-comparable; reranking is also a confound the evaluation must separate | bi-encoder score only; larger cross-encoder |
| 31 | Question entities: LLM (default) or n-gram dictionary match, both evaluated | LLM seeding costs a call per query; n-gram is free but brittle | NER model |
| 32 | Short-answer prompt for HotpotQA; citation markers stripped before EM/F1 | EM/F1 need short spans; `[1]` would otherwise count as a token | judge-based scoring (not ground truth) |
| 33 | HotpotQA **distractor** setting, 120 questions stratified by type; corpus = union of paragraphs; one paragraph = one document | PDF: build the corpus from the dataset's paragraphs and get answers + supporting facts free; shared pool behaves like a real index | open-domain/fullwiki (needs full Wikipedia) |
| 34 | Supporting-fact **recall is paragraph-level** | the unit retrieval returns | official HotpotQA SP metric is sentence-level (not comparable to the leaderboard) |
| 35 | Single-hop slice is LLM-written from one paragraph, `validated=false` until a human reviews | HotpotQA has no single-hop questions; needed to test for harm | none available |
| 36 | Extraction cache stores raw LLM output and re-validates on load; report counts total cost incl. cached calls, and fresh cost separately | rebuilds/ablations are free; the indexing-cost claim stays honest | cache validated output (stale after schema fixes) |
| 37 | Question decomposition **not implemented** | optional in the spec and must not delay the core | would be compared as `decomposed` vs `one-shot` |
| 38 | `SYSTEM=project3` does not enable the Project 1 cache/router | the evaluation isolates retrieval; a combined run would confound it | compose later (cache key would need the retrieval mode) |
