"""Corpus build: pinned sources + fetched files -> chunks + a reproducible manifest.

Inputs are explicit (file lists pinned to a 40-hex commit SHA), so there is no crawling and no URL is ever derived from
user input. `snapshot_id` hashes the exact bytes of every file, the chunker parameters AND the resulting chunks: the same
snapshot_id means the same chunks, ids and hashes (a behaviour change in chunking changes the id).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from app.devtrace.corpus.markdown_chunker import CHUNKER_VERSION, DEFAULT_MAX_WORDS, Chunk, chunk_markdown
from app.devtrace.corpus.safe_fetch import FetchPolicy, FetchPolicyError, FetchResult, Throttle, fetch_text

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_HEADING = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)(?P<pre>(?:a|b|rc)\d+)?\s+\((?P<date>\d{4}-\d{2}-\d{2})\)$")
RAW_HOST = "raw.githubusercontent.com"
REQUIRED_SOURCE_KEYS = {"id", "library", "display", "repo", "commit", "ref", "license", "copyright", "docs_base_url", "files"}


class SourceConfigError(ValueError):
    pass


def load_sources(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = set()
    for s in cfg.get("sources", []):
        missing = REQUIRED_SOURCE_KEYS - set(s)
        if missing:
            raise SourceConfigError(f"source {s.get('id')}: missing keys {sorted(missing)}")
        if not SHA_RE.match(s["commit"]):
            raise SourceConfigError(f"source {s['id']}: commit must be a full 40-hex SHA (tags can move)")
        if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", s["repo"]):
            raise SourceConfigError(f"source {s['id']}: bad repo")
        if s["id"] in ids:
            raise SourceConfigError(f"duplicate source id {s['id']}")
        ids.add(s["id"])
        for f in s["files"]:
            p = f["path"]
            if p.startswith("/") or ".." in p.split("/") or "\\" in p or not p.endswith(".md"):
                raise SourceConfigError(f"source {s['id']}: unsafe or non-markdown path {p!r}")
    return cfg


def raw_url(src: dict, path: str) -> str:
    return f"https://{RAW_HOST}/{src['repo']}/{src['commit']}/{path}"


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _slug(parts: list[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "-", " ".join(parts).lower()).strip("-")[:120] or "root"


def chunk_id(document_id: str, section_key: str, i: int) -> str:
    """Stable across rebuilds and snapshots for the same section (does not depend on neighbouring sections)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}#{section_key}#{i}"))


def _public_url(src: dict, f: dict, anchor: str | None) -> str:
    base = src["docs_base_url"].rstrip("/") + "/" + f["url_path"].strip("/") + ("/" if f["url_path"].strip("/") else "")
    return base + (f"#{anchor}" if anchor else "")


def build_file_chunks(src: dict, f: dict, text: str, fetched_at: str, snapshot_id: str | None,
                      max_words: int = DEFAULT_MAX_WORDS) -> tuple[list[dict], dict]:
    """Chunk one fetched file. Returns (chunk dicts, per-file stats)."""
    kind = f.get("source_type", "docs")
    is_release = kind == "release_notes"
    document_id = f"{src['library']}/{f['url_path'].strip('/')}"
    raw = raw_url(src, f["path"])
    out: list[dict] = []
    dup: dict[str, int] = {}
    dropped = oversize = 0
    # Label only (not a source fact): explicit `title` from config, else the page's own file name, humanised.
    page_label = f.get("title") or f["url_path"].strip("/").split("/")[-1].replace("_", " ").replace("-", " ").capitalize()
    for c in chunk_markdown(text, max_words=max_words, prefix=f"{src['display']} | ", root_title=page_label):
        version, published, vkind = src["ref"], None, "docs_as_of"
        if is_release:
            m = next((RELEASE_HEADING.match(p) for p in c.section_path if RELEASE_HEADING.match(p)), None)
            if m is None or (m["pre"] and f.get("stable_only", True)) or \
                    ("min_version" in f and _ver(m["version"]) < _ver(f["min_version"])):
                dropped += 1
                continue
            if any(h in f.get("skip_headings", []) for h in c.section_path):   # e.g. Translations / Internal PR lists
                dropped += 1
                continue
            version, published, vkind = m["version"] + (m["pre"] or ""), m["date"], "introduced_in"
        key = _slug(c.section_path)
        if c.index_in_section == 0:
            dup[key] = dup.get(key, -1) + 1   # disambiguate repeated headings within one document
        n = dup[key]
        skey = key + (f"~{n}" if n else "")
        oversize += c.oversize
        out.append({
            "chunk_id": chunk_id(document_id, skey, c.index_in_section), "document_id": document_id,
            "chunk_index": len(out), "text": c.text, "section_path": c.section_path,
            "section_heading": c.section_path[-1] if c.section_path else None, "section_anchor": c.section_anchor,
            "source_url": _public_url(src, f, c.section_anchor), "provenance_url": raw,
            "source_title": f.get("title") or (c.section_path[0] if c.section_path else document_id),
            "source_type": kind, "library": src["library"], "library_version": version, "version_kind": vkind,
            "published_at": published, "fetched_at": fetched_at, "content_hash": hashlib.sha256(c.text.encode()).hexdigest(),
            "snapshot_id": snapshot_id, "license": src["license"], "oversize": c.oversize})
    return out, {"chunks": len(out), "dropped_sections": dropped, "oversize_chunks": oversize}


