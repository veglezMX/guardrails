#!/usr/bin/env python3
"""Regenerate every machine-owned region in the solution-docs set.

A generated region is delimited by HTML comment markers and rebuilt from two
sources: the documents themselves (headings, statuses, identifiers, authority
levels) and tools/docmeta.json for editorial prose that has no derivable source.

On the first run a region has no markers yet, so the generator locates the
hand-maintained block it replaces and installs the markers around it. Afterwards
it only rewrites what is between them.

Usage: generate.py <docs-root> [--only NAME]... [--check]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import entities as E
import lib

TIGHT = "tight"
SPACED = "spaced"


def table(headers: list[str], rows: list[list[str]], style: str = TIGHT) -> str:
    sep = (
        "|" + "|".join("---" for _ in headers) + "|"
        if style == TIGHT
        else "| " + " | ".join("---" for _ in headers) + " |"
    )
    lines = ["| " + " | ".join(headers) + " |", sep]
    lines += ["| " + " | ".join(cell for cell in row) + " |" for row in rows]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Region location
# --------------------------------------------------------------------------- #

def _section_range(doc: lib.Doc, matcher: Callable[[lib.Heading], bool]) -> tuple[int, int]:
    """1-based line range of the first matching heading's section."""
    matches = [h for h in doc.headings if matcher(h)]
    if not matches:
        raise LookupError("heading not found")
    head = matches[0]
    later = [h.line for h in doc.headings if h.line > head.line and h.level <= head.level]
    return head.line, (later[0] - 1 if later else len(doc.text.splitlines()))


def _block_char_span(doc: lib.Doc, matcher, kind: str) -> tuple[int, int]:
    """Character span of the first table or list block inside a heading's section."""
    start_line, stop_line = _section_range(doc, matcher)
    lines = doc.text.splitlines(keepends=True)
    offsets, running = [], 0
    for line in lines:
        offsets.append(running)
        running += len(line)
    offsets.append(running)

    def is_block(text: str) -> bool:
        stripped = text.lstrip()
        if kind == "table":
            return stripped.startswith("|")
        if kind == "paragraph":
            return bool(stripped) and not stripped.startswith(("#", "|", "---"))
        # Lists are bulleted or ordered; several documents number their contents
        # to match numbered section headings.
        return stripped.startswith(("- ", "* ", "+ ")) or bool(
            re.match(r"^\d+\.\s", stripped)
        )

    first = last = -1
    for index in range(start_line, min(stop_line, len(lines))):
        if is_block(lines[index]):
            if first < 0:
                first = index
            last = index
        elif first >= 0 and lines[index].strip():
            break
    if first < 0:
        raise LookupError(f"no {kind} block under heading")
    return offsets[first], offsets[last + 1]


@dataclass
class Region:
    rel: str
    name: str
    body: str
    kind: str
    matcher: Callable[[lib.Heading], bool]


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #

class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.docs = {d.rel: d for d in lib.iter_docs(self.root)}
        self.meta = lib.load_docmeta(self.root)
        self.documents: dict = self.meta.get("documents", {})

    def doc(self, rel: str) -> lib.Doc:
        return self.docs[rel]

    def order(self, index: str) -> list[str]:
        """Curated order for an index, with unlisted documents appended."""
        listed = self.meta.get("order", {}).get(index, [])
        known = set(listed)
        extra = sorted(rel for rel in self.docs if rel not in known)
        return [rel for rel in listed if rel in self.docs] + extra

    def level(self, rel: str) -> str:
        """The authority level as the document's own header states it.

        The raw value is preferred so qualifiers survive — an ADR's
        "L1 (once Accepted)" must not be flattened to a bare "L1", which would
        assert authority the ADR does not yet hold. `PROVENANCE.md` states its
        position in a full sentence; that is too long for a table cell, so it
        falls back to the em dash the hand-maintained table used.
        """
        header = self.docs[rel].header
        raw = (header.authority_raw or "").strip()
        if raw and len(raw) <= 40:
            return raw
        found = header.authority_level
        return f"L{found}" if found else "—"


