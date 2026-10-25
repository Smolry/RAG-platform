from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, assert_serving_security, get_settings, serving_security_problems
from app.security.auth import AuthError, Principal, verify_token
from app.services.rag import RAGService

log = logging.getLogger(__name__)


class QueryRequest(BaseModel):
    # Identity fields (tenant_id / roles / user_id) are deliberately NOT declared. Anything extra a client
    # sends is ignored (pydantic default) and never reaches the retrieval filter.
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class AclUpdate(BaseModel):
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    classification: str | None = None


def create_app(service: RAGService | None = None, settings: Settings | None = None) -> FastAPI:
    boot = service.settings if service is not None else (settings or get_settings())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Fail fast: the server refuses to start with a forgeable JWT secret or an unauthenticated mode that
        # was not explicitly opted into (ENVIRONMENT=dev + ALLOW_UNAUTHENTICATED=true).
        for w in assert_serving_security(boot):
            log.warning("config warning (dev only): %s", w)
        yield

    # Interactive docs and the OpenAPI schema list every route (incl. admin ones): off unless ENABLE_DOCS=true.
    docs = {} if boot.enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="RAG Platform", version="0.2.0", lifespan=lifespan, **docs)
    _state: dict = {"service": service}

    def get_service() -> RAGService:
        if _state["service"] is None:  # lazy so importing the module never needs live infra
            from app.services.factory import build_service
            _state["service"] = build_service(settings or get_settings())
        return _state["service"]

    def cfg() -> Settings:
        return get_service().settings

    def principal(authorization: str | None = Header(default=None)) -> Principal | None:
        # Authenticate BEFORE the (expensive) service exists: an anonymous request must get a cheap 401 and must never
        # be able to trigger embedder loading or database connections. Uses the same settings the service is built from.
        errors, _ = serving_security_problems(boot)
        if errors:  # defence in depth: the lifespan check should already have stopped the server
            log.error("request refused: unsafe configuration (%d problem(s))", len(errors))
            raise HTTPException(503, "service misconfigured")
        if boot.system == "baseline":
            return None  # baseline mode: no auth (only reachable with ENVIRONMENT=dev + ALLOW_UNAUTHENTICATED=true)
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return verify_token(authorization.split(" ", 1)[1], boot.jwt_secret, boot.jwt_algorithm)
        except AuthError as e:
            log.info("authentication failed")  # reason intentionally not returned to the client
            raise HTTPException(401, "invalid or expired credentials") from e

    def admin(p: Principal | None = Depends(principal)) -> Principal:
        if p is None or "admin" not in p.roles:
            raise HTTPException(403, "admin role required")
        return p

    @app.get("/health")
    def health():
        """Liveness only: the process is up. Touches no dependency and exposes no configuration."""
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        """Readiness: vector store and relational DB answer. Failure detail is logged by type only, never returned."""
        try:
            svc = get_service()
            svc.store.client.collection_exists(svc.store.collection)
            svc.db.get_corpus_version("__readiness__")
        except Exception as e:  # noqa: BLE001 - any dependency failure means "not ready"
            log.warning("readiness check failed: %s", type(e).__name__)
            raise HTTPException(503, "not ready") from None
        return {"status": "ready"}

    @app.post("/query")
    def query(req: QueryRequest, p: Principal | None = Depends(principal), svc: RAGService = Depends(get_service)):
        return svc.answer(req.query, principal=p, top_k=req.top_k)

    @app.get("/admin/audit/documents/{document_id:path}")
    def who_retrieved(document_id: str, days: int = Query(30, ge=1, le=365), p: Principal = Depends(admin),
                      svc: RAGService = Depends(get_service)):
        """Who retrieved document X in the last N days. Scoped to the admin's own tenant."""
        rows = svc.db.who_retrieved(document_id, p.tenant_id, days)
        return {"document_id": document_id, "days": days, "retrievals": rows}

    @app.put("/admin/documents/{document_id:path}/acl")
    def update_acl(document_id: str, body: AclUpdate, p: Principal = Depends(admin),
                   svc: RAGService = Depends(get_service)):
        t0 = time.perf_counter()
        n = svc.store.update_acl(document_id, p.tenant_id, body.allowed_roles, body.allowed_users, body.classification)
        if svc.graph is not None:  # revocation must reach the graph too (chunk nodes AND edge ACLs)
            svc.graph.update_document_acl(document_id, p.tenant_id, body.allowed_roles, body.allowed_users)
        if svc.cache is not None:
            svc.cache.invalidate_tenant(p.tenant_id)  # revocation must beat the cache
        if n == 0:  # also covers "document belongs to another tenant": never reveal existence
            raise HTTPException(404, "document not found")
        return {"document_id": document_id, "chunks_updated": n, "update_ms": (time.perf_counter() - t0) * 1000}

    return app


app = create_app()