def compute_snapshot_id(file_hashes: dict[str, str], max_words: int, chunk_digests: list[tuple[str, str]] | None = None) -> str:
    """Identity of the corpus snapshot = exact source bytes + chunker parameters + the chunks they produced.

    Hashing the OUTPUT (chunk id + content hash) as well as the inputs means that any change in chunking behaviour,
    skip lists or version filters changes the id, even if the source bytes and CHUNKER_VERSION did not."""
    h = hashlib.sha256(f"chunker={CHUNKER_VERSION};max_words={max_words}\n".encode())
    for k in sorted(file_hashes):
        h.update(f"{k}={file_hashes[k]}\n".encode())
    for cid, ch in sorted(chunk_digests or []):
        h.update(f"{cid}:{ch}\n".encode())
    return h.hexdigest()[:16]


@dataclass
class BuildResult:
    chunks: list[dict]
    manifest: dict


def build_corpus(cfg: dict, get_text: Callable[[dict, dict], FetchResult], max_words: int = DEFAULT_MAX_WORDS) -> BuildResult:
    """`get_text(src, file)` supplies each file (network fetch or local cache). Two passes: hash everything first so the
    snapshot_id can be stamped into every chunk."""
    fetched: list[tuple[dict, dict, FetchResult]] = [(s, f, get_text(s, f)) for s in cfg["sources"] for f in s["files"]]
    chunks: list[dict] = []
    files_meta = []
    seen: set[str] = set()
    for s, f, r in fetched:
        cs, st = build_file_chunks(s, f, r.text, r.fetched_at, None, max_words)   # snapshot id is computed from these, then stamped
        for c in cs:
            if c["chunk_id"] in seen:
                raise SourceConfigError(f"duplicate chunk id in {c['document_id']} ({c['section_path']})")
            seen.add(c["chunk_id"])
        for i, c in enumerate(cs):
            c["chunk_index"] = i   # position within the document
        chunks += cs
        files_meta.append({"source_id": s["id"], "path": f["path"], "url": raw_url(s, f["path"]), "bytes": r.n_bytes,
                           "sha256": r.sha256, "fetched_at": r.fetched_at, **st})
    snapshot_id = compute_snapshot_id({f"{s['id']}:{s['commit']}:{f['path']}": r.sha256 for s, f, r in fetched}, max_words,
                                      [(c["chunk_id"], c["content_hash"]) for c in chunks])
    for c in chunks:
        c["snapshot_id"] = snapshot_id
    manifest = {
        "snapshot_id": snapshot_id, "chunker_version": CHUNKER_VERSION, "max_words": max_words,
        "totals": {"files": len(files_meta), "chunks": len(chunks),
                   "oversize_chunks": sum(m["oversize_chunks"] for m in files_meta)},
        "sources": [{k: s[k] for k in ("id", "library", "repo", "commit", "ref", "license", "copyright", "docs_base_url")}
                    for s in cfg["sources"]],
        "files": files_meta,
        "reproduce": "python -m scripts.fetch_devtrace_corpus --verify  (re-fetches pinned commits; snapshot_id must match)",
    }
    return BuildResult(chunks, manifest)


def cached_or_fetched(cfg: dict, cache_dir: Path, *, fetch: bool, resolver=None, client=None) -> Callable[[dict, dict], FetchResult]:
    """Disk-cached source access. With fetch=False nothing touches the network (offline rebuild)."""
    policy = FetchPolicy(allowed_hosts=frozenset(cfg["allowed_hosts"]))
    throttle = Throttle(policy.min_interval_seconds)
    kw = {"resolver": resolver} if resolver else {}

    def get(src: dict, f: dict) -> FetchResult:
        local = cache_dir / src["id"] / src["commit"] / f["path"]
        meta = local.with_suffix(local.suffix + ".meta.json")
        if local.is_file() and meta.is_file():
            m = json.loads(meta.read_text(encoding="utf-8"))
            text = local.read_text(encoding="utf-8")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != m["sha256"]:
                raise FetchPolicyError(f"cache entry corrupted: {f['path']}")
            return FetchResult(m["url"], text, m["sha256"], m["bytes"], m["fetched_at"])
        if not fetch:
            raise FetchPolicyError(f"not cached and --no-fetch given: {f['path']}")
        throttle.wait()
        r = fetch_text(raw_url(src, f["path"]), policy, client=client, **kw)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(r.text, encoding="utf-8", newline="\n")
        meta.write_text(json.dumps({"url": r.url, "sha256": r.sha256, "bytes": r.n_bytes, "fetched_at": r.fetched_at}), encoding="utf-8")
        return r
    return get
