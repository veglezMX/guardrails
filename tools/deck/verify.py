"""Verify a built deck without opening PowerPoint.

    python3 tools/deck/verify.py <deck-name | path.pptx>
    python3 tools/deck/verify.py --all

Checks, in order:

1. Slide geometry — nothing off-canvas, correct 16:9 size.
2. Production rules from solution-docs/40-sales-deck/VISUAL-DIRECTION.md —
   speaker notes on every slide carrying all four required parts (§8), and no
   flattened raster artwork (§9).
3. Text fit — estimates the height each text block actually needs and reports
   any block whose text would run into the shape below it or off the slide.
4. Claims boundary — scans all slide and notes text for phrasing that
   solution-docs/70-governance/CLAIMS-REGISTER.md restricts or prohibits.
5. Source-ID leaks — for every deck except the internal pitch, fails if an
   internal document reference (AGR-nnn, ADR-nnnn, FILE.md, §n) appears on a
   slide. Speaker notes are exempt: they keep full traceability and are never
   projected.

Exits non-zero if anything in 1, 2, 3 or 5 fails, or if a prohibited claim is
found. Restricted-phrase hits are reported for human review but do not fail the
run on their own, since most legitimate uses are negations ("this is *not* a
data-local model").

Run via `just deck-check <name>`.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation
from pptx.util import Inches

from officemeta import audit
from registry import DECKS, resolve

EMU = 914400

# Phrases the claims register prohibits outright, and phrases it restricts.
# Both are reported with surrounding context so a negation can be told apart
# from an assertion.
PROHIBITED = [
    r"\bbullet[- ]?proof\b", r"\bunbreakable\b", r"\brevolutionary\b",
    r"\bbattle[- ]tested\b", r"\benterprise[- ]ready\b", r"\bproduction[- ]proven\b",
    r"\bSOC ?2\b", r"\bHIPAA\b", r"\bcompliance[- ]certified\b",
    r"\bblocks all\b", r"\bstops every\b", r"\beliminates AI risk\b",
    r"\bmakes your AI safe\b", r"\bdeploys anywhere\b", r"\bmulti[- ]cloud\b",
    r"\bcross[- ]cloud (support|deployment) (is|will be) (available|supported)\b",
    r"\b100 ?%\b", r"\bzero[- ]config", r"\bone[- ]click\b", r"\bdrop[- ]in\b",
    r"\$\s?\d", r"\b\d+(\.\d+)?\s?%\b", r"\b\d+\s?ms\b",
    r"\b\d+(\.\d+)?\s?(second|minute|hour)s? (of )?latency\b",
]
RESTRICTED = [
    r"data[- ]local", r"private[- ]data", r"never leaves", r"guarantee",
    r"seamless", r"instant", r"certified", r"\bSLA\b", r"\bROI\b",
    r"multi[- ]region", r"active[- ]active",
]

NOTE_PARTS = ("SOURCING", "CAVEAT NOT ON SLIDE", "TALK TRACK", "IF ASKED")

# Internal document references. Decks written for a room outside the original
# investment review keep these in the speaker notes and off the slides.
SOURCE_IDS = [
    r"\bAGR-\d+", r"\bDEC-\d+", r"\bASM-\d+", r"\bMVP-\d+", r"\bOQ-\d+",
    r"\bCON-\d+", r"\bFR-\d+", r"\bADR-\d{4}", r"\b[A-Z][A-Z0-9-]{3,}\.md\b",
    r"§\s?\d",
]

# A banned phrase inside a negation ("this is *not* a data-local model") is the
# deck doing its job, not breaking the claims boundary. Look back a short way
# for a negation cue before failing on a match.
NEGATION = re.compile(
    r"\b(not|never|no|without|prohibit\w*|restrict\w*|must not|cannot|isn't|"
    r"is not|are not|does not|doesn't|excluded|rejects?)\b", re.I)
NEGATION_WINDOW = 90


def negated(text, match):
    """True if a negation cue appears shortly before the match."""
    return bool(NEGATION.search(text[max(0, match.start() - NEGATION_WINDOW):
                                     match.start()]))


def needed_height(shape):
    """Approximate the vertical space a shape's text actually needs, in inches."""
    w_in = shape.width / EMU
    total = 0.0
    for p in shape.text_frame.paragraphs:
        txt = "".join(r.text for r in p.runs)
        if not txt:
            continue
        sizes = [r.font.size.pt for r in p.runs if r.font.size]
        pts = max(sizes) if sizes else 12
        char_w = (pts * 0.48) / 72          # Calibri average advance width
        usable = max(w_in - 0.22, 0.3)
        lines = max(1, -(-len(txt) // max(1, int(usable / char_w))))
        after = (p.space_after.pt if p.space_after else 0) / 72
        total += lines * (pts * 1.24) / 72 + after
    return total


def check(path, forbid_source_ids=False, label=None):
    prs = Presentation(str(path))
    sw, sh = prs.slide_width, prs.slide_height
    failures, notices = list(audit(path)), []

    if abs(sw / EMU - 13.333) > 0.02 or abs(sh / EMU - 7.5) > 0.02:
        failures.append(f"slide size is {sw/EMU:.2f}x{sh/EMU:.2f} in, expected 16:9")

    all_text = []
    slide_text = []      # slide-visible only — notes are exempt from the ID check

    for i, slide in enumerate(prs.slides, 1):
        items = []
        for shape in slide.shapes:
            if shape.shape_type is not None and "PICTURE" in str(shape.shape_type):
                failures.append(f"s{i}: raster image present (VISUAL-DIRECTION §9)")
            if shape.left is None or shape.top is None:
                continue
            right = shape.left + (shape.width or 0)
            bottom = shape.top + (shape.height or 0)
            if (shape.left < -Inches(0.05) or shape.top < -Inches(0.05)
                    or right > sw + Inches(0.05) or bottom > sh + Inches(0.05)):
                failures.append(
                    f"s{i}: shape off-canvas — L={shape.left/EMU:.2f} "
                    f"T={shape.top/EMU:.2f} R={right/EMU:.2f} B={bottom/EMU:.2f}")

            has_text = shape.has_text_frame and shape.text_frame.text.strip()
            if has_text:
                all_text.append((i, shape.text_frame.text))
                slide_text.append((i, shape.text_frame.text))
            items.append({
                "t": shape.top / EMU,
                "l": shape.left / EMU,
                "r": right / EMU,
                "b": bottom / EMU,
                "need": needed_height(shape) if has_text else 0.0,
                "text": shape.text_frame.text[:44] if has_text else "",
                # autoshapes centre and clip their own text; only free-standing
                # textboxes actually spill into whatever sits below them
                "free": bool(has_text and shape.shape_type is not None
                             and "TEXT_BOX" in str(shape.shape_type)),
            })

        if not slide.has_notes_slide:
            failures.append(f"s{i}: no speaker notes (VISUAL-DIRECTION §8)")
        else:
            txt = slide.notes_slide.notes_text_frame.text
            all_text.append((i, txt))
            for part in NOTE_PARTS:
                if part not in txt:
                    failures.append(f"s{i}: speaker notes missing '{part}'")

        for a in items:
            if not a["free"]:
                continue
            est_b = a["t"] + a["need"]
            if est_b > 7.45:
                failures.append(
                    f"s{i}: text ends at {est_b:.2f} in, past the slide — "
                    f"{a['text']!r}")
                continue
            if est_b <= a["b"] + 0.02:
                continue
            for b in items:
                if b is a or not b["free"]:
                    continue
                if min(a["r"], b["r"]) - max(a["l"], b["l"]) < 0.4:
                    continue
                if a["t"] + 0.05 <= b["t"] < est_b - 0.03:
                    failures.append(
                        f"s{i}: {a['text']!r} needs {a['need']:.2f} in and runs "
                        f"into {b['text']!r}")
                    break

    for patterns, kind, sink in ((PROHIBITED, "prohibited claim phrasing", failures),
                                 (RESTRICTED, "restricted phrase", notices)):
        for pattern in patterns:
            for idx, txt in all_text:
                for m in re.finditer(pattern, txt, re.I):
                    start = max(0, m.start() - 70)
                    excerpt = f"…{txt[start:m.end() + 70]}…".replace("\n", " ")
                    if negated(txt, m):
                        notices.append(
                            f"s{idx}: /{pattern}/ appears inside a negation — "
                            f"{excerpt}")
                    else:
                        sink.append(f"s{idx}: {kind} /{pattern}/ — {excerpt}")

    if forbid_source_ids:
        for pattern in SOURCE_IDS:
            for idx, txt in slide_text:
                for m in re.finditer(pattern, txt):
                    start = max(0, m.start() - 50)
                    excerpt = f"…{txt[start:m.end() + 50]}…".replace("\n", " ")
                    failures.append(
                        f"s{idx}: internal reference /{pattern}/ visible on the "
                        f"slide — move it to the speaker notes — {excerpt}")

    header = f"{label}: " if label else ""
    print(f"{header}{len(prs.slides)} slides · {sw/EMU:.3f} x {sh/EMU:.3f} in")
    if notices:
        print(f"\n{len(notices)} restricted phrase(s) — confirm each is a negation "
              f"or a correctly qualified use:")
        for n in notices:
            print("  ·", n)
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for failure in failures:
            print("  ✗", failure)
        return 1
    checks = "geometry, speaker notes, text fit and prohibited claims"
    if forbid_source_ids:
        checks += " and no source-ID leaks"
    print(f"\nno failures: {checks} all clean")
    return 0


def check_deck(spec):
    if not spec.path.exists():
        print(f"✗ {spec.name}: {spec.path} not built — run `just deck {spec.name}`")
        return 1
    return check(spec.path, forbid_source_ids=not spec.show_source_ids,
                 label=spec.name)


def main(argv):
    if not argv:
        raise SystemExit(
            f"usage: verify.py <{' | '.join(DECKS)} | --all | path.pptx>"
        )
    if argv[0] == "--all":
        worst = 0
        for spec in DECKS.values():
            worst |= check_deck(spec)
            print()
        return worst
    if argv[0] in DECKS:
        return check_deck(resolve(argv[0]))
    return check(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
