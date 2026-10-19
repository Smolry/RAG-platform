# Experiments log
Each record: Hypothesis · Baseline · Change · Dataset · Configuration · Metric · Result · Interpretation · Failure · Decision.
**Status legend:** ✅ RUN (output reproducible from this repo) · ⏳ PENDING (needs real embedder/LLM/corpus; command given) · ⚠️ RUN-OFFLINE (logic only, not reportable).

---
## E1 ✅ Rule router: does it generalise beyond the queries I wrote?
- **Hypothesis:** the rule router separates simple from complex queries well enough to route most traffic to the small model.
- **Baseline:** "route everything to LARGE" (accuracy undefined; cost reference).
- **Change:** `RuleRouter` (`configs/routing.yaml`): length, trigger keywords, entity count, multi-part; **query-only** signals evaluated here.
- **Dataset:** `data/eval/router_set.jsonl` — 100 author-labeled queries (50 simple / 50 complex), split 60 dev / 40 test, **+12 adversarial "stress" queries** added *after* seeing the dev/test numbers. Labels are mine: simple = single-fact lookup, complex = comparison/explanation/multi-part synthesis.
- **Config:** untouched defaults; **no tuning was done on any split**.
- **Command:** `python -m scripts.eval_router --split {dev|test|stress}` → `results/router_eval_*.json`
- **Result:** dev 98.3% (59/60), test 97.5% (39/40), **stress 25.0% (3/12; complex recall 0/6)**.
- **Interpretation:** the headline ~98% is **not trustworthy**: I wrote the queries and the router, so complex queries carry the very trigger words the router looks for. The stress set (short complex questions with no trigger words; simple questions that contain "why"/"difference"/"vs") shows the router is essentially a keyword detector.
- **Failure:** 6/6 stress "complex" queries were sent to SMALL (risky direction); 3/6 stress "simple" queries went to LARGE (wasteful direction).
- **Decision:** keep the router simple (PDF), do **not** tune on the 12 stress queries (overfitting a tiny set), and rely on **escalation** as the quality backstop. The real routing accuracy must be measured on queries from the real corpus/traffic; until then quote **dev/test AND stress**, never dev/test alone. A learned router (embedding + logistic regression) is the natural next step if stress-like traffic is common.

## E2 ✅ Mutation testing of cache isolation (a failure of my own tests)
- **Hypothesis:** the Project 1 test suite would catch removal of any cache security control.
- **Change:** 7 manual mutants of `semantic_cache.py`/`rag.py`: M1 drop tenant filter · M2 drop permission-context filter · M3 drop corpus-version filter · M4 drop TTL filter · M5 skip live-ACL re-check · M6 cache everything · M7 never flag `user_specific`.
- **Result (first pass):** M3–M6 caught; **M1, M2, M7 SURVIVED** (suite still green).
- **Interpretation:** orchestrator-level isolation tests were masked by the *second* layer (live-ACL re-check rejects the wrongly-served entry). Defense in depth hid a broken first layer — good for security, bad for test power.
- **Fix:** `tests/unit/test_cache_layer.py` tests `SemanticCache.lookup` directly (tenant, role-set, per-user entry, version, TTL) + a test that the orchestrator sets `user_specific`. **All 7 mutants now killed.** Side finding: when layer 2 rejects a hit it **deletes** that entry — fine for safety, but a broken layer 1 would also thrash other users' cache entries.
- **Decision:** keep both layers; test each independently. (Not a CI job yet; the mutants were applied by hand.)

## E3 ✅ Cache threshold sweep (0.85 → 0.97)
- **Hypothesis:** a threshold exists in [0.85, 0.97] with **zero false hits** on near-miss pairs while still hitting most paraphrases.
- **Dataset:** `data/eval/cache_pairs.jsonl` — 50 paraphrase pairs (SHOULD hit) + 50 near-miss pairs (MUST NOT hit: swapped entity, negation, different qualifier/number). Author-written; **review before trusting**.
- **Command:** `python -m scripts.tune_cache_threshold` with the real embedder → `results/cache_threshold_sweep.{csv,json}`.
- **Selection rule:** choose zero false hits if any threshold achieves it; otherwise mark the sweep unsafe and investigate.
- **Result:** the real sentence-transformers sweep (`NOT_FOR_REPORTING=false`) found **no threshold with zero false hits**. The selected minimum-false-hit threshold was 0.92: 3/50 false hits (6%), 22/50 false misses (44%), and 56% hit rate on paraphrases. Hard near-misses scored as high as 1.0. The benchmark run later used 0.94; this is not validated as safe by the sweep.
- **Interpretation:** semantic similarity alone cannot reliably distinguish several meaning-reversing near-misses in this author-written 50+50 set. This result is a warning against treating a high cosine threshold as a safety guarantee.
- **Decision:** do not claim a safe threshold. Keep live ACL re-check and permission-aware cache boundaries; improve the near-miss set and consider bypassing cache for high-risk query classes.

