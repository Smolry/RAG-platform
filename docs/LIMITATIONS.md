# Limitations (current state)
- **No real-stack numbers yet.** All 29 tests ran offline: in-process Qdrant, hash embedder, stub LLM. The "0 leaks" there shows the logic is
  right; it is NOT a result to quote. Re-run `make security` and `make measure-p2` against docker-compose Qdrant with the real embedder.
- Local (in-memory) Qdrant ignores payload indexes, so filtering-overhead numbers are only meaningful on a server Qdrant.
- Synthetic tenant data is tiny (24 docs, 15 users); leakage tests prove absence only for the attacks written.
- The test oracle re-implements the same access rule in plain Python; it is independent of Qdrant's filter but not of the *policy*.
- Dev tokens are minted by `scripts/issue_token.py`; there is no login/IdP, key rotation, or token revocation list.
- Audit-log append-only is enforced with DB triggers (SQLite verified by test; Postgres DDL path written but **not executed here**).
- LLM judge answer-correctness is a proxy. Chunk size uses a words-per-token approximation.
- `fetch_corpus.py` and `create_eval_set.py` were not executed in this environment (no outbound network / API key).
- Summaries / whole-doc embeddings / chat history are out of scope today (see DECISIONS.md).

## Project 1 limitations
- **Nothing in Project 1 has been run against a real embedder or LLM.** Threshold sweep (E3) and benchmark (E4) are PENDING; sentence-transformers / HF were unreachable in the build sandbox. All 78 tests use the hash embedder + scripted LLM and prove *logic*, not performance.
- **Router accuracy is overstated by my own dataset** (E1: ~98% dev/test vs 25% stress). Router labels are author-assigned; 100 + 12 queries.
- Cache-pair set (50+50) is author-written; a threshold fit to 100 pairs may not transfer.
- Per-tenant `corpus_version` is computed from the *batch* being ingested; ingesting a partial tenant batch would change the version (safe: over-invalidates) but there is no incremental-ingest model.
- Cache hits still pay one embedding + one live-ACL point lookup; at very high QPS the re-check is an extra Qdrant round-trip.
- Escalation signals are heuristics; a confidently wrong small-model answer *with* a citation is not escalated.
- Dashboard shows quality only for judged requests (benchmark/eval runs), not live traffic.
- Benchmark cost uses the example price table in `configs/models.yaml`.
- **No DB migrations.** Project 1 adds columns to `request_log` and a `corpus_state` table; `create_all` does not alter existing tables. If you already ran Phase 0-2 against Postgres/SQLite, drop `request_log` (or the DB) before running Project 1. Alembic is a reasonable next step.
