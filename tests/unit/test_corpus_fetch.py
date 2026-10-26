"""SSRF / size / redirect / content policy for the corpus fetcher. No network: httpx.MockTransport + injected resolver."""
import httpx
import pytest

from app.devtrace.corpus.safe_fetch import FetchPolicy, FetchPolicyError, Throttle, fetch_text, is_public_ip, validate_url

HOST = "raw.githubusercontent.com"
PUBLIC = lambda h: ["185.199.110.133"]
POLICY = FetchPolicy(allowed_hosts=frozenset({HOST}), max_bytes=1000, max_redirects=2)
OK = f"https://{HOST}/o/r/{'a' * 40}/x.md"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def text_resp(body=b"# hi\n", ctype="text/plain; charset=utf-8", status=200, headers=None):
    return httpx.Response(status, content=body, headers={"content-type": ctype, **(headers or {})})


# ----------------------------------------------------------------------------- URL validation
@pytest.mark.parametrize("url", [
    f"http://{HOST}/x.md",                              # not https
    f"https://{HOST}.evil.example/x.md",                # look-alike suffix
    f"https://evil-{HOST}/x.md",                        # look-alike prefix
    "https://evil.example/x.md",
    "https://127.0.0.1/x.md", "https://[::1]/x.md", "https://169.254.169.254/latest/meta-data",
    f"https://user:pw@{HOST}/x.md",                     # userinfo
    f"https://{HOST}:8443/x.md",                        # non-443 port
    f"https://{HOST}@evil.example/x.md",                # userinfo trick
    "file:///etc/passwd", "ftp://raw.githubusercontent.com/x", "//raw.githubusercontent.com/x", "", "https://",
])
def test_rejected_urls(url):
    with pytest.raises(FetchPolicyError):
        validate_url(url, POLICY, PUBLIC)


def test_allowed_url_and_case_insensitive_host():
    assert validate_url(OK, POLICY, PUBLIC) == OK
    validate_url(f"https://{HOST.upper()}/x.md", POLICY, PUBLIC)


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.0.9", "169.254.169.254", "100.64.0.1",
                                  "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1", "224.0.0.1"])
def test_non_public_addresses_refused(addr):
    assert not is_public_ip(addr)
    with pytest.raises(FetchPolicyError, match="non-public"):
        validate_url(OK, POLICY, lambda h: [addr])


def test_one_bad_address_among_good_ones_is_refused():
    with pytest.raises(FetchPolicyError):
        validate_url(OK, POLICY, lambda h: ["185.199.110.133", "10.0.0.1"])


def test_public_addresses_accepted():
    assert is_public_ip("185.199.110.133") and is_public_ip("2606:50c0:8000::154")


def test_dns_failure_and_empty_answer_refused():
    def boom(h): raise OSError("dns down")
    with pytest.raises(FetchPolicyError):
        validate_url(OK, POLICY, boom)
    with pytest.raises(FetchPolicyError):
        validate_url(OK, POLICY, lambda h: [])


@pytest.mark.parametrize("hosts", [frozenset(), frozenset({"*.github.com"}), frozenset({"Raw.GitHub.com"}), frozenset({"a/b"})])
def test_policy_rejects_sloppy_allowlists(hosts):
    with pytest.raises(ValueError):
        FetchPolicy(allowed_hosts=hosts)


# ----------------------------------------------------------------------------- fetching
def test_successful_fetch_hashes_bytes():
    r = fetch_text(OK, POLICY, client=client(lambda req: text_resp(b"# hello\n")), resolver=PUBLIC)
    assert r.text == "# hello\n" and r.n_bytes == 8 and len(r.sha256) == 64


def test_http_error_does_not_leak_body():
    leak = b"SECRET-INTERNAL-PAGE token=abc123"
    with pytest.raises(FetchPolicyError) as e:
        fetch_text(OK, POLICY, client=client(lambda req: httpx.Response(500, content=leak)), resolver=PUBLIC)
    assert "500" in str(e.value) and "SECRET" not in str(e.value) and "abc123" not in str(e.value)


def test_redirect_to_other_host_is_blocked():
    def h(req):
        if req.url.host == HOST:
            return httpx.Response(302, headers={"location": "https://evil.example/steal"})
        raise AssertionError("must never request the redirect target")
    with pytest.raises(FetchPolicyError, match="allowlist"):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


def test_redirect_to_metadata_ip_is_blocked():
    h = lambda req: httpx.Response(307, headers={"location": "https://169.254.169.254/latest/meta-data/"})
    with pytest.raises(FetchPolicyError):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


def test_redirect_downgrade_to_http_is_blocked():
    h = lambda req: httpx.Response(302, headers={"location": f"http://{HOST}/x.md"})
    with pytest.raises(FetchPolicyError, match="https"):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


def test_same_host_redirect_followed_and_revalidated():
    seen = []

    def h(req):
        seen.append(str(req.url))
        return httpx.Response(301, headers={"location": "/moved.md"}) if req.url.path.endswith("x.md") else text_resp()
    r = fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)
    assert len(seen) == 2 and r.url.endswith("/moved.md")


def test_redirect_loop_is_bounded():
    h = lambda req: httpx.Response(302, headers={"location": OK})
    with pytest.raises(FetchPolicyError, match="too many redirects"):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


def test_redirect_without_location_rejected():
    with pytest.raises(FetchPolicyError):
        fetch_text(OK, POLICY, client=client(lambda req: httpx.Response(302)), resolver=PUBLIC)


def test_oversized_streamed_body_rejected_even_without_content_length():
    def gen():
        for _ in range(50):
            yield b"x" * 100        # 5000 bytes, no Content-Length header (chunked)
    h = lambda req: httpx.Response(200, content=gen(), headers={"content-type": "text/plain"})
    with pytest.raises(FetchPolicyError, match="larger than limit"):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


def test_oversized_declared_length_rejected_before_reading():
    h = lambda req: text_resp(b"x", headers={"content-length": "999999999"})
    with pytest.raises(FetchPolicyError, match="larger than limit"):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


@pytest.mark.parametrize("ctype", ["text/html", "application/json", "application/octet-stream", "application/x-sh", ""])
def test_non_text_content_types_rejected(ctype):
    h = lambda req: httpx.Response(200, content=b"<script>alert(1)</script>", headers={"content-type": ctype} if ctype else {})
    with pytest.raises(FetchPolicyError, match="content type"):
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)


def test_invalid_utf8_rejected():
    with pytest.raises(FetchPolicyError, match="UTF-8"):
        fetch_text(OK, POLICY, client=client(lambda req: text_resp(b"\xff\xfe\x00bad")), resolver=PUBLIC)


def test_network_errors_report_type_only():
    def h(req): raise httpx.ConnectError("failed to connect to https://user:hunter2@internal.example/")
    with pytest.raises(FetchPolicyError) as e:
        fetch_text(OK, POLICY, client=client(h), resolver=PUBLIC)
    assert "ConnectError" in str(e.value) and "hunter2" not in str(e.value) and "internal.example" not in str(e.value)


def test_downloaded_content_is_never_executed_or_interpreted():
    payload = b"__import__('os').system('echo pwned > /tmp/pwned_by_corpus')\n"
    r = fetch_text(OK, POLICY, client=client(lambda req: text_resp(payload)), resolver=PUBLIC)
    assert r.text.startswith("__import__") and not __import__("os").path.exists("/tmp/pwned_by_corpus")


def test_throttle_spaces_requests():
    t = [0.0]; slept = []
    th = Throttle(0.5, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    th.wait(); t[0] += 0.1; th.wait()
    assert slept and abs(slept[0] - 0.4) < 1e-9
