"""JWT issue/verify. Identity comes ONLY from a verified token (PDF Project 2, step 3)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    roles: tuple[str, ...]


def issue_token(p: Principal, secret: str, algorithm: str = "HS256", ttl_minutes: int = 60,
                now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    claims = {
        "user_id": p.user_id,
        "tenant_id": p.tenant_id,
        "roles": list(p.roles),
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(claims, secret, algorithm=algorithm)


def verify_token(token: str, secret: str, algorithm: str = "HS256") -> Principal:
    try:
        # algorithms is pinned: rejects alg=none and algorithm-confusion attacks.
        c = jwt.decode(token, secret, algorithms=[algorithm],
                       options={"require": ["exp", "user_id", "tenant_id", "roles"]})
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {e}") from e
    user_id, tenant_id, roles = c["user_id"], c["tenant_id"], c["roles"]
    if not (isinstance(user_id, str) and user_id.strip() and isinstance(tenant_id, str) and tenant_id.strip()):
        raise AuthError("token has blank user_id/tenant_id")
    if not (isinstance(roles, list) and all(isinstance(r, str) and r for r in roles)):
        raise AuthError("token roles malformed")
    return Principal(user_id=user_id, tenant_id=tenant_id, roles=tuple(roles))
