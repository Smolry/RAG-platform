"""Central configuration. Every tunable comes from env / .env (see .env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    neo4j_password: str = "rag-dev-password"
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
    jwt_secret: str = "CHANGE-ME-dev-only-secret-at-least-32-bytes"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
