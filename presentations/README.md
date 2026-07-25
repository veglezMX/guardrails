**AI Guardrails — Presentations**

**Authority level:** L5 (generated)
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** `solution-docs/` (L1–L4)

---

## What is here

| File | Slides | Audience | Purpose |
| --- | --- | --- | --- |
| `AI-Guardrails-Internal-Proposal.pptx` | 27 | Internal team review | The main proposal: capabilities, use cases/modes, integration overview, delivery plan, effort estimation |
| `Mode-A-Remote-Evaluation.pptx` | 11 | Developers integrating today | How a developer integrates Mode A — connector, Python SDK, TypeScript SDK, raw HTTP, failure behavior, observe→enforce |
| `Mode-B-Guarded-Gateway.pptx` | 6 | Developers, Phase 3 planning | How a developer integrates Mode B — base-URL swap, decision metadata, what changes and what does not |
| `Mode-C-Customer-Managed-Runtime.pptx` | 7 | Developers + platform, Phase 4 planning | How a team deploys and integrates Mode C — Helm/Terraform, local endpoint, offline behavior, telemetry control |

One integration file per deployment mode, as requested; the main deck's section 3 links to all three.

---

## Authority and precedence

These decks are **L5 — generated**. They are regenerated from `solution-docs/` and must never be
treated as a source of truth. Where a deck and any L1–L4 document ever disagree, the document wins
and the deck must be regenerated or corrected — never the reverse
(see [`../solution-docs/70-governance/DOCUMENT-GOVERNANCE.md`](../solution-docs/70-governance/DOCUMENT-GOVERNANCE.md)).

Before presenting any of this externally, read
[`../solution-docs/70-governance/CLAIMS-REGISTER.md`](../solution-docs/70-governance/CLAIMS-REGISTER.md).
The decks already carry the non-negotiable caveats — Mode A is not data-local (AGR-009), a cooperative
connector is not a hard security boundary (AGR-018), guardrails are not authorization (AGR-010) — and
those caveats must not be edited out.

---

## Two things in these decks that are *not* in `solution-docs/`

The source-of-truth set deliberately records headcount, duration, and budget as **TBD**, because no such
figure exists in `seed.md` or any accepted agreement. The proposal deck was asked for an effort estimate,
so two categories of new content were authored here and are labelled as such on the slides themselves:

1. **Effort and schedule figures** (main deck, section 5 and the "Proposed schedule and gates" slide):
   a bottom-up FTE × duration model — 9 roles, 5 phases, ≈545 person-weeks / ≈21,800 hours over ≈62 weeks.
   Rate and cost columns are intentionally left blank for finance. Phase *sequencing* comes from AGR-020;
   the *durations* do not.
2. **Illustrative code examples** (per-mode decks): the evaluation contract's shape — stages, decision
   fields, actions, reason codes — comes from
   [`../solution-docs/50-architecture/INTEGRATION-ARCHITECTURE.md`](../solution-docs/50-architecture/INTEGRATION-ARCHITECTURE.md) §3.
   Concrete endpoint paths, JSON field names, config keys, and package names are **not yet ratified** and
   will be pinned by the published API specification. Each mode deck states this on its title slide.

If leadership adopts the effort figures, they belong in
[`../solution-docs/00-source-of-truth/ASSUMPTIONS.md`](../solution-docs/00-source-of-truth/ASSUMPTIONS.md)
(or `AGREEMENTS.md` if committed) — not in this directory.

---

## Regenerating

```bash
pip install python-pptx
python3 presentations/_build/build_main.py     # main proposal deck
python3 presentations/_build/build_modes.py    # the three per-mode decks
```

`_build/deckkit.py` holds the shared theme (palette, type scale, slide primitives). Edit content in the
two `build_*.py` scripts rather than editing the `.pptx` files by hand, so the decks stay reproducible.
