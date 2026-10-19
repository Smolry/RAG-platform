# RAG Platform

A measured progression from baseline retrieval-augmented generation to multi-tenant authorization, cost-aware inference, and hybrid GraphRAG. The same FastAPI service is extended in layers and evaluated with saved result artifacts.

**Stack:** FastAPI, Qdrant, sentence-transformers, LiteLLM, PostgreSQL/SQLite, Streamlit, and Neo4j.

> **Result status:** The current `results/` artifacts include non-stub runs using LiteLLM, the sentence-transformers embedder, and Neo4j where applicable. Figures and analysis below are snapshots of saved files, not live dashboards. Costs use the project model price table and are not provider invoices. Evaluation sets are modest; see the caveats and future work before generalizing.

## Project progression

1. **Baseline RAG** — chunk and embed documents, retrieve top-k passages, generate cited answers, and log request metrics.
2. **Project 2: tenant security** — JWT principals, tenant/role/user ACLs, Qdrant pre-filtering, a second application-level permission check, audit logging, revocation, and adversarial leakage evaluation.
3. **Project 1: cache and routing** — permission-aware semantic cache with live ACL re-checks, small/large model routing with escalation, benchmark arms, and a Streamlit dashboard.
4. **Project 3: GraphRAG** — schema-constrained entity/relation extraction, precision-first entity resolution, tenant-scoped Neo4j graph, vector-plus-graph retrieval, cross-encoder reranking, and multi-hop evaluation.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/project1/README.md](docs/project1/README.md), and [docs/project3/README.md](docs/project3/README.md) for design and implementation detail.

## Results at a glance

These are measurements from the current saved outputs. Baseline and Project 2 used the same 60-question evaluation fingerprint. Project 1 is a four-arm cache/router benchmark; Project 3 compares four retrieval arms on 180 HotpotQA questions.

| Stage | Evaluation | Main result | Important qualification |
|---|---|---|---|
| Baseline | 60 questions | 96.7% retrieval hit, 70.0% LLM-judged answer correctness, 12.10 s p95, $0.000322/request | Citation correctness was 73.3%; judge score is a proxy. |
| Project 2 | Same 60 questions; separate 840-query security suite | 0 leaks in 840 queries; 100% adversarial pass; 96.7% retrieval hit | One run; the separate 200-query filtered-search timing showed noisy/negative measured overhead. |
| Project 1 | 60 questions, original/paraphrase/repeat passes | Full arm: $0.00012/request (50.3% below always-large), 54.4% cache hits, quality 0.733 vs 0.706 | Full-arm p95 was 35.5% higher than always-large; cache-only had better p95. Router stress accuracy was only 25%. |
| Project 3 | 180 questions: 120 multi-hop + 60 single-hop | Hybrid F1 71.1% vs 62.9% vector; multi-hop delta +10.0 points (95% CI +3.3 to +17.0) | One benchmark/configuration; graph indexing cost was $0.2482 for 1,188 chunks. |

### Phase 0 — Baseline

![Baseline RAG results: retrieval hit rate, judged answer quality, latency, and cost](docs/results/phase0-baseline.png)

The baseline retrieved a supporting document for 96.7% of questions, but judged answer correctness was 70.0% and citation correctness was 73.3%. This gives later phases a useful retrieval and quality reference point.

### Project 2 — Tenant security

![Project 2 results: security tests, revocation latency, and comparison with baseline](docs/results/project2-security.png)

The security suite returned 4,200 authorized chunks across 840 queries with zero leaks and a 100% adversarial pass rate. Across 20 revocation trials, permission changes became effective in a median 39.8 ms (maximum 61.3 ms). These tests are evidence for the implemented cases, not proof against every possible attack.

On the shared 60-question evaluation, measured retrieval hit rate stayed at 96.7%, judged answer correctness changed from 70% to 75%, p95 latency from 12.10 s to 2.82 s, and mean cost from $0.000322 to $0.000181. Treat these as observations, not causal improvements from ACL enforcement: model calls and runtime conditions can vary between runs. The measured p95 ACL-search time was lower than the no-filter time; that is likely measurement noise, not evidence that filtering makes search faster.

### Project 1 — Cache and routing

![Project 1 benchmark results: cost, p95 latency, cache hit rate, and quality by arm](docs/results/project1-benchmark.png)

The cache delivered the clearest operational gain. Cache-only reduced cost by 52.9% and p95 latency by 24.9% against always-large, with judged quality 0.711 versus 0.706. The full arm reduced cost by 50.3% and raised judged quality to 0.733 (paired 95% CI for the quality delta: +0.000 to +0.061), but its p95 latency was 35.5% higher than the always-large reference. Routing-only increased both cost (+21.7%) and p95 latency (+574.8%) in this workload. The full arm also had 11 false cache hits judged wrong, which is why the cost/quality result does not establish cache safety.

The router's author-written dev/test scores look strong (98.3% / 97.5%), but the 12-query stress set reached only 25.0% accuracy and missed all six complex stress questions. Separately, the 50-paraphrase/50-near-miss cache sweep found **no threshold with zero false hits**: the selected threshold had 3 false hits out of 50 near-misses (6%) and missed 22 of 50 paraphrases. The benchmark used threshold 0.94, so do not interpret the sweep's selected 0.92 as a safe threshold.

