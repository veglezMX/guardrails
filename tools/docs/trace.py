#!/usr/bin/env python3
"""Reverse index from an agreement to everything that implements it.

Replaces the hand-maintained TRACEABILITY-MATRIX.md. That document duplicated,
in reverse, relationships already recorded next to the things they describe —
`CAPABILITIES.md` names each capability's agreements, `adrs/README.md` names each
ADR's, and the architecture set cites agreements inline. A reverse index is
derivable from those forward citations, so it is computed on demand rather than
maintained by hand and left to drift.

Reported at file granularity for the architecture set: section anchors are not
machine-derivable from the current inline citations. For change control that is
sufficient — open the file and search for the agreement ID.

Usage: trace.py <docs-root> [AGR-0NN]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import entities as E
import lib


def build(root: Path) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    docs = {d.rel: d for d in lib.iter_docs(root)}
    agreements = E.agreements(docs["00-source-of-truth/AGREEMENTS.md"])
    index: dict[str, dict[str, list[str]]] = {
        agr: defaultdict(list) for agr in agreements
    }

    caps = E.capabilities(docs["20-product/CAPABILITIES.md"])
    for cap_id, row in caps.items():
        for agr in sorted(lib.ids_in(" ".join(row.cells))):
            if agr in index:
                index[agr]["capabilities"].append(cap_id)

    reqs = E.requirements(docs["50-architecture/REQUIREMENTS.md"])
    for fr_id, row in reqs.items():
        for agr in sorted(lib.ids_in(" ".join(row.cells))):
            if agr in index:
                index[agr]["requirements"].append(fr_id)

    for rel, doc in docs.items():
        if not rel.startswith("50-architecture/adrs/ADR-"):
            continue
        adr_id = rel.split("/")[-1][:8]
        head = doc.text.split("## Context")[0]
        for agr in sorted(lib.ids_in(head)):
            if agr in index:
                status = lib.decision_status(doc.text) or "?"
                index[agr]["adrs"].append(f"{adr_id} ({status})")

    for rel, doc in docs.items():
        if not rel.startswith("50-architecture/") or "/adrs/" in rel:
            continue
        for agr in sorted(lib.ids_in(doc.text)):
            if agr in index:
                index[agr]["architecture"].append(rel.split("/", 1)[1])

    claims = docs["70-governance/CLAIMS-REGISTER.md"]
    for line in claims.text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        for agr in sorted(lib.ids_in(line)):
            if agr in index:
                claim = cells[0][:70]
                if claim and claim not in index[agr]["claims"]:
                    index[agr]["claims"].append(claim)

    orphans = [agr for agr, hits in index.items() if not any(hits.values())]
    return index, orphans


LABELS = [
    ("capabilities", "capabilities"),
    ("requirements", "requirements"),
    ("adrs", "ADRs"),
    ("architecture", "architecture"),
    ("claims", "claims"),
]


def show(agr: str, hits: dict[str, list[str]], title: str) -> None:
    print(f"\n{agr} — {title}")
    for key, label in LABELS:
        values = hits.get(key) or []
        if not values:
            continue
        joined = ", ".join(dict.fromkeys(values))
        print(f"  {label:14} {joined}")
    if not any(hits.values()):
        print("  (nothing traces to this agreement)")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(args[0])
    index, orphans = build(root.resolve())
    docs = {d.rel: d for d in lib.iter_docs(root.resolve())}
    titles = {
        agr: entry.title
        for agr, entry in E.agreements(docs["00-source-of-truth/AGREEMENTS.md"]).items()
    }

    wanted = args[1].upper() if len(args) > 1 else None
    if wanted:
        if wanted not in index:
            print(f"unknown agreement: {wanted}", file=sys.stderr)
            return 2
        show(wanted, index[wanted], titles.get(wanted, ""))
        return 0

    for agr in sorted(index):
        show(agr, index[agr], titles.get(agr, ""))

    print()
    if orphans:
        print(f"{len(orphans)} agreement(s) with no implementing artifact: {', '.join(orphans)}")
    else:
        print("every agreement traces to at least one artifact")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
