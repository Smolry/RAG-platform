# Limitations and future work

The repository now contains non-stub measurements for the four phases. They are useful engineering evidence, not production guarantees: the samples are modest, some data is author-curated, and the measurements were collected in individual runs.

## Evaluation-wide limitations

- LLM-judged answer correctness is a proxy for human quality; model outputs and latency vary between runs.
- Cost is computed from `configs/models.yaml`, not provider invoices. Pricing, token accounting, caching, and concurrency can differ in a deployed service.
- Evaluation sizes are limited: 60 questions for baseline/Project 2, 60 for the Project 1 benchmark, and 180 for Project 3. Confidence intervals do not cover dataset-selection bias.
- `fetch_corpus.py` and evaluation-set authoring are not a repeatable public benchmark pipeline; preserve source data, hashes, model versions, and configuration when comparing runs.
- The synthetic tenant corpus is small (24 documents, 15 users). The 840-query security result covers implemented attacks, not all attack strategies.
- The security test oracle independently checks returned records against the fixture policy, but it does not prove the policy itself is complete.
- Dev tokens are minted by `scripts/issue_token.py`; there is no production identity provider, key rotation, or token revocation list.
- Audit append-only behavior has tests, but the PostgreSQL DDL path should be verified against the deployed database version.
- Chunk size uses a words-per-token approximation. Corpus fetch and generated evaluations depend on external network/model availability.

## Project 2: tenant security

- The saved suite observed zero leaks in 840 queries and a 100% adversarial pass rate. This result is bounded by the test corpus, policies, and attack cases; it is not a formal security proof.
- ACL search overhead is noisy: the saved run measured lower filtered-search latency than unfiltered latency. Do not interpret that as an ACL speedup; repeat controlled measurements at realistic scale.
- Permission revocation measurements use 20 trials. Add load, concurrent updates, multiple ACL types, and independent cross-tenant tests.

## Project 1: cache and routing

- The author-written router dev/test results (98.3% / 97.5%) conceal a poor stress-set result (25.0% over 12 examples; complex recall 0/6). Router labels and trigger features are correlated because they were created by the same author.
- The author-written cache set (50 paraphrases and 50 near-misses) found no threshold with zero false hits. At the minimum-false-hit threshold 0.92 there were 3/50 false hits and 22/50 false misses. The benchmark used 0.94; neither value should be treated as generally safe.
- Benchmark outputs show false cache hits judged wrong: 10 in cache-only and 11 in the full arm. Keep permission-context keys, corpus version, TTL, live ACL re-check, and ACL-change purge.
- Cache hits still require an embedding and live-ACL lookup. At high QPS this adds latency and Qdrant load.
- Router and escalation signals are heuristics. A confidently wrong small-model answer with citations may not be escalated.
- The dashboard reports quality only for requests with judge scores (such as benchmark/evaluation runs), not ordinary live traffic.
- Benchmark cost uses the sample price table in `configs/models.yaml`.
- There are no database migrations. Project 1 adds fields/tables that `create_all` does not alter in an existing database; introduce migrations before upgrading a persistent deployment.

## Project 3: GraphRAG

