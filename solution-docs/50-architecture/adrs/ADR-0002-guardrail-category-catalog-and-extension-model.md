**Title: ADR-0002 — Adopt a Guardrail-Category Catalog and a Four-Seam Extension Model**

**Authority level:** L1 (Accepted — pending owner ratification)
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

# ADR-0002: Adopt a Guardrail-Category Catalog and a Four-Seam Extension Model

## Status

**Accepted (pending owner ratification)**

This ADR records the design behind [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) and [DEC-009](../../00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision). Its Validation Required §2 exercise — the first end-to-end catalog graduation — has now been run and recorded as [DEC-020](../../00-source-of-truth/DECISIONS.md#dec-020--graduate-gr-017-per-category-decision-metrics-from-vision-to-committed-scope); see Validation Required below. On this working branch, all new decisions land `Proposed` / owner-to-ratify, and the owner ratifies by merging; this ADR's status is recorded here as `Accepted` in that sense — it becomes the L1 record of authority (superseding the need to cite AGR-021/DEC-009 for what this ADR itself settles) once the branch is merged. Per [`00-source-of-truth/TRACEABILITY-MATRIX.md`](../../00-source-of-truth/TRACEABILITY-MATRIX.md), the AGR-021 traceability row closes on the ADR-track upon that ratification.

## Context

The platform's growth surface is already open by design: the domain model reserves future evaluation stages and decision actions ([AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)), the evaluator-adapter pattern permits new engines without contract change ([AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)), connectors are additive clients of one invocation contract ([AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)), and `50-architecture/REQUIREMENTS.md` §13 records eighteen future-scope requirements phrased as "the architecture must permit adding X."

What the documentation set lacked was a **named taxonomy of guardrail controls**. "Category" existed only implicitly — inside `Policy.evaluator_configuration` and evaluator-local reason codes. Without a shared taxonomy:

- New guardrail ideas have no place to land except ad hoc additions to scope discussions, which the risk register identifies as the "product over-expansion" failure mode ([`RISK-REGISTER.md`](../../70-governance/RISK-REGISTER.md); `TECHNICAL-RISKS.md` §4.8).
- There is no honest, stable way to answer "what could this platform eventually guard against, and who owns what it will not" — every answer either understates the vision or overstates delivery.
- Externally informed control inventories (OWASP LLM Top-10-style risks, NIST AI RMF-style lifecycle controls) cannot be mapped onto the product without inventing vocabulary each time.

The question this ADR resolves: **how should the product represent a broad, growing guardrail vision without contradicting its own scope discipline (DEC-008, SCOPE.md §8, [`SCOPE.md`](../../10-charter/SCOPE.md), AGR-010)?**

## Decision

Adopt two artifacts, at two authority levels, plus a governance rule set:

1. **A guardrail-category catalog** — [`20-product/GUARDRAIL-CATALOG.md`](../../20-product/GUARDRAIL-CATALOG.md) (L2): eighteen change-controlled categories, `GR-001` through `GR-018`. Each category carries an enforcement layer (`evaluator` / `connector-gateway` / `control-plane` / `out-of-boundary`), control-type metadata, the stages it uses, its evaluator family, and a phase status drawn from a fixed four-value vocabulary: `Core (Phases 1–2)`, `Later phase — committed`, `Vision — uncommitted`, `Out-of-boundary`. Item-level guardrails inside a category are illustrative and not change-controlled.
2. **An extensibility architecture** — [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../EXTENSIBILITY-ARCHITECTURE.md) (L3): formalizes the **four extension seams** through which all guardrail growth flows — new evaluation stage, new evaluation engine, new decision action, new connector — each with its contract surface, change process, inherited constraints, conformance requirements, and governance gate; plus the graduation process by which a `Vision — uncommitted` catalog entry becomes committed scope.
3. **Governance rules:** category-level changes (add/remove/rename/reclassify) are register-level (Path A per `70-governance/DOCUMENT-GOVERNANCE.md` §6); item-level edits are derived-document edits (Path B). Graduation of a vision entry is the recorded scope-change decision required by `10-charter/SCOPE.md` §8 — never a silent promotion. Entries whose subject matter falls under [`SCOPE.md`](../../10-charter/SCOPE.md) or AGR-010 are classified `Out-of-boundary` with a named external owner and are never presented as future deliverables.

The catalog is **classification, not commitment**: it does not alter the defined-and-estimated full feature set (`CAP-001`–`CAP-034`, [DEC-008](../../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases)).

## Alternatives Considered

### Alternative 1 — Item-level change-controlled register (every guardrail gets a stable ID)

Give each of the several hundred individual guardrails its own change-controlled identifier (`GR-001-001` style) from day one.

**Why rejected:**

- Applies register-grade change control (never renumber, never delete, full traceability rows) to hundreds of entries, most of which are vision-status text — governance overhead out of proportion to their authority.
- Freezes an evolving inventory prematurely; vision items should be cheap to reword, split, and merge until they graduate.
- Item-level IDs can be introduced later, per category, exactly when an item graduates — no information is lost by deferring.

### Alternative 2 — Single combined document in `50-architecture/`

Put the catalog and the extension model in one architecture document.

**Why rejected:**

- Mixes authority levels: a catalog that defines what the product classifies is a **defining (L2)** artifact like `CAPABILITIES.md`; the extension mechanics are **analytical (L3)**. `70-governance/DOCUMENT-GOVERNANCE.md` §2 separates these deliberately.
- The catalog must be consumable by product, sales-adjacent, and governance readers who never open the architecture set; the extension model is for architects. One document serves neither well.

### Alternative 3 — No catalog; grow ad hoc through the existing future-scope lists

Keep relying on [`SCOPE.md`](../../10-charter/SCOPE.md), `SCOPE.md` §4, and `REQUIREMENTS.md` §13 as the only statements of growth direction.

**Why rejected:**

- Those lists enumerate committed later-phase items only; they cannot hold uncommitted vision without corrupting their meaning — which is precisely how silent scope creep starts.
- Leaves no stable vocabulary for classifying externally sourced control inventories, so every such conversation re-invents categories.
- Leaves the out-of-boundary story implicit; AGR-010 and [`SCOPE.md`](../../10-charter/SCOPE.md) exclusions are strongest when the catalog names an external owner for each excluded control family.

### Alternative 4 — Catalog with category-level IDs, split L2/L3 placement (chosen)

**Why accepted:**

- Category-level change control matches the granularity at which decisions are actually made, while item tables stay cheap to evolve.
- The four-value status vocabulary lets one catalog hold delivered, committed, envisioned, and excluded controls without misrepresenting any of them.
- The four-seam extension model formalizes machinery that already exists (AGR-004/005/006/007/017), so no new runtime concepts are introduced — the domain model is extended, never forked (AGR-017).

## Consequences

### Positive

- Guardrail growth has a stated direction: any new control idea is first classified into a GR category, then (if pursued) graduated through a governed path — replacing ad hoc scope pressure with a pipeline.
- Honest external communication becomes easier, not harder: the catalog is the canonical map of what is delivered, committed, envisioned, and explicitly someone else's job.
- The extension seams give implementers a checklist per extension type — which registers change, which constraints are inherited (CON-015/CON-016 for stages; CON-003/004/005/006 for evaluators), which conformance rules apply (AGR-006 back-compat).
- Externally informed inventories (OWASP/NIST-style) can be mapped onto stable category IDs without contract impact.

### Negative

- Two more documents to maintain, plus cross-references in roughly twenty existing documents; drift between the catalog and `CAPABILITIES.md` statuses becomes a new failure mode to police.
- The catalog's breadth invites misreading as a commitment or as delivered coverage despite its status vocabulary — this must be actively countered in claims discipline.

### Risks

- **Catalog misread as commitment** — mitigated by the four-value status vocabulary, the claims-register row rejecting "covers all guardrail categories," and DEC-009's explicit statement that vision items are outside the DEC-008 estimation. Tracked in `70-governance/RISK-REGISTER.md` (RSK-010).
- **Taxonomy drift / evaluator sprawl** — categories or evaluators multiplying without discipline; mitigated by Path A control on category changes, `NFR-MAINT-003`, and the adapter-pattern requirement that every engine sits behind the standard contract. Tracked in `TECHNICAL-RISKS.md` §4.9.
- **Boundary erosion** — out-of-boundary entries (GR-010 identity/authorization, DLP-complete items in GR-003, org-wide enforcement in GR-008, business authorization in GR-018) drifting toward "future work" phrasing over time, contradicting AGR-010 and [`SCOPE.md`](../../10-charter/SCOPE.md); mitigated by the catalog's out-of-boundary map naming an external owner per exclusion and by claims-register enforcement.

## Validation Required

This ADR is validated when:

1. The catalog and extensibility documents exist, are registered in `70-governance/DOCUMENT-GOVERNANCE.md` §4, and contain no statement contradicting `AGREEMENTS.md`, `CONSTRAINTS.md`, or [`SCOPE.md`](../../10-charter/SCOPE.md) (checked via the claims-register review).
2. The first real graduation exercise is executed end-to-end: one `Vision — uncommitted` entry is taken through OQ → AGR/DEC → CAP/FR classification per `EXTENSIBILITY-ARCHITECTURE.md` §10, demonstrating the pipeline works and produces a recorded scope-change decision rather than a silent promotion.

   **Executed.** [`GUARDRAIL-CATALOG.md`](../../20-product/GUARDRAIL-CATALOG.md) GR-017's "Per-category decision metrics" item was taken through the pipeline: gating question [OQ-019](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-019--reason-code-taxonomy-and-guardrail-category-as-an-auditmetric-dimension) (resolved by [DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension)) → recorded scope-change decision [DEC-020](../../00-source-of-truth/DECISIONS.md#dec-020--graduate-gr-017-per-category-decision-metrics-from-vision-to-committed-scope) → CAP classification (new CAP-037, pending formal `CAPABILITIES.md`/`TRACEABILITY-MATRIX.md` registration at ratification). The catalog entry now reads `Later phase — committed (proposed, DEC-020)`, not a silent status edit — the change is traceable to DEC-020 by name.
3. The claims register review confirms no approved external wording claims delivered or committed coverage for vision or out-of-boundary categories.

## Related Agreements

| Agreement | Relevance to this ADR |
|---|---|
| [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) | The agreement this ADR documents: the taxonomy, the extension model, and their governance. |
| [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | Extend-don't-fork rule: guardrail category is a classification dimension, not a seventh domain entity. |
| [AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract) | The evaluator-adapter seam this model formalizes. |
| [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) | Categories and items must stay expressible in vendor-neutral terms. |
| [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) | Every seam change is a versioned, backward-compatible contract change across all three modes. |
| [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) | The connector seam: new connectors implement the invocation contract only. |
| [AGR-010](../../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization) | Forces GR-010 (and parts of GR-009/GR-018) to out-of-boundary status with a named external owner. |
| [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) | `Warn` is a binding-mode state on this ladder, not a decision action — a classification the extension model makes normative. |
| [AGR-020](../../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) | Phase-sequences the later-phase categories (GR-005, GR-006, GR-009, GR-011, GR-013); Phase 5 remains a separate expansion. |
| [DEC-008](../../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases) / [DEC-009](../../00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision) | The estimation basis the catalog must not alter, and the decision adopting the catalog as vision. |

## Related Documents

- [Guardrail-Category Catalog](../../20-product/GUARDRAIL-CATALOG.md)
- [Extensibility Architecture](../EXTENSIBILITY-ARCHITECTURE.md)
- [Capability Catalog](../../20-product/CAPABILITIES.md)
- [Scope](../../10-charter/SCOPE.md)
- [Technical Risks](../TECHNICAL-RISKS.md)
- [Claims Register](../../70-governance/CLAIMS-REGISTER.md)
