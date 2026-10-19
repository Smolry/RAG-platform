from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.security.auth import AuthError, Principal, issue_token, verify_token

S = "s" * 40


def test_roundtrip():
    p = Principal("u", "t", ("admin", "hr"))
    assert verify_token(issue_token(p, S), S) == p


def test_wrong_secret_expired_tampered_none_alg_missing_claims():
    p = Principal("u", "t", ("admin",))
    with pytest.raises(AuthError):
        verify_token(issue_token(p, S), "x" * 40)
    old = issue_token(p, S, now=datetime.now(timezone.utc) - timedelta(hours=3))
    with pytest.raises(AuthError):
        verify_token(old, S)
    good = issue_token(p, S)
    h, pl, sig = good.split(".")
    with pytest.raises(AuthError):
        verify_token(f"{h}.{pl[:-2]}AA.{sig}", S)
    none_tok = jwt.encode({"user_id": "u", "tenant_id": "t", "roles": ["admin"],
                           "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, None, algorithm="none")
    with pytest.raises(AuthError):
        verify_token(none_tok, S)
    no_tenant = jwt.encode({"user_id": "u", "roles": ["admin"],
                            "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, S, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_token(no_tenant, S)
    blank = jwt.encode({"user_id": "u", "tenant_id": " ", "roles": ["admin"],
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, S, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_token(blank, S)
