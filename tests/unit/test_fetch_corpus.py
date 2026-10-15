import sys

import httpx
import pytest

import scripts.fetch_corpus as fetch_corpus


def response(status_code, content, headers=None):
    request = httpx.Request("GET", "https://example.test")
    return httpx.Response(
        status_code, content=content, headers=headers, request=request
    )


def test_fetch_extract_returns_wikipedia_extract(monkeypatch):
    monkeypatch.setattr(
        fetch_corpus.httpx,
        "get",
        lambda *args, **kwargs: response(
            200,
            b'{"query":{"pages":{"1":{"extract":"Article text"}}}}',
            {"content-type": "application/json"},
        ),
    )

    assert fetch_corpus.fetch_extract("Example") == "Article text"


def test_fetch_extract_retries_non_json_response(monkeypatch):
    replies = iter(
        [
            response(200, b"<html>temporary proxy error</html>", {"content-type": "text/html"}),
            response(200, b'{"query":{"pages":{"1":{"extract":"Article text"}}}}'),
        ]
    )
    sleeps = []
    monkeypatch.setattr(fetch_corpus.httpx, "get", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(fetch_corpus.time, "sleep", sleeps.append)

    assert fetch_corpus.fetch_extract("Example") == "Article text"
    assert sleeps == [1]


def test_fetch_extract_reports_non_json_failure_with_title_and_response(monkeypatch):
    monkeypatch.setattr(
        fetch_corpus.httpx,
        "get",
        lambda *args, **kwargs: response(200, b"<html>blocked</html>", {"content-type": "text/html"}),
    )
    monkeypatch.setattr(fetch_corpus.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match=r"Example.*HTTP 200.*text/html.*blocked"):
        fetch_corpus.fetch_extract("Example")


def test_fetch_extract_reports_http_failure(monkeypatch):
    monkeypatch.setattr(fetch_corpus.httpx, "get", lambda *args, **kwargs: response(403, b"Forbidden"))

    with pytest.raises(RuntimeError, match=r"HTTP 403.*Example.*Forbidden"):
        fetch_corpus.fetch_extract("Example")


def test_main_writes_unicode_extract_as_utf8(tmp_path, monkeypatch):
    titles_file = tmp_path / "titles.txt"
    titles_file.write_text("Unicode article\n", encoding="utf-8")
    output_dir = tmp_path / "raw"
    extract = ("Nonbreaking hyphen ‑. " * 150).strip()
    monkeypatch.setattr(fetch_corpus, "fetch_extract", lambda _: extract)
    monkeypatch.setattr(fetch_corpus.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_corpus",
            "--titles",
            str(titles_file),
            "--out",
            str(output_dir),
        ],
    )

    fetch_corpus.main()

    output_file = output_dir / "Unicode_article.txt"
    assert "Nonbreaking hyphen ‑." in output_file.read_text(encoding="utf-8")


def test_main_reads_unicode_title_as_utf8(tmp_path, monkeypatch):
    title = "Tf–idf"
    titles_file = tmp_path / "titles.txt"
    titles_file.write_text(f"{title}\n", encoding="utf-8")
    output_dir = tmp_path / "raw"
    fetched_titles = []
    monkeypatch.setattr(
        fetch_corpus,
        "fetch_extract",
        lambda value: fetched_titles.append(value) or ("article text " * 150),
    )
    monkeypatch.setattr(fetch_corpus.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_corpus",
            "--titles",
            str(titles_file),
            "--out",
            str(output_dir),
        ],
    )

    fetch_corpus.main()

    assert fetched_titles == [title]
    assert (output_dir / "Tf_idf.txt").exists()
