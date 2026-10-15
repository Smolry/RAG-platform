"""Relational store: baseline request log (instrumentation) + append-only audit log.

SQLite for dev/tests, PostgreSQL via docker-compose. Same SQLAlchemy code path for both.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    make_url,
    select,
    text,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _database_url(url: str) -> URL:
    parsed_url = make_url(url)
    if parsed_url.drivername == "postgresql":
        return parsed_url.set(drivername="postgresql+psycopg2")
    return parsed_url


class Base(DeclarativeBase):
    pass


class RequestLog(Base):
    __tablename__ = "request_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    system: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    latency_ms: Mapped[float] = mapped_column(Float)
    retrieval_ms: Mapped[float] = mapped_column(Float)
    generation_ms: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSON)
    retrieval_scores: Mapped[list] = mapped_column(JSON)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # --- Project 1 ---
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    route: Mapped[str | None] = mapped_column(String(16), nullable=True)  # simple | complex | None
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    run_label: Mapped[str] = mapped_column(String(64), default="", index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CorpusState(Base):
    """Current corpus_version per tenant. Ingestion bumps it; the cache only trusts the current value."""

    __tablename__ = "corpus_state"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    """Append-only (enforced by DB triggers). event_type: 'retrieval' | 'acl_violation'."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    query: Mapped[str] = mapped_column(Text)
    returned_chunk_ids: Mapped[list] = mapped_column(JSON)
    returned_document_ids: Mapped[list] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AuditDocument(Base):
    """One row per (audit event, document): makes 'who retrieved doc X' an indexed lookup."""

    __tablename__ = "audit_document"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey("audit_log.id"), index=True)
    document_id: Mapped[str] = mapped_column(String(256), index=True)


_SQLITE_TRIGGERS = [
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'audit tables are append-only'); END"
    for t in ("audit_log", "audit_document")
    for op in ("UPDATE", "DELETE")
]
_PG_STATEMENTS = [
    "CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit tables are append-only'; END; $$ LANGUAGE plpgsql",
    *[
        f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}"
        for t in ("audit_log", "audit_document")
    ],
    *[
        f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} "
        f"FOR EACH ROW EXECUTE FUNCTION audit_append_only()"
        for t in ("audit_log", "audit_document")
    ],
]


class Database:
    def __init__(self, url: str):
        parsed_url = _database_url(url)
        kw: dict = {}
        if parsed_url.drivername.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
            if parsed_url.database in (None, ":memory:"):
                kw["poolclass"] = StaticPool
        self.engine = create_engine(parsed_url, **kw)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            stmts = _SQLITE_TRIGGERS if self.engine.dialect.name == "sqlite" else _PG_STATEMENTS
            for s in stmts:
                conn.execute(text(s))
        self._Session = sessionmaker(self.engine, expire_on_commit=False)

    # --- instrumentation ---
    def log_request(self, **fields) -> None:
        with self._Session.begin() as s:
            s.add(RequestLog(**fields))

    def set_quality(self, request_id: str, score: float) -> None:
        with self._Session.begin() as s:
            row = s.execute(select(RequestLog).where(RequestLog.request_id == request_id)).scalar_one_or_none()
            if row:
                row.quality_score = score

    def set_corpus_version(self, tenant_id: str, version: str) -> None:
        with self._Session.begin() as s:
            row = s.get(CorpusState, tenant_id)
            if row:
                row.version, row.updated_at = version, _now()
            else:
                s.add(CorpusState(tenant_id=tenant_id, version=version))

    def get_corpus_version(self, tenant_id: str) -> str:
        with self._Session() as s:
            row = s.get(CorpusState, tenant_id)
            return row.version if row else "unversioned"

    def request_log_frame(self, run_label: str | None = None):
        import pandas as pd
        q = "SELECT * FROM request_log" + (" WHERE run_label = :l" if run_label is not None else "") + " ORDER BY ts"
        with self.engine.connect() as c:
            return pd.read_sql(text(q), c, params={"l": run_label} if run_label is not None else None)

    # --- audit ---
    def audit(self, *, request_id: str, event_type: str, user_id: str, tenant_id: str, query: str,
              chunk_ids: list[str], document_ids: list[str], model: str) -> None:
        with self._Session.begin() as s:
            row = AuditLog(request_id=request_id, event_type=event_type, user_id=user_id, tenant_id=tenant_id,
                           query=query, returned_chunk_ids=chunk_ids, returned_document_ids=sorted(set(document_ids)),
                           model=model)
            s.add(row)
            s.flush()
            for d in sorted(set(document_ids)):
                s.add(AuditDocument(audit_id=row.id, document_id=d))

    def who_retrieved(self, document_id: str, tenant_id: str, days: int = 30) -> list[dict]:
        """'Who retrieved document X in the last N days' - scoped to the asking admin's tenant."""
        since = _now() - timedelta(days=days)
        q = (
            select(AuditLog.request_id, AuditLog.user_id, AuditLog.created_at, AuditLog.query)
            .join(AuditDocument, AuditDocument.audit_id == AuditLog.id)
            .where(AuditDocument.document_id == document_id, AuditLog.tenant_id == tenant_id,
                   AuditLog.event_type == "retrieval", AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc())
        )
        with self._Session() as s:
            return [dict(r._mapping) for r in s.execute(q)]

    def audit_events(self, event_type: str | None = None) -> list[AuditLog]:
        with self._Session() as s:
            q = select(AuditLog)
            if event_type:
                q = q.where(AuditLog.event_type == event_type)
            return list(s.execute(q).scalars())
