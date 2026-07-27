"""The workbook registry — the single place that knows which workbooks exist.

Mirrors tools/deck/registry.py: build.py, verify.py and the `xlsx-all` justfile
recipe all read this, so adding a workbook is one entry plus one module under
books/.
"""

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "presentations"


@dataclass(frozen=True)
class BookSpec:
    name: str
    module: str
    filename: str
    audience: str
    purpose: str

    @property
    def path(self) -> Path:
        return OUT_DIR / self.filename


BOOKS = {
    spec.name: spec
    for spec in (
        BookSpec(
            name="delivery",
            module="delivery",
            filename="AI-Guardrails-Delivery-Model.xlsx",
            audience="Delivery lead, PMO, engineering management",
            purpose="Effort per role across the five milestones, with the estimation "
                    "assumptions held in one editable sheet",
        ),
    )
}


def _print_table():
    width = max(len(name) for name in BOOKS)
    for spec in BOOKS.values():
        print(f"{spec.name:<{width}}  {spec.audience}\n"
              f"{'':<{width}}  {spec.purpose}\n"
              f"{'':<{width}}  {spec.filename}\n")


def resolve(name):
    try:
        return BOOKS[name]
    except KeyError:
        raise SystemExit(
            f"unknown workbook {name!r} — choose one of: {', '.join(BOOKS)}"
        ) from None


if __name__ == "__main__":
    _print_table()
