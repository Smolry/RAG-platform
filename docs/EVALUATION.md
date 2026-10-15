# Evaluation definitions (phases implemented so far)
| Metric | Formula | Measures | Limitation |
|---|---|---|---|
| Retrieval hit rate | mean over Qs of 1[gold source doc ∈ docs of retrieved top-k] | does retrieval find the right document | doc-level, not chunk-level; multi-source Qs need supporting-fact recall |
| Citation correctness | 1[gold doc ∈ docs of chunks the answer cites]| are citations honest | model may cite without using the passage |
| Answer correctness (LLM judge) | mean of judge 0/1 vs reference answer | end quality | judge is a proxy: bias, variance, same-family leniency. Model+prompt recorded in `*_summary.json` |
| p50 / p95 latency | linear-interpolated percentiles of end-to-end ms | typical / tail latency | depends on network + provider load |
| Cost / request | (in_tok·p_in + out_tok·p_out)/1e6 from `configs/models.yaml` | LLM spend | prices in the yaml are examples - verify |
| Cross-tenant leaks | count of returned chunk ids outside the *independent oracle's* allowed set (target 0) | isolation | proves absence only for the attacks written |
| Adversarial pass rate | queries with zero leaks / total queries | suite health | - |
| Filtering overhead | p50/p95 search ms with ACL filter − without (same vectors, k) | cost of security | local vs server Qdrant differ greatly |
| Revoke propagation | ms from `update_acl` call start until a query no longer returns the doc | how fast revocation bites | Qdrant `wait=True` => expected ~ update time; cache layers (Project 1) will extend this |

Every run writes config + eval-set hash so numbers are reproducible and auditable.
Metrics for caching/routing/GraphRAG (cache hit rate, routing accuracy, EM/F1, supporting-fact recall) are added with those phases.

## Project 1 metrics
| Metric | Formula | Measures | Limitation |
|---|---|---|---|
| Cache hit rate | hits / requests (per pass: original / paraphrase / repeat) | reuse | depends on workload mix; repeats inflate it — always report per pass |
| False cache hit | near-miss pair with cos ≥ τ (sweep) / hit whose answer judged wrong (benchmark) | wrong-answer risk | author-written pairs; judge is a proxy |
| False miss | paraphrase pair with cos < τ | wasted cost | — |
| Cost / request | mean logged `cost_usd` (cache hit = 0; escalation = small + large) | spend | price table is configurable *example* values |
| Cost reduction % | 1 − cost(arm)/cost(always_large) | savings | reference choice matters |
| p50 / p95 latency | percentile of end-to-end ms; **cache hits included** | typical / tail latency | p95 dominated by cold LLM calls when hit rate is low |
| Route split | share simple / complex among routed requests | how often SMALL is used | excludes cache hits |
| Routing accuracy | (TP+TN)/N vs author labels; report dev, test **and stress** | router quality | labels are subjective; E1 shows dev/test can be misleading |
| Escalation rate | escalated / simple-routed | small-model reliability | depends on `ESCALATION_MIN_TOP_SCORE` |
| Quality delta | paired mean of (arm − always_large) judged correctness per item, 95% bootstrap CI | quality cost of optimisation | noninferiority only if CI lower bound > −margin |
