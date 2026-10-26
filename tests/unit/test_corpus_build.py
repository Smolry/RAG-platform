import json
from pathlib import Path

import httpx
import pytest

from app.core.models import ChunkRecord
from app.devtrace.corpus.build import (SourceConfigError, build_corpus, build_file_chunks, cached_or_fetched, chunk_id,
                                       compute_snapshot_id, load_sources, raw_url)
from app.devtrace.corpus.ingest import ingest_snapshot
from app.devtrace.corpus.safe_fetch import FetchPolicyError, FetchResult
from app.retrieval.embedder import HashEmbedder
from app.retrieval.store import VectorStore, make_client
from app.security.acl import acl_filter
from app.security.auth import Principal

SHA = "a" * 40
SRC = {"id": "fastapi", "library": "fastapi", "display": "FastAPI", "repo": "fastapi/fastapi", "commit": SHA, "ref": "0.162.0",
       "license": "MIT", "copyright": "c", "docs_base_url": "https://fastapi.tiangolo.com/", "files": []}
RELEASES = """# Release Notes

## Latest Changes

* unreleased thing.

## 0.162.0 (2026-10-23)

Intro of this release.

### Breaking Changes

* Restore default handling.

## 0.100.0b1 (2023-07-01)

* a pre-release.

## 0.100.0 (2023-07-06)

### Features

* Pydantic v2.

## 0.99.1 (2023-06-30)

### Fixes

* too old.
"""
REL_FILE = {"path": "docs/en/docs/release-notes.md", "url_path": "release-notes", "source_type": "release_notes", "min_version": "0.100.0"}
DOC_FILE = {"path": "docs/en/docs/tutorial/body.md", "url_path": "tutorial/body", "source_type": "docs"}


def test_release_sections_get_version_and_date_from_heading_only():
    chunks, stats = build_file_chunks(SRC, REL_FILE, RELEASES, "t", "snap")
    versions = {(c["library_version"], c["published_at"]) for c in chunks}
    assert versions == {("0.162.0", "2026-10-23"), ("0.100.0", "2023-07-06")}
    assert all(c["version_kind"] == "introduced_in" for c in chunks)
    assert stats["dropped_sections"] >= 3          # Latest Changes, pre-release, too old (and the h1 intro)
    assert not any("pre-release" in c["text"] or "too old" in c["text"] or "unreleased" in c["text"] for c in chunks)


def test_breaking_changes_is_its_own_chunk_with_breadcrumb():
    chunks, _ = build_file_chunks(SRC, REL_FILE, RELEASES, "t", "snap")
    bc = next(c for c in chunks if c["section_heading"] == "Breaking Changes")
    assert bc["text"].startswith("FastAPI | Release Notes > 0.162.0 (2026-10-23) > Breaking Changes")


def test_docs_pages_are_versioned_as_of_ref_without_invented_dates():
    chunks, _ = build_file_chunks(SRC, DOC_FILE, "# Body { #body }\n\nText.\n\n## Part { #part }\n\nMore.\n", "t", "snap")
    assert {c["library_version"] for c in chunks} == {"0.162.0"} and {c["version_kind"] for c in chunks} == {"docs_as_of"}
    assert all(c["published_at"] is None for c in chunks)
    part = next(c for c in chunks if c["section_anchor"] == "part")
    assert part["source_url"] == "https://fastapi.tiangolo.com/tutorial/body/#part"
    assert part["provenance_url"] == raw_url(SRC, DOC_FILE["path"]) and SHA in part["provenance_url"]
    assert part["content_hash"] and part["license"] == "MIT" and part["document_id"] == "fastapi/tutorial/body"


def test_every_chunk_validates_as_chunk_record():
    chunks, _ = build_file_chunks(SRC, DOC_FILE, "# B\n\nText.\n", "t", "snap")
    for c in chunks:
        ChunkRecord(**{k: v for k, v in c.items() if k in ChunkRecord.model_fields}, tenant_id="x", classification="public")


