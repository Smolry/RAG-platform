import httpx
import pytest

from scripts.fetch_corpus import FetchError, page_from_response


def test_page_from_response_returns_page_extract():
    response = httpx.Response(
        200,
        json={"query": {"pages": {"123": {"title": "Example", "extract": "Text"}}}},
    )

    assert page_from_response(response, "Example") == {"title": "Example", "extract": "Text"}


def test_page_from_response_reports_http_error_body():
    response = httpx.Response(
        403,
        text="Please respect our robot policy.",
        headers={"content-type": "text/plain"},
    )

    with pytest.raises(FetchError, match=r"HTTP 403.*robot policy"):
        page_from_response(response, "Example")


def test_page_from_response_reports_non_json_body():
    response = httpx.Response(
        200,
        text="upstream returned plain text",
        headers={"content-type": "text/plain"},
    )

    with pytest.raises(FetchError, match="non-JSON response.*plain text"):
        page_from_response(response, "Example")


def test_page_from_response_reports_api_error():
    response = httpx.Response(
        200,
        json={"error": {"code": "badtitle", "info": "Invalid title"}},
    )

    with pytest.raises(FetchError, match="Invalid title"):
        page_from_response(response, "Example")