## E4 ✅ Project 1 benchmark (cache + routing vs always-large)
- **Hypothesis:** cache + routing cut cost/p95 latency with no quality drop.
- **Arms:** `always_large`, `routing_only`, `cache_only`, `full` (`app/evaluation/benchmark.py`). Workload: originals → paraphrases → repeats.
- **Metrics:** cost/request, p50/p95, cache hit rate, route split, escalation rate, judged quality, **paired bootstrap 95% CI of the quality delta**, false cache hits judged wrong.
- **Claim rule:** "no quality drop" is printed **only if the CI lower bound > −0.05** (configurable `--margin`); otherwise the report says NOT supported. A 50-item eval set often cannot establish non-inferiority — that is a legitimate result.
- **Command:** `python -m scripts.run_benchmark --tenant acme --user eval@acme --roles employee --only-validated` → `results/project1_{summary.json,report.md,csv}`.
- **Result:** the saved non-stub run used 60 questions (original, paraphrase, repeat passes), LiteLLM models `ollama/nemotron-3-nano:30b-cloud` / `ollama/gemma4:31b:cloud`, sentence-transformers, and threshold 0.94. `always_large`: $0.00024/request, p95 1,782 ms, quality 0.706. `cache_only`: $0.00011/request (-52.9%), p95 1,338 ms (-24.9%), quality 0.711. `full`: $0.00012/request (-50.3%), p95 2,414 ms (+35.5%), quality 0.733, cache hit 54.4%; 11 false cache hits were judged wrong. The full-arm quality delta was +0.028 with 95% CI [0.000, +0.061]; the report's no-quality-drop rule was satisfied. Routing-only increased cost 21.7% and p95 574.8%.
- **Interpretation:** cache-only provided the clearest cost/latency balance in this run. The combined arm improved judged quality and cost but worsened tail latency; routing-only was not beneficial on this workload.
- **Caveat:** this is one modest, author-curated evaluation with model-price-table costs. The 50+50 cache-pair set produced false hits at every threshold in the sweep. Treat the benchmark claim as a measured result on this set, not a production-wide guarantee.

