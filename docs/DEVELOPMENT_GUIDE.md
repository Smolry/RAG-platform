# DevTrace development guide (Windows / PowerShell first)

> **Status: Phase 1 of 8.** This guide documents only what exists today. Sections marked **Planned** are placeholders
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
python -m pip install -r req-min.txt
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
- **Success (as of Phase 1, verified on Linux):** `191 passed, 12 skipped`. The 12 skips are the Neo4j contract tests;
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

## 9. Planned (filled in by later phases)

Corpus fetching and ingestion (Phase 2) · asking an investigation question and reading citations/traces (Phases 3, 5, 6) ·
evaluation runs and reading metrics (Phases 4, 7) · optional deployment and cleanup (Phase 8).

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
