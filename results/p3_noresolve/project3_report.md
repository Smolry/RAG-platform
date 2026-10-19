# Project 3 report (generated - do not edit by hand)

- timestamp 2026-10-19T17:03:36.908481+00:00 | questions: 120 | questions_sha256 `bd2a9990a0ff9737`
- LLM backend **litellm** (generation `ollama/nemotron-3-nano:30b-cloud`, extraction `ollama/gemma4:31b:cloud`) | embedder `sentence-transformers` / `sentence-transformers/all-MiniLM-L6-v2` | reranker `cross-encoder` / `cross-encoder/ms-marco-MiniLM-L-6-v2` | graph `neo4j` | top_k=5 vector_k=10 hops=2

## multi_hop

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 120 | 41.7 | 51.3 | ref | 74.6 | 53.3 | n/a | $0.00012 | 4270 |
| hybrid | 120 | 43.3 | 55.6 | +4.3 [-2.3, +10.9] | 82.5 | 65.0 | 96.2 | $0.00016 | 8723 |

## bridge

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 96 | 40.6 | 50.2 | ref | 71.4 | 46.9 | n/a | $0.00012 | 4308 |
| hybrid | 96 | 39.6 | 52.0 | +1.8 [-5.6, +9.3] | 79.2 | 58.3 | 95.3 | $0.00015 | 8896 |

## comparison

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 24 | 45.8 | 55.4 | ref | 87.5 | 79.2 | n/a | $0.00012 | 3379 |
| hybrid | 24 | 58.3 | 69.7 | +14.4 [+1.9, +29.2] | 95.8 | 91.7 | 100.0 | $0.00016 | 8196 |

## all

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 120 | 41.7 | 51.3 | ref | 74.6 | 53.3 | n/a | $0.00012 | 4270 |
| hybrid | 120 | 43.3 | 55.6 | +4.3 [-2.3, +10.9] | 82.5 | 65.0 | 96.2 | $0.00016 | 8723 |

## Claims the data supports

- **hybrid / multi_hop**: ΔF1 +4.3 pts, ΔSF-recall +7.9 pts - F1 improvement NOT supported by this data (CI includes 0 or negative)

## Indexing cost (one-time, paid before the first query)

- 1188 chunks, 1188 extraction calls, 447943 in / 611453 out tokens, **$0.2482** ($0.00021/chunk), 29s wall, entities 8567 → 8567 after resolution, 8222 relations, rejections {'ungrounded_entity': 66, 'dangling_relation': 67, 'self_relation': 3}
