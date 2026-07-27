"""Shared parsing primitives for the solution-docs tooling.

Every generator and validator builds on this module so that they cannot disagree
with each other. The slugger in particular is used by both the table-of-contents
generator and the anchor validator: if those two ever computed slugs differently,
the tooling would "fix" working links and pass broken ones.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Slugs
# --------------------------------------------------------------------------- #

# GitHub computes a heading's anchor from its *rendered* text, so inline markdown
# is reduced before slugging.
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_CODE_SPAN = re.compile(r"`([^`]*)`")
_HTML_TAG = re.compile(r"<[^>]+>")

# Keep letters, numbers, underscore, hyphen and spaces; drop everything else.
# Underscore survives deliberately: `log_only` is an identifier, not emphasis,
# and GitHub keeps it. Asterisks are dropped as emphasis markers.
_DISALLOWED = re.compile(r"[^\w\s-]", re.UNICODE)


def strip_inline(text: str) -> str:
    """Reduce inline markdown to the text a reader actually sees."""
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _CODE_SPAN.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    return text.replace("*", "")


def slug(heading_text: str) -> str:
    """Return the GitHub anchor for a heading.

    Each surviving space becomes exactly one hyphen — runs are never collapsed.
    That is why `Slide 5 — How it works: three modes` yields
    `slide-5--how-it-works-three-modes`: two hyphens from the spaces flanking the
    removed em-dash, one from the single space after the removed colon.
    """
    text = strip_inline(heading_text).strip()
    text = text.lower()
    text = _DISALLOWED.sub("", text)
    return text.replace(" ", "-")


class Slugger:
    """Slug generator that reproduces GitHub's duplicate-heading suffixes."""

    def __init__(self) -> None:
        self._seen: Counter[str] = Counter()

    def add(self, heading_text: str) -> str:
        base = slug(heading_text)
        count = self._seen[base]
        self._seen[base] += 1
        return base if count == 0 else f"{base}-{count}"


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str
    line: int


@dataclass(frozen=True)
class Link:
    text: str
    target: str
    line: int


def _code_fence_mask(lines: list[str]) -> list[bool]:
    """Mark lines that sit inside a fenced code block."""
    inside = False
    mask: list[bool] = []
    for line in lines:
        if _FENCE.match(line):
            mask.append(True)
            inside = not inside
            continue
        mask.append(inside)
    return mask


def headings(text: str) -> list[Heading]:
    """Headings outside fenced code blocks, with GitHub anchors resolved."""
    lines = text.splitlines()
    mask = _code_fence_mask(lines)
    slugger = Slugger()
    found: list[Heading] = []
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        raw = match.group(2)
        found.append(
            Heading(
                level=len(match.group(1)),
                text=raw,
                anchor=slugger.add(raw),
                line=index + 1,
            )
        )
    return found


def links(text: str) -> list[Link]:
    """Inline markdown links outside fenced code blocks.

    Fenced blocks are skipped because ADR templates contain placeholder links
    such as `[AGR-NNN](…#agr-nnn--slug)` that are illustrations, not references.
    """
    lines = text.splitlines()
    mask = _code_fence_mask(lines)
    found: list[Link] = []
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        for match in _INLINE_LINK.finditer(line):
            found.append(Link(text=match.group(1), target=match.group(2), line=index + 1))
    return found


# --------------------------------------------------------------------------- #
# Header block
# --------------------------------------------------------------------------- #

_FIELD = re.compile(r"^\*\*(?P<key>[A-Za-z ]+):\*\*\s*(?P<value>.*?)\s*$")
# Anchored at the start so a document that declares itself *outside* the
# hierarchy ("Not a member of the L1–L5 hierarchy") is not read as claiming L1.
_LEVEL = re.compile(r"^L([1-5])\b")

HEADER_FIELDS = ("Authority level", "Status", "Date", "Derived from")


@dataclass
class Header:
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def authority_raw(self) -> str | None:
        return self.fields.get("Authority level")

    @property
    def authority_level(self) -> int | None:
        """The declared L1–L5 level, or None when the document opts out.

        `PROVENANCE.md` declares itself outside the hierarchy in prose, and
        several ADRs qualify their level ("L1 (once Accepted)"). Both are valid;
        only a missing field is a defect.
        """
        raw = self.authority_raw
        if not raw:
            return None
        match = _LEVEL.search(raw)
        return int(match.group(1)) if match else None

    @property
    def status(self) -> str | None:
        return self.fields.get("Status")

    @property
    def date(self) -> str | None:
        return self.fields.get("Date")


def parse_header(text: str, limit: int = 12) -> Header:
    """Parse the bold key/value block at the top of a document."""
    header = Header()
    for line in text.splitlines()[:limit]:
        match = _FIELD.match(line)
        if match:
            header.fields[match.group("key").strip()] = match.group("value").strip()
    return header


_BOLD_ONLY = re.compile(r"^\*\*(?P<value>[^*]+)\*\*\s*$")