- The saved evaluation used 180 validated HotpotQA questions. Its distractor setting is easier than open-domain retrieval, and supporting-fact recall is paragraph-level; the values are not directly comparable to published open-domain results.
- The resolved graph's hybrid arm scored 57.0% multi-hop F1 versus 55.6% for the no-resolution ablation on 120 matching questions, but the exploratory paired 95% interval included zero (-0.9 to +4.1 points). Supporting-fact paragraph recall was equal (82.5%). Vector-only F1 also shifted 4.3 points between the two separate runs, so repeat with controlled generation before claiming a resolution effect.
- The graph build reported $0.2482 for 1,188 extraction calls/chunks and about 48 minutes of wall time in the saved report. This is price-table-based and amortizes only if the graph is reused; cached rebuilds can take much less time.
- Extraction grounding and fixed schemas constrain errors but do not eliminate missed relations, grounded-but-wrong relations, or unresolved coreference.
- Entity resolution is precision-first and can miss valid merges. Merge decisions are logged in `graph_resolution_audit.json`, but that audit requires human review.
- The hub-degree cap trades recall for noise control and may cut valid paths through popular entities.
- Graph and vector stores are separate systems joined by chunk ID and can drift if only one is updated. A changed document currently requires rebuilding that tenant's graph.
- Edge ACLs use the union of evidence ACLs. An edge backed by both readable and restricted evidence is traversable when at least one readable evidence chunk supports it; the restricted chunk itself is still independently filtered.
- The saved build/evaluation artifacts record `graph_backend=neo4j`, but do not record whether the entire Neo4j contract test suite passed. Run `make test-neo4j` before relying on all store-contract cases.
- Question decomposition is not implemented. `SYSTEM=project3` is not combined with the Project 1 cache/router.

## Suggested next steps

1. Grow and independently label evaluation sets; freeze held-out data and report repeated-run uncertainty.
2. Redesign cache validation around false-hit cost, and use a conservative bypass for high-risk or meaning-sensitive queries.
3. Improve routing using representative traffic and conservative confidence/fallback behavior.
4. Add migrations, identity lifecycle, secret rotation, and production retention/observability controls.
5. Add incremental graph updates and automated vector/graph consistency checks.
6. Repeat GraphRAG and resolution-ablation runs with controlled model settings, broader corpora, and the live Neo4j contract suite.

## DevTrace corpus (Phase 2)

Snapshot `976aea99e8507ef2`: FastAPI 0.162.0 and Pydantic 2.14.1 docs/release notes, 38 files, 1,103 chunks, 920 sections (your original spec said ~100-300; scope was approved at full size on 2026-10-26 because a tiny corpus makes retrieval trivially easy).

- **Code samples are missing from FastAPI tutorial pages.** They are `{* file.py *}` includes whose code lives outside `docs/`; the include lines are dropped, not resolved. Prose that refers to "the code above" may have nothing above it.
- **Docs pages are versioned "as of" the pinned release only** (FastAPI 0.162.0, Pydantic 2.14.1). A page does not say when each statement became true. Applicability comes only from release-note entries (dated by their heading) and Pydantic's explicit `version-added`/`version-changed` notes, which are currently preserved as text, not parsed into fields.
- **Release-note subsections `Internal`, `Translations`, `Docs`, `New Contributors` are excluded** (configured in `sources.yaml`). A relevant fix reported under `Docs` would be missed.
- **Release history starts at FastAPI 0.100.0 and Pydantic 2.0.0**; stable releases only, no pre-releases. Older questions will correctly find nothing.
- **24 oversize chunks** (long code blocks or PR lists, up to 765 words) exceed the embedder's ~256-token window; only their beginning is represented in the embedding.
- **Public links are derived** from file paths and checked against each project's site config (nav and `site_url`), not fetched one by one. `provenance_url` (pinned raw URL) is the exact, reliable reference.
- **Indexing was tested with the hash embedder only.** Real MiniLM embeddings and retrieval quality are unmeasured until Phase 3/4.
- **No curated troubleshooting cases or third-party sources yet.** Only official docs.
- **Chunk text includes a breadcrumb prefix** (`FastAPI | Page > Section`), so exact-quote validation must allow for it.

## DevTrace retrieval (Phase 3)
- Retrieval *quality* with the real embedding model is **not measured yet**. Tests use a hash embedder; the model could not be downloaded in my
  build sandbox. Numbers appear only after the Phase 4 evaluation run on real embeddings.
- Version logic ignores pre-release tags and handles only `major.minor.patch`. Release notes are attributed to the version in their heading;
  a note without a parsable version cannot be range-filtered.
- Dedupe is by content hash, so near-duplicate text across pages is kept.
- Caveats about versions are rule-based wording, not a claim that a given bug affects the user's code.
