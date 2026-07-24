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
| [DEC-005](#dec-005--bound-the-mvp-to-reusable-policy-import-inputoutput-evaluation-litellm-integration-observeenforce-and-normalized-audit-decisions) | 2026-07-24 | Bound the MVP to reusable policy import, input/output evaluation, LiteLLM integration, observe/enforce, and normalized audit decisions | Accepted | AGR-019, AGR-020, AGR-021 |
| [DEC-006](#dec-006--sequence-delivery-into-five-phases) | 2026-07-24 | Sequence delivery into five phases, from integration proof to advanced agent protection | Accepted | AGR-022 |
| [DEC-007](#dec-007--defer-advanced-isolation-distribution-security-agent-authorization-and-org-wide-enforcement) | 2026-07-24 | Defer advanced infrastructure isolation, policy-distribution security, agent authorization, and organization-wide enforcement until justified by adoption, compliance, or commercial need | Accepted | AGR-019, AGR-022 |

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
**Status:** Accepted
**Decision:** The MVP focuses on the following capabilities, and no others (seed.md §16, §22):

* Reusable guardrail policy import (versioned policy format, storage, workload registration, and policy-to-workload binding).
* Input and output evaluation only (no retrieval, tool-call, tool-result, or memory-write stages in the MVP).
* LiteLLM integration via pre-call and post-call connectors.
* Observe and enforce modes.
* Normalized, audited evaluation decisions (decision metadata logging and evaluation latency metrics).

**Context:** The MVP exists to prove that one reusable policy can be applied consistently to multiple independent LiteLLM deployments (seed.md §16). A tightly bounded MVP validates this core policy-reuse hypothesis before committing to the larger investment implied by adjacent capabilities such as a full administration portal, policy signing, or streaming enforcement (seed.md §17). The MVP demonstration criteria (seed.md §16) require: two separate LiteLLM instances consuming the same policy without sharing model-provider credentials with the guardrail platform; both receiving consistent normalized decisions; one workload in observe mode and another in enforce mode; a policy that can be upgraded and rolled back; raw content persistence that can be disabled; evaluation failures that follow a configured failure policy; and a runtime that can later be packaged for customer-managed deployment without changing the connector contract.
**Related agreements:** [AGR-019](AGREEMENTS.md#agr-019--mvp-scope-boundaries), [AGR-020](AGREEMENTS.md#agr-020--mvp-success-demonstration), [AGR-021](AGREEMENTS.md#agr-021--meaning-of-import)
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
**Related agreements:** [AGR-022](AGREEMENTS.md#agr-022--five-phase-delivery)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).
**See also:** [`60-delivery/MILESTONES.md`](../60-delivery/MILESTONES.md), [`60-delivery/IMPLEMENTATION-STRATEGY.md`](../60-delivery/IMPLEMENTATION-STRATEGY.md), [`20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md)

---

## DEC-007 — Defer advanced isolation, distribution security, agent authorization, and org-wide enforcement

**Date:** 2026-07-24
**Status:** Accepted
**Decision:** Advanced infrastructure isolation, policy-distribution security, agent authorization, and organization-wide enforcement will be introduced only when justified by adoption, compliance, or commercial requirements — not built proactively during the MVP or the first delivery phases (seed.md §22).
**Context:** The solution charter identifies "product over-expansion" as a key risk: the platform could attempt to include gateway management, full AI governance, authorization, agent orchestration, and compliance certification before validating the core integration and policy-reuse hypothesis (seed.md §20). This decision keeps future-scope items (seed.md §6.2) — such as tool-call validation, MCP authorization, policy signing, air-gapped installation, and a policy marketplace — as candidates for later, separately approved phases rather than implicit MVP or near-term scope.
**Related agreements:** [AGR-019](AGREEMENTS.md#agr-019--mvp-scope-boundaries), [AGR-022](AGREEMENTS.md#agr-022--five-phase-delivery)
**Related ADRs:** See [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md).

---

## Cross-references

* [AGREEMENTS.md](AGREEMENTS.md) — authoritative agreements (AGR-001 through AGR-022) that this log derives from.
* [`50-architecture/adrs/README.md`](../50-architecture/adrs/README.md) — index of Architecture Decision Records for deep technical/design decisions.
* [`60-delivery/MILESTONES.md`](../60-delivery/MILESTONES.md) — phase-level delivery milestones corresponding to DEC-006.
* [`60-delivery/IMPLEMENTATION-STRATEGY.md`](../60-delivery/IMPLEMENTATION-STRATEGY.md) — implementation approach per phase.
* [`20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) — product-facing view of the phased roadmap.
* [`10-charter/CHARTER.md`](../10-charter/CHARTER.md) — charter document reflecting DEC-001 through DEC-004.
