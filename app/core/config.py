"""Central configuration. Every tunable comes from env / .env (see .env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


#: The placeholder that used to ship in .env.example and as the settings default. It is PUBLIC, so a server
#: running with it accepts tokens that anyone can forge. It is only tolerated when ENVIRONMENT=dev.
INSECURE_DEFAULT_JWT_SECRET = "CHANGE-ME-dev-only-secret-at-least-32-bytes"
MIN_JWT_SECRET_BYTES = 32
ALLOWED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


class ConfigError(RuntimeError):
    """Raised at server startup when the configuration is unsafe to serve traffic with.

    Messages name the VARIABLE and the rule, never its value (values may be secrets)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- deployment safety ---
    # Fail-closed default: anything other than the exact value "dev" is treated as a real deployment.
    environment: str = "production"      # "dev" relaxes ONLY the checks in serving_security_problems()
    allow_unauthenticated: bool = False  # honoured only together with environment=dev (see SYSTEM=baseline)
    enable_docs: bool = False            # /docs, /redoc, /openapi.json are off unless explicitly enabled

    # --- system mode: "baseline" (no auth, no ACL) | "project2" (JWT + ACL) ---
    system: str = "project2"  # baseline | project2 | project1

    # --- embeddings ---
    embedder_backend: str = "sentence-transformers"  # "hash" = offline, TEST ONLY
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- vector DB ---
    qdrant_url: str = "http://localhost:6333"  # ":memory:" for tests
    qdrant_collection: str = "chunks"

    # --- relational DB (request log + audit log) ---
    database_url: str = "sqlite:///./rag.db"  # docker-compose uses Postgres

    # --- chunking / retrieval ---
    chunk_tokens: int = 500
    chunk_overlap_tokens: int = 50
    top_k: int = 5

    # --- LLM (LiteLLM model strings) ---
    llm_backend: str = "litellm"  # "stub" = offline smoke tests ONLY; stamped into every result file
    llm_model: str = "gpt-4o-mini"
    judge_model: str = "gpt-4o-mini"
    models_config_path: str = "configs/models.yaml"
    # Hard bounds on every provider call (cost / hang protection). The output cap is deliberately generous so graph
    # extraction JSON is not truncated; DevTrace will use a tighter, per-workflow budget.
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 4096
    llm_max_retries: int = 2

    # --- Project 1: model tiers, cache, routing (all ablation-friendly) ---
    small_model: str = "gpt-4o-mini"
    large_model: str = "gpt-4o"
    cache_enabled: bool = True
    routing_enabled: bool = True
    cache_collection: str = "semantic_cache"
    # Conservative default = top of the sweep range. Set the MEASURED value after `scripts.tune_cache_threshold`.
    cache_threshold: float = 0.97
    cache_ttl_seconds: int = 86400
    routing_config_path: str = "configs/routing.yaml"
    escalation_min_top_score: float = 0.30  # embedder-dependent; tune alongside the router
    run_label: str = ""  # tags request_log rows so the dashboard/benchmarks can separate runs

    # --- Project 3: GraphRAG ---
    graph_backend: str = "neo4j"  # "memory" = tests / tiny local runs only
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""  # no default credential in source: set NEO4J_PASSWORD in .env (empty => auth fails closed)
    extraction_model: str = "gpt-4o-mini"      # builds the graph + extracts question entities
    graph_hops: int = 2                        # PDF: expand 1-2 hops
    graph_hub_degree_cap: int = 50             # do not traverse THROUGH entities with more neighbours than this
    vector_k: int = 10                         # vector candidates fed to the merge (final context is still top_k)
    graph_limit: int = 20                      # graph candidates fed to the merge
    reranker_backend: str = "cross-encoder"    # "lexical" = offline TEST ONLY
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    question_entities: str = "llm"             # "llm" | "ngram" (dictionary match, zero LLM cost)
    graph_enabled: bool = True                 # ablation: False => vector candidates + rerank only
    rerank_enabled: bool = True
    answer_style: str = "cited"                # "short" = HotpotQA-style short answers (EM/F1 evaluation)
    extraction_cache_path: str = "data/processed/extraction_cache.jsonl"

    # --- auth ---
    jwt_secret: str = INSECURE_DEFAULT_JWT_SECRET  # never valid outside dev; set JWT_SECRET (>= 32 random bytes)
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_dev(s: Settings) -> bool:
    return s.environment == "dev"


def serving_security_problems(s: Settings) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for running the HTTP API with these settings. Never includes secret VALUES.

    Errors block startup. Warnings are tolerated only in dev."""
    errors: list[str] = []
    warnings: list[str] = []
    dev = is_dev(s)
    if s.system == "baseline":
        # Baseline mode turns authentication AND ACL enforcement off for /query. It exists for offline experiments.
        if not (dev and s.allow_unauthenticated):
            errors.append("SYSTEM=baseline disables authentication and ACLs. The API only starts in this mode when "
                          "ENVIRONMENT=dev and ALLOW_UNAUTHENTICATED=true are both set explicitly.")
        else:
            warnings.append("Running UNAUTHENTICATED (SYSTEM=baseline, ALLOW_UNAUTHENTICATED=true). Local development only.")
        return errors, warnings
    if s.jwt_algorithm not in ALLOWED_JWT_ALGORITHMS:
        errors.append(f"JWT_ALGORITHM must be one of {sorted(ALLOWED_JWT_ALGORITHMS)}.")
    secret = s.jwt_secret or ""
    if not secret.strip():
        errors.append("JWT_SECRET is empty. Generate one (see docs/DEVELOPMENT_GUIDE.md) and put it in .env.")
    elif secret == INSECURE_DEFAULT_JWT_SECRET or len(secret.encode()) < MIN_JWT_SECRET_BYTES:
        msg = (f"JWT_SECRET is the published placeholder or shorter than {MIN_JWT_SECRET_BYTES} bytes, so tokens can be "
               "forged. Generate a random secret (see docs/DEVELOPMENT_GUIDE.md).")
        (warnings if dev else errors).append(msg)
    return errors, warnings


def assert_serving_security(s: Settings) -> list[str]:
    """Raise ConfigError if the settings are unsafe to serve with; otherwise return the (dev-only) warnings."""
    errors, warnings = serving_security_problems(s)
    if errors:
        raise ConfigError("Refusing to start: " + " | ".join(errors))
    return warnings
