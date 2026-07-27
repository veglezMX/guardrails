"""Per-deck rendering context.

Carries the one policy every slide has to respect: whether internal document
references (AGR-xxx, ADR-xxxx, FILE.md, §n) may appear on the slide itself.

The internal investment-review deck prints its sourcing in the footer, on
purpose — it is the historical record of where each claim came from. Every other
audience gets clean slides and a standing caveat line instead. Speaker notes keep
full sourcing on all decks; they are presenter-only and never projected.

Slides call ``ctx.src(...)`` for their footer rather than composing one, so the
policy lives here and nowhere else.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeckContext:
    name: str
    show_source_ids: bool
    footer_line: str | None = None

    def src(self, refs):
        """Footer for a slide whose sourcing is ``refs``.

        Returns the sourcing line when this deck shows internal references, and
        the deck's standing footer line (or nothing) when it must not.
        """
        return f"Source: {refs}" if self.show_source_ids else self.footer_line