def decision_status(text: str) -> str | None:
    """An ADR's own lifecycle status, distinct from its document `Status` header.

    Two templates coexist in the set and both are valid. ADR-0001 through
    ADR-0003 use a `## Status` section whose value is a bold line; ADR-0004
    onwards use a second `**Status:** …` field below the title. Reading only one
    form makes the other look like a missing status.
    """
    found = statuses(text)
    if len(found) > 1:
        return found[1][1]

    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match or match.group(2).strip().lower() != "status":
            continue
        for candidate in lines[index + 1 : index + 6]:
            if not candidate.strip():
                continue
            bold = _BOLD_ONLY.match(candidate.strip())
            return bold.group("value").strip() if bold else candidate.strip()
    return None


def statuses(text: str) -> list[tuple[int, str]]:
    """Every `**Status:** …` line with its line number.

    ADRs carry two: the document header's and the ADR's own. Both matter, and a
    mismatch between them is a defect the validator reports.
    """
    found: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines()):
        match = _FIELD.match(line)
        if match and match.group("key").strip() == "Status":
            found.append((index + 1, match.group("value").strip()))
    return found


# --------------------------------------------------------------------------- #
# Identifier namespaces
# --------------------------------------------------------------------------- #

# The document that owns each namespace. An identifier referenced anywhere in the
# set but absent from its owning document is dangling.
ID_OWNERS: dict[str, str] = {
    "AGR": "00-source-of-truth/AGREEMENTS.md",
    "DEC": "00-source-of-truth/DECISIONS.md",
    "OQ": "00-source-of-truth/OPEN-QUESTIONS.md",
    "CON": "00-source-of-truth/CONSTRAINTS.md",
    "ASM": "00-source-of-truth/ASSUMPTIONS.md",
    "CAP": "20-product/CAPABILITIES.md",
    "GR": "20-product/GUARDRAIL-CATALOG.md",
    "FR": "50-architecture/REQUIREMENTS.md",
    "RSK": "70-governance/RISK-REGISTER.md",
}

# \b keeps `GR-001` from matching inside `AGR-001` and `FR-012` from matching
# inside `NFR-...`, since both boundaries fall between word characters.
_ID = re.compile(r"\b(" + "|".join(ID_OWNERS) + r")-(\d{3})\b")
_ADR_ID = re.compile(r"\bADR-(\d{4})\b")


def ids_in(text: str) -> set[str]:
    """Every namespaced identifier mentioned in a body of text."""
    found = {f"{ns}-{num}" for ns, num in _ID.findall(text)}
    found |= {f"ADR-{num}" for num in _ADR_ID.findall(text)}
    return found


# --------------------------------------------------------------------------- #
# Generated regions
# --------------------------------------------------------------------------- #

BEGIN = "<!-- BEGIN GENERATED: {name} -->"
END = "<!-- END GENERATED: {name} -->"


def fence_span(text: str, name: str) -> tuple[int, int] | None:
    """Character offsets of a generated region's inner content, markers excluded."""
    begin = BEGIN.format(name=name)
    end = END.format(name=name)
    start = text.find(begin)
    if start == -1:
        return None
    stop = text.find(end, start)
    if stop == -1:
        raise ValueError(f"unterminated generated region {name!r}")
    return start + len(begin), stop


def read_fence(text: str, name: str) -> str | None:
    span = fence_span(text, name)
    if span is None:
        return None
    return text[span[0] : span[1]].strip("\n")


def write_fence(text: str, name: str, body: str) -> str:
    """Replace a generated region's contents, preserving its markers."""
    span = fence_span(text, name)
    if span is None:
        raise KeyError(f"no generated region named {name!r}")
    return text[: span[0]] + "\n" + body.strip("\n") + "\n" + text[span[1] :]


def install_fence(text: str, name: str, body: str, start: int, stop: int) -> str:
    """Wrap a character range in fresh markers, replacing it with `body`.

    Used once per region when migrating hand-maintained content to generated.
    """
    marker_open = BEGIN.format(name=name)
    marker_close = END.format(name=name)
    block = f"{marker_open}\n{body.strip(chr(10))}\n{marker_close}"
    return text[:start] + block + text[stop:]


# --------------------------------------------------------------------------- #
# Document set
# --------------------------------------------------------------------------- #


@dataclass
class Doc:
    path: Path
    root: Path
    text: str

    @property
    def rel(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    @property
    def header(self) -> Header:
        return parse_header(self.text)

    @property
    def headings(self) -> list[Heading]:
        return headings(self.text)

    @property
    def links(self) -> list[Link]:
        return links(self.text)

    @property
    def anchors(self) -> set[str]:
        return {h.anchor for h in self.headings}


def iter_docs(root: Path) -> list[Doc]:
    """Every Markdown document in the set, in stable path order."""
    root = root.resolve()
    return [
        Doc(path=path, root=root, text=path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("*.md"))
    ]


def repo_root(docs_root: Path) -> Path:
    return docs_root.resolve().parent


def load_docmeta(docs_root: Path) -> dict:
    """Editorial per-document metadata that cannot be derived from the documents."""
    path = repo_root(docs_root) / "tools" / "docmeta.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