### Project 3 — GraphRAG

![Project 3 results: all-question F1 by retrieval arm with multi-hop and indexing metrics](docs/results/project3-graphrag.png)

On 180 validated questions, hybrid GraphRAG achieved 71.1% overall token F1 versus 62.9% for vector-only. The paired 95% confidence interval for the overall improvement was +3.5 to +13.2 points. On the 120 multi-hop questions, the gain was +10.0 points (95% CI +3.3 to +17.0); supporting-fact paragraph recall rose from 74.6% to 82.5%. On the 60 single-hop questions, hybrid F1 was 99.4% versus 94.8% for vector-only, with no observed single-hop harm in this sample.

Hybrid p95 latency was 7.89 s versus 22.12 s for vector-only in this run, and estimated query cost was $0.00015 versus $0.00040. The graph build processed 1,188 chunks, made 1,188 extraction calls, produced 8,156 relations, and reported $0.2482 one-time indexing cost. The original evaluation report records 2,883 seconds for indexing; a later cached build report records about 32 seconds with zero fresh extraction calls. Costs are based on the configured price table.

The separate entity-resolution ablation found 57.0% hybrid multi-hop F1 with resolution and 55.6% without on 120 matching questions: an exploratory paired delta of +1.4 points (95% bootstrap CI -0.9 to +4.1). Supporting-fact recall was 82.5% in both. The vector-only baseline shifted by 4.3 points between those two runs, so the small resolution effect is inconclusive and should be repeated with controlled generation.

## Analysis and conclusions

- **Retrieval coverage was already high at baseline.** Subsequent gains are more visible in answer quality, citations, authorization guarantees, and evidence recall than in top-level retrieval hit rate.
- **Security has encouraging measured behavior, but narrow evidence.** Zero observed leaks in the defined suite and fast revocation are positive. Expand tenants, document ACL combinations, attack patterns, and independent test oracles before treating this as a broad security guarantee.
- **Caching is the strongest Project 1 result, with a real safety tradeoff.** The observed savings are material, but the near-miss sweep still produced false hits. Keep the live ACL re-check and strict permission-aware cache key; improve cache validation before relaxing the similarity threshold.
- **The rule router is not ready to be trusted on arbitrary traffic.** Its stress-set misses show that keyword/length heuristics can fail on short complex questions. Escalation helps, but should not substitute for representative evaluation.
- **Project 3 improves answer and supporting-fact metrics in the evaluated setup.** The rerank-only arm also improved over vector-only, so some of the observed gain comes from reranking; the hybrid arm additionally raised candidate recall to 98.1%. Larger, independently reviewed evaluations are needed before assuming the same gain on another corpus.
- **Entity resolution's contribution remains uncertain.** The ablation point estimate favored resolution, but its exploratory interval crossed zero and supporting-fact recall was unchanged.
- **Latency and cost are workload/configuration specific.** P1 routing-only and full-arm p95 results demonstrate that average cost savings do not guarantee lower tail latency. Re-run with controlled concurrency, warm-up, fixed model versions, and repeated trials.

## Future improvements

1. **Strengthen evaluation quality.** Expand and independently review benchmark sets; freeze a held-out test split; include production-like queries, short complex prompts, multilingual and adversarial cases; report repeated-run confidence intervals.
2. **Make cache use demonstrably safe.** Enlarge and diversify near-miss pairs, measure false-hit impact, test threshold stability by query type, and consider answer-aware validation or disabling semantic cache on high-risk classes.
3. **Replace or constrain the heuristic router.** Train/calibrate against labeled traffic or use conservative confidence-based routing; preserve a safe fallback to the large model and measure escalation quality, not only classifier accuracy.
4. **Improve operational measurements.** Run controlled load tests with fixed concurrency and warm/cold cache profiles; report p50/p95/p99, throughput, provider-reported billing, and uncertainty.
5. **Harden data and schema lifecycle.** Add database migrations, incremental graph rebuilds, graph/vector consistency checks, retries/backoff for provider rate limits, and explicit run provenance.
6. **Expand GraphRAG validation.** Run the Neo4j contract suite, test ACL revocation on graph evidence, compare resolved/no-resolution builds, and evaluate on larger non-Wikipedia corpora with human-checked evidence.
7. **Add production controls.** Use a managed identity provider, JWT rotation/revocation, secret management, retention policies, observability alerts, and deployment guidance.

## Quick start

### Prerequisites

- Python 3.10 or newer.
- Docker Desktop (Qdrant and PostgreSQL; Neo4j for Project 3).
- GNU Make (`make`) on PATH.
- A LiteLLM-supported model/provider configuration. The evaluation snapshots used the model names recorded in the result JSON; configure provider credentials locally.

### Windows PowerShell

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env: set a strong JWT_SECRET and configure the desired provider/model credentials.
make up
```

If PowerShell blocks activation, use the virtual-environment executable directly, for example `.\.venv\Scripts\python.exe -m pytest -q`.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env: set a strong JWT_SECRET and configure the desired provider/model credentials.
make up
```

