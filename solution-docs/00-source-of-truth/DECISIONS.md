**AI Guardrails — Decision Log**

**Authority level:** L1
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

## Purpose and scope

This document is a lightweight, chronological log of **product, organizational, and delivery decisions** made for the AI Guardrails solution. Each entry records what was decided, why, and which authoritative [agreement](AGREEMENTS.md) it traces back to.

This log is **not** the place for deep technical or architectural decisions. Those are recorded as individual Architecture Decision Records (ADRs) in [`50-architecture/adrs/`](../50-architecture/adrs/README.md), which capture design alternatives, trade-off analysis, and consequences at the component level. Where a decision below has a corresponding ADR, it is noted in that entry.

Precedence rule (unchanged from [AGREEMENTS.md](AGREEMENTS.md)): [AGREEMENTS.md](AGREEMENTS.md) is authoritative (L1). This decision log is derived from it and from `seed.md`, and must not contradict either. If a conflict is found, AGREEMENTS.md wins and this log must be corrected.

Each entry uses a fixed shape:

| Field | Meaning |
| --- | --- |
| **ID** | `DEC-NNN`, assigned in chronological order. |
| **Date** | Date the decision was accepted. |
| **Decision** | The decision itself, stated as a single accepted position. |
| **Context** | Why the decision was needed and what it responds to. |
| **Status** | `Accepted` unless later superseded (a superseding entry would say so explicitly and reference the prior ID). |
| **Related agreements** | The `AGR-NNN` entries in [AGREEMENTS.md](AGREEMENTS.md) that this decision derives from or implements. |

---

## Decision log summary

