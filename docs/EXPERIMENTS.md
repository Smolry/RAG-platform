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

## E3 ⏳ Cache threshold sweep (0.85 → 0.97)
- **Hypothesis:** a threshold exists in [0.85, 0.97] with **zero false hits** on near-miss pairs while still hitting most paraphrases.
- **Dataset:** `data/eval/cache_pairs.jsonl` — 50 paraphrase pairs (SHOULD hit) + 50 near-miss pairs (MUST NOT hit: swapped entity, negation, different qualifier/number). Author-written; **review before trusting**.
- **Command:** `python -m scripts.tune_cache_threshold` with the real embedder → `results/cache_threshold_sweep.{csv,json}`.
- **Selection rule (PDF):** lowest threshold with zero false hits; if none, flag UNSAFE and keep 0.97.
- **Result:** **TBD — not run with a real embedder** (sentence-transformers/HF unreachable in the build sandbox). ⚠️ Offline smoke with the *hash* embedder only verified plumbing (it reported no safe threshold, as expected for a non-semantic embedder) and is **not** a result.
- **Expected failure mode to look for:** near-misses like "Redis port" vs "MongoDB port" are lexically almost identical; MiniLM-class models may score them >0.9, in which case the rule will force a high threshold and a low hit rate. That outcome is still a valid, reportable finding.

## E4 ⏳ Project 1 benchmark (cache + routing vs always-large)
- **Hypothesis:** cache + routing cut cost/p95 latency with no quality drop.
- **Arms:** `always_large`, `routing_only`, `cache_only`, `full` (`app/evaluation/benchmark.py`). Workload: originals → paraphrases → repeats.
- **Metrics:** cost/request, p50/p95, cache hit rate, route split, escalation rate, judged quality, **paired bootstrap 95% CI of the quality delta**, false cache hits judged wrong.
- **Claim rule:** "no quality drop" is printed **only if the CI lower bound > −0.05** (configurable `--margin`); otherwise the report says NOT supported. A 50-item eval set often cannot establish non-inferiority — that is a legitimate result.
- **Command:** `python -m scripts.run_benchmark --tenant acme --user eval@acme --roles employee --only-validated` → `results/project1_{summary.json,report.md,csv}`.
- **Result:** **TBD — requires a real LLM, validated eval set, real embedder.** The offline run (stub LLM) exercises the plumbing only and the report self-labels "STUB/HASH RUN".
