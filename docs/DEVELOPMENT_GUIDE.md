# DevTrace development guide (Windows / PowerShell first)

> **Status: Phase 2 of 8 done (corpus fetched and snapshot `976aea99e8507ef2` built; embedding/ingest not yet run on your machine).** This guide documents only what exists today. Sections marked **Planned** are placeholders
> that each later phase fills in. Commands were verified on Linux (Python 3.11); the Windows/PowerShell variants are
> written from the same steps but **have not been run on Windows by me**, so tell me about any that fail.

Run every command from the repository root (the folder that contains `app/`, `tests/` and `Makefile`) unless a step
says otherwise. PowerShell is assumed; differences for Git Bash/Linux are labelled.

---

## 1. What is in this repository

| Path | What it is |
|---|---|
| `app/api/main.py` | FastAPI app: `/health`, `/ready`, `/query`, admin routes. Starts only with a safe configuration |
| `app/core/config.py` | All settings (from `.env`) **and** the startup safety rules |
| `app/security/` | JWT verification and the two-layer ACL (Qdrant pre-filter + application check) |
| `app/services/rag.py` | The RAG pipeline used by the baseline (Arm A) |
| `app/generation/llm.py` | LLM client (LiteLLM) with timeout / token / retry bounds |
| `scripts/` | Runnable tools, including `check_config.py` (safe configuration check) |
| `tests/` | `unit/`, `integration/`, `security/`, `evaluation/` |
| `docs/` | This guide, `SECURITY.md`, plus the earlier project docs |
| `devtrace` (Git branch) | All DevTrace work. `main` is untouched until you merge |

DevTrace itself (`app/devtrace/`) does not exist yet; it arrives in Phases 2-6.

---

## 2. Install Python 3.11

**Why 3.11:** CI uses it and the test suite was verified on it. The project declares 3.10+.

```powershell
py --list            # shows installed versions; look for -3.11
```
If 3.11 is missing: install from python.org, or `winget install Python.Python.3.11` (check the id with `winget search python 3.11`).

- **Contacts an external service:** only the installer download. **Cost:** none.
- **Success:** `py -3.11 --version` prints `Python 3.11.x`.

## 3. Create a virtual environment and install dependencies

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```
If PowerShell says scripts are disabled, either run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or skip
activation and call the interpreter directly, e.g. `.\.venv\Scripts\python.exe -m pytest -q`.

Choose **one**:

**A. Lightweight (enough to run all current tests, no torch/model download, recommended on 8 GB RAM):**
```powershell
Get-Content requirements.txt | Where-Object { $_ -notmatch '^(sentence-transformers|datasets|streamlit)' } | Set-Content req-min.txt
python -m pip install -r req-min.txt -c requirements-test.lock   # -c pins the tested versions
```
**B. Full (needed later to run real embeddings, the reranker and the dashboard; several GB of disk):**
```powershell
python -m pip install -r requirements.txt
```
Git Bash / Linux for A: `grep -vE '^(sentence-transformers|datasets|streamlit)' requirements.txt > req-min.txt`

- **Contacts:** PyPI (and, for B, torch wheels). **Cost:** none. **Security:** installs third-party code; dependencies are
  not yet pinned (planned Phase 2) and `pip-audit` currently reports 3 advisories on a transitive package, see `docs/SECURITY.md`.
- **Success:** `python -m pip check` prints `No broken requirements found.`
- **Delete `req-min.txt` when done** (it is a local helper, not part of the project).

## 4. Configure environment variables safely

**Secrets involved:** `JWT_SECRET` (signs login tokens), your LLM provider key, and optionally `NEO4J_PASSWORD` /
`POSTGRES_PASSWORD`. **They may only live in `.env`** (git-ignored) or in real environment variables. They must never
appear in source, chat messages, screenshots, issues, logs, or command lines.

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```
The second command prints a random string. Open `.env` in an editor and paste it after `JWT_SECRET=`. (Do not paste it
anywhere else. If you ever do, generate a new one.) Leave `ENVIRONMENT=dev` for local work.

Verify without revealing anything:
```powershell
python -m scripts.check_config
```
- **What it does:** lists each secret-bearing variable as `PRESENT` or `missing` (never the value) and runs the same
  startup safety rules the API enforces.
- **Success:** last line `RESULT: configuration passes the startup safety checks.` (exit code 0).
- **Failure:** `RESULT: API would refuse to start.` with the rule that failed (exit code 1).
- It is safe to paste this command's output when asking for help: it contains names and statuses only.

**LLM provider key:** not needed for the current test suite (tests use scripted fakes). When we reach Phase 4 I will
verify which free providers and quotas exist *at that time* and give exact steps for obtaining and storing a key.
Do not obtain or paste one before then.

## 5. Run the tests

