"""Phase 1 hardening: unsafe configuration must stop the server (or be refused per request), never degrade silently.

All credentials in this file are deliberately invalid test values.
"""
import re
import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import (INSECURE_DEFAULT_JWT_SECRET, ConfigError, Settings, assert_serving_security,
                             serving_security_problems)
from app.generation.llm import LiteLLMClient
from app.security.auth import Principal, issue_token
from scripts.check_config import build_report

STRONG = "t" * 48  # not a real secret; just long enough to pass the length rule
PLACEHOLDER = INSECURE_DEFAULT_JWT_SECRET


def S(**kw) -> Settings:
    base = dict(system="project2", jwt_secret=STRONG, environment="production")
    return Settings(_env_file=None, **{**base, **kw})


# ---------------------------------------------------------------- S1: forgeable JWT secret
def test_production_rejects_placeholder_secret():
    errors, _ = serving_security_problems(S(jwt_secret=PLACEHOLDER))
    assert errors and "JWT_SECRET" in errors[0]


def test_production_rejects_short_and_empty_secret():
    assert serving_security_problems(S(jwt_secret="short"))[0]
    assert serving_security_problems(S(jwt_secret=""))[0]
    assert serving_security_problems(S(jwt_secret="   "))[0]


def test_production_accepts_strong_secret():
    assert serving_security_problems(S()) == ([], [])


def test_default_settings_are_fail_closed():
    """A fresh Settings() (no .env, no env vars) must NOT be servable: environment defaults to production."""
    errors, _ = serving_security_problems(Settings(_env_file=None))
    assert errors


def test_dev_tolerates_placeholder_with_warning_but_never_empty():
    errors, warnings = serving_security_problems(S(environment="dev", jwt_secret=PLACEHOLDER))
    assert not errors and warnings
    assert serving_security_problems(S(environment="dev", jwt_secret=""))[0]


def test_unknown_environment_value_is_treated_as_production():
    # e.g. a typo such as "development" must not unlock the dev relaxations
    assert serving_security_problems(S(environment="development", jwt_secret=PLACEHOLDER))[0]


@pytest.mark.parametrize("alg", ["none", "None", "RS256", ""])
def test_unsafe_jwt_algorithms_rejected(alg):
    assert serving_security_problems(S(jwt_algorithm=alg))[0]


def test_error_messages_never_contain_secret_values():
    secret = "sup3r-secret-value-that-must-not-leak-0123456789"
    msgs = serving_security_problems(S(jwt_secret=secret, jwt_algorithm="none"))[0]
    assert msgs and all(secret not in m for m in msgs)
    with pytest.raises(ConfigError) as ei:
        assert_serving_security(S(jwt_secret="x" + PLACEHOLDER[:3]))
    assert "xCHA" not in str(ei.value)


# ---------------------------------------------------------------- S2: unauthenticated bypass
def test_baseline_refused_in_production_even_with_flag():
    assert serving_security_problems(S(system="baseline", allow_unauthenticated=True))[0]


def test_baseline_refused_in_dev_without_explicit_flag():
    assert serving_security_problems(S(system="baseline", environment="dev"))[0]


def test_baseline_allowed_only_with_dev_and_flag_and_warns():
    errors, warnings = serving_security_problems(S(system="baseline", environment="dev", allow_unauthenticated=True))
    assert not errors and warnings


def _app_with(service, **settings_update):
    service.settings = service.settings.model_copy(update=settings_update)
    return create_app(service=service, settings=service.settings)


def test_server_refuses_to_start_with_placeholder_secret(service):
    app = _app_with(service, jwt_secret=PLACEHOLDER)
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


def test_server_refuses_to_start_in_unauthenticated_mode(service):
    app = _app_with(service, system="baseline")
    with pytest.raises(ConfigError):
        with TestClient(app):
            pass


def test_requests_fail_closed_even_if_lifespan_was_bypassed(service):
    """Defence in depth: a server that skipped the startup check still refuses to serve /query."""
    client = TestClient(_app_with(service, system="baseline"))  # no `with`: lifespan does not run
    r = client.post("/query", json={"query": "anything"})
    assert r.status_code == 503 and "baseline" not in r.text.lower()


def test_dev_unauthenticated_mode_works_only_when_fully_opted_in(service):
    app = _app_with(service, system="baseline", environment="dev", allow_unauthenticated=True)
    with TestClient(app) as c:
        assert c.post("/query", json={"query": "salary sheet"}).status_code == 200