# --------------------------------------------------------------------------- #
# Tables of contents
# --------------------------------------------------------------------------- #

def toc_regions(ctx: Ctx) -> list[Region]:
    out: list[Region] = []
    for rel, doc in ctx.docs.items():
        heads = [h for h in doc.headings if h.text.strip().lower() == "table of contents"]
        if not heads:
            continue
        settings = ctx.documents.get(rel, {})
        depth = int(settings.get("toc_depth", 3))
        style = settings.get("toc_style", "bullet")
        toc_line = heads[0].line
        entries = [
            h
            for h in doc.headings
            if 2 <= h.level <= depth and h.line != toc_line
        ]
        lines = []
        for h in entries:
            indent = "  " * (h.level - 2)
            label = h.text.strip()
            if style == "number":
                # The heading's own number becomes the list marker, so it is not
                # repeated inside the link label.
                numbered = re.match(r"^(\d+)\.\s*(.*)$", label)
                if numbered:
                    lines.append(
                        f"{indent}{numbered.group(1)}. "
                        f"[{numbered.group(2)}](#{h.anchor})"
                    )
                    continue
            lines.append(f"{indent}- [{label}](#{h.anchor})")
        if not lines:
            continue
        out.append(
            Region(
                rel=rel,
                name="toc",
                body="\n".join(lines),
                kind="list",
                matcher=lambda h: h.text.strip().lower() == "table of contents",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Register summaries
# --------------------------------------------------------------------------- #

def oq_summary(ctx: Ctx) -> Region:
    rel = "00-source-of-truth/OPEN-QUESTIONS.md"
    doc = ctx.doc(rel)
    condensed = ctx.meta.get("open_questions", {})
    rows = []
    for oq_id, entry in sorted(E.open_questions(doc).items()):
        anchor = next(
            (h.anchor for h in doc.headings if h.text.strip().startswith(oq_id)), ""
        )
        resolution = entry.fields.get("Resolution") or entry.status or ""
        rows.append(
            [
                f"[{oq_id}](#{anchor})",
                condensed.get(oq_id, entry.title),
                entry.fields.get("Owner", ""),
                entry.fields.get("Target decision date", ""),
                resolution,
            ]
        )
    return Region(
        rel,
        "oq-summary",
        table(
            ["ID", "Question (condensed)", "Owner", "Target decision date", "Resolution"],
            rows,
            TIGHT,
        ),
        "table",
        lambda h: h.text.strip() == "Summary table",
    )


def decision_log(ctx: Ctx) -> Region:
    rel = "00-source-of-truth/DECISIONS.md"
    doc = ctx.doc(rel)
    rows = []
    for dec_id, entry in sorted(E.decisions(doc).items()):
        anchor = next(
            (h.anchor for h in doc.headings if h.text.strip().startswith(dec_id)), ""
        )
        agreements = sorted(
            i for i in lib.ids_in(entry.fields.get("Related agreements", "")) if i.startswith("AGR-")
        )
        rows.append(
            [
                f"[{dec_id}](#{anchor})",
                entry.fields.get("Date", ""),
                entry.title,
                entry.status or "",
                ", ".join(agreements) or "—",
            ]
        )
    return Region(
        rel,
        "decision-log",
        table(["ID", "Date", "Decision", "Status", "Related agreements"], rows, SPACED),
        "table",
        lambda h: h.text.strip() == "Decision log summary",
    )


def con_summary(ctx: Ctx) -> Region:
    rel = "00-source-of-truth/CONSTRAINTS.md"
    doc = ctx.doc(rel)
    prose = ctx.meta.get("constraints", {})
    rows = [
        [con_id, prose.get(con_id, {}).get("short", entry.title), prose.get(con_id, {}).get("category", "")]
        for con_id, entry in sorted(E.constraints(doc).items())
    ]
    return Region(
        rel,
        "con-summary",
        table(["ID", "Constraint (short form)", "Category"], rows, TIGHT),
        "table",
        lambda h: h.text.strip() == "Summary table",
    )


_NFR_HEAD = re.compile(r"^(?P<id>NFR-[A-Z]+-\d{3})\s*(?:—|-)\s*(?P<title>.*)$")


def nfr_summary(ctx: Ctx) -> Region:
    rel = "50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md"
    doc = ctx.doc(rel)
    prose = ctx.meta.get("nfr", {})
    rows = []
    for h in doc.headings:
        match = _NFR_HEAD.match(h.text.strip())
        if not match:
            continue
        nfr_id = match.group("id")
        stored = prose.get(nfr_id, {})
        rows.append(
            [
                nfr_id,
                stored.get("short", match.group("title").strip()),
                stored.get("target", "TBD"),
                stored.get("source", ""),
            ]
        )
    return Region(
        rel,
        "nfr-summary",
        table(["ID", "Requirement (short form)", "Target", "Source"], rows, TIGHT),
        "table",
        lambda h: h.text.strip() == "Summary table",
    )


def gr_index(ctx: Ctx) -> Region:
    rel = "20-product/GUARDRAIL-CATALOG.md"
    doc = ctx.doc(rel)
    prose = ctx.meta.get("guardrail_categories", {})
    rows = []
    for gr_id, entry in sorted(E.guardrail_categories(doc).items()):
        anchor = next(
            (h.anchor for h in doc.headings if h.text.strip().startswith(gr_id)), ""
        )
        stored = prose.get(gr_id, {})
        rows.append(
            [
                f"[{gr_id}](#{anchor})",
                stored.get("category", entry.title),
                stored.get("layer", ""),
                stored.get("dominant_status", ""),
                stored.get("external_mapping", ""),
            ]
        )
    return Region(
        rel,
        "gr-index",
        table(
            ["ID", "Category", "Enforcement layer", "Dominant status", "External mapping (orientation only)"],
            rows,
            TIGHT,
        ),
        "table",
        lambda h: h.text.strip() == "Category index",
    )


def fr_traceability(ctx: Ctx) -> Region:
    rel = "50-architecture/REQUIREMENTS.md"
    areas = ctx.meta.get("requirement_areas", [])
    rows = [[a["range"], a["area"], a["capabilities"], a["anchors"]] for a in areas]
    return Region(
        rel,
        "fr-traceability",
        table(
            ["FR range", "Functional area", "Primary capability IDs", "Primary agreements / constraints"],
            rows,
            TIGHT,
        ),
        "table",
        lambda h: "Traceability summary" in h.text,
    )


def glossary_index(ctx: Ctx) -> Region:
    rel = "70-governance/GLOSSARY.md"
    doc = ctx.doc(rel)
    terms = [h for h in doc.headings if h.level == 3]
    body = " · ".join(f"[{h.text.strip()}](#{h.anchor})" for h in terms)
    return Region(
        rel,
        "glossary-index",
        body,
        "paragraph",
        lambda h: h.text.strip() == "Quick index",
    )


def adr_index(ctx: Ctx) -> Region:
    rel = "50-architecture/adrs/README.md"
    rows = []
    for adr_rel in sorted(r for r in ctx.docs if r.startswith("50-architecture/adrs/ADR-")):
        doc = ctx.doc(adr_rel)
        adr_id = adr_rel.split("/")[-1][:8]
        status = lib.decision_status(doc.text) or (doc.header.status or "")
        title = ""
        for h in doc.headings:
            if h.text.strip().startswith(adr_id):
                title = h.text.strip()[len(adr_id) :].lstrip(":—- ")
                break
        agreements = sorted(
            i
            for i in lib.ids_in(doc.text.split("## Context")[0])
            if i.startswith("AGR-")
        )
        links = ", ".join(
            f"[{a}](../../00-source-of-truth/AGREEMENTS.md#"
            f"{_agr_anchor(ctx, a)})"
            for a in agreements
        )
        name = adr_rel.split("/")[-1]
        rows.append([adr_id, title, status, links or "—", f"[`{name}`](./{name})"])
    return Region(
        rel,
        "adr-index",
        table(["ID", "Title", "Status", "Related agreements", "File"], rows, SPACED),
        "table",
        lambda h: h.text.strip() == "Current ADRs",
    )


def _agr_anchor(ctx: Ctx, agr_id: str) -> str:
    doc = ctx.doc("00-source-of-truth/AGREEMENTS.md")
    for h in doc.headings:
        if h.text.strip().startswith(agr_id):
            return h.anchor
    return ""


# --------------------------------------------------------------------------- #
# Counts
# --------------------------------------------------------------------------- #

_RANGE = re.compile(r"`?(?P<ns>CAP|GR)-(?P<lo>\d{3})`?\s*[–-]\s*`?(?P=ns)-(?P<hi>\d{3})`?")


def cap_counts(ctx: Ctx) -> Region:
    rel = "20-product/CAPABILITIES.md"
    doc = ctx.doc(rel)
    defined = set(E.capabilities(doc))
    rows = []
    for spec in ctx.meta.get("capability_counts", []):
        label = spec["label"]
        covered: set[str] = set()
        for match in _RANGE.finditer(label):
            if match.group("ns") != "CAP":
                continue
            covered |= {
                f"CAP-{n:03d}"
                for n in range(int(match.group("lo")), int(match.group("hi")) + 1)
            }
        covered &= defined
        if covered:
            count = str(len(covered))
        elif "Total" in label:
            count = f"**{len(defined)}**"
        else:
            count = spec.get("count", "")
        rows.append([label, count])
    return Region(
        rel,
        "cap-counts",
        table(["Category", "Count"], rows, TIGHT),
        "table",
        lambda h: h.text.strip() == "Capability count summary",
    )


def gr_counts(ctx: Ctx) -> Region:
    rel = "20-product/GUARDRAIL-CATALOG.md"
    doc = ctx.doc(rel)
    defined = set(E.guardrail_categories(doc))
    rows = []
    for spec in ctx.meta.get("guardrail_counts", []):
        label, members = spec["label"], spec["members"]
        if "Total" in label:
            members = f"**{len(defined)}**"
        rows.append([label, members])
    return Region(
        rel,
        "gr-counts",
        table(["Dominant status", "Categories"], rows, TIGHT),
        "table",
        lambda h: h.text.strip() == "Category count summary",
    )


# --------------------------------------------------------------------------- #
# Index documents
# --------------------------------------------------------------------------- #

def _label(rel: str, style: str) -> str:
    if style == "full":
        return rel
    parts = rel.split("/", 1)
    return parts[1] if len(parts) > 1 else rel


def _index_regions(
    ctx: Ctx,
    index_rel: str,
    order_key: str,
    headers: list[str],
    row_for: Callable[[str], list[str]],
    style: str,
) -> list[Region]:
    specs = ctx.meta.get("index_tables", {}).get(index_rel, [])
    ordered = ctx.order(order_key)
    out: list[Region] = []
    for spec in specs:
        dirs = set(spec["dirs"])
        members = [
            rel
            for rel in ordered
            if (rel.split("/")[0] if "/" in rel else ".") in dirs
            and rel != index_rel
        ]
        if not members:
            continue
        heading_text = spec["heading"]
        out.append(
            Region(
                index_rel,
                f"{order_key}-index:{'+'.join(sorted(dirs))}",
                table(headers, [row_for(rel) for rel in members], style),
                "table",
                (lambda text: (lambda h: h.text.strip() == text))(heading_text.strip()),
            )
        )
    return out


def readme_index(ctx: Ctx) -> list[Region]:
    def row(rel: str) -> list[str]:
        meta = ctx.documents.get(rel, {})
        return [
            f"[`{_label(rel, 'short')}`]({rel})",
            ctx.level(rel),
            meta.get("description", ""),
        ]

    return _index_regions(
        ctx, "README.md", "readme", ["Document", "Level", "Description"], row, SPACED
    )


def documentmap_index(ctx: Ctx) -> list[Region]:
    def row(rel: str) -> list[str]:
        meta = ctx.documents.get(rel, {})
        return [f"[`{_label(rel, 'short')}`]({rel})", meta.get("purpose", "")]

    return _index_regions(
        ctx, "DOCUMENT-MAP.md", "documentmap", ["Document", "Purpose"], row, SPACED
    )


def references_index(ctx: Ctx) -> list[Region]:
    def row(rel: str) -> list[str]:
        meta = ctx.documents.get(rel, {})
        return [
            f"[`{rel}`](../{rel})",
            meta.get("authority_note", ctx.level(rel)),
            meta.get("role", ""),
            "Linked",
        ]

    return _index_regions(
        ctx,
        "70-governance/REFERENCES.md",
        "references",
        ["Document / Reference", "Authority", "Role / Description", "Link status"],
        row,
        SPACED,
    )


# --------------------------------------------------------------------------- #

SINGLE: dict[str, Callable[[Ctx], Region]] = {
    "oq-summary": oq_summary,
    "decision-log": decision_log,
    "con-summary": con_summary,
    "nfr-summary": nfr_summary,
    "gr-index": gr_index,
    "fr-traceability": fr_traceability,
    "glossary-index": glossary_index,
    "adr-index": adr_index,
    "cap-counts": cap_counts,
    "gr-counts": gr_counts,
}

MULTI: dict[str, Callable[[Ctx], list[Region]]] = {
    "toc": toc_regions,
    "readme-index": readme_index,
    "documentmap-index": documentmap_index,
    "references-index": references_index,
}


def collect(ctx: Ctx, only: set[str]) -> list[Region]:
    regions: list[Region] = []
    for name, builder in MULTI.items():
        if only and name not in only:
            continue
        regions.extend(builder(ctx))
    for name, builder in SINGLE.items():
        if only and name not in only:
            continue
        try:
            regions.append(builder(ctx))
        except (LookupError, KeyError) as error:
            print(f"  skip {name}: {error}", file=sys.stderr)
    return regions


def apply(ctx: Ctx, regions: list[Region], dry_run: bool) -> tuple[int, int]:
    by_doc: dict[str, list[Region]] = {}
    for region in regions:
        by_doc.setdefault(region.rel, []).append(region)

    changed = installed = 0
    for rel, doc_regions in sorted(by_doc.items()):
        path = ctx.root / rel
        text = original = path.read_text(encoding="utf-8")
        for region in doc_regions:
            doc = lib.Doc(path=path, root=ctx.root, text=text)
            if lib.fence_span(text, region.name) is not None:
                text = lib.write_fence(text, region.name, region.body)
            else:
                try:
                    start, stop = _block_char_span(doc, region.matcher, region.kind)
                except LookupError as error:
                    print(f"  skip {rel} [{region.name}]: {error}", file=sys.stderr)
                    continue
                text = lib.install_fence(text, region.name, region.body, start, stop)
                installed += 1
        if text != original:
            changed += 1
            if not dry_run:
                path.write_text(text, encoding="utf-8")
    return changed, installed


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(args[0])
    only: set[str] = set()
    dry_run = False
    index = 1
    while index < len(args):
        if args[index] == "--only" and index + 1 < len(args):
            only.add(args[index + 1])
            index += 2
        elif args[index] == "--check":
            dry_run = True
            index += 1
        else:
            print(f"unknown argument: {args[index]}", file=sys.stderr)
            return 2

    ctx = Ctx(root)
    if not ctx.meta:
        print("tools/docmeta.json is missing; run bootstrap_meta.py first", file=sys.stderr)
        return 2
    regions = collect(ctx, only)
    changed, installed = apply(ctx, regions, dry_run)
    verb = "would change" if dry_run else "changed"
    print(f"{len(regions)} generated region(s); {verb} {changed} document(s)", end="")
    print(f"; installed {installed} new marker pair(s)" if installed else "")
    # In --check mode a pending change means committed output is stale.
    return 1 if (dry_run and changed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
