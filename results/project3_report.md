# Project 3 report (generated - do not edit by hand)

- timestamp 2026-10-18T17:35:08.941731+00:00 | questions: 180 | questions_sha256 `78895dab5a099a6e`
- LLM backend **litellm** (generation `ollama/nemotron-3-nano:30b-cloud`, extraction `ollama/gemma4:31b:cloud`) | embedder `sentence-transformers` / `sentence-transformers/all-MiniLM-L6-v2` | reranker `cross-encoder` / `cross-encoder/ms-marco-MiniLM-L-6-v2` | graph `neo4j` | top_k=5 vector_k=10 hops=2

## multi_hop

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 120 | 39.2 | 47.0 | ref | 74.6 | 53.3 | n/a | $0.00048 | 26087 |
| vector_rerank | 120 | 43.3 | 54.7 | +7.8 [+1.5, +14.4] | 81.7 | 63.3 | 87.1 | $0.00018 | 12937 |
| hybrid | 120 | 44.2 | 57.0 | +10.0 [+3.3, +17.0] | 82.5 | 65.0 | 97.1 | $0.00016 | 7890 |
| hybrid_ngram | 120 | 42.5 | 53.8 | +6.8 [+0.6, +13.4] | 82.5 | 65.0 | 95.4 | $0.00016 | 7225 |

## bridge

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 96 | 36.5 | 43.8 | ref | 71.4 | 46.9 | n/a | $0.00050 | 27557 |
| vector_rerank | 96 | 39.6 | 51.0 | +7.2 [-0.3, +14.6] | 78.1 | 56.2 | 84.4 | $0.00017 | 12373 |
| hybrid | 96 | 39.6 | 52.8 | +9.0 [+1.7, +16.2] | 79.2 | 58.3 | 96.4 | $0.00016 | 8335 |
| hybrid_ngram | 96 | 39.6 | 50.8 | +7.0 [-0.1, +14.0] | 79.2 | 58.3 | 94.3 | $0.00016 | 7437 |

## comparison

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 24 | 50.0 | 59.5 | ref | 87.5 | 79.2 | n/a | $0.00038 | 18606 |
| vector_rerank | 24 | 58.3 | 69.7 | +10.2 [+0.0, +22.7] | 95.8 | 91.7 | 97.9 | $0.00019 | 12737 |
| hybrid | 24 | 62.5 | 73.9 | +14.4 [-2.3, +32.9] | 95.8 | 91.7 | 100.0 | $0.00015 | 6537 |
| hybrid_ngram | 24 | 54.2 | 65.6 | +6.0 [-8.3, +20.8] | 95.8 | 91.7 | 100.0 | $0.00015 | 7044 |

## single

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 60 | 88.3 | 94.8 | ref | 100.0 | 100.0 | n/a | $0.00024 | 6596 |
| vector_rerank | 60 | 95.0 | 98.3 | +3.6 [+0.6, +7.7] | 100.0 | 100.0 | 100.0 | $0.00008 | 2450 |
| hybrid | 60 | 96.7 | 99.4 | +4.7 [+0.9, +9.3] | 100.0 | 100.0 | 100.0 | $0.00014 | 7948 |
| hybrid_ngram | 60 | 96.7 | 99.2 | +4.4 [+0.9, +9.1] | 100.0 | 100.0 | 100.0 | $0.00011 | 5460 |

## all

| arm | n | EM | F1 | ΔF1 vs vector [95% CI] | SF recall | all-found | cand. recall | $/query | p95 ms |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| vector | 180 | 55.6 | 62.9 | ref | 83.1 | 68.9 | n/a | $0.00040 | 22118 |
| vector_rerank | 180 | 60.6 | 69.3 | +6.4 [+2.1, +11.0] | 87.8 | 75.6 | 91.4 | $0.00014 | 10118 |
| hybrid | 180 | 61.7 | 71.1 | +8.2 [+3.5, +13.2] | 88.3 | 76.7 | 98.1 | $0.00015 | 7890 |
| hybrid_ngram | 180 | 60.6 | 68.9 | +6.0 [+1.6, +10.6] | 88.3 | 76.7 | 96.9 | $0.00014 | 6617 |

## Claims the data supports

- **vector_rerank / multi_hop**: ΔF1 +7.8 pts, ΔSF-recall +7.1 pts - F1 improvement SUPPORTED (CI lower bound > 0)
- **vector_rerank / single**: ΔF1 +3.6 pts, ΔSF-recall +0.0 pts - no F1 harm SUPPORTED (CI lower bound > -0.05)
- **hybrid / multi_hop**: ΔF1 +10.0 pts, ΔSF-recall +7.9 pts - F1 improvement SUPPORTED (CI lower bound > 0)
- **hybrid / single**: ΔF1 +4.7 pts, ΔSF-recall +0.0 pts - no F1 harm SUPPORTED (CI lower bound > -0.05)
- **hybrid_ngram / multi_hop**: ΔF1 +6.8 pts, ΔSF-recall +7.9 pts - F1 improvement SUPPORTED (CI lower bound > 0)
- **hybrid_ngram / single**: ΔF1 +4.4 pts, ΔSF-recall +0.0 pts - no F1 harm SUPPORTED (CI lower bound > -0.05)

## Indexing cost (one-time, paid before the first query)

- 1188 chunks, 1188 extraction calls, 447943 in / 611453 out tokens, **$0.2482** ($0.00021/chunk), 2883s wall, entities 8567 → 8400 after resolution, 8156 relations, rejections {'ungrounded_entity': 66, 'dangling_relation': 67, 'self_relation': 3}