---
## E5 ✅ Mutation testing of Project 3 security and extraction controls
- **Hypothesis:** the existing 154-test suite catches removal of any graph security / extraction control. (Project 1's E2 showed that a second security layer can mask a broken first layer.)
- **Change:** 20 hand-applied mutants (G1–G20) over `InMemoryGraphStore`, `Neo4jGraphStore` Cypher strings, `HybridRetriever`, the ACL-update API hook, extraction and resolution; plus 4 targeted resolution mutants (R1–R4). Each applied alone, full suite run, file restored.
- **Result:** G2–G14, G16–G20 killed. **G1 survived: an *equivalent* mutant** (the explicit edge-tenant check is redundant because `_edge_ok` already enforces the tenant). **G15 was an *invalid* mutant** (my edit changed nothing). R1 (bare surname merge) and R2 (guess on ambiguity) killed. **R3 (transitive chaining) and R4 (corporate-suffix rule applied to PERSON) survived.**
- **Interpretation:** R3 exposed a vacuous test: `test_no_transitive_chaining...` asserted `>= 2 entities`, true whatever the code did, and its example strings did not form an A~B, B~C, A≁C chain. R4 showed the type guard on the suffix rule was untested.
- **Fix:** replaced the test with a measured chain (fuzzy ratios 92.6 / 92.6 / 85.2) and added a type-guard test; R3 and R4 are now killed. Suite: 155 passed, 12 skipped.
- **Process failure (mine):** the first mutation run exceeded the tool timeout and was killed *after* applying G18 and *before* restoring `store.py`, leaving a security check removed in the working tree. I noticed from the file-changed notice, located the applied mutant by pattern search, restored it, re-verified every mutant pattern, and rewrote the harness to back files up on disk and restore in `finally`. Lesson: never run in-place mutation without a crash-safe restore.
- **Decision:** both resolution tests kept; mutation testing stays manual (not a CI job).
- **Not covered:** the Cypher mutants (G18–G20) are killed by *string-level* fake-driver tests; whether the Cypher is semantically correct is E8's job.

## E6 ✅ Does entity resolution matter? (graph with vs without resolution)
- **Hypothesis:** without resolution the graph is fragmented, traversal reaches fewer bridge documents, and multi-hop supporting-fact recall and F1 drop.
- **Baseline:** `hybrid` arm on the resolved graph. **Change:** `scripts.build_graph --no-resolve` (identity resolution: one node per distinct (type, normalised name)).
- **Dataset:** the same 120 HotpotQA questions. **Command:** `make p3-ablate-resolution` (extraction is cached, so rebuilds make no new LLM calls) → `results/p3_noresolve/` vs `results/`.
- **Metric:** SF recall, all-found, F1 on the multi-hop slice; entity count before/after resolution (`graph_build_report.json`).
- **Result:** both real-stack artifacts are saved. On the 120 matching multi-hop question IDs, resolved hybrid F1 was 57.0% and no-resolution hybrid F1 was 55.6%, a paired mean delta of +1.4 points (exploratory bootstrap 95% CI: -0.9 to +4.1). Supporting-fact recall was 82.5% in both runs. The resolved and no-resolution vector-only F1 baselines differed by 4.3 points (47.0% vs 51.3%), indicating substantial run-to-run variation and limiting causal interpretation.
- **Interpretation:** the point estimate favors resolution, but this sample does not establish a reliable F1 gain, and paragraph-level supporting-fact recall did not change. Repeat the ablation with deterministic generation or multiple controlled runs before attributing gains to resolution.
- **Failure mode to look for:** HotpotQA paragraphs are Wikipedia leads, so surface forms are often *already* consistent ("Dana Reed" everywhere) and resolution may add little. That would be a legitimate, reportable finding: it would mean resolution matters more on messier corpora than on this benchmark. Also read `graph_resolution_audit.json`: a *wrong* merge (two distinct people) is worse than a missed one.

## E7 ✅ GraphRAG vs vector RAG on HotpotQA (multi-hop and single-hop)
- **Hypotheses:** (a) `hybrid` raises multi-hop F1 and SF recall over `vector`; (b) part of any gain is just the reranker (`vector_rerank` isolates it); (c) single-hop quality is not harmed; (d) n-gram seeding gets most of the LLM-seeding benefit at lower cost.
- **Arms / metrics / claim rules:** see `docs/project3/README.md`. Paired bootstrap CIs; "improvement SUPPORTED" only if the CI lower bound > 0.
- **Command:** `make p3-eval` → `results/project3_report.md`.
- **Result:** the saved run contains 180 validated questions (120 multi-hop, 60 single-hop), with LiteLLM, sentence-transformers, Neo4j, and a cross-encoder. Overall hybrid F1 was 71.1% vs 62.9% vector-only (+8.2 points; paired 95% CI +3.5 to +13.2). Multi-hop hybrid F1 was 57.0% vs 47.0% (+10.0; CI +3.3 to +17.0), and supporting-fact paragraph recall was 82.5% vs 74.6%. Single-hop hybrid F1 was 99.4% vs 94.8% (+4.7; CI +0.9 to +9.3). Graph indexing reported $0.2482 for 1,188 chunks and 1,188 extraction calls.
- **Interpretation:** this run supports a multi-hop F1 improvement and no single-hop harm on the tested set. Vector-plus-rerank alone also improved F1, so the hybrid-vs-vector difference is not all attributable to graph retrieval. The benchmark is small and HotpotQA distractor setting is not open-domain retrieval.
- **Honest priors:** with a 120-question sample, CIs on F1 will be wide (roughly ±6–8 points for a paired difference of this kind), so a real 3–5 point gain may not be distinguishable from zero; EM is harsh on free-form answers; the distractor setting is easier than open-domain retrieval. Indexing cost is paid up front and is substantial relative to a vector index (one LLM call per chunk): report it next to any gain.
- **Decision rule:** report the multi-hop result *and* single-hop result *and* the indexing cost, whichever way they point.

## E8 ⏳ Neo4j Cypher correctness (contract tests on a live server)
- **Hypothesis:** the Cypher in `Neo4jGraphStore` has the same semantics as the in-memory reference (hops, path counts, ACL on edges and chunks, tenant isolation, hub cap, revocation).
- **Test:** the 12 contract tests in `tests/integration/test_graph_store_contract.py` run against both stores; the Neo4j variants are skipped unless `NEO4J_URI` is set.
- **Command:** `make neo4j-up && make test-neo4j`.
- **Result:** the saved Project 3 build and hybrid evaluation used `graph_backend=neo4j`, so production-path graph construction and retrieval queries executed. The saved artifacts do not establish that all 12 contract tests passed; run `make neo4j-up` and `make test-neo4j` to verify the full contract suite. Things still worth checking include relationship-uniqueness semantics inside variable-length paths, how `min(length(p))` interacts with `count(*)`, and `nodes(p)[1..-1]` on zero-length paths.

## Defect found and fixed during the Project 3 review
`app/core/config.py` contained **two** Project 3 settings blocks with overlapping names and *conflicting defaults* (e.g. Neo4j password `ragplatform-dev` vs `rag-dev-password`). Pydantic keeps the later definition silently, and 13 fields in the earlier block were read by nothing. Removed the stale block, verified no duplicate field names remain and the suite is unchanged.
