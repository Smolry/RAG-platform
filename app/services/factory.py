from __future__ import annotations

from app.caching.semantic_cache import SemanticCache
from app.core.config import Settings, get_settings
from app.generation.llm import LiteLLMClient, LLMClient, PriceTable, RecordingLLM
from app.observability.db import Database
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.store import VectorStore, make_client
from app.graph.hybrid import HybridRetriever
from app.graph.rerank import build_reranker
from app.graph.store import InMemoryGraphStore, Neo4jGraphStore
from app.routing.router import RuleRouter
from app.services.rag import RAGService


def build_service(settings: Settings | None = None, llm: LLMClient | None = None,
                  embedder: Embedder | None = None, store: VectorStore | None = None,
                  db: Database | None = None, graph_store=None, reranker=None) -> RAGService:
    s = settings or get_settings()
    embedder = embedder or build_embedder(s)
    store = store or VectorStore(make_client(s.qdrant_url), s.qdrant_collection)
    store.ensure_collection(embedder.dim)
    cache = router = None
    if s.system == "project1":
        cache = SemanticCache(store.client, s.cache_collection, s.cache_threshold, s.cache_ttl_seconds)
        cache.ensure(embedder.dim)
        router = RuleRouter.from_yaml(s.routing_config_path)
    llm = llm or (RecordingLLM() if s.llm_backend == "stub" else LiteLLMClient(
        timeout=s.llm_timeout_seconds, max_tokens=s.llm_max_output_tokens, num_retries=s.llm_max_retries))
    prices = PriceTable.from_yaml(s.models_config_path)
    hybrid = graph = None
    if s.system == "project3":
        if s.graph_enabled:
            graph = graph_store or (InMemoryGraphStore() if s.graph_backend == "memory"
                                    else Neo4jGraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password))
        hybrid = HybridRetriever(s, store, embedder, graph, reranker or build_reranker(s), llm, prices)
    return RAGService(s, store, embedder, llm, db or Database(s.database_url), prices, cache, router, hybrid, graph)
