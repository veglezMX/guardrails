"""Build an audience deck.

    python3 tools/deck/build.py <deck-name> [output.pptx]
    python3 tools/deck/build.py --all

Deck names come from registry.py; the slides themselves live in decks/<name>.py
as an ordered SLIDES list. Layout tokens and shape primitives are in lib.py,
reusable diagram geometry in diagrams.py, and the material every deck shares in
facts.py — so a deck module holds narrative and slide order, nothing else.

Output lands in presentations/ (gitignored) unless a path is given.

Run via `just deck <name>`.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation
from pptx.util import Inches

from context import DeckContext
from lib import SLIDE_H, SLIDE_W
from officemeta import PPTX_APP, scrub, stamp_pptx
from registry import DECKS, resolve


def build(spec, out=None):
    module = importlib.import_module(f"decks.{spec.module}")

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)

    ctx = DeckContext(
        name=spec.name,
        show_source_ids=spec.show_source_ids,
        footer_line=spec.footer_line,
    )
    for slide in module.SLIDES:
        slide(prs, ctx)

    stamp_pptx(prs, title=f"AI Guardrails — {spec.audience}", subject=spec.decision)

    path = Path(out) if out else spec.path
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    scrub(path, PPTX_APP)
    print(f"wrote {path}: {len(prs.slides._sldIdLst)} slides")
    return path


def main(argv):
    if not argv:
        raise SystemExit(
            f"usage: build.py <{' | '.join(DECKS)} | --all> [output.pptx]"
        )
    if argv[0] == "--all":
        for spec in DECKS.values():
            build(spec)
        return 0
    build(resolve(argv[0]), argv[1] if len(argv) > 1 else None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