def test_duplicate_headings_get_distinct_stable_ids():
    md = "# T\n\n## Same\n\nfirst\n\n## Same\n\nsecond\n"
    a, _ = build_file_chunks(SRC, DOC_FILE, md, "t", "s")
    assert len({c["chunk_id"] for c in a}) == len(a) == 2
    b, _ = build_file_chunks(SRC, DOC_FILE, md, "t", "s")
    assert [c["chunk_id"] for c in a] == [c["chunk_id"] for c in b]


def test_chunk_id_is_stable_when_an_unrelated_section_changes():
    v1 = "# T\n\n## A\n\nalpha\n\n## B\n\nbeta\n"
    v2 = "# T\n\n## A\n\nalpha CHANGED and longer\n\n## B\n\nbeta\n"
    a = {c["section_heading"]: c for c in build_file_chunks(SRC, DOC_FILE, v1, "t", "s")[0]}
    b = {c["section_heading"]: c for c in build_file_chunks(SRC, DOC_FILE, v2, "t", "s")[0]}
    assert a["B"]["chunk_id"] == b["B"]["chunk_id"] and a["B"]["content_hash"] == b["B"]["content_hash"]
    assert a["A"]["chunk_id"] == b["A"]["chunk_id"] and a["A"]["content_hash"] != b["A"]["content_hash"]
    assert chunk_id("d", "k", 0) != chunk_id("d", "k", 1)


def test_snapshot_id_depends_on_bytes_and_chunker_params_only():
    h = {"a": "1" * 64, "b": "2" * 64}
    assert compute_snapshot_id(h, 180) == compute_snapshot_id(dict(reversed(list(h.items()))), 180)
    assert compute_snapshot_id(h, 180) != compute_snapshot_id({**h, "b": "3" * 64}, 180)
    assert compute_snapshot_id(h, 180) != compute_snapshot_id(h, 120)


def _cfg(files):
    return {"allowed_hosts": ["raw.githubusercontent.com"], "sources": [{**SRC, "files": files}]}


def _fetcher(texts):
    import hashlib
    def get(src, f):
        t = texts[f["path"]]
        return FetchResult("u", t, hashlib.sha256(t.encode()).hexdigest(), len(t), "2026-01-01T00:00:00+00:00")
    return get


def test_build_is_reproducible_and_manifest_is_complete():
    texts = {REL_FILE["path"]: RELEASES, DOC_FILE["path"]: "# B\n\nText.\n"}
    a = build_corpus(_cfg([DOC_FILE, REL_FILE]), _fetcher(texts))
    b = build_corpus(_cfg([DOC_FILE, REL_FILE]), _fetcher(texts))
    assert a.manifest["snapshot_id"] == b.manifest["snapshot_id"] and a.chunks == b.chunks
    assert all(c["snapshot_id"] == a.manifest["snapshot_id"] for c in a.chunks)
    m = a.manifest
    assert m["totals"]["chunks"] == len(a.chunks) and {f["path"] for f in m["files"]} == set(texts)
    assert all(len(f["sha256"]) == 64 and SHA in f["url"] for f in m["files"])
    assert "token" not in json.dumps(m).lower() and "authorization" not in json.dumps(m).lower()
    changed = build_corpus(_cfg([DOC_FILE, REL_FILE]), _fetcher({**texts, DOC_FILE["path"]: "# B\n\nText!\n"}))
    assert changed.manifest["snapshot_id"] != m["snapshot_id"]


# ----------------------------------------------------------------------------- source config safety
def _write(tmp_path, **over):
    import yaml
    src = {**SRC, "files": [DOC_FILE], **over}
    p = tmp_path / "s.yaml"; p.write_text(yaml.safe_dump({"allowed_hosts": ["raw.githubusercontent.com"], "sources": [src]}))
    return p


def test_load_sources_accepts_valid(tmp_path):
    assert load_sources(_write(tmp_path))["sources"][0]["id"] == "fastapi"


