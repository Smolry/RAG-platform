# Project 1 benchmark report (generated - do not edit by hand)

- timestamp: 2026-10-15T03:05:03.872449+00:00  - n_questions: 78  - eval_set_sha256: `7b433bc6d97c6fbe`
- LLM backend: **litellm**, small=`ollama/nemotron-3-nano:30b-cloud`, large=`ollama/gemma4:31b:cloud`, judge=`ollama/gemma4:31b:cloud`, embedder=`sentence-transformers` / `sentence-transformers/all-MiniLM-L6-v2`
- cache threshold=0.97 ttl=86400s

| arm | cost/req | cost vs always_large | p50 ms | p95 ms | p95 vs ref | cache hit | quality | quality Δ vs ref (95% CI) | retrieval hit |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| always_large | $0.00024 | ref | 835 | 1986 | ref | 0.0% | 0.786 | ref | 92.3% |
| routing_only | $0.00047 | +94.4% | 1375 | 6859 | +245.3% | 0.0% | 0.825 | +0.038 [+0.009, +0.081] | 92.3% |
| cache_only | $0.00012 | -50.6% | 255 | 1229 | -38.1% | 50.9% | 0.795 | +0.009 [+0.000, +0.021] | 92.3% |
| full | $0.00017 | -28.6% | 160 | 11428 | +475.3% | 52.6% | 0.808 | +0.021 [-0.021, +0.068] | 92.3% |

## Per-pass (cache hit rate / quality / cost per request)

| arm | pass | n | cache hit | quality | cost/req |
|---|---|--:|--:|--:|--:|
| always_large | original | 78 | 0.0% | 0.795 | $0.00024 |
| always_large | paraphrase | 78 | 0.0% | 0.769 | $0.00024 |
| always_large | repeat | 78 | 0.0% | 0.795 | $0.00024 |
| routing_only | original | 78 | 0.0% | 0.821 | $0.00024 |
| routing_only | paraphrase | 78 | 0.0% | 0.833 | $0.00095 |
| routing_only | repeat | 78 | 0.0% | 0.821 | $0.00023 |
| cache_only | original | 78 | 0.0% | 0.795 | $0.00025 |
| cache_only | paraphrase | 78 | 66.7% | 0.795 | $0.00008 |
| cache_only | repeat | 78 | 85.9% | 0.795 | $0.00003 |
| full | original | 78 | 0.0% | 0.808 | $0.00035 |
| full | paraphrase | 78 | 70.5% | 0.808 | $0.00013 |
| full | repeat | 78 | 87.2% | 0.808 | $0.00004 |

## Claims the data supports

- **routing_only**: cost +94.4%, p95 +245.3%; no quality drop SUPPORTED (CI lower bound above -margin) (margin 0.05, n=78 items); false cache hits judged wrong: 0; route split: {'simple': 0.7435897435897436, 'complex': 0.2564102564102564}; escalation rate of simple: 0.14367816091954022
- **cache_only**: cost -50.6%, p95 -38.1%; no quality drop SUPPORTED (CI lower bound above -margin) (margin 0.05, n=78 items); false cache hits judged wrong: 9; route split: None; escalation rate of simple: None
- **full**: cost -28.6%, p95 +475.3%; no quality drop SUPPORTED (CI lower bound above -margin) (margin 0.05, n=78 items); false cache hits judged wrong: 10; route split: {'simple': 0.7350427350427351, 'complex': 0.26495726495726496}; escalation rate of simple: 0.13953488372093023
