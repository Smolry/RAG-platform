"""Ingest documents into Qdrant.

Baseline corpus : python -m scripts.ingest --path data/raw --tenant acme --classification internal \
                      --roles employee,manager,hr,admin
Tenant corpus   : python -m scripts.ingest --manifest data/synthetic/manifest.json
Every chunk MUST carry a tenant_id (ingestion fails loudly otherwise).
"""
import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.observability.db import Database
from app.retrieval.embedder import build_embedder
from app.retrieval.store import VectorStore, make_client
from app.services.ingestion import DocumentInput, ingest_documents


def load_path(path: Path, tenant: str, classification: str, roles: list[str], users: list[str]) -> list[DocumentInput]:
    docs = []
    for f in sorted(path.rglob("*")):
        if f.suffix.lower() in {".txt", ".md"} and f.is_file():
            docs.append(DocumentInput(document_id=f"{tenant}/{f.relative_to(path).with_suffix('').as_posix()}",
                                      text=f.read_text(errors="ignore"), tenant_id=tenant,
                                      classification=classification, allowed_roles=roles, allowed_users=users))
    return docs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--classification", default="public", choices=["public", "internal", "confidential"])
    ap.add_argument("--roles", default="employee,manager,hr,admin,contractor")
    ap.add_argument("--users", default="")
    a = ap.parse_args()
    if bool(a.path) == bool(a.manifest):
        ap.error("provide exactly one of --path or --manifest")
    if a.manifest:
        docs = [DocumentInput(document_id=m["document_id"], text=Path(m["path"]).read_text(), tenant_id=m["tenant_id"],
                              classification=m["classification"], allowed_roles=m["allowed_roles"],
                              allowed_users=m["allowed_users"]) for m in json.loads(a.manifest.read_text())]
    else:
        docs = load_path(a.path, a.tenant, a.classification, [r for r in a.roles.split(",") if r],
                         [u for u in a.users.split(",") if u])
    s = get_settings()
    rep = ingest_documents(docs, VectorStore(make_client(s.qdrant_url), s.qdrant_collection), build_embedder(s), s,
                           db=Database(s.database_url))
    print(f"ingested {rep.documents} documents / {rep.chunks} chunks; corpus_version={rep.corpus_version}")


if __name__ == "__main__":
    main()
