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

## Project 3 metrics
| Metric | Formula | Measures | Limitation |
|---|---|---|---|
| Exact Match (EM) | 1[normalise(pred) == normalise(gold)]; normalise = lowercase, strip punctuation + articles + whitespace; **citation markers stripped first** | exact answer correctness | harsh on correct-but-differently-phrased answers |
| F1 | token-overlap F1 after the same normalisation | partial answer credit | rewards verbose answers that contain the gold tokens (hence the short-answer prompt) |
| Supporting-fact recall | \|gold paragraphs ∩ paragraphs in final context\| / \|gold paragraphs\| | did retrieval deliver the evidence | **paragraph-level**, not HotpotQA's sentence-level SP metric |
| All-supporting-found | 1 iff every gold paragraph is in the final context | what a 2-hop question actually needs | stricter than recall; ignores how the LLM uses the context |
| Candidate recall | recall of gold paragraphs in the merged pool **before** rerank | recall of the retrieval stage | separates "not retrieved" from "retrieved but reranked out" |
| Indexing cost | sum of extraction-call cost (tokens × price table) over all chunks, incl. cached calls; plus fresh-only cost, wall time, rejections, entities before/after resolution, relations | one-time price of the graph | price table is example values; extraction model dependent |
| ΔF1 / ΔSF vs vector | mean of per-question paired differences, 95% percentile bootstrap CI | whether an arm beats the baseline | 120 questions ⇒ wide CIs; "SUPPORTED" requires the CI lower bound > 0 (improvement) or > −0.05 (no harm) |

## DevTrace retrieval metrics (Phase 3 definitions)
Implemented in `app/evaluation/devtrace/retrieval_metrics.py`; hand-computed cases in `tests/unit/test_devtrace_retrieval_metrics.py`.
- **Unit of relevance:** source page/document id, with chunks collapsed to their first-seen document (duplicates cannot inflate scores).
- **Recall@k** = relevant found in top k / all relevant. **Precision@k** = relevant in top k / k (divides by k even if fewer results returned).
  **Hit@k**, **MRR** (rank among distinct documents), **source coverage**, **all-required-found@k** for multi-source questions.
- **Not applicable ≠ zero:** cases with no relevant labels (e.g. must-abstain) return `None` and are excluded from means; the report states n per metric.
- **Version correctness:** share of version-bearing evidence inside the requested range and library; documentation pages are not judged.
- No results exist yet. Do not quote any figure from this project until a saved run artifact (Phase 4+) backs it.
