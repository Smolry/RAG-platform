"""Write the synthetic 3-tenant corpus (+ users) to data/synthetic/ for ingestion and inspection."""
import json
from pathlib import Path

from app.evaluation.synthetic_tenants import build_corpus

OUT = Path("data/synthetic")


def main() -> None:
    docs, users = build_corpus()
    manifest = []
    for d in docs:
        p = OUT / "tenants" / f"{d.document_id}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(d.text + "\n")
        manifest.append({"document_id": d.document_id, "path": str(p), "tenant_id": d.tenant_id,
                         "classification": d.classification, "allowed_roles": d.allowed_roles,
                         "allowed_users": d.allowed_users})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (OUT / "users.json").write_text(json.dumps(users, indent=2))
    print(f"wrote {len(docs)} docs, {len(users)} users to {OUT}")


if __name__ == "__main__":
    main()
