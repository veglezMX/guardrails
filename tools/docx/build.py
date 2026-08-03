"""Build a stakeholder document.

    python3 tools/docx/build.py <doc-name> [output.docx]
    python3 tools/docx/build.py --all

Document names come from registry.py; the content lives in docs/<name>.py as an
ordered SECTIONS list. Styling primitives are in lib.py, so a document module
holds section composition and nothing else.

Output lands in presentations/ (gitignored) unless a path is given.

Run via `just docx <name>`.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from lib import base_styles, page_setup
from officemeta import DOCX_APP, scrub, stamp_docx
from registry import DOCS, resolve


def build(spec, out=None):
    module = importlib.import_module(f"docs.{spec.module}")

    doc = Document()
    page_setup(doc)
    base_styles(doc)
    for make_section in module.SECTIONS:
        make_section(doc)

    stamp_docx(doc, title=module.TITLE, subject=spec.purpose)

    path = Path(out) if out else spec.path
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    scrub(path, DOCX_APP)
    return path


def main(argv):
    if not argv:
        raise SystemExit(f"usage: build.py <{'|'.join(DOCS)}|--all> [output.docx]")

    if argv[0] == "--all":
        for spec in DOCS.values():
            print(f"built {build(spec)}")
        return

    spec = resolve(argv[0])
    print(f"built {build(spec, argv[1] if len(argv) > 1 else None)}")


if __name__ == "__main__":
    main(sys.argv[1:])
