"""Build a workbook.

    python3 tools/xlsx/build.py <book-name> [output.xlsx]
    python3 tools/xlsx/build.py --all

Book names come from registry.py; the sheets live in books/<name>.py as an
ordered SHEETS list. Styling primitives are in lib.py and the delivery data in
facts.py, so a book module holds sheet composition and nothing else.

Output lands in presentations/ (gitignored) unless a path is given.

Run via `just xlsx <name>`.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

from officemeta import XLSX_APP, scrub, stamp_xlsx
from registry import BOOKS, resolve


def build(spec, out=None):
    module = importlib.import_module(f"books.{spec.module}")

    wb = Workbook()
    wb.remove(wb.active)                      # drop the default empty sheet
    for make_sheet in module.SHEETS:
        make_sheet(wb)
    wb.active = 0

    stamp_xlsx(wb, title=f"AI Guardrails — {spec.name} model", subject=spec.purpose)

    path = Path(out) if out else spec.path
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    scrub(path, XLSX_APP)
    print(f"wrote {path}: {len(wb.sheetnames)} sheets")
    return path


def main(argv):
    if not argv:
        raise SystemExit(
            f"usage: build.py <{' | '.join(BOOKS)} | --all> [output.xlsx]"
        )
    if argv[0] == "--all":
        for spec in BOOKS.values():
            build(spec)
        return 0
    build(resolve(argv[0]), argv[1] if len(argv) > 1 else None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
