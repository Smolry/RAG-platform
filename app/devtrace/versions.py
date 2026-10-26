"""Version parsing and ordering for release-note filtering.

Versions are compared per LIBRARY (FastAPI 0.100.0 and Pydantic 2.0.0 are unrelated numbering schemes), so callers must always
filter by library as well. `version_key` maps MAJOR.MINOR.PATCH to a sortable int so Qdrant can apply an exact range filter
inside the ANN search (no over-fetching and post-filtering).

Pre-release tags (b1, rc2, ...) are accepted as input but ignored for ordering: 2.14.0b1 is treated as 2.14.0. The corpus holds
stable releases only, so this only matters for a user's installed version.
"""
from __future__ import annotations

import re

_VER = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})(?:(?:a|b|rc)\d{1,3})?$")
MAX_COMPONENT = 9999


class VersionError(ValueError):
    pass


def parse_version(v: str) -> tuple[int, int, int]:
    m = _VER.match((v or "").strip())
    if not m:
        raise VersionError("expected a version like 0.110.0 or v2.14.1")
    return int(m[1]), int(m[2]), int(m[3])


def version_key(v: str) -> int:
    major, minor, patch = parse_version(v)
    return major * 10**8 + minor * 10**4 + patch


def in_range(v: str, gt: str | None = None, lte: str | None = None) -> bool:
    """True if gt < v <= lte (either bound optional). Upgrade semantics: changes AFTER the old version, up to the new one."""
    k = version_key(v)
    return (gt is None or k > version_key(gt)) and (lte is None or k <= version_key(lte))