def test_normal_secure_server_starts(service):
    with TestClient(create_app(service=service, settings=service.settings)) as c:
        assert c.get("/health").status_code == 200


# ---------------------------------------------------------------- S7: docs, 401 detail, readiness
def test_docs_and_schema_are_off_by_default(service):
    c = TestClient(create_app(service=service, settings=service.settings))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404, path


def test_docs_can_be_enabled_explicitly(service):
    c = TestClient(_app_with(service, enable_docs=True))
    assert c.get("/openapi.json").status_code == 200


def test_401_does_not_echo_jwt_library_internals(service):
    c = TestClient(create_app(service=service, settings=service.settings))
    good = issue_token(Principal("u", "helios", ("employee",)), service.settings.jwt_secret)
    forged = good[:-3] + "abc"
    for hdr in ({"Authorization": f"Bearer {forged}"}, {"Authorization": "Bearer garbage"}):
        r = c.post("/query", json={"query": "x"}, headers=hdr)
        assert r.status_code == 401
        assert r.json()["detail"] == "invalid or expired credentials"


def test_health_exposes_no_configuration(service):
    c = TestClient(create_app(service=service, settings=service.settings))
    assert c.get("/health").json() == {"status": "ok"}


def test_ready_ok_and_failure_leaks_nothing(service, monkeypatch):
    c = TestClient(create_app(service=service, settings=service.settings))
    assert c.get("/ready").json() == {"status": "ready"}

    def boom(*_a, **_k):
        raise RuntimeError("connect failed to http://user:hunter2@qdrant.internal:6333")
    monkeypatch.setattr(service.store.client, "collection_exists", boom)
    r = c.get("/ready")
    assert r.status_code == 503
    assert "hunter2" not in r.text and "qdrant.internal" not in r.text


# ---------------------------------------------------------------- S6: bounded provider calls
def test_litellm_client_bounds_every_call(monkeypatch):
    seen = {}

    def fake_completion(**kw):
        seen.update(kw)
        usage = types.SimpleNamespace(prompt_tokens=3, completion_tokens=2)
        msg = types.SimpleNamespace(content="ok")
        return types.SimpleNamespace(usage=usage, choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=fake_completion))
    res = LiteLLMClient(timeout=12.5, max_tokens=321, num_retries=1).complete([{"role": "user", "content": "hi"}], "m")
    assert res.text == "ok"
    assert seen["timeout"] == 12.5 and seen["max_tokens"] == 321 and seen["num_retries"] == 1


@pytest.mark.parametrize("kw", [dict(timeout=0), dict(max_tokens=0), dict(num_retries=-1)])
def test_litellm_client_rejects_unbounded_settings(kw):
    with pytest.raises(ValueError):
        LiteLLMClient(**kw)


# ---------------------------------------------------------------- check_config never prints values
def test_check_config_reports_presence_never_values(tmp_path, monkeypatch):
    for name in ("JWT_SECRET", "OPENAI_API_KEY", "GEMINI_API_KEY", "QDRANT_API_KEY", "NEO4J_PASSWORD", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "ENVIRONMENT=production\n"
        f"JWT_SECRET={STRONG}\n"
        "OPENAI_API_KEY=sk-test-NOT-A-REAL-KEY-000000000000  # gitleaks:allow\n"
        "NEO4J_PASSWORD=not-a-real-password-123\n", encoding="utf-8")
    lines, code = build_report(env)
    out = "\n".join(lines)
    assert code == 0
    assert STRONG not in out and "sk-test" not in out and "not-a-real-password" not in out
    assert re.search(r"OPENAI_API_KEY\s+PRESENT", out) and re.search(r"GEMINI_API_KEY\s+missing", out)


def test_check_config_fails_on_placeholder_in_production(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"JWT_SECRET={PLACEHOLDER}\n", encoding="utf-8")
    lines, code = build_report(env)
    assert code == 1 and "refuse to start" in "\n".join(lines)


# ---------------------------------------------------------------- anonymous requests must not build the service
def test_anonymous_request_gets_401_without_constructing_the_service(monkeypatch):
    built = []

    def spy(*_a, **_k):
        built.append(1)
        raise AssertionError("service must not be built for an unauthenticated request")

    monkeypatch.setattr("app.services.factory.build_service", spy)
    c = TestClient(create_app(settings=S()))   # no injected service: it would be built lazily on first use
    assert c.post("/query", json={"query": "x"}).status_code == 401
    assert c.post("/query", json={"query": "x"}, headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert c.put("/admin/documents/d/acl", json={}, headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert not built
