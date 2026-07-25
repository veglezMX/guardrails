# Close-Out — All Open Questions Resolved + Doc Refactor

**Branch:** `close-open-questions`
**Date:** 2026-07-24
**Status of everything below:** `Proposed` / **pending owner ratification** — the owner ratifies by merging this branch. Nothing here carries L1 authority until then.

This branch closes all 20 open questions, records the decisions that close them, accepts ADR-0002 (with its first graduation exercise), absorbs two owner-chosen scope expansions, and refactors the derived documentation set to stay internally consistent.

## OQ → closing decision → where it landed

| OQ | Closing decision | Kind |
|---|---|---|
| OQ-001 enforcement posture | DEC-010 (Mode A front door, cooperative) | design |
| OQ-002 PrivateLink | DEC-017 (public GA; on-demand) | deferral+trigger |
| OQ-003 regions | DEC-018 (multi-region availability from GA, tenant-pinned) — **scope expansion** | owner |
| OQ-004 streaming | DEC-016 (Phase 5) | deferral |
| OQ-005 retrieval/injection | DEC-016 (Phase 5) | deferral |
| OQ-006 tool/MCP | DEC-016 (Phase 5) | deferral |
| OQ-007 approvals | DEC-016 (Phase 5) | deferral |
| OQ-008 languages | DEC-017 (EN-only launch) | owner |
| OQ-009 accounts | DEC-017 (2-account until triggered) | owner |
| OQ-010 air-gap | DEC-017 (trigger-gated) | deferral+trigger |
| OQ-011 commercial | DEC-017 (free MVP; meter Phase 3) | owner |
| OQ-012 signing/inheritance | DEC-011 (inheritance v0) + DEC-015 (signing Phase 4) | design |
| OQ-013 evaluator plugins | DEC-015 (Phase-5, security-gated) | design |
| OQ-014 observe→enforce | DEC-012 (criteria framework; thresholds owner-set) | design |
| OQ-015 compliance | DEC-017 (SOC 2 on trigger) | owner |
| OQ-016 active-active | DEC-019 (committed later-phase) — **scope expansion, supersedes seed §6.3** | owner |
| OQ-017 token/cost metadata | DEC-014 (contract stays lean) | design |
| OQ-018 stage vocabulary | DEC-014 (seven stages frozen) | design |
| OQ-019 reason codes | DEC-013 + ADR-0003 (category-namespaced, `guardrail_category` first-class) | design |
| OQ-020 mandatory authority (new) | DEC-011 (security-role pins; strengthen-only) | design |

## New artifacts

- **Decisions:** DEC-010 … DEC-020 (11 entries) in `00-source-of-truth/DECISIONS.md`.
- **ADR-0003** (reason-code taxonomy) — Proposed.
- **ADR-0002** — flipped to **Accepted (pending owner ratification)** after its Validation §2 graduation exercise (DEC-020 graduates GR-017 per-category metrics vision→committed).
- **Capabilities:** CAP-035 (multi-region availability + residency enforcement), CAP-036 (active-active), CAP-037 (per-category decision metrics). Rollup: later-phase CAP-018–037 = 20, total 37.
- **`20-product/SERVING-MODEL.md`** — product-facing serving/consumption model (DEC-010/011).
- **`60-delivery/FEATURE-BACKLOG.md`** — 12-axis evolution ladders.

## Register-level changes requiring explicit owner ratification

1. **DEC-018** amends **AGR-013** (multi-region availability from GA; two-account model retained).
2. **DEC-019** supersedes a **seed §6.3** exclusion (multi-region active-active) and **expands the DEC-008 estimation basis** (CAP-035/036/037 added). Recorded via a DEC-008 "Later amendment (Proposed)" note; seed.md itself left unedited (frozen historical input).
3. **ADR-0002 acceptance** closes the AGR-021 traceability gap (TRACEABILITY-MATRIX AGR-021 row → ADR-0002 Accepted).

## Refactor / consistency sweep

Reconciled to the decisions above: TRACEABILITY-MATRIX, SCOPE, CAPABILITIES, PRODUCT-ROADMAP, DEPLOYMENT-ARCHITECTURE, DATA-ARCHITECTURE, CLAIMS-REGISTER, TARGET-USERS, GLOSSARY, GUARDRAIL-CATALOG, FEATURE-BACKLOG, README, and (after final review) CHARTER, PRODUCT-OVERVIEW, IMPLEMENTATION-STRATEGY, OBJECTION-HANDLING, DECK-BRIEF, ASSUMPTIONS, OPERABILITY, ARCHITECTURE-REPORT, COMPONENT-ARCHITECTURE, FEASIBILITY-ASSESSMENT.

## Verification (final gate)

- No OQ left `Status: Open` (0).
- No dangling `.md` links in changed docs.
- DEC-010…020 all present with summary rows; anchors resolve.
- CAP rollup arithmetic correct (20 / 37).
- Active-active no longer framed as excluded anywhere except the two intentional historical/annotated mentions (OQ-016 question body; DEC-008 original list with supersession note).
- `Assigned region` rename reverted to `Allowed Regions`; ADR-0002 index status consistent; no ephemeral plan/spec-section refs in L1.
- Final whole-branch review run; all findings (C1, C2, I1, I2, I3, I4) fixed and re-verified.

## Next step

Review the branch. **Merging = owner ratification** of the 11 decisions, ADR-0002 acceptance, ADR-0003, the three new capabilities, and the two scope expansions. Until merge, all of it stands as Proposed.
