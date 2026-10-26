# Security notes (DevTrace branch)

Scope: what is implemented **today** (Phase 1), what was checked, and what is still open. Nothing here is a guarantee of
security; it is a record of controls and the evidence behind them. See also `docs/LIMITATIONS.md` (earlier projects).

## Controls implemented in Phase 1

| ID | Risk | Control | Evidence |
|---|---|---|---|
| S1 | Server accepted tokens forgeable by anyone (public default `JWT_SECRET`) | Startup refuses empty / <32-byte / placeholder secret unless `ENVIRONMENT=dev` (dev tolerates the placeholder with a warning, never an empty secret). `ENVIRONMENT` defaults to `production`; unknown values count as production. Algorithm restricted to HS256/384/512 | `tests/security/test_config_hardening.py`; negative control: re-opening the hole made 7 tests fail |
| S2 | `SYSTEM=baseline` disabled auth + ACLs on `/query` | API starts in that mode only with `ENVIRONMENT=dev` **and** `ALLOW_UNAUTHENTICATED=true`; the auth dependency re-checks per request and returns 503 | same file |
| S3 | Credentials in source/compose (Neo4j, Postgres) | No default Neo4j password in `Settings`; compose reads passwords from `.env`; Postgres/Neo4j are opt-in profiles; all ports bind `127.0.0.1` | `docker compose config` parses (containers not started) |
| S6 | Unbounded provider calls | `LiteLLMClient` always passes timeout, max output tokens, retries; invalid values rejected | unit tests with a fake `litellm` |
| S7 | API surface and error detail | `/docs`, `/redoc`, `/openapi.json` off unless `ENABLE_DOCS=true`; 401 returns a fixed message (reason only in server log); `/health` is liveness-only; `/ready` returns 503 with no detail | tests assert no exception text/host/credential in the body |
| S8 | `.gitignore` gaps | Added `.env.*` (except `.env.example`), key/cert files, credential JSONs, local venvs and Qdrant storage. Existing entries preserved | `git check-ignore` |
| S12 (found during Phase 1) | Anonymous requests could force the whole RAG service (embedding model, Qdrant, DB) to be constructed before the token was checked, and a plain 401 depended on infrastructure being up. Pre-existing: reproduced on the original code | Authentication now runs before, and independently of, service construction | `test_anonymous_request_gets_401_without_constructing_the_service`; real `uvicorn` returns 401 |
| S10 (part) | No secret scanning | gitleaks via pre-commit and CI (full history); `.gitleaks.toml` | see below |

Added tooling: `python -m scripts.check_config` reports PRESENT/missing for each secret-bearing variable and never prints a value.

## Secret scan results (Phase 1)

- **Tool/version:** gitleaks 8.21.2 with default rules, run with `--redact`.
- **Scope:** all 6 commits reachable from `main` (the whole history at the time) and the working tree including the Phase 1 changes.
- **Raw result:** 1,186 findings, **all** rule `generic-api-key`, **all** in `data/processed/extraction_cache.jsonl`.
- **Triage:** each record has fields `key`, `raw`, `usage`; all 1,188 `key` values are exactly 64 hexadecimal characters
  (SHA-256 content hashes used as cache keys). They are not credentials.
- **Action:** a path-scoped allowlist for that one file in `.gitleaks.toml`. A path allowlist skips **all** rules for that file, so a
  real secret written into the extraction cache would not be caught. Accepted because the file is generated LLM output over
  public Wikipedia text; do not widen the list.
- **Result with the allowlist:** no leaks in history or working tree. A planted real-looking key in another location was still
  detected (negative control).
- **Not covered:** this does not prove the absence of secrets in any fork, local clone, or deleted branch that I cannot see.

## Controls added in Phase 2 (corpus ingestion)

| Risk | Control | Evidence |
|---|---|---|
| SSRF / fetching arbitrary URLs | Only `app/devtrace/corpus/safe_fetch.py` fetches; https, exact-host allowlist, port 443, no userinfo, every redirect re-validated (max 3), non-public IPs refused, user input never reaches it (source list is committed config pinned to commit SHAs) | 54 test cases in `tests/unit/test_corpus_fetch.py` using a mock transport |
| Resource exhaustion | Hard byte cap enforced while streaming (a false `Content-Length` does not help), connect/read timeouts, sequential requests with a minimum interval | tests |
| Hostile content | Text content types and strict UTF-8 only; downloaded text is data: never executed, rendered or used to choose URLs; prompt-injection text inside documents is stored verbatim (handled at generation/validation time, Phase 5) | tests |
| Secret/detail leakage | Errors carry status code or exception *type* only, never bodies, headers or URLs from exceptions | tests |
| Source tampering / drift | Commit-SHA pins (tags rejected), per-file SHA-256 in the manifest, cache integrity check on read, `--verify` | tests |
| Tenant spoofing via data files | `tenant_id`, `allowed_*`, `classification`, `corpus_version`, `chunk_id` in a chunk file are ignored; the ingest call supplies them | `test_acl_comes_from_the_call_not_from_chunk_data` (found a real bug first) |
| Cross-tenant overwrite | Qdrant point ids scoped by tenant | `test_same_public_corpus_in_two_tenants_does_not_collide` (found a real bug first) |
| Dependency drift / advisories | `requirements-test.lock` (verified: fresh install, full suite green, `pip-audit` clean). This cleared the 3 `filelock` advisories from Phase 1 by moving to filelock 4.2.4 | audit output |

**Residual risk (SSRF):** the IP check and the TCP connect are separate lookups (DNS rebinding). For an allowlisted HTTPS host the
rebinding target would also need a valid certificate for that hostname, which it cannot obtain. Accepted; no proxy-pinning.

## Open items (not yet fixed)

| ID | Item | Planned |
|---|---|---|
| S4 | Raw queries/answers stored in plaintext in `request_log`/`audit_log`. DevTrace inputs will contain stack traces | Phase 6: redaction before persistence |
| S5 | Semantic cache has no demonstrated safe threshold (repo's own sweep: 3/50 false hits at best) | DevTrace paths will not use the cache (Phase 3+) |
| S9 | Prompt-injection tests cover forbidden chunks, not malicious *authorized* documents | Phase 5 |
| S10 (rest, partly done) | **Phase 2:** `requirements-test.lock` pins the offline-test environment and clears the advisories below. Still open: CI and the full `requirements.txt` (torch etc.) are unpinned. _Historical finding:_  `pip-audit` on the 79 packages installed for the tests reported 3 advisories (CVE ids `CVE-2026-112907`, `-112913`, `-112940`) on `filelock 3.32.7`, pulled in by `litellm` and `huggingface-hub`; the reported fix version is a major bump (4.2.1). I have not assessed exploitability. CI runs `pip-audit` informationally | Phase 2: lock file, re-audit, decide |
| - | CI changes (`secret-scan`, `dependency-audit` jobs) and the pre-commit hook have **not been executed**; only the YAML was parsed and the scanner was run locally | Confirmed on first push |
| - | No rate limiting or request-size policy for the future `/investigate` endpoint | Phase 6 |
| - | Qdrant has no authentication locally (localhost-bound only). `make_client` cannot yet pass an API key for Qdrant Cloud | Phase 8 if deployed |

## Rules for contributors (including me)

1. No credential in source, tests, docs, Docker images, URLs, command lines or chat. Tests use deliberately invalid values.
2. `.env` stays local. `.env.example` holds names and empty placeholders only.
3. Inspect `git diff --staged` before every commit; the pre-commit hook and CI are a second line, not the first.
4. A found secret means: revoke at the provider, then clean up. Never rewrite history without explicit approval.
