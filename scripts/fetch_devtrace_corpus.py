"""Build the DevTrace corpus snapshot from the pinned sources.

  python -m scripts.fetch_devtrace_corpus               # fetch (cached) + build + write snapshot + manifest
  python -m scripts.fetch_devtrace_corpus --no-fetch    # offline rebuild from the local cache only
  python -m scripts.fetch_devtrace_corpus --dry-run     # list what WOULD be fetched; no network

Contacts only the hosts in data/devtrace/sources.yaml `allowed_hosts` (raw.githubusercontent.com), sequentially, ~4 requests
per second at most. No credentials are used or needed. Cost: none.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.devtrace.corpus.build import build_corpus, cached_or_fetched, load_sources, raw_url
from app.devtrace.corpus.safe_fetch import FetchPolicyError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", type=Path, default=Path("data/devtrace/sources.yaml"))
    ap.add_argument("--cache-dir", type=Path, default=Path("data/devtrace/cache"))
    ap.add_argument("--out-dir", type=Path, default=Path("data/devtrace/snapshots"))
    ap.add_argument("--manifest", type=Path, default=Path("data/devtrace/manifest.json"))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="fail unless the built snapshot_id equals the committed manifest's")
    a = ap.parse_args()
    cfg = load_sources(a.sources)
    if a.dry_run:
        for s in cfg["sources"]:
            for f in s["files"]:
                print(raw_url(s, f["path"]))
        print(f"{sum(len(s['files']) for s in cfg['sources'])} files, hosts: {cfg['allowed_hosts']}")
        return 0
    try:
        res = build_corpus(cfg, cached_or_fetched(cfg, a.cache_dir, fetch=not a.no_fetch))
    except FetchPolicyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sid = res.manifest["snapshot_id"]
    if a.verify:
        want = json.loads(a.manifest.read_text(encoding="utf-8"))["snapshot_id"]
        print(f"built {sid}, committed {want}: {'MATCH' if sid == want else 'MISMATCH'}")
        return 0 if sid == want else 2
    out = a.out_dir / sid
    out.mkdir(parents=True, exist_ok=True)
    (out / "chunks.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in res.chunks) + "\n", encoding="utf-8")
    a.manifest.write_text(json.dumps(res.manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    t = res.manifest["totals"]
    print(f"snapshot {sid}: {t['files']} files, {t['chunks']} chunks ({t['oversize_chunks']} oversize) -> {out / 'chunks.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