```powershell
python -m pytest -q
```
- **What it does:** runs ~200 offline tests with in-memory Qdrant/SQLite and fake LLMs. **No network, no API key, no cost.**
- **Success (as of Phase 2, verified on Linux):** `288 passed, 12 skipped`. The 12 skips are the Neo4j contract tests;
  they skip unless you set `NEO4J_URI` and `NEO4J_PASSWORD` for a live Neo4j.
- **Security tests only:** `python -m pytest -q tests/security`

If it fails on import with `ModuleNotFoundError`, the virtual environment is not active (section 3).

## 6. Start the vector database (needed for real runs, not for tests)

Docker Desktop on an 8 GB machine: in `%UserProfile%\.wslconfig` consider `[wsl2]` / `memory=3GB` so it cannot take all RAM.

```powershell
docker compose up -d        # starts Qdrant ONLY, bound to 127.0.0.1:6333
Invoke-RestMethod http://localhost:6333/healthz
```
- **Success:** the second command returns a short "passed"-style message (Qdrant's `/healthz`). I could not run Docker containers in my
  environment, so this step is unverified by me; compose syntax was validated with `docker compose config` only.
- Postgres and Neo4j are **opt-in** and not needed for DevTrace: `docker compose --profile postgres up -d`,
  `docker compose --profile graph up -d neo4j` (each needs its password set in `.env` first; empty password = it refuses to start).
- **Stop, keep data:** `docker compose down`. **Stop and DELETE all stored data:** `docker compose down -v`.

## 7. Secret scanning and safe commits

Before every commit:
```powershell
git status
git diff --staged        # read it. Anything that looks like a key, token, password or private URL must not be committed
```
One-time setup of the automatic scanner:
```powershell
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files     # scan everything now
```
CI also scans the **full Git history** with gitleaks on every push and pull request.

**If a secret is ever found in the repo or its history:** stop. Revoke/rotate it at the provider first (deleting the file
does not un-leak it, because Git history keeps it). Then ask me; I will not rewrite history without your explicit approval.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Refusing to start: JWT_SECRET is ...` | `.env` missing or `JWT_SECRET` empty/short/placeholder. Section 4 |
| `Refusing to start: SYSTEM=baseline disables authentication...` | You set unauthenticated mode. Use `SYSTEM=project2`, or for local-only experiments set `ENVIRONMENT=dev` **and** `ALLOW_UNAUTHENTICATED=true` |
| `/docs` returns 404 | Intended. Set `ENABLE_DOCS=true` in `.env` for local use |
| 401 `invalid or expired credentials` | Token bad/expired/wrong secret. The reason is deliberately not returned; see the server log |
| `/ready` returns 503 | Qdrant or the database is unreachable. Start it (section 6) |
| Port 6333 already in use | Another Qdrant is running: `docker ps` |

## 9. The DevTrace corpus (Phase 2)

**What it is:** official FastAPI and Pydantic documentation, release notes and migration guides, pinned to exact commits and
split into section-level chunks with provenance. **Where it comes from:** `data/devtrace/sources.yaml` (explicit file list,
40-hex commit SHAs). **Licences:** both MIT, see `data/devtrace/NOTICE.md`.

```powershell
python -m scripts.fetch_devtrace_corpus --dry-run     # lists the exact URLs; NO network, safe to run any time
```
**The fetch has been run once (by me, 2026-10-26) and its manifest is committed.** To rebuild it yourself:
```powershell
python -m scripts.fetch_devtrace_corpus               # ~40 HTTPS requests to raw.githubusercontent.com, sequential
python -m scripts.fetch_devtrace_corpus --verify      # re-checks the snapshot id against data/devtrace/manifest.json
python -m scripts.fetch_devtrace_corpus --no-fetch    # rebuild offline from the local cache
docker compose up -d                                  # Qdrant
python -m scripts.ingest_devtrace --snapshot data/devtrace/snapshots/<snapshot_id>
```
- **Contacts:** only `raw.githubusercontent.com`. **Cost:** none. **Credentials:** none are used or needed.
- **Ingest** downloads the ~90 MB embedding model from Hugging Face on first use and embeds ~1,000 chunks on CPU (minutes, not hours, on an i3).
- **Files written:** `data/devtrace/cache/` and `data/devtrace/snapshots/` (git-ignored, rebuildable) and `data/devtrace/manifest.json` (committed: hashes, counts, commit SHAs; no content).
- **Success:** the fetch prints `snapshot 976aea99e8507ef2: 38 files, 1103 chunks (24 oversize)`; `--verify` prints `MATCH`. A different id means a source byte or chunking behaviour changed: do not ignore it.
- **If a fetch fails:** the message gives the URL and HTTP status or error *type* only. Nothing is retried in a loop and nothing is invented; re-run later or tell me.

**Security properties (tested in `tests/unit/test_corpus_fetch.py`):** https only; exact-host allowlist; every redirect re-validated; public IPs only
(loopback/private/link-local/CGNAT/mapped-IPv6 refused); size cap while streaming; text content types and strict UTF-8 only;
downloaded text is never executed; user queries can never cause a fetch.

## 10. Planned (filled in by later phases)

Asking an investigation question and reading citations/traces (Phases 3, 5, 6) · evaluation runs and metrics (Phases 4, 7) ·
optional deployment and cleanup (Phase 8).

---

## Phase 1 interview notes

- **Why fail at startup instead of warning?** A forgeable-token server looks healthy and answers 200s; the only safe moment
  to catch it is before it serves a request. I also re-check per request (`principal` dependency) because a server can be
  launched in ways that skip the startup hook. Two independent checks, same idea as the existing ACL double-check.
- **Why is the default `ENVIRONMENT=production`?** Fail-closed: forgetting a variable must make things *stricter*, never looser.
  A typo such as `development` is treated as production (tested).
- **Why does `check_config` print only PRESENT/missing?** So the most common support request ("is my setup right?") never
  requires anyone to reveal a secret.
- **Trade-off I accepted:** local developers now must generate a `JWT_SECRET` once. That is one command and is the cost of
  not shipping a public default.
- **Alternative rejected:** removing `SYSTEM=baseline` entirely. It is useful for offline experiments, so it is gated
  rather than deleted.

## Phase 2 interview notes

- **Why pin to a commit SHA, not a tag or "latest"?** Tags can be moved and "latest" changes daily; an evaluation is only
  reproducible if the exact bytes are. The `snapshot_id` hashes those bytes plus the chunker settings.
- **Why not scrape the docs website?** The sites are built from Markdown in Git. Fetching the source gives heading structure,
  stable versions and a verifiable provenance URL, with far less parsing and a much smaller attack surface.
- **Why a custom chunker?** The old word-window chunker could not name the section a passage came from (needed for
  citations) and could cut code in half. Also, the old default of 500 tokens exceeds all-MiniLM-L6-v2's 256-token input
  limit, so the tail of every chunk was silently ignored by the embedder. New default: 180 words.
- **Bugs the tests caught while building this:** (1) a chunk file could smuggle its own `tenant_id`; ACL fields are now
  server-owned. (2) Point ids were global, so the same public docs ingested into two tenants overwrote each other; ids are
  now tenant-scoped.
- **A reproducibility bug I found in my own design:** the first snapshot id hashed only the input bytes and chunker version, so changing the skip list left the id unchanged. It now also hashes the produced chunks, with a regression test.
- **Version metadata is only what the source states:** release-note sections take version/date from their own heading;
  docs pages are labelled "as of" the pinned release; Pydantic's explicit `version-added` notes are preserved as text.

## 11. Retrieval tools (Phase 3, "Arm B")

What exists: `app/devtrace/retrieval.py` (filtered, ACL-safe search), `tools.py` (three typed tools a workflow may call), `versions.py`
(version parsing/ranges), `app/evaluation/devtrace/retrieval_metrics.py` (Recall@k, Precision@k, MRR, ...).

Try it on your laptop (needs Qdrant running and a corpus ingested, see section 9; the first run downloads the embedding model):

```powershell
python -m scripts.devtrace_search "RequestValidationError 422 body" --library fastapi --user-version 0.100.0
python -m scripts.devtrace_search "what changed" --release-notes --library fastapi --from 0.100.0 --to 0.110.0
```

Read the output yourself: are the top 5 the pages you would open? That judgement is the real retrieval check. The unit tests use a
hash embedder, so they prove the *plumbing* (filters, ACLs, versions) and say nothing about semantic quality.

Version semantics (easy to get wrong, so pinned by tests): a release-note range is `from` EXCLUSIVE, `to` INCLUSIVE, always scoped to one
library. "Fixed in 0.101.0" is therefore relevant to a user on 0.100.0 only after upgrading, and the caveat text says exactly that.

The three tools: `retrieve_docs`, `search_release_notes`, `inspect_sources`. The model supplies only query-like arguments. Tenant, roles,
collection and limits are server-owned; unknown arguments are *rejected*, not ignored, so a model cannot even try to set them.

### Phase 3 interview notes
- **Why filter inside the vector search?** A post-filter can return fewer than k results after dropping unauthorized ones, and any bug
  there leaks. A pre-filter makes unauthorized chunks unreachable; we still re-check after retrieval (defense in depth) and audit violations.
- **Why integer version keys?** Qdrant range filters need numbers; `major*1e8 + minor*1e4 + patch` keeps ordering exact. Strings sort `0.9` after `0.10`.
- **Why reject unknown tool arguments?** `extra="forbid"` turns a prompt-injected `{"tenant_id": "other"}` into a visible error instead of a silent no-op.
- **Why an evidence ledger?** Every passage shown to the model gets a ref (E1..). Later citation validation checks against that set, so a
  citation to something never retrieved cannot pass.
- **Bug found by my own test:** `ledger or EvidenceLedger()` discarded a caller's empty ledger, because an empty ledger is falsy. Fixed with `is None`.
