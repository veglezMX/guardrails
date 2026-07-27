#!/usr/bin/env python3
"""Validate the solution-docs set.

Each check corresponds to a class of drift found by hand once. Encoding them here
turns a discovery into a command failure.

Usage: validate.py <docs-root>
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import entities as E
import lib

# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Finding:
    check: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"  {self.where}\n      {self.message}"


class Set:
    """The document set, parsed once and shared by every check."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.docs = lib.iter_docs(self.root)
        self.by_rel = {doc.rel: doc for doc in self.docs}
        self.meta = lib.load_docmeta(self.root)

    def doc(self, rel: str) -> lib.Doc | None:
        return self.by_rel.get(rel)

    @property
    def adr_files(self) -> list[lib.Doc]:
        return [
            doc
            for doc in self.docs
            if doc.rel.startswith("50-architecture/adrs/ADR-")
        ]


# --------------------------------------------------------------------------- #
# V1 — relative file links resolve
# --------------------------------------------------------------------------- #

def check_file_links(s: Set) -> list[Finding]:
    out: list[Finding] = []
    for doc in s.docs:
        for link in doc.links:
            target = link.target
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#")[0]
            if not path:
                continue
            resolved = (doc.path.parent / path).resolve()
            if not resolved.exists():
                out.append(
                    Finding(
                        "file-links",
                        f"{doc.rel}:{link.line}",
                        f"link target does not exist: {target}",
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# V2 — heading anchors resolve
# --------------------------------------------------------------------------- #

def check_anchors(s: Set) -> list[Finding]:
    out: list[Finding] = []
    for doc in s.docs:
        for link in doc.links:
            if link.target.startswith(("http://", "https://", "mailto:")):
                continue
            if "#" not in link.target:
                continue
            path, anchor = link.target.split("#", 1)
            if not anchor:
                continue
            if path:
                resolved = (doc.path.parent / path).resolve()
                if not resolved.exists() or resolved.is_dir():
                    continue  # already reported by V1
                try:
                    rel = resolved.relative_to(s.root).as_posix()
                except ValueError:
                    continue
                target_doc = s.doc(rel)
            else:
                target_doc = doc
            if target_doc is None:
                continue
            if anchor not in target_doc.anchors:
                out.append(
                    Finding(
                        "anchors",
                        f"{doc.rel}:{link.line}",
                        f"no such heading anchor: {link.target}",
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# V3 — identifier references resolve
# --------------------------------------------------------------------------- #

def _defined_ids(s: Set) -> dict[str, set[str]]:
    defined: dict[str, set[str]] = {}
    for namespace, owner_rel in lib.ID_OWNERS.items():
        owner = s.doc(owner_rel)
        if owner is None:
            continue
        defined[namespace] = {
            found
            for found in lib.ids_in(owner.text)
            if found.startswith(f"{namespace}-")
        }
    defined["ADR"] = {
        doc.rel.split("/")[-1][:8] for doc in s.adr_files
    }
    return defined


def check_identifiers(s: Set) -> list[Finding]:
    defined = _defined_ids(s)
    out: list[Finding] = []
    for doc in s.docs:
        for identifier in sorted(lib.ids_in(doc.text)):
            namespace = identifier.rsplit("-", 1)[0]
            known = defined.get(namespace)
            if known is None or identifier in known:
                continue
            owner = lib.ID_OWNERS.get(namespace, "50-architecture/adrs/")
            out.append(
                Finding(
                    "identifiers",
                    doc.rel,
                    f"{identifier} is referenced but not defined in {owner}",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# V4 — header block present and parseable
# --------------------------------------------------------------------------- #

# `AGREEMENTS.md` is the root of the derivation chain and `PROVENANCE.md` sits
# outside the hierarchy entirely, so neither can state what it is derived from.
_NO_DERIVED_FROM = ("00-source-of-truth/AGREEMENTS.md", "00-source-of-truth/PROVENANCE.md")


def check_headers(s: Set) -> list[Finding]:
    out: list[Finding] = []
    for doc in s.docs:
        header = doc.header
        required = [
            f
            for f in lib.HEADER_FIELDS
            if not (f == "Derived from" and doc.rel in _NO_DERIVED_FROM)
        ]
        missing = [f for f in required if f not in header.fields]
        if missing:
            out.append(
                Finding(
                    "headers",
                    doc.rel,
                    f"header block missing field(s): {', '.join(missing)}",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# V5 — ADR status coherence
# --------------------------------------------------------------------------- #

_ADR_INDEX_ROW = re.compile(r"^\|\s*(?P<id>ADR-\d{4})\s*\|(?P<rest>.*)$")
_STATUS_WORD = re.compile(r"\b(Proposed|Accepted|Rejected|Superseded|Draft)\b")


def _index_statuses(s: Set) -> dict[str, str]:
    index = s.doc("50-architecture/adrs/README.md")
    if index is None:
        return {}
    found: dict[str, str] = {}
    for line in index.text.splitlines():
        match = _ADR_INDEX_ROW.match(line)
        if not match:
            continue
        cells = [c.strip() for c in match.group("rest").split("|")]
        if len(cells) < 2:
            continue
        word = _STATUS_WORD.search(cells[1])
        if word:
            found[match.group("id")] = word.group(1)
    return found


def check_adr_status(s: Set) -> list[Finding]:
    index = _index_statuses(s)
    out: list[Finding] = []
    for doc in s.adr_files:
        adr_id = doc.rel.split("/")[-1][:8]
        adr_status = lib.decision_status(doc.text)
        if adr_status is None:
            out.append(
                Finding(
                    "adr-status",
                    doc.rel,
                    "ADR states no decision status of its own (neither a '## Status' "
                    "section nor a second '**Status:**' field)",
                )
            )
            continue
        declared = index.get(adr_id)
        if declared is None:
            out.append(
                Finding("adr-status", doc.rel, f"{adr_id} is absent from the ADR index")
            )
            continue
        word = _STATUS_WORD.search(adr_status)
        actual = word.group(1) if word else adr_status
        if actual != declared:
            out.append(
                Finding(
                    "adr-status",
                    doc.rel,
                    f"status disagrees with the ADR index: file says {actual!r}, "
                    f"index says {declared!r}",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# V6 — declared counts and ranges match reality
# --------------------------------------------------------------------------- #

_RANGE = re.compile(
    r"`?(?P<ns>AGR|CAP|FR|GR|CON|DEC|OQ|RSK|ASM)-(?P<lo>\d{3})`?\s*[–-]{1,2}\s*`?(?P=ns)-(?P<hi>\d{3})`?"
)

# Additive-only or deliberately historical documents are exempt: their ranges are
# accurate records of what was true when written.
_RANGE_EXEMPT = ("CHANGELOG.md", "90-archive/", "00-source-of-truth/PROVENANCE.md")


def check_ranges(s: Set) -> list[Finding]:
    """Flag documents whose identifier ranges no longer reach the register's end.

    A single range is not evidence of a stale claim: registers legitimately cite
    subsets, and tables often partition a namespace across many rows. So ranges
    are unioned per document, and a document is flagged only when its ranges
    start at 001 yet stop short of the highest defined identifier.

    A namespace's own owning document is exempt: it defines the entries, so a
    subset reference inside it is contextual rather than a claim about extent.
    """
    defined = _defined_ids(s)
    maxima = {
        ns: max(int(i.rsplit("-", 1)[1]) for i in ids)
        for ns, ids in defined.items()
        if ids
    }
    out: list[Finding] = []
    for doc in s.docs:
        if any(doc.rel.startswith(x) or doc.rel == x for x in _RANGE_EXEMPT):
            continue
        spans: dict[str, set[int]] = {}
        lines_seen: dict[str, list[int]] = {}
        for line_no, line in enumerate(doc.text.splitlines(), start=1):
            for match in _RANGE.finditer(line):
                ns = match.group("ns")
                if lib.ID_OWNERS.get(ns) == doc.rel:
                    continue
                lo, hi = int(match.group("lo")), int(match.group("hi"))
                spans.setdefault(ns, set()).update(range(lo, hi + 1))
                lines_seen.setdefault(ns, []).append(line_no)
        for ns, covered in sorted(spans.items()):
            top = maxima.get(ns)
            if top is None or 1 not in covered:
                continue
            reached = max(covered)
            if reached < top:
                where = ", ".join(str(n) for n in sorted(set(lines_seen[ns])))
                out.append(
                    Finding(
                        "ranges",
                        f"{doc.rel}  (line{'s' if ',' in where else ''} {where})",
                        f"{ns} ranges start at 001 but stop at {ns}-{reached:03d}; "
                        f"{ns}-{top:03d} is defined",
                    )
                )
    return out


_COUNT_ROW = re.compile(r"^\|(?P<label>[^|]*)\|\s*(?P<count>\d+)\s*\|\s*$")


def check_counts(s: Set) -> list[Finding]:
    out: list[Finding] = []
    targets = [
        ("20-product/CAPABILITIES.md", "CAP", lambda d: set(E.capabilities(d))),
        (
            "20-product/GUARDRAIL-CATALOG.md",
            "GR",
            lambda d: set(E.guardrail_categories(d)),
        ),
    ]
    for rel, namespace, extract in targets:
        doc = s.doc(rel)
        if doc is None:
            continue
        actual = extract(doc)
        for line_no, line in enumerate(doc.text.splitlines(), start=1):
            match = _COUNT_ROW.match(line)
            if not match:
                continue
            label = match.group("label")
            claimed = int(match.group("count"))
            listed = {
                i for i in lib.ids_in(label) if i.startswith(f"{namespace}-")
            }
            ranges = list(_RANGE.finditer(label))
            if not ranges:
                continue
            covered: set[str] = set(listed)
            for r in ranges:
                if r.group("ns") != namespace:
                    continue
                for n in range(int(r.group("lo")), int(r.group("hi")) + 1):
                    covered.add(f"{namespace}-{n:03d}")
            covered &= actual
            if covered and len(covered) != claimed:
                out.append(
                    Finding(
                        "counts",
                        f"{rel}:{line_no}",
                        f"row claims {claimed} but its own identifier ranges "
                        f"cover {len(covered)} defined {namespace} entries",
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# V7 — orphan checks (the retired matrix's only non-duplicated function)
# --------------------------------------------------------------------------- #

_CORE_STATUS = re.compile(r"^\**Core\b")


def check_capability_tracing(s: Set) -> list[Finding]:
    """Every core capability must cite a governing agreement.

    Scoped to core deliberately: this is the assertion the retired traceability
    matrix actually made. Later-phase capabilities legitimately cite `SCOPE.md`
    instead of an agreement, because scope — not an agreement — is what defers
    them.
    """
    doc = s.doc("20-product/CAPABILITIES.md")
    if doc is None:
        return []
    out: list[Finding] = []
    for cap_id, row in sorted(E.capabilities(doc).items()):
        if not any(_CORE_STATUS.match(cell) for cell in row.cells):
            continue
        agreements = {
            i for i in lib.ids_in(" ".join(row.cells)) if i.startswith("AGR-")
        }
        if not agreements:
            out.append(
                Finding(
                    "tracing",
                    f"{doc.rel}:{row.line}",
                    f"{cap_id} is core but cites no governing AGR, so it is "
                    "untraceable to an agreement",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# V8 — index completeness
# --------------------------------------------------------------------------- #

_INDEX_DOCS = (
    "README.md",
    "DOCUMENT-MAP.md",
    "70-governance/REFERENCES.md",
)


def _is_archived(rel: str) -> bool:
    """Archived documents are deliberately absent from the live indexes."""
    return "--archived-" in rel


def check_index_completeness(s: Set) -> list[Finding]:
    out: list[Finding] = []
    for index_rel in _INDEX_DOCS:
        index = s.doc(index_rel)
        if index is None:
            continue
        for doc in s.docs:
            if doc.rel == index_rel or _is_archived(doc.rel):
                continue
            name = doc.path.name
            if name in index.text or doc.rel in index.text:
                continue
            out.append(
                Finding(
                    "index-completeness",
                    index_rel,
                    f"does not list {doc.rel}",
                )
            )
    if s.meta:
        described = set(s.meta.get("documents", {}))
        for doc in s.docs:
            if doc.rel not in described and not _is_archived(doc.rel):
                out.append(
                    Finding(
                        "index-completeness",
                        "tools/docmeta.json",
                        f"no description entry for {doc.rel}",
                    )
                )
        for rel in sorted(described - {d.rel for d in s.docs}):
            out.append(
                Finding(
                    "index-completeness",
                    "tools/docmeta.json",
                    f"describes {rel}, which does not exist",
                )
            )
    else:
        out.append(
            Finding(
                "index-completeness",
                "tools/docmeta.json",
                "sidecar is missing; index rows cannot be generated without it",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# V11 — generated-region markers are well formed
# --------------------------------------------------------------------------- #

def check_markers(s: Set) -> list[Finding]:
    """Every BEGIN marker outside a code fence must have a matching END.

    A marker-shaped string loose in prose is a live hazard: the generator finds a
    region by searching for its opening marker, so an unmatched or duplicated one
    could make it rewrite the wrong span of a document.
    """
    out: list[Finding] = []
    for doc in s.docs:
        lines = doc.text.splitlines()
        mask = lib._code_fence_mask(lines)
        open_names: list[tuple[int, str]] = []
        seen: set[str] = set()
        for index, line in enumerate(lines):
            if mask[index]:
                continue  # documentation of the syntax, not a marker
            for kind in ("BEGIN", "END"):
                token = f"<!-- {kind} GENERATED: "
                if token not in line:
                    continue
                name = line.split(token, 1)[1].split("-->", 1)[0].strip()
                if kind == "BEGIN":
                    if name in seen:
                        out.append(
                            Finding(
                                "markers",
                                f"{doc.rel}:{index + 1}",
                                f"duplicate generated region {name!r}",
                            )
                        )
                    seen.add(name)
                    open_names.append((index + 1, name))
                else:
                    if not open_names or open_names[-1][1] != name:
                        out.append(
                            Finding(
                                "markers",
                                f"{doc.rel}:{index + 1}",
                                f"END marker for {name!r} without a matching BEGIN",
                            )
                        )
                    else:
                        open_names.pop()
        for line_no, name in open_names:
            out.append(
                Finding(
                    "markers",
                    f"{doc.rel}:{line_no}",
                    f"generated region {name!r} is never closed",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# V10 — a resolved open question needs an accepted decision
# --------------------------------------------------------------------------- #

def check_resolution_authority(s: Set) -> list[Finding]:
    oq_doc = s.doc("00-source-of-truth/OPEN-QUESTIONS.md")
    dec_doc = s.doc("00-source-of-truth/DECISIONS.md")
    if oq_doc is None or dec_doc is None:
        return []
    decisions = E.decisions(dec_doc)
    out: list[Finding] = []
    for oq_id, entry in sorted(E.open_questions(oq_doc).items()):
        status = (entry.status or "").strip()
        if status.lower() != "resolved":
            continue
        resolving = E.resolving_decisions(entry)
        if not resolving:
            out.append(
                Finding(
                    "resolution-authority",
                    f"{oq_doc.rel}:{entry.line}",
                    f"{oq_id} is Resolved but names no resolving decision",
                )
            )
            continue
        for dec_id in sorted(resolving):
            decision = decisions.get(dec_id)
            actual = (decision.status if decision else None) or "missing"
            if not actual.startswith("Accepted"):
                out.append(
                    Finding(
                        "resolution-authority",
                        f"{oq_doc.rel}:{entry.line}",
                        f"{oq_id} is Resolved by {dec_id}, whose status is "
                        f"{actual!r} — not Accepted",
                    )
                )
    return out


# --------------------------------------------------------------------------- #

CHECKS = (
    ("file-links", "relative file links resolve", check_file_links),
    ("anchors", "heading anchors resolve", check_anchors),
    ("identifiers", "identifier references resolve", check_identifiers),
    ("headers", "header blocks complete", check_headers),
    ("adr-status", "ADR status agrees with the ADR index", check_adr_status),
    ("ranges", "declared identifier ranges are current", check_ranges),
    ("counts", "declared counts match reality", check_counts),
    ("tracing", "every capability traces to an agreement", check_capability_tracing),
    ("index-completeness", "indexes list every document", check_index_completeness),
    (
        "resolution-authority",
        "resolved questions cite accepted decisions",
        check_resolution_authority,
    ),
    ("markers", "generated-region markers are well formed", check_markers),
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    docs = Set(root)
    total = 0
    print(f"solution-docs: {len(docs.docs)} documents\n")
    for _, description, check in CHECKS:
        findings = check(docs)
        total += len(findings)
        mark = "ok  " if not findings else "FAIL"
        count = "" if not findings else f"  ({len(findings)})"
        print(f"{mark}  {description}{count}")
        for finding in findings:
            print(finding)
        if findings:
            print()

    print()
    if total:
        print(f"{total} finding(s)")
        return 1
    print("no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
