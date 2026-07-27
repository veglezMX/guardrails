#!/usr/bin/env python3
"""One-time extractor that seeds tools/docmeta.json from existing content.

The index and summary tables carry editorial prose — condensed questions, short
forms, categories, targets, role descriptions — that has no source anywhere else
in the set. Retyping it would introduce errors, so it is lifted verbatim from the
current tables instead. After this runs, generate.py rebuilds those same tables
from the sidecar, and the resulting diff should be near-empty. That near-empty
diff is the proof the extraction was faithful.

Run once: bootstrap_meta.py <docs-root>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import lib

_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$")
_FIRST_LINK = re.compile(r"\[[`']?(?P<label>[^\]]*?)[`']?\]\((?P<target>[^)\s]+)\)")


def _cells(line: str) -> list[str] | None:
    match = _ROW.match(line.rstrip())
    if not match:
        return None
    return [c.strip() for c in match.group("cells").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return all(set(c) <= set("-: ") and c for c in cells)


def _linked_rel(cell: str, doc: lib.Doc, root: Path) -> str | None:
    """Resolve the first markdown link in a cell to a path relative to the set."""
    match = _FIRST_LINK.search(cell)
    if not match:
        return None
    target = match.group("target").split("#")[0]
    if not target or target.startswith(("http", "mailto:")):
        return None
    resolved = (doc.path.parent / target).resolve()
    try:
        rel = resolved.relative_to(root).as_posix()
    except ValueError:
        return None
    return rel if rel.endswith(".md") else None


def _rows_of_tables(doc: lib.Doc, start: int = 0, stop: int | None = None):
    """Yield (line_no, cells) for every data row of every table in a line range."""
    lines = doc.text.splitlines()
    stop = stop if stop is not None else len(lines)
    for index in range(start, min(stop, len(lines))):
        cells = _cells(lines[index])
        if not cells or _is_separator(cells):
            continue
        yield index + 1, cells


def _section_bounds(doc: lib.Doc, predicate) -> tuple[int, int]:
    """Line range of the first heading satisfying `predicate`, to the next peer."""
    matches = [h for h in doc.headings if predicate(h)]
    if not matches:
        return (0, 0)
    head = matches[0]
    later = [
        h.line for h in doc.headings if h.line > head.line and h.level <= head.level
    ]
    return (head.line, later[0] - 1 if later else len(doc.text.splitlines()))


# --------------------------------------------------------------------------- #
# Per-document prose
# --------------------------------------------------------------------------- #

def readme_descriptions(docs: dict[str, lib.Doc], root: Path) -> dict[str, str]:
    """README §5 — `| Document | Level | Description |`."""
    doc = docs["README.md"]
    start, stop = _section_bounds(
        doc, lambda h: h.text.startswith("5.") or "Directory-by-directory" in h.text
    )
    out: dict[str, str] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        if len(cells) < 3:
            continue
        rel = _linked_rel(cells[0], doc, root)
        if rel:
            out.setdefault(rel, cells[2])
    return out


def documentmap_purposes(docs: dict[str, lib.Doc], root: Path) -> dict[str, str]:
    """DOCUMENT-MAP §Full repository index — `| Document | Purpose |`."""
    doc = docs["DOCUMENT-MAP.md"]
    start, stop = _section_bounds(doc, lambda h: "Full repository index" in h.text)
    out: dict[str, str] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        if len(cells) < 2:
            continue
        rel = _linked_rel(cells[0], doc, root)
        if rel:
            out.setdefault(rel, cells[1])
    return out


def references_roles(docs: dict[str, lib.Doc], root: Path) -> dict[str, dict[str, str]]:
    """REFERENCES §§3–7 — `| Document | Authority | Role / Description | Link status |`."""
    doc = docs["70-governance/REFERENCES.md"]
    numbered = [
        h for h in doc.headings if re.match(r"^(3|4|5|6|7)\.", h.text.strip())
    ]
    if not numbered:
        return {}
    start = numbered[0].line
    after = [h.line for h in doc.headings if h.line > start and re.match(r"^8\.", h.text.strip())]
    stop = after[0] - 1 if after else len(doc.text.splitlines())
    out: dict[str, dict[str, str]] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        if len(cells) < 3:
            continue
        rel = _linked_rel(cells[0], doc, root)
        if rel:
            out.setdefault(rel, {"authority": cells[1], "role": cells[2]})
    return out


# --------------------------------------------------------------------------- #
# Editorial summary columns
# --------------------------------------------------------------------------- #

def oq_condensed(docs: dict[str, lib.Doc]) -> dict[str, str]:
    """OPEN-QUESTIONS §Summary table — the condensed question column."""
    doc = docs["00-source-of-truth/OPEN-QUESTIONS.md"]
    start, stop = _section_bounds(doc, lambda h: h.text.strip() == "Summary table")
    out: dict[str, str] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        ids = [i for i in lib.ids_in(cells[0]) if i.startswith("OQ-")]
        if len(cells) >= 2 and ids:
            out.setdefault(ids[0], cells[1])
    return out


def con_short(docs: dict[str, lib.Doc]) -> dict[str, dict[str, str]]:
    """CONSTRAINTS §Summary table — short form and category."""
    doc = docs["00-source-of-truth/CONSTRAINTS.md"]
    start, stop = _section_bounds(doc, lambda h: h.text.strip() == "Summary table")
    out: dict[str, dict[str, str]] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        ids = [i for i in lib.ids_in(cells[0]) if i.startswith("CON-")]
        if len(cells) >= 3 and ids:
            out.setdefault(ids[0], {"short": cells[1], "category": cells[2]})
    return out


_NFR_ID = re.compile(r"\bNFR-[A-Z]+-\d{3}\b")


def nfr_short(docs: dict[str, lib.Doc]) -> dict[str, dict[str, str]]:
    """NON-FUNCTIONAL-REQUIREMENTS §Summary table — short form, target, source."""
    doc = docs["50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md"]
    start, stop = _section_bounds(doc, lambda h: h.text.strip() == "Summary table")
    out: dict[str, dict[str, str]] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        match = _NFR_ID.search(cells[0])
        if len(cells) >= 4 and match:
            out.setdefault(
                match.group(0),
                {"short": cells[1], "target": cells[2], "source": cells[3]},
            )
    return out


def gr_index(docs: dict[str, lib.Doc]) -> dict[str, dict[str, str]]:
    """GUARDRAIL-CATALOG §Category index — layer, dominant status, external mapping."""
    doc = docs["20-product/GUARDRAIL-CATALOG.md"]
    start, stop = _section_bounds(doc, lambda h: h.text.strip() == "Category index")
    out: dict[str, dict[str, str]] = {}
    for _, cells in _rows_of_tables(doc, start, stop):
        ids = [i for i in lib.ids_in(cells[0]) if i.startswith("GR-")]
        if len(cells) >= 5 and ids:
            out.setdefault(
                ids[0],
                {
                    "category": cells[1],
                    "layer": cells[2],
                    "dominant_status": cells[3],
                    "external_mapping": cells[4],
                },
            )
    return out


def fr_areas(docs: dict[str, lib.Doc]) -> list[dict[str, str]]:
    """REQUIREMENTS §18 — the editorial grouping of FR ranges into functional areas."""
    doc = docs["50-architecture/REQUIREMENTS.md"]
    start, stop = _section_bounds(doc, lambda h: "Traceability summary" in h.text)
    out: list[dict[str, str]] = []
    for _, cells in _rows_of_tables(doc, start, stop):
        if len(cells) >= 4 and cells[0].startswith("FR-"):
            out.append(
                {
                    "range": cells[0],
                    "area": cells[1],
                    "capabilities": cells[2],
                    "anchors": cells[3],
                }
            )
    return out


# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    docs = {d.rel: d for d in lib.iter_docs(root)}

    readme = readme_descriptions(docs, root)
    purposes = documentmap_purposes(docs, root)
    roles = references_roles(docs, root)

    documents: dict[str, dict[str, str]] = {}
    for rel in sorted(docs):
        entry: dict[str, str] = {}
        if rel in readme:
            entry["description"] = readme[rel]
        if rel in purposes:
            entry["purpose"] = purposes[rel]
        if rel in roles:
            entry["authority_note"] = roles[rel]["authority"]
            entry["role"] = roles[rel]["role"]
        documents[rel] = entry

    meta = {
        "_comment": (
            "Editorial prose that cannot be derived from the documents themselves. "
            "Generated tables read from here; see tools/docs/generate.py."
        ),
        "documents": documents,
        "open_questions": oq_condensed(docs),
        "constraints": con_short(docs),
        "nfr": nfr_short(docs),
        "guardrail_categories": gr_index(docs),
        "requirement_areas": fr_areas(docs),
    }

    out_path = lib.repo_root(root) / "tools" / "docmeta.json"
    if out_path.exists() and "--force" not in argv:
        print(
            f"{out_path} already exists.\n"
            "This extractor is one-time: the tables it reads are now generated FROM "
            "this file, so re-running would read back its own output and could lose "
            "editorial prose. Pass --force only if you mean to re-seed from scratch.",
            file=sys.stderr,
        )
        return 2
    out_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    missing = [rel for rel, entry in documents.items() if not entry.get("description")]
    print(f"wrote {out_path.relative_to(lib.repo_root(root))}")
    print(f"  documents            {len(documents)}")
    print(f"    with description   {sum(1 for e in documents.values() if e.get('description'))}")
    print(f"    with purpose       {sum(1 for e in documents.values() if e.get('purpose'))}")
    print(f"    with role          {sum(1 for e in documents.values() if e.get('role'))}")
    print(f"  open_questions       {len(meta['open_questions'])}")
    print(f"  constraints          {len(meta['constraints'])}")
    print(f"  nfr                  {len(meta['nfr'])}")
    print(f"  guardrail_categories {len(meta['guardrail_categories'])}")
    print(f"  requirement_areas    {len(meta['requirement_areas'])}")
    if missing:
        print(f"\n  {len(missing)} document(s) need a description authored by hand:")
        for rel in missing:
            print(f"    {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
