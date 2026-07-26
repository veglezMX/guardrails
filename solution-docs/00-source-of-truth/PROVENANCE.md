**AI Guardrails — Provenance of the Original Solution Charter**

**Authority level:** Not a member of the L1–L5 hierarchy — historical provenance record
**Status:** Accepted
**Date:** 2026-07-26

---

> This document records where the content of the **original solution charter** ended up. That charter was a single raw file (`seed.md`) at the repository root, written before this documentation set existed. Its content has been fully absorbed into `solution-docs/`, and the file has been removed so that this set is self-contained.
>
> This is a **map, not a source.** It carries no product content of its own. Nothing here may be cited as evidence for a requirement, claim, figure, or decision — cite the owning document named in the table below. If you find content that appears to exist only in this file, that is a defect: the content belongs in the owning document.

---

## Table of contents

- [1. Why this document exists](#1-why-this-document-exists)
- [2. How to read an "original charter §N" citation](#2-how-to-read-an-original-charter-n-citation)
- [3. Section-by-section map](#3-section-by-section-map)
- [4. Where the current registers supersede the original charter](#4-where-the-current-registers-supersede-the-original-charter)
- [5. Rules for using this document](#5-rules-for-using-this-document)
- [Related documents](#related-documents)

---

## 1. Why this document exists

The original charter was the raw input every document in `solution-docs/` was derived from. While it existed, documents cited it directly (`seed.md §9`, `seed.md §6.3`) as their provenance, and each document's `Derived from:` header named it.

That made the documentation set dependent on a file outside its own tree. Because the charter's content is now carried in full by the documents in this set — and because [`AGREEMENTS.md`](AGREEMENTS.md) had already become the accepted, currently governing distillation of it — the charter file was removed and every citation re-pointed at the document that actually owns the content today.

Two things follow, and both matter:

* **No content was lost.** Every claim, list, table, diagram, and requirement the charter carried is present in this set. The section-by-section map in [§3](#3-section-by-section-map) names the owning document for each.
* **The registers govern, not the charter.** Where the accepted agreements and decisions narrowed, expanded, or reversed something the charter stated, the register wins. Those deliberate divergences are listed in [§4](#4-where-the-current-registers-supersede-the-original-charter) so that a reader who remembers the original wording is not misled by it.

---

## 2. How to read an "original charter §N" citation

Most former charter citations were replaced with a direct link to the owning register or document — `seed.md §9` became [AGR-017](AGREEMENTS.md#agr-017--core-domain-model), `seed.md §6.3` became [`SCOPE.md`](../10-charter/SCOPE.md), and so on.

A citation that reads **"original charter §N"** and links back here survives in one situation only: where the document doing the citing *is itself* the owning document. [`CONSTRAINTS.md`](CONSTRAINTS.md), for example, is the document that now owns the charter's security requirements, so its own `Source` column cannot cite itself — it cites the charter section the constraint originated from, and links here.

Read such a citation as: *"this statement entered the documentation set from section N of the original charter, and this document is where it now lives."* It is a statement about origin, not about authority.

---

## 3. Section-by-section map

Every section of the original charter, and the document that owns its content today. Where a section's content is spread across several documents, the **owning document** column names the one that is authoritative for it; the **also expanded in** column names the significant secondary treatments.

| Charter § | Subject | Owning document | Also expanded in |
| --- | --- | --- | --- |
| §1 | Purpose | [`CHARTER.md`](../10-charter/CHARTER.md) | [`PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md), [`PROBLEM-STATEMENT.md`](../20-product/PROBLEM-STATEMENT.md) |
| §2 | Product vision | [`CHARTER.md`](../10-charter/CHARTER.md) | [`VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md), [`DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) |
| §3 | Core architectural decision (Modes A, B, C) | [AGR-002](AGREEMENTS.md#agr-002--three-deployment-modes) | [`DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md), [AGR-009](AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model), [`TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md) |
| §4 | Definition of the import capability | [AGR-019](AGREEMENTS.md#agr-019--meaning-of-import) | [`GLOSSARY.md`](../70-governance/GLOSSARY.md) ("Import"), [`SERVING-MODEL.md`](../20-product/SERVING-MODEL.md) |
| §5 | Product principles | [`PRINCIPLES.md`](../10-charter/PRINCIPLES.md) | — |
| §5.1 | Policy portability | [AGR-005](AGREEMENTS.md#agr-005--policy-portability) | [CON-003](CONSTRAINTS.md), [CON-004](CONSTRAINTS.md) |
| §5.2 | One contract across deployment modes | [AGR-006](AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) | [`INTEGRATION-ARCHITECTURE.md`](../50-architecture/INTEGRATION-ARCHITECTURE.md) |
| §5.3 | Policies are separate from integrations | [AGR-007](AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) | [`INTEGRATION-ARCHITECTURE.md`](../50-architecture/INTEGRATION-ARCHITECTURE.md) |
| §5.4 | Explicit content-exposure model | [AGR-008](AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns) | [`DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md) |
| §5.5 | Guardrails are not authorization | [AGR-010](AGREEMENTS.md#agr-010--guardrails-are-not-authorization) | [`CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md), [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) |
| §5.6 | Observable before enforceable | [AGR-011](AGREEMENTS.md#agr-011--observe-before-enforce-progression) | [ADR-0004](../50-architecture/adrs/ADR-0004-enforcement-ladder-states-and-decision-actions.md) |
| §5.7 | Failure behavior is part of policy | [AGR-012](AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) | [ADR-0009](../50-architecture/adrs/ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) |
| §6 | Product scope | [`SCOPE.md`](../10-charter/SCOPE.md) | [`CAPABILITIES.md`](../20-product/CAPABILITIES.md) |
| §6.1 | In scope | [`SCOPE.md`](../10-charter/SCOPE.md) | [`CAPABILITIES.md`](../20-product/CAPABILITIES.md), [`REQUIREMENTS.md`](../50-architecture/REQUIREMENTS.md) |
| §6.2 | Future scope | [`SCOPE.md`](../10-charter/SCOPE.md) | [`GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md), [`FEATURE-BACKLOG.md`](../60-delivery/FEATURE-BACKLOG.md) |
| §6.3 | Out of scope (product-category exclusions) | [`SCOPE.md`](../10-charter/SCOPE.md) | [`CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) |
| §7 | Initial users and stakeholders | [`TARGET-USERS.md`](../20-product/TARGET-USERS.md) | [`VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md), [`AUDIENCE-AND-PERSONAS.md`](../40-sales-deck/AUDIENCE-AND-PERSONAS.md) |
| §8 | Trust and enforcement model | [AGR-018](AGREEMENTS.md#agr-018--trust-and-enforcement-model) | [`SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md) |
| §9 | Core domain model | [AGR-017](AGREEMENTS.md#agr-017--core-domain-model) | [`GLOSSARY.md`](../70-governance/GLOSSARY.md) (per-entity attributes), [`DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md), [`REQUIREMENTS.md`](../50-architecture/REQUIREMENTS.md) |
| §10 | Logical architecture | [`CONTAINER-ARCHITECTURE.md`](../50-architecture/CONTAINER-ARCHITECTURE.md) | [`COMPONENT-ARCHITECTURE.md`](../50-architecture/COMPONENT-ARCHITECTURE.md), [`SYSTEM-CONTEXT.md`](../50-architecture/SYSTEM-CONTEXT.md) |
| §11 | AWS implementation direction | [`DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) | [AGR-013](AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) (account model), [`TRADE-OFFS.md`](../50-architecture/TRADE-OFFS.md) |
| §12 | LiteLLM integration | [AGR-003](AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) | [`INTEGRATION-ARCHITECTURE.md`](../50-architecture/INTEGRATION-ARCHITECTURE.md) |
| §13 | Data handling requirements | [`DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md) | [AGR-014](AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default), [AGR-016](AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime) |
| §14 | Security requirements | [`CONSTRAINTS.md`](CONSTRAINTS.md) | [`SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md) |
| §15 | Operational requirements | [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) | [`OPERABILITY.md`](../50-architecture/OPERABILITY.md) |
| §16 | MVP definition and demonstration | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) | [`VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md), [`RELEASE-CRITERIA.md`](../60-delivery/RELEASE-CRITERIA.md) |
| §17 | MVP non-goals | [`SCOPE.md`](../10-charter/SCOPE.md) | [DEC-008](DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases) |
| §18 | Delivery phases | [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery) | [`PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md), [`MILESTONES.md`](../60-delivery/MILESTONES.md), [`IMPLEMENTATION-STRATEGY.md`](../60-delivery/IMPLEMENTATION-STRATEGY.md) |
| §19 | Success metrics | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) | [`EXPECTED-OUTCOMES.md`](../30-executive/EXPECTED-OUTCOMES.md), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) |
| §20 | Key risks | [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) | [`TECHNICAL-RISKS.md`](../50-architecture/TECHNICAL-RISKS.md), [`RISKS-AND-DEPENDENCIES.md`](../30-executive/RISKS-AND-DEPENDENCIES.md) |
| §21 | Ownership boundaries | [`TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md) | [`DOCUMENT-GOVERNANCE.md`](../70-governance/DOCUMENT-GOVERNANCE.md) (documentation ownership, distinct from product ownership) |
| §22 | Decision summary | [AGR-001](AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) | [`DECISIONS.md`](DECISIONS.md) (DEC-001 – DEC-008), [ADR-0001](../50-architecture/adrs/ADR-0001-example-decision.md) |
| §23 | Product statement | [`CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) | [`README.md`](../README.md) §1, [`PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md) |

The charter's header block (product type, primary cloud, initial integration target, deployment framework) is carried by [`CHARTER.md`](../10-charter/CHARTER.md) and [AGR-002](AGREEMENTS.md#agr-002--three-deployment-modes) / [AGR-013](AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model).

---

## 4. Where the current registers supersede the original charter

The charter was never edited to reflect later decisions. The following divergences are deliberate. In each case the register listed governs, and the charter wording is of historical interest only.

| Charter said | Current position | Governed by |
| --- | --- | --- |
| §16–§17 framed delivery as an **MVP** with a bounded component list and a matching non-goals list. | The MVP framing is superseded: the **full feature set is defined and estimated across five phases**, with the former MVP content retained as the **Phase 1–2 core** and its validation demonstration. "MVP" survives in this set only as historical wording and in identifiers such as `MVP-01`…`MVP-09`. | [DEC-008](DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases) (superseding [DEC-005](DECISIONS.md)), [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery) |
| §6.3 listed **multi-region active-active availability** as a standing product-category exclusion. | Active-active is **committed as a later-phase capability** (`CAP-036`) and is no longer excluded. Region availability with tenant pinning (`CAP-035`) is separately committed. | [DEC-019](DECISIONS.md#dec-019--multi-region-active-active-availability-committed-as-a-later-phase), [DEC-018](DECISIONS.md#dec-018--multi-region-availability-at-ga) (both Proposed, pending owner ratification) |
| §11 stated a **two-account AWS model** (one non-production, one production) with additional separation only on named triggers. | Retained, but explicitly amended to confirm that multi-region availability does **not** by itself trigger additional accounts. | [AGR-013](AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) (amended), [OQ-009](OPEN-QUESTIONS.md) |
| §7 named **four** stakeholder groups. | Unchanged for product purposes. The sales deck adds a fifth **Executive Sponsor** persona, explicitly synthesized rather than sourced from the charter — an addition, not a contradiction. | [`AUDIENCE-AND-PERSONAS.md`](../40-sales-deck/AUDIENCE-AND-PERSONAS.md) |
| §6.2 listed future-scope capabilities as an undifferentiated list of possibilities. | Future capabilities now carry explicit commitment status — `Core (Phases 1–2)`, `Later phase — committed`, `Vision — uncommitted`, or `Out-of-boundary` — and a governed graduation path. A listing in the original §6.2 is not by itself a commitment. | [AGR-021](AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model), [DEC-009](DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision), [`GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) |

No other contradiction between the original charter and this set is known. If one is found, it is a defect in the derived document, and the resolution rule in [`DOCUMENT-GOVERNANCE.md`](../70-governance/DOCUMENT-GOVERNANCE.md) applies: the more authoritative document wins and the derived document is corrected.

---

## 5. Rules for using this document

* **Do not cite this document as a source.** Cite the owning document from [§3](#3-section-by-section-map). A requirement, claim, or figure whose only support is "the original charter said so" is not supported.
* **Do not add content here.** This file records where content went; it does not hold content. If something needs saying, it belongs in the owning document.
* **Do not reintroduce the charter file.** The set is self-contained by design. Restoring an external root document would recreate the dependency this removal eliminated.
* **Keep the map current.** If a document is renamed, split, or merged, update the owning-document column in the same change set — the `original charter §N` links throughout the set resolve against the anchors in [§3](#3-section-by-section-map).

---

## Related documents

| Document | Relationship |
| --- | --- |
| [`AGREEMENTS.md`](AGREEMENTS.md) | The authoritative (L1) register — the accepted, currently governing distillation of the original charter. |
| [`CONSTRAINTS.md`](CONSTRAINTS.md) | The authoritative (L1) checkable boundaries, owning the charter's §14 security requirements. |
| [`DECISIONS.md`](DECISIONS.md) | The decision log, including every decision that supersedes charter wording (see [§4](#4-where-the-current-registers-supersede-the-original-charter)). |
| [`../70-governance/DOCUMENT-GOVERNANCE.md`](../70-governance/DOCUMENT-GOVERNANCE.md) | The L1–L5 authority model, precedence rules, and change control this record sits outside of. |
| [`../README.md`](../README.md) | Entry point for the documentation set, including where ground truth lives. |
