"""Register-entry parsing.

The registers state each entry twice: once as a per-entry block or table row, and
again in a summary table near the top. The per-entry form is the source; the
summary is derived from it. This module reads the source form so that generators
can rebuild the summaries and validators can check assertions made about them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lib import Doc, _FIELD, _code_fence_mask

_HEADING_AT = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


@dataclass
class Section:
    heading: str
    level: int
    line: int
    body: str


def sections(text: str, level: int) -> list[Section]:
    """Split a document into the bodies under each heading of a given level."""
    lines = text.splitlines()
    mask = _code_fence_mask(lines)
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        match = _HEADING_AT.match(line)
        if match and len(match.group(1)) == level:
            starts.append((index, match.group(2)))

    found: list[Section] = []
    for position, (index, heading) in enumerate(starts):
        stop = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        found.append(
            Section(
                heading=heading,
                level=level,
                line=index + 1,
                body="\n".join(lines[index + 1 : stop]),
            )
        )
    return found


def _field_values(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in body.splitlines():
        match = _FIELD.match(line)
        if match:
            key = match.group("key").strip()
            # First occurrence wins; later ones belong to nested content.
            values.setdefault(key, match.group("value").strip())
    return values


# --------------------------------------------------------------------------- #
# Register entries
# --------------------------------------------------------------------------- #

_ENTRY_ID = re.compile(r"^(?P<id>[A-Z]{2,3}-\d{3})\s*(?:—|-)\s*(?P<title>.*)$")
_DEC_REF = re.compile(r"\bDEC-(\d{3})\b")


@dataclass
class Entry:
    id: str
    title: str
    line: int
    fields: dict[str, str] = field(default_factory=dict)
    body: str = ""

    @property
    def status(self) -> str | None:
        return self.fields.get("Status")


def _entries(doc: Doc, level: int, prefix: str) -> dict[str, Entry]:
    found: dict[str, Entry] = {}
    for section in sections(doc.text, level):
        match = _ENTRY_ID.match(section.heading.strip())
        if not match or not match.group("id").startswith(f"{prefix}-"):
            continue
        found[match.group("id")] = Entry(
            id=match.group("id"),
            title=match.group("title").strip(),
            line=section.line,
            fields=_field_values(section.body),
            body=section.body,
        )
    return found


def agreements(doc: Doc) -> dict[str, Entry]:
    return _entries(doc, 2, "AGR")


def decisions(doc: Doc) -> dict[str, Entry]:
    return _entries(doc, 2, "DEC")


def open_questions(doc: Doc) -> dict[str, Entry]:
    return _entries(doc, 3, "OQ")


def constraints(doc: Doc) -> dict[str, Entry]:
    return _entries(doc, 3, "CON")


def risks(doc: Doc) -> dict[str, Entry]:
    return _entries(doc, 3, "RSK")


def guardrail_categories(doc: Doc) -> dict[str, Entry]:
    """Guardrail categories are headed sections, not table rows."""
    return _entries(doc, 2, "GR")


def resolving_decisions(entry: Entry) -> set[str]:
    """The decisions an open question claims resolved it."""
    resolution = entry.fields.get("Resolution") or ""
    if not resolution:
        return set()
    return {f"DEC-{num}" for num in _DEC_REF.findall(resolution)}


# --------------------------------------------------------------------------- #
# Table-row registers
# --------------------------------------------------------------------------- #

_ROW_ID = re.compile(r"^\|\s*\**\s*(?P<id>(?:CAP|FR|ASM)-\d{3})\b")


@dataclass
class Row:
    id: str
    line: int
    cells: list[str]

    def cell(self, index: int) -> str:
        return self.cells[index] if index < len(self.cells) else ""


def table_rows(doc: Doc, prefix: str) -> dict[str, Row]:
    """Registers whose entries are table rows rather than headed sections."""
    lines = doc.text.splitlines()
    mask = _code_fence_mask(lines)
    found: dict[str, Row] = {}
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        match = _ROW_ID.match(line)
        if not match or not match.group("id").startswith(f"{prefix}-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        found.setdefault(
            match.group("id"), Row(id=match.group("id"), line=index + 1, cells=cells)
        )
    return found


def capabilities(doc: Doc) -> dict[str, Row]:
    return table_rows(doc, "CAP")


def requirements(doc: Doc) -> dict[str, Row]:
    return table_rows(doc, "FR")


def assumptions(doc: Doc) -> dict[str, Row]:
    return table_rows(doc, "ASM")