| ID | Date | Decision | Status | Related agreements |
| --- | --- | --- | --- | --- |
| [DEC-001](#dec-001--build-as-a-standalone-platform-not-embedded-in-the-internal-litellm) | 2026-07-24 | Build AI Guardrails as a standalone platform, not embedded in the internal LiteLLM | Accepted | AGR-001 |
| [DEC-002](#dec-002--adopt-litellm-as-the-first-connector-not-the-product-boundary) | 2026-07-24 | Adopt LiteLLM as the first connector, not the product boundary | Accepted | AGR-003, AGR-007 |
| [DEC-003](#dec-003--adopt-amazon-bedrock-guardrails-as-the-initial-evaluation-engine-behind-a-vendor-independent-contract) | 2026-07-24 | Adopt Amazon Bedrock Guardrails as the initial evaluation engine behind a vendor-independent contract | Accepted | AGR-004, AGR-005 |
| [DEC-004](#dec-004--support-three-deployment-modes-as-the-primary-deployment-framework) | 2026-07-24 | Support three deployment modes as the primary deployment framework | Accepted | AGR-002, AGR-006, AGR-008, AGR-009 |
| [DEC-005](#dec-005--bound-the-mvp-to-reusable-policy-import-inputoutput-evaluation-litellm-integration-observeenforce-and-normalized-audit-decisions) | 2026-07-24 | Bound the MVP to reusable policy import, input/output evaluation, LiteLLM integration, observe/enforce, and normalized audit decisions | Superseded by DEC-008 | Former AGR-019/AGR-020 (removed), AGR-019 |
| [DEC-006](#dec-006--sequence-delivery-into-five-phases) | 2026-07-24 | Sequence delivery into five phases, from integration proof to advanced agent protection | Accepted | AGR-020 |
| [DEC-007](#dec-007--defer-advanced-isolation-distribution-security-agent-authorization-and-org-wide-enforcement) | 2026-07-24 | Defer advanced infrastructure isolation, policy-distribution security, agent authorization, and organization-wide enforcement until justified by adoption, compliance, or commercial need | Accepted | AGR-020 |
| [DEC-008](#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases) | 2026-07-24 | Supersede the MVP-bounded scope; define and estimate the full feature set across five phases | Accepted | AGR-019, AGR-020 |
| [DEC-009](#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision) | 2026-07-24 | Adopt the guardrail-category catalog (GR-001 through GR-018) as directional vision, without altering the estimated feature set | Accepted | AGR-021, AGR-017, AGR-020 |

---

## DEC-001 — Build as a standalone platform, not embedded in the internal LiteLLM

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** AI Guardrails will be built and delivered as a standalone policy and enforcement platform, rather than as functionality embedded inside the existing internal LiteLLM instance.
**Context:** The product vision requires that multiple teams apply consistent organizational safeguards without being required to use the same model provider, application framework, or LiteLLM deployment (seed.md §1–§2). Embedding guardrails inside one internal LiteLLM instance would couple policy enforcement to that gateway's lifecycle and prevent reuse across teams with different gateways or providers (seed.md §22).
**Related agreements:** [AGR-001](AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md) for the corresponding component- and deployment-level design records.

---

## DEC-002 — Adopt LiteLLM as the first connector, not the product boundary

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** LiteLLM is implemented as the first supported gateway adapter/connector and may also serve as the substrate for the Guarded Gateway mode, but it does not define the boundary of the AI Guardrails product (seed.md §12, §22).
**Context:** The evaluation contract and policy model must remain independent of any single gateway's request/response structures so that additional connectors — other gateways or a generic SDK/HTTP path — can be added later without changing policy semantics (seed.md §5.3, §12). This keeps the separation between what a policy evaluates (organizational intent) and how a specific gateway invokes and enforces it (integration mechanics) mandatory rather than incidental.
**Related agreements:** [AGR-003](AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-007](AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## DEC-003 — Adopt Amazon Bedrock Guardrails as the initial evaluation engine behind a vendor-independent contract

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** Amazon Bedrock Guardrails serves as the initial managed evaluation engine (seed.md §10, §11, §22), but the public evaluation contract exposed by the platform remains vendor-independent. Bedrock-specific response formats are normalized into the platform's own decision structure before reaching policies or connectors.
**Context:** Bedrock Guardrails accelerates delivery of a working managed evaluator without requiring the platform to build a proprietary detection model in the MVP (seed.md §6.3). Permanently coupling the public contract to one vendor's response format would undermine policy portability (seed.md §5.1) and block the future addition of deterministic rules, PII evaluators, or third-party engines behind the same adapter pattern (seed.md §10).
**Related agreements:** [AGR-004](AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](AGREEMENTS.md#agr-005--policy-portability)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## DEC-004 — Support three deployment modes as the primary deployment framework

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** The product adopts a three-mode deployment model as its primary framework (seed.md §3, §22):

| Mode | Summary |
| --- | --- |
| A — Remote Evaluation | Consuming team operates its own LiteLLM (or equivalent) gateway and calls the hosted Evaluation API before and after inference. |
| B — Guarded Gateway | The AI Guardrails provider operates both the LLM gateway and the guardrail runtime as a fully managed service. |
| C — Customer-Managed Runtime | The guardrail runtime is deployed inside the consuming team's own AWS account, capable of executing policies without a synchronous control-plane connection. |

**Context:** Different consuming teams have different operational-ownership, data-residency, and gateway-ownership requirements that no single deployment topology can satisfy (seed.md §1, §3). All three modes must share a common policy model, evaluation contract, and runtime behavior (seed.md §5.2). Mode A must never be described as a private-data or data-local model, since tenant content still reaches the hosted Evaluation API — only operational ownership of the inference path is reduced (seed.md §3, Mode A).
**Related agreements:** [AGR-002](AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-006](AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-008](AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), [AGR-009](AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## DEC-005 — Bound the MVP to reusable policy import, input/output evaluation, LiteLLM integration, observe/enforce, and normalized audit decisions

**Date:** 2026-07-24
**Status:** Superseded by [DEC-008](#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases)
**Decision:** The MVP focuses on the following capabilities, and no others (seed.md §16, §22):

* Reusable guardrail policy import (versioned policy format, storage, workload registration, and policy-to-workload binding).
* Input and output evaluation only (no retrieval, tool-call, tool-result, or memory-write stages in the MVP).
* LiteLLM integration via pre-call and post-call connectors.
* Observe and enforce modes.
* Normalized, audited evaluation decisions (decision metadata logging and evaluation latency metrics).

**Context:** The MVP exists to prove that one reusable policy can be applied consistently to multiple independent LiteLLM deployments (seed.md §16). A tightly bounded MVP validates this core policy-reuse hypothesis before committing to the larger investment implied by adjacent capabilities such as a full administration portal, policy signing, or streaming enforcement (seed.md §17). The MVP demonstration criteria (seed.md §16) require: two separate LiteLLM instances consuming the same policy without sharing model-provider credentials with the guardrail platform; both receiving consistent normalized decisions; one workload in observe mode and another in enforce mode; a policy that can be upgraded and rolled back; raw content persistence that can be disabled; evaluation failures that follow a configured failure policy; and a runtime that can later be packaged for customer-managed deployment without changing the connector contract.
**Related agreements:** Former AGR-019 (MVP scope boundaries) and former AGR-020 (MVP success demonstration) — both removed from the register by DEC-008; [AGR-019](AGREEMENTS.md#agr-019--meaning-of-import) ("Meaning of import", formerly AGR-021). This entry is retained as a historical record only.
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## DEC-006 — Sequence delivery into five phases

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** Delivery proceeds through five phases, each completed and validated before the next phase's scope is committed (seed.md §18, §22):

| Phase | Objective | Key deliverables |
| --- | --- | --- |
| 1 — Integration proof | Prove that guardrail policy and evaluation can be separated from LiteLLM and reused across multiple gateways. | Evaluation contract, policy format, Bedrock Guardrails adapter, LiteLLM connector, observe mode, basic auditing, internal deployment. |
| 2 — Internal multi-team platform | Allow multiple internal teams to onboard and operate distinct workloads. | Tenant and workload registry, policy bindings, IAM authentication, quotas, policy promotion and rollback, service-level monitoring, basic administration workflow. |
| 3 — Guarded gateway | Offer a fully hosted model and guardrail path for teams without their own gateway. | Managed LiteLLM gateway, model-provider configuration, per-tenant routing, cost and rate controls, managed credentials, unified inference/guardrail availability objectives. |
| 4 — Customer-managed runtime | Support workloads that require local evaluation and content isolation. | Runtime container, Helm chart, ECS deployment template, infrastructure module, local policy cache, policy synchronization, customer-controlled telemetry, upgrade compatibility checks. |
| 5 — Advanced agent protection | Extend guardrail coverage beyond prompt and response evaluation. | Retrieval-content evaluation, tool-call/tool-result validation, memory-write controls, human approvals, agent delegation restrictions, MCP integration, advanced authorization integration. |

Phase 5 is treated as a separate product expansion, not an MVP prerequisite, and must not be scheduled or resourced as part of MVP delivery.
**Context:** Sequencing delivery from a narrow integration proof toward progressively larger operating models lets the platform validate policy reuse and connector independence (Phases 1–2) before taking on the greater operational and security scope of hosting a gateway (Phase 3) and supporting customer-managed deployment (Phase 4). Advanced agent protection (Phase 5) — retrieval evaluation, tool-call validation, MCP authorization, and similar — is explicitly out of MVP scope and only justified after adoption and policy effectiveness are measured (seed.md §6.2, §20 "Product over-expansion").
**Related agreements:** [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).
**See also:** [`60-delivery/MILESTONES.md`](../60-delivery/MILESTONES.md), [`60-delivery/IMPLEMENTATION-STRATEGY.md`](../60-delivery/IMPLEMENTATION-STRATEGY.md), [`20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md)

---

## DEC-007 — Defer advanced isolation, distribution security, agent authorization, and org-wide enforcement

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** Advanced infrastructure isolation, policy-distribution security, agent authorization, and organization-wide enforcement will be introduced only when justified by adoption, compliance, or commercial requirements — not built proactively during the MVP or the first delivery phases (seed.md §22).
**Context:** The solution charter identifies "product over-expansion" as a key risk: the platform could attempt to include gateway management, full AI governance, authorization, agent orchestration, and compliance certification before validating the core integration and policy-reuse hypothesis (seed.md §20). This decision keeps future-scope items (seed.md §6.2) — such as tool-call validation, MCP authorization, policy signing, air-gapped installation, and a policy marketplace — as candidates for later, separately approved phases rather than implicit MVP or near-term scope.
**Related agreements:** [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery); the former AGR-019 (MVP scope boundaries) this decision also cited was removed by DEC-008 — the deferral itself remains in force through the five-phase sequencing.
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## DEC-008 — Supersede the MVP-bounded scope; define and estimate the full feature set across five phases

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** The MVP scope boundary (former AGR-019) and the MVP success demonstration (former AGR-020) are removed from the agreements register, and DEC-005 is superseded. The solution is defined — and its delivery estimated — over the **full feature set**: the Phase 1–2 core capabilities (the former MVP list) plus the capabilities previously classified as future scope, organized across the five delivery phases. The register was renumbered accordingly: "Meaning of import" is now [AGR-019](AGREEMENTS.md#agr-019--meaning-of-import) and "Five-phase delivery" is now [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery), leaving an AGR-001 through AGR-020 register.
**Context:** The MVP-bounded scope proved too narrow to support a valid delivery estimation: bounding definition and estimation to the Phase 1–2 core left the larger share of the platform unspecified and unestimated, making any investment figure for the initiative unreliable. Defining the full feature set up front provides the basis for a valid estimation, while delivery remains sequenced through five phases, each validated before the next proceeds (per AGR-020). Three things deliberately survive this change unchanged:

* The two-independent-LiteLLM-instances demonstration formerly recorded as AGR-020 remains a **Phase 1–2 validation milestone** in the delivery plan ([`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md)); it is no longer the register-level definition of solution success.
* The product-category exclusions of seed.md §6.3 (general-purpose gateway replacement, complete AI governance platform, complete DLP platform, proprietary detection models, model training, end-user authentication, business authorization, organization-wide AWS enforcement, cross-cloud deployment, multi-region active-active availability, full compliance certification, general MCP security platform) remain excluded — they were never part of the feature set and are not made in-scope by this decision.
* The claims discipline (AGR-009, AGR-010, AGR-018; no invented figures — TBD where the source supplies none) is unaffected.

**Supersedes:** [DEC-005](#dec-005--bound-the-mvp-to-reusable-policy-import-inputoutput-evaluation-litellm-integration-observeenforce-and-normalized-audit-decisions). DEC-006 and DEC-007 remain accepted, re-anchored to AGR-020.
**Related agreements:** [AGR-019](AGREEMENTS.md#agr-019--meaning-of-import), [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## DEC-009 — Adopt the guardrail-category catalog as directional vision

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** The product adopts an eighteen-category guardrail taxonomy — GR-001 through GR-018, recorded in [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) — as its directional vision for guardrail coverage, together with the extension model documented in [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md). Catalog entries carry an explicit status: `Core (Phases 1–2)`, `Later phase — committed`, `Vision — uncommitted`, or `Out-of-boundary`. Entries with status `Vision — uncommitted` are **not** scheduled, resourced, or included in the full-solution estimation fixed by [DEC-008](#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases); entries with status `Out-of-boundary` remain excluded per seed.md §6.3 and AGR-010 and are never future deliverables. A vision-classified entry may enter committed scope only through the graduation process defined in `EXTENSIBILITY-ARCHITECTURE.md` §10, which is the recorded scope-change path required by [`10-charter/SCOPE.md`](../10-charter/SCOPE.md) §8.
**Context:** The product needs a stated growth direction so that incremental guardrail additions extend a shared taxonomy instead of accreting ad hoc. Adopting the catalog as *classification* — with per-entry status and a governed graduation path — provides that direction while preserving the scope discipline DEC-008 established: the estimated feature set stays CAP-001 through CAP-034, the seed.md §6.3 exclusions stay excluded, and the risk register's "product over-expansion" concern is mitigated rather than triggered, because vision items are parked in the catalog rather than scheduled on the roadmap.
**Related agreements:** [AGR-021](AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model), [AGR-017](AGREEMENTS.md#agr-017--core-domain-model), [AGR-020](AGREEMENTS.md#agr-020--five-phase-delivery)
**Related ADRs:** [`ADR-0002`](../50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md) (Proposed) — records the design alternatives and consequences of the catalog and extension model.

---

## Cross-references

* [AGREEMENTS.md](AGREEMENTS.md) — authoritative agreements (AGR-001 through AGR-021) that this log derives from.
* [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md) — index of Architecture Decision Records for deep technical/design decisions.
* [`60-delivery/MILESTONES.md`](../60-delivery/MILESTONES.md) — phase-level delivery milestones corresponding to DEC-006.
* [`60-delivery/IMPLEMENTATION-STRATEGY.md`](../60-delivery/IMPLEMENTATION-STRATEGY.md) — implementation approach per phase.
* [`20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) — product-facing view of the phased roadmap.
* [`10-charter/CHARTER.md`](../10-charter/CHARTER.md) — charter document reflecting DEC-001 through DEC-004.
