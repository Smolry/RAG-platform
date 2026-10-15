from __future__ import annotations

import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.security.auth import AuthError, Principal, verify_token
from app.services.rag import RAGService

log = logging.getLogger("uvicorn.error")


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
    app = FastAPI(title="RAG Platform", version="0.2.0")
    _state: dict = {"service": service}

    @app.middleware("http")
    async def log_request_progress(request, call_next):
        started = time.perf_counter()
        log.info("request started: %s %s", request.method, request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.exception(
                "request failed: %s %s after %.1f ms",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.info(
            "request completed: %s %s -> %d in %.1f ms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    def get_service() -> RAGService:
        if _state["service"] is None:  # lazy so importing the module never needs live infra
            from app.services.factory import build_service
            system = (settings or get_settings()).system
            log.info("initializing RAG service (system=%s)", system)
            started = time.perf_counter()
            try:
                _state["service"] = build_service(settings or get_settings())
            except Exception:
                log.exception("RAG service initialization failed")
                raise
            log.info(
                "RAG service ready in %.1f ms",
                (time.perf_counter() - started) * 1000,
            )
        return _state["service"]

    def cfg() -> Settings:
        return get_service().settings

    def principal(authorization: str | None = Header(default=None), svc: RAGService = Depends(get_service)) -> Principal | None:
        if svc.settings.system == "baseline":
            return None  # baseline mode: no auth
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return verify_token(authorization.split(" ", 1)[1], svc.settings.jwt_secret, svc.settings.jwt_algorithm)
        except AuthError as e:
            raise HTTPException(401, str(e)) from e

    def admin(p: Principal | None = Depends(principal)) -> Principal:
        if p is None or "admin" not in p.roles:
            raise HTTPException(403, "admin role required")
        return p

    @app.get("/health")
    def health():
        return {"status": "ok"}

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
        if svc.cache is not None:
            svc.cache.invalidate_tenant(p.tenant_id)  # revocation must beat the cache
        if n == 0:  # also covers "document belongs to another tenant": never reveal existence
            raise HTTPException(404, "document not found")
        return {"document_id": document_id, "chunks_updated": n, "update_ms": (time.perf_counter() - t0) * 1000}

    return app


app = create_app()
