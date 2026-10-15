from __future__ import annotations

from app.caching.semantic_cache import SemanticCache
from app.core.config import Settings, get_settings
from app.generation.llm import LiteLLMClient, LLMClient, PriceTable, RecordingLLM
from app.observability.db import Database
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.store import VectorStore, make_client
from app.routing.router import RuleRouter
from app.services.rag import RAGService


def build_service(settings: Settings | None = None, llm: LLMClient | None = None,
                  embedder: Embedder | None = None, store: VectorStore | None = None,
                  db: Database | None = None) -> RAGService:
    s = settings or get_settings()
    embedder = embedder or build_embedder(s)
    store = store or VectorStore(make_client(s.qdrant_url), s.qdrant_collection)
    store.ensure_collection(embedder.dim)
    cache = router = None
    if s.system == "project1":
        cache = SemanticCache(store.client, s.cache_collection, s.cache_threshold, s.cache_ttl_seconds)
        cache.ensure(embedder.dim)
        router = RuleRouter.from_yaml(s.routing_config_path)
    llm = llm or (RecordingLLM() if s.llm_backend == "stub" else LiteLLMClient())
    return RAGService(s, store, embedder, llm, db or Database(s.database_url),
                      PriceTable.from_yaml(s.models_config_path), cache, router)
