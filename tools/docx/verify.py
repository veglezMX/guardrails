"""Check a built document.

    python3 tools/docx/verify.py <doc-name>
    python3 tools/docx/verify.py --all

Four checks, each catching something a reader would notice before we would:

  * structure — every section heading the registry promises is present, and no
    heading is empty
  * placeholders — no TBD, TODO or lorem left in body text; the documentation
    set marks genuine unknowns as TBD deliberately, so they are allowed only in
    a table cell, never in a heading or a standalone paragraph
  * tables — no empty cells, since an empty cell in a plan reads as an omission
    rather than as a deliberate blank
  * metadata — the shared package audit, so a library upgrade that reintroduces
    its own defaults is caught at build time rather than in a meeting

Run via `just docx-check <name>`.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from officemeta import audit
from registry import DOCS, resolve

REQUIRED_HEADINGS = (
    "Executive summary",
    "Delivery roadmap",
    "Milestones and decision gates",
    "Deliverable plan",
)

PLACEHOLDER = re.compile(r"\b(TODO|FIXME|lorem ipsum|XXX)\b", re.I)

# Only the helpers that take rich text interpret ** as bold; a plain paragraph
# prints the markers verbatim. Easy to write, invisible until someone opens the
# file, so it is checked rather than remembered.
MARKUP = re.compile(r"[*_`]")

# These documents are circulated on their own. A reader has the file and nothing
# else, so a pointer into the documentation set is not a citation — it is a dead
# end that makes the document unusable for the decision it asks for. Restate the
# substance instead.
OUTWARD = re.compile(
    r"solution-docs"
    r"|\b[A-Z][A-Z-]*\.md\b"
    r"|\b(AGR|CON|DEC|OQ|ADR|ASM|FR|NFR|CAP|GR)-\d"
    r"|§"
)


def check(spec):
    failures = []
    path = spec.path
    if not path.exists():
        return [f"{spec.name}: not built — run `just docx {spec.name}`"]

    doc = Document(path)

    headings = [p.text.strip() for p in doc.paragraphs
                if p.style.name.startswith("Heading")]
    for required in REQUIRED_HEADINGS:
        if not any(required.lower() in h.lower() for h in headings):
            failures.append(f"{path.name}: no heading covering {required!r}")
    for h in headings:
        if not h:
            failures.append(f"{path.name}: an empty heading")

    for p in doc.paragraphs:
        found = PLACEHOLDER.search(p.text)
        if found:
            failures.append(
                f"{path.name}: {found.group(0)!r} left in body text — "
                f"{p.text[:60]!r}")
        if p.style.name.startswith("Heading") and "TBD" in p.text:
            failures.append(
                f"{path.name}: TBD in a heading — {p.text[:60]!r}")

    for p in doc.paragraphs:
        found = OUTWARD.search(p.text)
        if found:
            failures.append(
                f"{path.name}: points outside itself with {found.group(0)!r} — "
                f"{p.text[:60]!r}")
        found = MARKUP.search(p.text)
        if found:
            failures.append(
                f"{path.name}: unrendered {found.group(0)!r} markup — "
                f"{p.text[:60]!r}")

    for t_index, t in enumerate(doc.tables):
        seen = set()
        for r_index, row in enumerate(t.rows):
            for c_index, cell in enumerate(row.cells):
                if cell._tc in seen:      # a merged cell spans several columns
                    continue
                seen.add(cell._tc)
                if not cell.text.strip():
                    failures.append(
                        f"{path.name}: table {t_index + 1} has an empty cell at "
                        f"row {r_index + 1}, column {c_index + 1}")
                for pattern, label in ((OUTWARD, "points outside itself with"),
                                       (MARKUP, "has unrendered markup")):
                    found = pattern.search(cell.text)
                    if found:
                        failures.append(
                            f"{path.name}: table {t_index + 1} {label} "
                            f"{found.group(0)!r} — {cell.text[:40]!r}")

    failures.extend(audit(path))
    return failures


def main(argv):
    specs = DOCS.values() if argv[:1] == ["--all"] else [resolve(argv[0])]
    failures = [f for spec in specs for f in check(spec)]
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        raise SystemExit(f"{len(failures)} failure(s)")
    for spec in specs:
        print(f"ok {spec.path.name}")


if __name__ == "__main__":
    if not sys.argv[1:]:
        raise SystemExit(f"usage: verify.py <{'|'.join(DOCS)}|--all>")
    main(sys.argv[1:])
