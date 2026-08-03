"""The document registry — the single place that knows which documents exist.

Mirrors tools/deck/registry.py and tools/xlsx/registry.py: build.py, verify.py
and the `docx-all` justfile recipe all read this, so adding a document is one
entry plus one module under docs/.
"""

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "presentations"


@dataclass(frozen=True)
class DocSpec:
    name: str
    module: str
    filename: str
    audience: str
    purpose: str

    @property
    def path(self) -> Path:
        return OUT_DIR / self.filename


DOCS = {
    spec.name: spec
    for spec in (
        DocSpec(
            name="local-first",
            module="local_first_mvp",
            filename="AI-Guardrails-Local-First-MVP-Plan.docx",
            audience="Product lead, delivery lead, technical lead, "
                     "security and governance lead",
            purpose="Delivery plan for the Phase 1-2 core built entirely on "
                    "local infrastructure, with every AWS dependency deferred "
                    "to a single final stage",
        ),
    )
}


def _print_table():
    width = max(len(name) for name in DOCS)
    for spec in DOCS.values():
        print(f"{spec.name:<{width}}  {spec.audience}\n"
              f"{'':<{width}}  {spec.purpose}\n"
              f"{'':<{width}}  {spec.filename}\n")


def resolve(name):
    try:
        return DOCS[name]
    except KeyError:
        raise SystemExit(
            f"unknown document {name!r} — choose one of: {', '.join(DOCS)}"
        ) from None


if __name__ == "__main__":
    _print_table()
