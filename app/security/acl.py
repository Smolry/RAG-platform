"""ACL logic: (1) pre-retrieval Qdrant filter, (2) post-retrieval application check.

Access rule (implementation decision, mirrors PDF filter code):
    chunk.tenant_id == user.tenant_id  AND  ( user.roles ∩ chunk.allowed_roles ≠ ∅  OR  user.id ∈ chunk.allowed_users )
Classification is stored/audited and is used to derive default allowed_roles at ingestion
(see app/evaluation/synthetic_tenants.py); the rule above is the single enforcement point.
"""
from __future__ import annotations

from qdrant_client import models

from app.security.auth import Principal


def acl_filter(user: Principal) -> models.Filter:
    should: list[models.Condition] = [
        models.FieldCondition(key="allowed_users", match=models.MatchValue(value=user.user_id))
    ]
    if user.roles:
        should.append(models.FieldCondition(key="allowed_roles", match=models.MatchAny(any=list(user.roles))))
    return models.Filter(
        must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=user.tenant_id))],
        should=should,  # Qdrant: with `must` present, `should` requires >= 1 match
    )


def is_authorized(user: Principal, payload: dict) -> bool:
    """Second, independent, fail-closed check run in application code on every retrieved chunk."""
    tenant = payload.get("tenant_id")
    if not isinstance(tenant, str) or not tenant or tenant != user.tenant_id:
        return False
    roles = payload.get("allowed_roles") or []
    users = payload.get("allowed_users") or []
    return bool(set(roles) & set(user.roles)) or user.user_id in users