`make up` starts Qdrant and PostgreSQL. Neo4j is started separately for Project 3.

## Run the phases

### Baseline

Place `.txt` or `.md` files in `data/raw/`, or try the corpus fetcher (network access may be restricted):

```powershell
python -m scripts.fetch_corpus
make ingest-baseline
python -m scripts.create_eval_set --path data/raw --n 80 --doc-prefix acme
python -m scripts.create_eval_set --review data/eval/eval_set.jsonl
make eval-baseline
# For unauthenticated baseline API mode, set SYSTEM=baseline in .env first.
make api
```

The API docs are at `http://localhost:8000/docs`. The baseline evaluation writes CSV and JSON summaries under `results/`.

### Project 2 — security

```powershell
make tenants
make ingest-tenants
make security
make measure-p2
python -m scripts.run_evaluation --system project2 --tenant acme --user eval@acme --roles employee
```

`make security` runs the security tests and adversarial leakage suite. `make measure-p2` records ACL search and revocation measurements.

To mint a development token and submit a query in PowerShell while the API runs in Project 2 mode:

```powershell
$token = (& .\.venv\Scripts\python.exe -m scripts.issue_token --user hr@helios).Trim()
$body = @{ query = "What is the salary band for engineers?" } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/query" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" -Body $body
```

Admin endpoints include `GET /admin/audit/documents/{doc_id}?days=30` and `PUT /admin/documents/{doc_id}/acl`.

### Project 1 — cache, routing, dashboard

```powershell
make tune-cache
make eval-router
make paraphrases
python -m scripts.run_benchmark --tenant acme --user eval@acme --roles employee --only-validated
make p1
```

Open a second terminal and run `make dashboard` to view logged request metrics. The dashboard reads `request_log`; benchmark rows include quality only when judged.

### Project 3 — GraphRAG

```powershell
make neo4j-up
make test-neo4j
make hotpot
make ingest-hotpot
make build-graph
make singlehop
```

`make singlehop` generates draft single-hop questions in `data/hotpot/questions_single.jsonl`. Review each question and its answer against its source document; set `validated` to `true` only for verified rows. Then run:

```powershell
make p3-eval
```

The evaluation writes `project3_report.md`, `project3_summary.json`, and `project3.csv` under `results/`. Review `graph_resolution_audit.json` after graph construction. The graph is shared per tenant: `make p3-ablate-resolution` rebuilds it without resolution and then restores the resolved graph, so do not run that target concurrently with other graph operations.

## Tests and result artifacts

```powershell
python -m pytest -q
make test-neo4j
```

`make test-neo4j` requires a running Neo4j instance. Offline tests use test doubles/in-memory stores for selected external services; they validate application behavior, not provider performance or every live database behavior.

Saved reports and raw summaries are in [`results/`](results/). The four README figures in [`docs/results/`](docs/results/) are generated from those summaries. Re-run the relevant evaluation scripts to update the raw outputs, then regenerate figures if the underlying results change.

## Repository map

| Path | Purpose |
|---|---|
| `app/api/` | FastAPI endpoints and request handling |
| `app/core/` | Settings, models, chunking |
| `app/retrieval/`, `app/generation/` | Vector retrieval, embeddings, LLM interface |
| `app/security/`, `app/observability/` | JWT/ACL enforcement, audit and request logs |
| `app/caching/`, `app/routing/` | Project 1 cache and rule router |
| `app/graph/` | Project 3 extraction, graph store, entity resolution, hybrid retrieval and reranking |
| `app/evaluation/`, `scripts/` | Evaluation harnesses and runnable workflows |
| `dashboard/` | Streamlit dashboard |
| `tests/` | Unit, integration, security and evaluation tests |
| `docs/` | Architecture, phase guides, experiments and result figures |

## Troubleshooting

### PostgreSQL password or port mismatch

Compose publishes PostgreSQL on host port 5433. If ingest reports password authentication failure, first check the database inside the container:

```powershell
docker compose exec postgres psql -U rag -d rag -c "SELECT current_user, current_database(), inet_server_addr();"
docker compose port postgres 5432
```

The `rag` role password is initialized only when the `pg_data` volume is first created. If the volume predates this configuration, the existing password may differ. A process-level `DATABASE_URL` overrides `.env`; to test the published IPv4 port for one PowerShell session:

```powershell
$env:DATABASE_URL = "postgresql://rag:rag@127.0.0.1:5433/rag"
make ingest-baseline
```

If you can connect locally as `rag` but the in-container password is stale, reset it without deleting database data:

```powershell
docker compose exec postgres psql -U rag -d rag -c "ALTER ROLE rag WITH PASSWORD 'rag';"
```

## Further reading

- [Architecture](docs/ARCHITECTURE.md)
- [Experiments and methodology](docs/EXPERIMENTS.md)
- [Project 1 design](docs/project1/README.md)
- [Project 3 design](docs/project3/README.md)
- [Limitations](docs/LIMITATIONS.md)
- [Design decisions](docs/DECISIONS.md)
