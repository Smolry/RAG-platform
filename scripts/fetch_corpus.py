"""Fetch Wikipedia plain-text extracts into data/raw/.

python -m scripts.fetch_corpus --titles data/raw_titles.txt
Wikipedia text is CC BY-SA 4.0. Each file's first line includes the source
title for attribution. Alternatively, add 50-200 .txt/.md files to data/raw/.
"""
import argparse
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": os.environ.get(
        "WIKIPEDIA_USER_AGENT",
        "rag-platform/0.2 (educational corpus fetcher)",
    )
}
MAX_ATTEMPTS = 3

class FetchError(RuntimeError):
    """Raised when a Wikipedia response cannot be used."""


def page_from_response(
    response: httpx.Response, title: str
) -> dict[str, Any]:
    if response.status_code >= 400:
        body = response.text[:300].replace("\n", " ")
        guidance = (
            " Set WIKIPEDIA_USER_AGENT to identify your application "
            "and provide a real contact address; if access remains "
            "blocked, follow Wikimedia's bot-traffic guidance."
            if response.status_code == 403
            else ""
        )
        raise FetchError(
            f"Wikipedia returned HTTP {response.status_code} "
            f"for {title!r}: {body}{guidance}"
        )

    try:
        payload: Any = response.json()
    except ValueError as exc:
        body = response.text[:300].replace("\n", " ")
        raise FetchError(
            f"Wikipedia returned non-JSON response for {title!r} "
            f"(HTTP {response.status_code}, "
            f"Content-Type: {response.headers.get('content-type', 'unknown')}): "
            f"{body}"
        ) from exc

    if "error" in payload:
        error = payload["error"]
        detail = (
            error.get("info", str(error))
            if isinstance(error, dict)
            else str(error)
        )
        raise FetchError(
            f"Wikipedia API error for {title!r}: {detail}"
        )

    try:
        page = next(iter(payload["query"]["pages"].values()))
    except (KeyError, StopIteration, AttributeError, TypeError) as exc:
        raise FetchError(
            f"Wikipedia response did not contain a page for {title!r}"
        ) from exc

    return page



def fetch_extract(title: str) -> str:
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "titles": title,
        "format": "json",
        "redirects": 1,
        "maxlag": 5,
    }

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = httpx.get(
                API, headers=HEADERS, timeout=30, params=params
            )
        except httpx.RequestError as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(f"Wikipedia request failed for {title!r}: {exc}") from exc
        else:
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS - 1:
                    body = response.text[:300].replace("\n", " ")
                    raise RuntimeError(
                        f"Wikipedia returned HTTP {response.status_code} for {title!r}: {body}"
                    )
            else:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    body = response.text[:300].replace("\n", " ")
                    guidance = (
                        " Set WIKIPEDIA_USER_AGENT to identify your application "
                        "and provide a real contact address; if access remains "
                        "blocked, follow Wikimedia's bot-traffic guidance."
                        if response.status_code == 403
                        else ""
                    )
                    raise RuntimeError(
                        f"Wikipedia returned HTTP {response.status_code} "
                        f"for {title!r}: {body}{guidance}"
                    ) from exc
                try:
                    payload: Any = response.json()
                except ValueError as exc:
                    if attempt == MAX_ATTEMPTS - 1:
                        body = response.text[:300].replace("\n", " ")
                        raise RuntimeError(
                            f"Wikipedia returned non-JSON for {title!r} "
                            f"(HTTP {response.status_code}, "
                            f"Content-Type: {response.headers.get('content-type', 'unknown')}): {body}"
                        ) from exc
                else:
                    page = page_from_response(response, title)
                    return page.get("extract", "")

        time.sleep(2**attempt)

    raise RuntimeError(f"Wikipedia request failed for {title!r} after {MAX_ATTEMPTS} attempts")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", type=Path, default=Path("data/raw_titles.txt"))
    parser.add_argument("--out", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    count = 0
    titles = [
        title.strip()
        for title in args.titles.read_text(encoding="utf-8").splitlines()
        if title.strip() and not title.startswith("#")
    ]
    for title in titles:
        text = fetch_extract(title)
        if len(text.split()) < 150:
            print("skip (too short/missing):", title)
        else:
            filename = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_") + ".txt"
            (args.out / filename).write_text(
                f"{title} (source: Wikipedia, CC BY-SA 4.0)\n\n{text}",
                encoding="utf-8",
            )
            count += 1
        time.sleep(1)
    print(f"saved {count} documents to {args.out}")


if __name__ == "__main__":
    main()
