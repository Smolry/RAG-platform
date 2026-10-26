"""Restricted HTTPS fetcher for the DevTrace corpus. The ONLY code in the project that may fetch remote documents.

Threat model: a source list is trusted configuration, but responses are untrusted. The fetcher therefore
  * accepts https URLs only, on an exact-match host allowlist (no wildcards, no userinfo, port 443 only);
  * resolves the host and refuses any non-public address (loopback, private, link-local, CGNAT, reserved, multicast,
    including IPv4-mapped IPv6) - protects against allowlist entries that are poisoned in DNS;
  * follows redirects MANUALLY (max `max_redirects`), re-validating every hop against the same rules;
  * enforces connect/read timeouts and a hard response-size cap while streaming (a lying Content-Length does not help);
  * accepts only text content types and strict UTF-8;
  * returns status codes and URLs in errors, never response bodies, headers, or environment details;
  * never executes, evaluates or renders what it downloads.
Residual risk (documented in docs/SECURITY.md): DNS can change between our check and the TCP connect. For an allowlisted
HTTPS host, a rebinding target would also have to present a valid certificate for that hostname, which it cannot.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[[str], list[str]]
ALLOWED_CONTENT_TYPES = ("text/plain", "text/markdown")


class FetchPolicyError(RuntimeError):
    """A URL, redirect, or response violated policy. Safe to show: contains no response content."""


@dataclass(frozen=True)
class FetchPolicy:
    allowed_hosts: frozenset[str]
    max_bytes: int = 3_000_000
    connect_timeout: float = 10.0
    read_timeout: float = 20.0
    max_redirects: int = 3
    min_interval_seconds: float = 0.25   # politeness: sequential requests, at least this far apart

    def __post_init__(self) -> None:
        if not self.allowed_hosts or any(h != h.lower() or "*" in h or "/" in h for h in self.allowed_hosts):
            raise ValueError("allowed_hosts must be non-empty, lowercase, exact hostnames")
        if self.max_bytes <= 0 or self.max_redirects < 0:
            raise ValueError("max_bytes must be positive and max_redirects non-negative")


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    sha256: str
    n_bytes: int
    fetched_at: str


def default_resolver(host: str) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def is_public_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global and not ip.is_multicast)


def validate_url(url: str, policy: FetchPolicy, resolver: Resolver = default_resolver) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise FetchPolicyError("malformed URL") from e
    if parts.scheme != "https":
        raise FetchPolicyError("only https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise FetchPolicyError("credentials in URLs are not allowed")
    if port not in (None, 443):
        raise FetchPolicyError("only port 443 is allowed")
    host = (parts.hostname or "").lower()
    if host not in policy.allowed_hosts:
        raise FetchPolicyError(f"host not on allowlist: {host or '<none>'}")
    try:
        addrs = resolver(host)
    except OSError as e:
        raise FetchPolicyError(f"could not resolve host: {host}") from e
    if not addrs:
        raise FetchPolicyError(f"host did not resolve: {host}")
    bad = [a for a in addrs if not is_public_ip(a)]
    if bad:
        raise FetchPolicyError(f"host {host} resolves to a non-public address; refusing")
    return url


def fetch_text(url: str, policy: FetchPolicy, *, client: httpx.Client | None = None,
               resolver: Resolver = default_resolver) -> FetchResult:
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(policy.read_timeout, connect=policy.connect_timeout),
                                    follow_redirects=False)
    try:
        current = url
        for _hop in range(policy.max_redirects + 1):
            validate_url(current, policy, resolver)
            with client.stream("GET", current, follow_redirects=False,
                               headers={"Accept": "text/plain, text/markdown", "User-Agent": "devtrace-corpus-fetcher/0.1"}) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        raise FetchPolicyError("redirect without Location")
                    current = urljoin(current, loc)   # re-validated at the top of the loop
                    continue
                if r.status_code != 200:
                    raise FetchPolicyError(f"HTTP {r.status_code} for {current}")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_CONTENT_TYPES:
                    raise FetchPolicyError(f"unexpected content type {ctype or '<none>'}")
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > policy.max_bytes:
                    raise FetchPolicyError("response larger than limit")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > policy.max_bytes:
                        raise FetchPolicyError("response larger than limit")
                raw = bytes(buf)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                raise FetchPolicyError("response is not valid UTF-8") from e
            return FetchResult(current, text, hashlib.sha256(raw).hexdigest(), len(raw),
                               datetime.now(timezone.utc).isoformat(timespec="seconds"))
        raise FetchPolicyError("too many redirects")
    except httpx.HTTPError as e:
        raise FetchPolicyError(f"network error: {type(e).__name__}") from None   # type only: no URLs/bodies/headers
    finally:
        if own:
            client.close()


class Throttle:
    """Keeps requests at least `interval` seconds apart (sequential fetching, no concurrency)."""

    def __init__(self, interval: float, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep, self._last = interval, clock, sleep, None

    def wait(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.interval:
            self.sleep(self.interval - (now - self._last))
        self._last = self.clock()
