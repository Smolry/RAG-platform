# Project 1 benchmark report (generated - do not edit by hand)

- timestamp: 2026-10-19T16:07:21.451795+00:00  - n_questions: 60  - eval_set_sha256: `a2b831d2f12f26d6`
- LLM backend: **litellm**, small=`ollama/nemotron-3-nano:30b-cloud`, large=`ollama/gemma4:31b:cloud`, judge=`ollama/gemma4:31b:cloud`, embedder=`sentence-transformers` / `sentence-transformers/all-MiniLM-L6-v2`
- cache threshold=0.94 ttl=86400s

| arm | cost/req | cost vs always_large | p50 ms | p95 ms | p95 vs ref | cache hit | quality | quality Δ vs ref (95% CI) | retrieval hit |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| always_large | $0.00024 | ref | 895 | 1782 | ref | 0.0% | 0.706 | ref | 96.7% |
| routing_only | $0.00029 | +21.7% | 1723 | 12024 | +574.8% | 0.0% | 0.711 | +0.006 [-0.011, +0.022] | 96.7% |
| cache_only | $0.00011 | -52.9% | 63 | 1338 | -24.9% | 52.8% | 0.711 | +0.006 [+0.000, +0.017] | 96.7% |
| full | $0.00012 | -50.3% | 56 | 2414 | +35.5% | 54.4% | 0.733 | +0.028 [+0.000, +0.061] | 96.7% |

## Per-pass (cache hit rate / quality / cost per request)

| arm | pass | n | cache hit | quality | cost/req |
|---|---|--:|--:|--:|--:|
| always_large | original | 60 | 0.0% | 0.700 | $0.00024 |
| always_large | paraphrase | 60 | 0.0% | 0.717 | $0.00024 |
| always_large | repeat | 60 | 0.0% | 0.700 | $0.00024 |
| routing_only | original | 60 | 0.0% | 0.683 | $0.00035 |
| routing_only | paraphrase | 60 | 0.0% | 0.750 | $0.00028 |
| routing_only | repeat | 60 | 0.0% | 0.700 | $0.00025 |
| cache_only | original | 60 | 0.0% | 0.700 | $0.00024 |
| cache_only | paraphrase | 60 | 78.3% | 0.717 | $0.00005 |
| cache_only | repeat | 60 | 80.0% | 0.717 | $0.00005 |
| full | original | 60 | 0.0% | 0.700 | $0.00024 |
| full | paraphrase | 60 | 78.3% | 0.750 | $0.00007 |
| full | repeat | 60 | 85.0% | 0.750 | $0.00005 |

## Claims the data supports

- **routing_only**: cost +21.7%, p95 +574.8%; no quality drop SUPPORTED (CI lower bound above -margin) (margin 0.05, n=60 items); false cache hits judged wrong: 0; route split: {'simple': 0.7444444444444445, 'complex': 0.25555555555555554}; escalation rate of simple: 0.23880597014925373
- **cache_only**: cost -52.9%, p95 -24.9%; no quality drop SUPPORTED (CI lower bound above -margin) (margin 0.05, n=60 items); false cache hits judged wrong: 10; route split: None; escalation rate of simple: None
- **full**: cost -50.3%, p95 +35.5%; no quality drop SUPPORTED (CI lower bound above -margin) (margin 0.05, n=60 items); false cache hits judged wrong: 11; route split: {'simple': 0.7555555555555555, 'complex': 0.24444444444444444}; escalation rate of simple: 0.16176470588235295
