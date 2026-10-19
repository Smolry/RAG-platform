import pytest
from pydantic import ValidationError

from app.core.chunking import chunk_text, make_chunk_id
from app.core.models import ChunkRecord
from app.generation.llm import PriceTable
from app.security.acl import acl_filter, is_authorized
from app.security.auth import Principal


def test_chunking_overlap_and_coverage():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=20)  # 75 words, 15 overlap
    assert all(len(c.split()) <= 75 for c in chunks)
    assert chunks[0].split()[-15:] == chunks[1].split()[:15]
    assert set(" ".join(chunks).split()) == set(text.split())  # nothing dropped


def test_chunking_edge_cases():
    assert chunk_text("") == []
    assert chunk_text("one two", 100, 10) == ["one two"]
    with pytest.raises(ValueError):
        chunk_text("x", 10, 10)


def test_chunk_ids_deterministic():
    assert make_chunk_id("a/b", 0) == make_chunk_id("a/b", 0) != make_chunk_id("a/b", 1)


@pytest.mark.parametrize("tenant", ["", "   ", None])
def test_ingestion_rejects_missing_tenant(tenant):
    with pytest.raises(ValidationError):
        ChunkRecord(chunk_id="x", document_id="d", chunk_index=0, text="t", tenant_id=tenant, classification="public")


def test_is_authorized_rules_and_fail_closed():
    p = Principal("u1", "t1", ("employee",))
    ok = {"tenant_id": "t1", "allowed_roles": ["employee"], "allowed_users": []}
    assert is_authorized(p, ok)
    assert is_authorized(p, {"tenant_id": "t1", "allowed_roles": [], "allowed_users": ["u1"]})
    assert not is_authorized(p, {**ok, "tenant_id": "t2"})
    assert not is_authorized(p, {**ok, "allowed_roles": ["hr"]})
    assert not is_authorized(p, {"allowed_roles": ["employee"]})  # missing tenant -> deny
    assert not is_authorized(p, {})


def test_acl_filter_shape_uses_token_identity():
    f = acl_filter(Principal("u1", "t1", ("a", "b")))
    assert f.must[0].key == "tenant_id" and f.must[0].match.value == "t1"
    assert {c.key for c in f.should} == {"allowed_users", "allowed_roles"}
    no_roles = acl_filter(Principal("u1", "t1", ()))
    assert [c.key for c in no_roles.should] == ["allowed_users"]


def test_price_table():
    pt = PriceTable({"m": {"input_per_1m": 1.0, "output_per_1m": 2.0}})
    assert pt.cost("m", 1_000_000, 500_000) == pytest.approx(2.0)
    assert pt.cost("unknown", 10, 10) == 0.0