@pytest.mark.parametrize("over", [
    {"commit": "0.162.0"}, {"commit": "main"}, {"commit": "A" * 40}, {"repo": "../etc/passwd"}, {"repo": "a/b/c"},
    {"files": [{"path": "../../etc/passwd.md", "url_path": "x"}]}, {"files": [{"path": "/abs.md", "url_path": "x"}]},
    {"files": [{"path": "a\\b.md", "url_path": "x"}]}, {"files": [{"path": "script.py", "url_path": "x"}]},
])
def test_load_sources_rejects_unsafe_entries(tmp_path, over):
    with pytest.raises(SourceConfigError):
        load_sources(_write(tmp_path, **over))


def test_committed_sources_file_is_valid_and_pinned():
    cfg = load_sources(Path("data/devtrace/sources.yaml"))
    assert cfg["allowed_hosts"] == ["raw.githubusercontent.com"]
    for s in cfg["sources"]:
        assert s["license"] == "MIT"
        for f in s["files"]:
            assert raw_url(s, f["path"]).startswith("https://raw.githubusercontent.com/") and s["commit"] in raw_url(s, f["path"])


# ----------------------------------------------------------------------------- cache behaviour
def _mock_client(calls):
    def h(req):
        calls.append(str(req.url))
        return httpx.Response(200, content=b"# T\n\nbody\n", headers={"content-type": "text/plain"})
    return httpx.Client(transport=httpx.MockTransport(h), follow_redirects=False)


def test_cache_miss_fetches_once_then_serves_from_disk(tmp_path):
    cfg = _cfg([DOC_FILE]); calls = []
    get = cached_or_fetched(cfg, tmp_path, fetch=True, resolver=lambda h: ["185.199.110.133"], client=_mock_client(calls))
    r1 = get(cfg["sources"][0], DOC_FILE); r2 = get(cfg["sources"][0], DOC_FILE)
    assert len(calls) == 1 and r1.sha256 == r2.sha256


def test_no_fetch_mode_never_touches_network(tmp_path):
    cfg = _cfg([DOC_FILE])
    get = cached_or_fetched(cfg, tmp_path, fetch=False)
    with pytest.raises(FetchPolicyError, match="not cached"):
        get(cfg["sources"][0], DOC_FILE)


def test_corrupted_cache_is_detected(tmp_path):
    cfg = _cfg([DOC_FILE]); calls = []
    get = cached_or_fetched(cfg, tmp_path, fetch=True, resolver=lambda h: ["185.199.110.133"], client=_mock_client(calls))
    get(cfg["sources"][0], DOC_FILE)
    f = next(tmp_path.rglob("body.md")); f.write_text("tampered", encoding="utf-8")
    with pytest.raises(FetchPolicyError, match="corrupted"):
        get(cfg["sources"][0], DOC_FILE)


# ----------------------------------------------------------------------------- ingestion
def _snapshot(text, sid):
    chunks, _ = build_file_chunks(SRC, DOC_FILE, text, "t", sid)
    return chunks


def _store():
    return VectorStore(make_client(":memory:"), "chunks")


def test_ingest_stores_provenance_and_enforces_tenant_acl():
    store, emb = _store(), HashEmbedder()
    n = ingest_snapshot(_snapshot("# Body\n\nrequest body validation error\n", "s1"), "s1", store, emb, tenant_id="devtrace")
    assert n >= 1
    me = Principal("u", "devtrace", ("user",))
    hit = store.search(emb.embed(["validation error"])[0], 5, acl_filter(me))
    assert hit and hit[0].payload["snapshot_id"] == "s1" and hit[0].payload["provenance_url"].startswith("https://raw.")
    assert hit[0].payload["library"] == "fastapi" and hit[0].payload["corpus_version"] == "s1"
    assert store.search(emb.embed(["validation error"])[0], 5, acl_filter(Principal("x", "other-tenant", ("user",)))) == []
    assert store.search(emb.embed(["validation error"])[0], 5, acl_filter(Principal("x", "devtrace", ("contractor",)))) == []


