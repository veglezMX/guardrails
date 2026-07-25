**AI Guardrails — Changelog**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

> This is the chronological ledger of meaningful changes to the AI Guardrails **solution definition** — the substance of what the product is, how it is scoped, and how it is governed — as recorded across the `solution-docs/` set. It follows [Keep a Changelog](https://keepachangelog.com/)-style conventions: entries are grouped under a version/date heading with `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, and `Security` subsections, newest first, and the log is additive-only — prior entries are never rewritten, only appended to.
>
> This ledger is distinct from [`00-source-of-truth/DECISIONS.md`](00-source-of-truth/DECISIONS.md), which records product, organizational, and delivery decisions (`DEC-NNN`). A single accepted decision or agreement can, and typically will, produce several entries here as each affected document is authored or corrected. It is also distinct from [`00-source-of-truth/AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md), which is the authoritative (L1) register of *what was agreed*; this file records *when the documentation came to reflect it*. The governing rules for how this ledger is used — including the Path A / Path B change-control distinction — are defined in [`70-governance/DOCUMENT-GOVERNANCE.md §9`](70-governance/DOCUMENT-GOVERNANCE.md#9-change-history--changelogmd).
>
> As with every document at Authority level L4, this changelog summarizes and dates changes already settled at L1–L3; it introduces no new agreement, scope, or claim of its own.

---

## Table of contents

- [Unreleased](#unreleased)
- [2026-07-24 — Guardrail-category catalog and extensibility architecture](#2026-07-24--guardrail-category-catalog-and-extensibility-architecture)
- [2026-07-24 — Initial documentation set](#2026-07-24--initial-documentation-set)

---

## [Unreleased]

**Change type:** Path A (amendment of the L1 register, propagated to every derived layer)
**Trigger:** [DEC-008](00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases) — the MVP-bounded scope was too narrow to support a valid delivery estimation; the solution is now defined and estimated over the full feature set, delivered across five phases.
**Owner:** Product lead, Technical lead (joint)

### Added
- [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) **DEC-008** — supersedes DEC-005; records the removal of the MVP scope boundary and MVP success demonstration from the register, the register renumbering, and the full-feature-set estimation basis.

### Changed
- [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) renumbered: "Meaning of import" is now **AGR-019**, "Five-phase delivery" is now **AGR-020**; register is AGR-001–AGR-020 (20 agreements).
- Scope reframed across the derived set (charter, product, executive, sales-deck, architecture, delivery, governance, agent-input layers): the core capability set (former "MVP scope") is now **Phases 1–2 core scope**, and the former "future scope" capabilities are **later-phase scope — committed and estimated (Phases 3–5)**. Phase gating per AGR-020 is unchanged; the seed.md §6.3 product-category exclusions remain excluded; the claims discipline (AGR-009, AGR-010, AGR-018, TBD figures) is unchanged.
- Executive and sales-deck ask reframed: approval of the full solution (five-phase program) on a full-feature estimation basis, replacing the Phase-1-only ask; budget/headcount figures remain TBD until supplied.
- The two-independent-LiteLLM-instances demonstration reframed from register-level "MVP success demonstration" to the **Phase 1–2 validation demonstration** owned by [`60-delivery/VALIDATION-PLAN.md`](60-delivery/VALIDATION-PLAN.md).
- [`40-sales-deck/AI-Guardrails-Project-Proposal.pptx`](40-sales-deck/) regenerated to match the reframed proposal.

### Deprecated
- _Nothing._

### Removed
- Former **AGR-019 — MVP scope boundaries** and former **AGR-020 — MVP success demonstration** removed from [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) per DEC-008.

### Fixed
- _Nothing._

### Security
- _Nothing._

---

## [2026-07-24] — Guardrail-category catalog and extensibility architecture

**Change type:** Path A ([AGR-021](00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) appended to the L1 register) propagated through the derived set
**Trigger:** [DEC-009](00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision) — adopt an eighteen-category guardrail taxonomy as directional vision and formalize the architecture's growth model, so incremental guardrail additions are governed instead of ad hoc.
**Owner:** Product lead, Technical lead, Security and governance lead (joint)

### Added
- [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) **AGR-021** — Guardrail-category taxonomy and extension model (register is now AGR-001–AGR-021).
- [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) **DEC-009** — adopt the catalog as directional vision; Vision-status entries stay outside the DEC-008 estimation basis.
- [`OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md) **OQ-017** (token/cost metadata in the evaluation contract), **OQ-018** (stage-vocabulary expansion beyond the reserved seven), **OQ-019** (reason-code taxonomy and category as an audit/metric dimension), under a new theme "I. Guardrail-catalog growth and contract vocabulary".
- [`20-product/GUARDRAIL-CATALOG.md`](20-product/GUARDRAIL-CATALOG.md) — **new L2 register**: eighteen change-controlled categories `GR-001`–`GR-018` with enforcement layer, control types, stages, evaluator family, four-value phase status (`Core` / `Later phase — committed` / `Vision — uncommitted` / `Out-of-boundary`), illustrative item tables, an out-of-boundary and exclusion map with named external owners, and a vision-status policy-pack sketch.
- [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](50-architecture/EXTENSIBILITY-ARCHITECTURE.md) — **new L3 document**: the four extension seams (new stage / new engine / new action / new connector) with per-seam process, inherited constraints, conformance rules, and governance gates; the category attachment model; and the catalog graduation pipeline (§10).
- [`50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md`](50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md) — **Proposed** ADR recording the design and alternatives.
- [`50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md`](50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) **NFR-MAINT-003** — bounded evaluator and category sprawl.
- [`50-architecture/TECHNICAL-RISKS.md`](50-architecture/TECHNICAL-RISKS.md) **§4.9** — category-taxonomy drift and evaluator sprawl (risk #9).
- [`50-architecture/TRADE-OFFS.md`](50-architecture/TRADE-OFFS.md) **§10** — guardrail-coverage breadth vs. contract surface and evaluation latency (decision #7); subsequent sections renumbered.
- [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md) **RSK-010** — guardrail catalog misread as commitment / taxonomy drift.
- [`70-governance/CLAIMS-REGISTER.md`](70-governance/CLAIMS-REGISTER.md) §7 — new **Not approved** row: "covers all eighteen guardrail categories / complete AI-risk coverage", with approved substitute wording.
- [`70-governance/GLOSSARY.md`](70-governance/GLOSSARY.md) — new terms: guardrail category, guardrail item, control type, enforcement layer, extension seam, graduation, policy pack, Vision — uncommitted.

### Changed
- [`TRACEABILITY-MATRIX.md`](00-source-of-truth/TRACEABILITY-MATRIX.md) — AGR-021 row, ADR-0002 backlog row, coverage-check row for the new Not-approved claim; register-range text updated to AGR-001–AGR-021 (also in [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) and [`OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md)).
- [`20-product/CAPABILITIES.md`](20-product/CAPABILITIES.md) — new "Guardrail-category cross-reference" section (GR→CAP mapping; "the catalog classifies, CAPABILITIES commits").
- [`20-product/PRODUCT-ROADMAP.md`](20-product/PRODUCT-ROADMAP.md) — new "Guardrail-category coverage by phase" view; Vision/Out-of-boundary categories appear in no phase row.
- [`50-architecture/CONTAINER-ARCHITECTURE.md`](50-architecture/CONTAINER-ARCHITECTURE.md) §5, [`REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md) §13–§14 (new `Guardrail category` column on FR-078–FR-094), [`SECURITY-ARCHITECTURE.md`](50-architecture/SECURITY-ARCHITECTURE.md) §10–§11 (GR-009/GR-010 boundary reconciliation), [`DEPLOYMENT-ARCHITECTURE.md`](50-architecture/DEPLOYMENT-ARCHITECTURE.md) §5 (catalog owns the category×mode statement), [`OPERABILITY.md`](50-architecture/OPERABILITY.md) §3/§6.1 (category as planned metric dimension, gated on OQ-019), [`ARCHITECTURE-REPORT.md`](50-architecture/ARCHITECTURE-REPORT.md) §3.3/§7.2, and [`TECHNICAL-RISKS.md`](50-architecture/TECHNICAL-RISKS.md) §4.5/§4.8 — cross-references to the catalog and extension model added.
- [`70-governance/DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) §4 — documentation-set map rows for both new documents; [`70-governance/REFERENCES.md`](70-governance/REFERENCES.md) — internal rows for both new documents, §11 retitled to include the NIST AI RMF orientation reference; [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md) RSK-006 — catalog noted as the over-expansion mitigation channel; [`50-architecture/adrs/README.md`](50-architecture/adrs/README.md) — ADR-0002 indexed as Proposed.
- [`README.md`](README.md) §5 and [`DOCUMENT-MAP.md`](DOCUMENT-MAP.md) — both new documents and ADR-0002 registered in the navigation set.

### Deprecated
- _Nothing._

### Removed
- _Nothing._

### Fixed
- _Nothing._

### Security
- _Nothing._

---

## [2026-07-24] — Initial documentation set

**Change type:** Path A (baseline authorship — establishes the documentation set itself, not a correction against a prior version)
**Trigger:** Initial distillation of [`seed.md`](../seed.md) into the accepted [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) register, followed by full derivation of the layered documentation set.
**Owner:** Product lead, Technical lead, Security and governance lead (joint)

### Added

Generated the complete initial `solution-docs/` documentation set from the raw solution charter ([`seed.md`](../seed.md)), distilled first into the authoritative agreements register and then derived outward into every downstream layer. This is the founding entry of the changelog: it establishes the baseline against which all future changes are tracked.

- **Source-of-truth agreements (`00-source-of-truth/`)** — [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) (AGR-001 through AGR-022, the authoritative L1 register), [`CONSTRAINTS.md`](00-source-of-truth/CONSTRAINTS.md), [`DECISIONS.md`](00-source-of-truth/DECISIONS.md), [`ASSUMPTIONS.md`](00-source-of-truth/ASSUMPTIONS.md), and [`OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md).
- **Charter (`10-charter/`)** — [`CHARTER.md`](10-charter/CHARTER.md), [`PRINCIPLES.md`](10-charter/PRINCIPLES.md), [`SCOPE.md`](10-charter/SCOPE.md), and [`SUCCESS-CRITERIA.md`](10-charter/SUCCESS-CRITERIA.md).
- **Product materials (`20-product/`)** — [`CAPABILITIES.md`](20-product/CAPABILITIES.md), [`PRODUCT-OVERVIEW.md`](20-product/PRODUCT-OVERVIEW.md), [`USE-CASES.md`](20-product/USE-CASES.md), [`TARGET-USERS.md`](20-product/TARGET-USERS.md), [`VALUE-PROPOSITION.md`](20-product/VALUE-PROPOSITION.md), [`DIFFERENTIATORS.md`](20-product/DIFFERENTIATORS.md), [`PROBLEM-STATEMENT.md`](20-product/PROBLEM-STATEMENT.md), [`PRODUCT-ROADMAP.md`](20-product/PRODUCT-ROADMAP.md), and [`FAQ.md`](20-product/FAQ.md).
- **Executive materials (`30-executive/`)** — [`EXECUTIVE-BRIEF.md`](30-executive/EXECUTIVE-BRIEF.md), [`EXPECTED-OUTCOMES.md`](30-executive/EXPECTED-OUTCOMES.md), [`DECISION-REQUEST.md`](30-executive/DECISION-REQUEST.md), [`BUSINESS-CASE.md`](30-executive/BUSINESS-CASE.md), [`INVESTMENT-SUMMARY.md`](30-executive/INVESTMENT-SUMMARY.md), and [`RISKS-AND-DEPENDENCIES.md`](30-executive/RISKS-AND-DEPENDENCIES.md).
- **Sales-deck inputs (`40-sales-deck/`)** — [`DECK-BRIEF.md`](40-sales-deck/DECK-BRIEF.md), [`SLIDE-OUTLINE.md`](40-sales-deck/SLIDE-OUTLINE.md), [`SALES-NARRATIVE.md`](40-sales-deck/SALES-NARRATIVE.md), [`OBJECTION-HANDLING.md`](40-sales-deck/OBJECTION-HANDLING.md), [`PROOF-POINTS.md`](40-sales-deck/PROOF-POINTS.md), [`CUSTOMER-PAIN-POINTS.md`](40-sales-deck/CUSTOMER-PAIN-POINTS.md), [`AUDIENCE-AND-PERSONAS.md`](40-sales-deck/AUDIENCE-AND-PERSONAS.md), [`BENEFITS-AND-OUTCOMES.md`](40-sales-deck/BENEFITS-AND-OUTCOMES.md), [`CALL-TO-ACTION.md`](40-sales-deck/CALL-TO-ACTION.md), and [`VISUAL-DIRECTION.md`](40-sales-deck/VISUAL-DIRECTION.md).
- **Architecture report and supporting docs (`50-architecture/`)** — [`ARCHITECTURE-REPORT.md`](50-architecture/ARCHITECTURE-REPORT.md), [`SYSTEM-CONTEXT.md`](50-architecture/SYSTEM-CONTEXT.md), [`COMPONENT-ARCHITECTURE.md`](50-architecture/COMPONENT-ARCHITECTURE.md), [`CONTAINER-ARCHITECTURE.md`](50-architecture/CONTAINER-ARCHITECTURE.md), [`DATA-ARCHITECTURE.md`](50-architecture/DATA-ARCHITECTURE.md), [`DEPLOYMENT-ARCHITECTURE.md`](50-architecture/DEPLOYMENT-ARCHITECTURE.md), [`INTEGRATION-ARCHITECTURE.md`](50-architecture/INTEGRATION-ARCHITECTURE.md), [`SECURITY-ARCHITECTURE.md`](50-architecture/SECURITY-ARCHITECTURE.md), [`NON-FUNCTIONAL-REQUIREMENTS.md`](50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md), [`REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md), [`OPERABILITY.md`](50-architecture/OPERABILITY.md), [`FEASIBILITY-ASSESSMENT.md`](50-architecture/FEASIBILITY-ASSESSMENT.md), [`TECHNICAL-RISKS.md`](50-architecture/TECHNICAL-RISKS.md), [`TRADE-OFFS.md`](50-architecture/TRADE-OFFS.md), and the ADR log ([`adrs/README.md`](50-architecture/adrs/README.md), [`adrs/ADR-0001-example-decision.md`](50-architecture/adrs/ADR-0001-example-decision.md)).
- **Delivery plan (`60-delivery/`)** — [`IMPLEMENTATION-STRATEGY.md`](60-delivery/IMPLEMENTATION-STRATEGY.md), [`MILESTONES.md`](60-delivery/MILESTONES.md), [`RELEASE-CRITERIA.md`](60-delivery/RELEASE-CRITERIA.md), [`VALIDATION-PLAN.md`](60-delivery/VALIDATION-PLAN.md), [`DEPENDENCIES.md`](60-delivery/DEPENDENCIES.md), and [`TEAM-AND-OWNERSHIP.md`](60-delivery/TEAM-AND-OWNERSHIP.md).
- **Governance (`70-governance/`)** — [`DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md), [`CLAIMS-REGISTER.md`](70-governance/CLAIMS-REGISTER.md), [`RISK-REGISTER.md`](70-governance/RISK-REGISTER.md), [`GLOSSARY.md`](70-governance/GLOSSARY.md), [`REFERENCES.md`](70-governance/REFERENCES.md), and this changelog.
- **Agent-input contracts (`80-agent-inputs/`)** — [`README.md`](80-agent-inputs/README.md), [`OUTPUT-CONTRACTS.md`](80-agent-inputs/OUTPUT-CONTRACTS.md), [`CHARTER-AGENT.md`](80-agent-inputs/CHARTER-AGENT.md), [`ARCHITECTURE-REVIEW-AGENT.md`](80-agent-inputs/ARCHITECTURE-REVIEW-AGENT.md), [`EXECUTIVE-BRIEF-AGENT.md`](80-agent-inputs/EXECUTIVE-BRIEF-AGENT.md), and [`SALES-DECK-AGENT.md`](80-agent-inputs/SALES-DECK-AGENT.md).
- **Archive scaffold (`90-archive/`)** — [`README.md`](90-archive/README.md), reserved for future superseded and archived document versions per the lifecycle defined in [`DOCUMENT-GOVERNANCE.md §10`](70-governance/DOCUMENT-GOVERNANCE.md#10-document-lifecycle-and-archival).

**Also established:** the **L1–L5 authority model** governing the entire documentation set — Authoritative, Defining, Analytical, Communicative, and Generated — together with the non-contradiction rule, per-area ownership, and the change-control process (Path A for amending an L1 register, Path B for editing a derived document), all defined in [`DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md). Every document added in this entry declares its authority level in its own header per that model, and every document is traceable back to one or more entries in [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md).

| Directory | Authority level(s) added | Scope |
| --- | --- | --- |
| [`00-source-of-truth/`](00-source-of-truth/) | L1 | Agreements, constraints, decisions, assumptions, open questions |
| [`10-charter/`](10-charter/) | L2 | Charter, principles, scope, success criteria |
| [`20-product/`](20-product/) | L2 (`CAPABILITIES.md`) / L4 (remainder) | Product-facing overview, use cases, roadmap, FAQ |
| [`30-executive/`](30-executive/) | L3 (business case, investment, risks/dependencies) / L4 (brief, outcomes, decision request) | Executive narrative and analysis |
| [`40-sales-deck/`](40-sales-deck/) | L4 | Sales-enablement inputs |
| [`50-architecture/`](50-architecture/) | L2 (NFRs) / L3 (remainder) | Architecture report and supporting analysis |
| [`60-delivery/`](60-delivery/) | L3 | Delivery and implementation planning |
| [`70-governance/`](70-governance/) | L2 (governance, claims, glossary) / L3 (risk register) / cross-cutting (this changelog) | Documentation-set governance |
| [`80-agent-inputs/`](80-agent-inputs/) | L5 | Machine-consumable agent digests |
| [`90-archive/`](90-archive/) | — (scaffold only) | Reserved for archived document versions |

### Changed
- Not applicable — this is the founding entry of the documentation set.

### Deprecated
- Not applicable.

### Removed
- Not applicable.

### Fixed
- Not applicable.

### Security
- Not applicable — see [`50-architecture/SECURITY-ARCHITECTURE.md`](50-architecture/SECURITY-ARCHITECTURE.md) for the security requirements established in this initial set.

---

## Related documents

| Document | Relationship |
| --- | --- |
| [`00-source-of-truth/AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) | Authoritative (L1) register this changelog traces every substantive addition back to. |
| [`00-source-of-truth/DECISIONS.md`](00-source-of-truth/DECISIONS.md) | Chronological log of product/delivery decisions (`DEC-NNN`) — distinct from this file, which logs documentation-set edits. |
| [`70-governance/DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) | Defines the L1–L5 authority model, the Path A/Path B change-control process, and the required fields for entries in this changelog (§9). |
| [`70-governance/CLAIMS-REGISTER.md`](70-governance/CLAIMS-REGISTER.md) | Approved/rejected external-facing claim patterns enforced across every document added in this entry. |
| [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md) | Actively tracked product risk register introduced in this initial set. |
| [`10-charter/CHARTER.md`](10-charter/CHARTER.md) | The L2 charter this entire documentation set is derived from, alongside `seed.md`. |
