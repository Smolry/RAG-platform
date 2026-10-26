"""Heading-aware Markdown chunker.

Why not the existing word-window chunker (app/core/chunking.py)? It cannot say which SECTION a chunk came from, so an
answer could not cite one, and it cuts code blocks in half. This one:
  * splits at headings of level 1-3 and records the heading path (breadcrumb) and any explicit `{ #anchor }` id;
  * never treats `#` inside a fenced code block as a heading;
  * keeps fenced code blocks whole (a block longer than the limit becomes its own oversize chunk, flagged);
  * packs paragraphs up to `max_words` (default 180 ~ 240 word-pieces, under the 256-token limit of
    all-MiniLM-L6-v2, which silently truncates anything longer - the old 500-token default exceeds it);
  * prepends the breadcrumb to every chunk so the embedding knows what the passage is about.
Content that cannot be resolved (docs `{* file.py *}` includes) is dropped, not guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CHUNKER_VERSION = "md-1"
DEFAULT_MAX_WORDS = 180

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_ANCHOR = re.compile(r"\s*\{\s*#([A-Za-z0-9_.\-]+)\s*\}\s*$")
_INCLUDE = re.compile(r"^\s*\{\*.*\*\}\s*$")
_ADMON_OPEN = re.compile(r"^\s*/{3,}\s*([A-Za-z][A-Za-z\-]*)\s*(?:\|\s*(.*?))?\s*$")   # /// note | Title, //// tab | Python 3.10+
_ADMON_CLOSE = re.compile(r"^\s*/{3,}\s*$")
_ADMON_MKDOCS = re.compile(r'^\s*(?:!!!|\?\?\?\+?)\s+([A-Za-z]+)(?:\s+"(.*)")?\s*$')
SPLIT_LEVEL = 3


@dataclass
class Section:
    path: list[str]
    anchor: str | None
    body: str


@dataclass
class Chunk:
    section_path: list[str]
    section_anchor: str | None
    text: str
    oversize: bool = False
    index_in_section: int = 0
    extra: dict = field(default_factory=dict)


def strip_frontmatter(md: str) -> str:
    if md.startswith("---\n"):
        end = md.find("\n---", 4)
        if end != -1:
            return md[end + 4:].lstrip("\n")
    return md


def clean_markdown(md: str) -> str:
    """Remove constructs that carry no standalone text; keep admonition TEXT. Fence-aware."""
    out: list[str] = []
    fence: str | None = None
    for line in strip_frontmatter(md).splitlines():
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)[0]
            fence = None if fence == marker else (fence or marker)
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        if _INCLUDE.match(line) or _ADMON_CLOSE.match(line):
            continue
        a = _ADMON_OPEN.match(line) or _ADMON_MKDOCS.match(line)
        if a:
            kind, title = a.group(1).capitalize(), (a.group(2) or "").strip()
            if kind.lower() == "api":   # pydantic's collapsed "API Documentation" link blocks: navigation noise
                continue
            out.append(f"{kind}: {title}" if title else f"{kind}:")
            continue
        out.append(line)
    return "\n".join(out)


def split_sections(md: str, split_level: int = SPLIT_LEVEL, root_title: str | None = None) -> list[Section]:
    """Split cleaned markdown at headings of level <= split_level. Deeper headings stay in the body.

    `root_title` is a fallback page label for documents without a top-level `# Title` (e.g. Pydantic's docs, whose site
    derives titles from navigation config). An explicit h1 replaces it; h2/h3 nest under it."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = [(1, root_title)] if root_title else []
    anchor: str | None = None
    body: list[str] = []
    fence: str | None = None

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append(Section([t for _, t in stack], anchor, text))

    for line in md.splitlines():
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)[0]
            fence = None if fence == marker else (fence or marker)
            body.append(line)
            continue
        h = None if fence else _HEADING.match(line)
        if h and len(h.group(1)) <= split_level:
            flush()
            body = []
            level, title = len(h.group(1)), h.group(2)
            am = _ANCHOR.search(title)
            anchor = am.group(1) if am else None
            title = _ANCHOR.sub("", title).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        body.append(line)
    flush()
    return sections


def _blocks(body: str) -> list[tuple[str, bool]]:
    """Paragraph blocks; a fenced block is one atomic block. Returns (text, is_fence)."""
    blocks: list[tuple[str, bool]] = []
    cur: list[str] = []
    fence: str | None = None

    def push(is_fence: bool) -> None:
        t = "\n".join(cur).strip("\n")
        if t.strip():
            blocks.append((t, is_fence))
        cur.clear()

    for line in body.splitlines():
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)[0]
            if fence is None:
                push(False)
                fence = marker
                cur.append(line)
            elif fence == marker:
                cur.append(line)
                fence = None
                push(True)
            else:
                cur.append(line)
            continue
        if fence is None and not line.strip():
            push(False)
        else:
            cur.append(line)
    push(fence is not None)
    return blocks


def chunk_section(sec: Section, max_words: int = DEFAULT_MAX_WORDS, prefix: str = "") -> list[Chunk]:
    crumb = " > ".join(sec.path)
    head = f"{prefix}{crumb}".strip()
    packs: list[tuple[list[str], bool]] = []
    cur: list[str] = []
    n = 0
    for text, is_fence in _blocks(sec.body):
        w = len(text.split())
        if w > max_words:                       # oversize block: emit alone, never cut code in half
            if cur:
                packs.append((cur, False)); cur, n = [], 0
            packs.append(([text], True))
            continue
        if cur and n + w > max_words:
            packs.append((cur, False)); cur, n = [], 0
        cur.append(text); n += w
    if cur:
        packs.append((cur, False))
    return [Chunk(list(sec.path), sec.anchor, (f"{head}\n\n" if head else "") + "\n\n".join(parts), over, i)
            for i, (parts, over) in enumerate(packs)]


def chunk_markdown(md: str, max_words: int = DEFAULT_MAX_WORDS, prefix: str = "", root_title: str | None = None) -> list[Chunk]:
    return [c for s in split_sections(clean_markdown(md), root_title=root_title) for c in chunk_section(s, max_words, prefix)]
