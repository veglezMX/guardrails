"""The deck registry — the single place that knows which decks exist.

build.py, verify.py and the `deck-all` justfile recipe all read this, so adding
an audience is one entry here plus one module under decks/.

Every deck is written from the same source material in solution-docs/ but for a
different room making a different decision. `show_source_ids` decides whether
that source material is visible on the slides or only in the speaker notes; see
context.DeckContext.
"""

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "presentations"

# Standing caveat carried on every slide of the variant decks, in place of the
# internal sourcing footer. Nothing in any deck is built or piloted today.
CAVEAT = "AI Guardrails · design intent and delivery plan — nothing built or piloted today"


@dataclass(frozen=True)
class DeckSpec:
    name: str
    module: str
    filename: str
    show_source_ids: bool
    audience: str
    decision: str
    footer_line: str | None = None

    @property
    def path(self) -> Path:
        return OUT_DIR / self.filename


DECKS = {
    spec.name: spec
    for spec in (
        DeckSpec(
            name="internal",
            module="internal",
            filename="AI-Guardrails-Internal-Pitch.pptx",
            show_source_ids=True,
            audience="Internal platform investment review",
            decision="Approve the solution — full feature set, five phases",
        ),
        DeckSpec(
            name="architecture",
            module="architecture",
            filename="AI-Guardrails-Architecture-Review.pptx",
            show_source_ids=False,
            footer_line=CAVEAT,
            audience="Architecture / governance review board",
            decision="Sign off the contracts; rule on the open questions",
        ),
        DeckSpec(
            name="security",
            module="security",
            filename="AI-Guardrails-Security-Review.pptx",
            show_source_ids=False,
            footer_line=CAVEAT,
            audience="Security and compliance leadership",
            decision="Approve which deployment modes may carry which data classes",
        ),
        DeckSpec(
            name="enablement",
            module="enablement",
            filename="AI-Guardrails-Team-Enablement.pptx",
            show_source_ids=False,
            footer_line=CAVEAT,
            audience="Adopting pilot team",
            decision="Commit as the second independent instance, starting in observe mode",
        ),
    )
}


def _print_table():
    width = max(len(name) for name in DECKS)
    for spec in DECKS.values():
        ids = "slide sourcing on" if spec.show_source_ids else "slide sourcing off"
        print(f"{spec.name:<{width}}  {spec.audience}\n"
              f"{'':<{width}}  ask: {spec.decision}\n"
              f"{'':<{width}}  {spec.filename} · {ids}\n")


def resolve(name):
    """Look up a deck by name, with a usable error when the name is wrong."""
    try:
        return DECKS[name]
    except KeyError:
        raise SystemExit(
            f"unknown deck {name!r} — choose one of: {', '.join(DECKS)}"
        ) from None


if __name__ == "__main__":
    _print_table()
