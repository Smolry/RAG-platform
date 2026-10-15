import pytest

from app.core.config import Settings
from app.evaluation.synthetic_tenants import build_corpus
from app.generation.llm import PriceTable, RecordingLLM
from app.observability.db import Database
from app.retrieval.embedder import HashEmbedder
from app.retrieval.store import VectorStore, make_client
from app.security.auth import Principal
from app.services.ingestion import ingest_documents
from app.services.rag import RAGService


@pytest.fixture
def settings():
    # Small chunks so each synthetic doc yields >1 chunk (exercises chunk-level ACLs).
    return Settings(system="project2", embedder_backend="hash", qdrant_url=":memory:", database_url="sqlite://",
                    chunk_tokens=40, chunk_overlap_tokens=8, top_k=5, jwt_secret="test-secret-test-secret-test-secret!!")


@pytest.fixture
def corpus():
    return build_corpus()


@pytest.fixture
def llm():
    return RecordingLLM()


@pytest.fixture
def service(settings, corpus, llm):
    docs, _ = corpus
    emb = HashEmbedder()
    store = VectorStore(make_client(":memory:"), "chunks")
    ingest_documents(docs, store, emb, settings)
    return RAGService(settings, store, emb, llm, Database("sqlite://"),
                      PriceTable({"gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.6}}))


def principal_for(users, user_id):
    u = next(x for x in users if x["user_id"] == user_id)
    return Principal(u["user_id"], u["tenant_id"], tuple(u["roles"]))


# ---------------- Project 1 fixtures ----------------
class ScriptedLLM:
    """Offline LLM whose reply depends on the requested model, so escalation can be exercised deterministically."""

    def __init__(self, small_reply="Answer based on the provided context [1].", large_reply="Large answer [1]."):
        self.small_reply, self.large_reply, self.calls_by_model = small_reply, large_reply, []

    def complete(self, messages, model, temperature=0.0):
        from app.generation.llm import LLMResult
        self.calls_by_model.append(model)
        text = self.large_reply if model == "gpt-4o" else self.small_reply
        return LLMResult(text, sum(len(m["content"].split()) for m in messages), 10, model)


@pytest.fixture
def p1_settings(settings):
    return settings.model_copy(update={"system": "project1", "cache_threshold": 0.9, "cache_ttl_seconds": 3600,
                                       "escalation_min_top_score": 0.0})


def build_p1(p1_settings, corpus, llm, clock=None):
    from app.caching.semantic_cache import SemanticCache
    from app.routing.router import RuleRouter
    docs, _ = corpus
    emb, db = HashEmbedder(), Database("sqlite://")
    store = VectorStore(make_client(":memory:"), "chunks")
    ingest_documents(docs, store, emb, p1_settings, db=db)
    kw = {"clock": clock} if clock else {}
    cache = SemanticCache(store.client, "semantic_cache", p1_settings.cache_threshold, p1_settings.cache_ttl_seconds, **kw)
    cache.ensure(emb.dim)
    prices = PriceTable({"gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.6},
                         "gpt-4o": {"input_per_1m": 2.5, "output_per_1m": 10.0}})
    return RAGService(p1_settings, store, emb, llm, db, prices, cache, RuleRouter.from_yaml("configs/routing.yaml"))


@pytest.fixture
def p1(p1_settings, corpus):
    return build_p1(p1_settings, corpus, ScriptedLLM())