def test_new_snapshot_replaces_old_one_for_that_tenant_only():
    store, emb = _store(), HashEmbedder()
    ingest_snapshot(_snapshot("# A\n\nold text\n", "s1"), "s1", store, emb, tenant_id="devtrace")
    ingest_snapshot(_snapshot("# A\n\nsame doc other tenant\n", "s1"), "s1", store, emb, tenant_id="keep")
    ingest_snapshot(_snapshot("# A\n\nnew text\n", "s2"), "s2", store, emb, tenant_id="devtrace")
    snaps = {(p["tenant_id"], p["snapshot_id"]) for _, p in store.iter_chunks()}
    assert snaps == {("devtrace", "s2"), ("keep", "s1")}


def test_ingest_refuses_empty_and_mismatched_snapshots():
    store, emb = _store(), HashEmbedder()
    with pytest.raises(ValueError):
        ingest_snapshot([], "s1", store, emb)
    with pytest.raises(ValueError, match="snapshot"):
        ingest_snapshot(_snapshot("# A\n\ntext\n", "s1"), "other", store, emb)


def test_acl_comes_from_the_call_not_from_chunk_data():
    store, emb = _store(), HashEmbedder()
    chunks = _snapshot("# A\n\ntext\n", "s1")
    for c in chunks:
        c.update({"allowed_roles": ["contractor"], "tenant_id": "attacker", "allowed_users": ["evil"]})
    ingest_snapshot(chunks, "s1", store, emb, tenant_id="devtrace", allowed_roles=["user"])
    _, payload = next(iter(store.iter_chunks()))
    assert payload["tenant_id"] == "devtrace" and payload["allowed_roles"] == ["user"] and payload["allowed_users"] == []


def test_same_public_corpus_in_two_tenants_does_not_collide():
    store, emb = _store(), HashEmbedder()
    ch = _snapshot("# A\n\nshared public text\n", "s1")
    ingest_snapshot(ch, "s1", store, emb, tenant_id="t1")
    ingest_snapshot(ch, "s1", store, emb, tenant_id="t2")
    rows = list(store.iter_chunks())
    assert len(rows) == 2 * len(ch) and len({cid for cid, _ in rows}) == len(rows)
    assert {p["tenant_id"] for _, p in rows} == {"t1", "t2"} and all(p["source_chunk_id"] for _, p in rows)


def test_release_subsections_on_the_skip_list_are_dropped_not_hidden():
    md = ("# Release Notes\n\n## 0.150.0 (2025-01-01)\n\n### Fixes\n\n* real fix.\n\n### Translations\n\n* Add Klingon.\n\n"
          "### Internal\n\n* bump ci.\n")
    f = {**REL_FILE, "skip_headings": ["Translations", "Internal"]}
    chunks, stats = build_file_chunks(SRC, f, md, "t", "s")
    assert [c["section_heading"] for c in chunks] == ["Fixes"] and stats["dropped_sections"] >= 2
    kept, _ = build_file_chunks(SRC, REL_FILE, md, "t", "s")                    # without the config nothing is silently dropped
    assert {"Translations", "Internal", "Fixes"} <= {c["section_heading"] for c in kept}


def test_snapshot_id_changes_when_chunking_behaviour_changes_even_if_bytes_do_not():
    """Regression: the id used to hash only file bytes + params, so a changed skip list left it unchanged."""
    texts = {REL_FILE["path"]: RELEASES + "\n### Translations\n\n* x\n"}
    # put the Translations subsection under a kept release so the skip list actually matters
    texts[REL_FILE["path"]] = RELEASES.replace("### Breaking Changes", "### Translations\n\n* Add Klingon.\n\n### Breaking Changes")
    plain = build_corpus(_cfg([REL_FILE]), _fetcher(texts))
    skipping = build_corpus(_cfg([{**REL_FILE, "skip_headings": ["Translations"]}]), _fetcher(texts))
    assert plain.manifest["snapshot_id"] != skipping.manifest["snapshot_id"]
    assert len(plain.chunks) > len(skipping.chunks)
    assert {c["snapshot_id"] for c in skipping.chunks} == {skipping.manifest["snapshot_id"]}
