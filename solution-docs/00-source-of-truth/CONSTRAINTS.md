**AI Guardrails — Constraints**

**Authority level:** L1
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

> **Local evolution (2026-10-09):** [DEC-025](DECISIONS.md#dec-025--plan-the-local-guardrails-evolution-after-the-technical-proof) carries the local delivery boundary into the next plan. AWS IAM/KMS and cloud-placement requirements retain their hosted applicability; local credentials, keys and network tests do not prove those cloud controls. Shared-service exposure is now selected locally under AGR-032 and AGR-036.

> **Current local release (2026-10-08):** [DEC-022](DECISIONS.md#dec-022--define-a-separately-accepted-local-technical-proof), grounded in AGR-022 through AGR-024, defines a separately accepted local technical proof for internal application teams, operated through an API and CLI. AWS integration and service exposure to other teams are later milestones. This document retains its programme and hosted-phase obligations; cloud-specific requirements are not local-release exit gates, and local substitutes do not prove those controls. The current boundary and interview status are in [RELEASE-DEFINITION.md](../60-delivery/RELEASE-DEFINITION.md).

## Purpose and authority

This register enumerates the **non-negotiable boundaries** for the AI Guardrails solution: cloud placement, product-contract independence, transport and cryptographic security, identity and authentication, content handling, and resource limits. Each entry has an ID (`CON-001` …), a precise constraint statement, the source material it is derived from, and the rationale for why the boundary exists.

This document is **L1 — Authoritative**, alongside [`AGREEMENTS.md`](AGREEMENTS.md). The two documents are complementary, not competing:

* [`AGREEMENTS.md`](AGREEMENTS.md) records the negotiated decisions (the "what was agreed and why") behind the solution.
* `CONSTRAINTS.md` (this document) restates the subset of those decisions — plus the security, data-handling, and infrastructure requirements carried over from the original solution charter ([`PROVENANCE.md`](PROVENANCE.md) §14) — as sharp, checkable boundary conditions that every architecture document, product artifact, and delivery decision must respect.

No derived document (charter, product materials, architecture set, sales or executive collateral, delivery plan) may contradict a constraint listed here. Where a conflict is found, this register and `AGREEMENTS.md` win, and the derived document must be corrected. A constraint may only be relaxed by first amending the corresponding agreement in `AGREEMENTS.md`; this document must never be edited to accommodate a design that has not first been reconciled at the agreement level.

Constraints not yet backed by a concrete figure (SLA numbers, capacity limits, dollar values) are marked **TBD** rather than populated with an invented value, per source-of-truth policy.

---

## Table of contents

<!-- BEGIN GENERATED: toc -->
- [Purpose and authority](#purpose-and-authority)
- [Cloud placement and account model](#cloud-placement-and-account-model)
  - [CON-001 — AWS as primary cloud](#con-001--aws-as-primary-cloud)
  - [CON-002 — Minimal MVP account model](#con-002--minimal-mvp-account-model)
- [Product-contract independence](#product-contract-independence)
  - [CON-003 — No coupling to LiteLLM configuration structures](#con-003--no-coupling-to-litellm-configuration-structures)
  - [CON-004 — No coupling to Bedrock Guardrails response formats](#con-004--no-coupling-to-bedrock-guardrails-response-formats)
  - [CON-005 — One logical evaluation contract across all modes](#con-005--one-logical-evaluation-contract-across-all-modes)
  - [CON-006 — Policies separate from connectors](#con-006--policies-separate-from-connectors)
  - [CON-007 — Explicit content-exposure documentation per deployment](#con-007--explicit-content-exposure-documentation-per-deployment)
- [Transport and cryptographic security](#transport-and-cryptographic-security)
  - [CON-008 — TLS for all runtime communication](#con-008--tls-for-all-runtime-communication)
  - [CON-009 — KMS encryption in transit and at rest](#con-009--kms-encryption-in-transit-and-at-rest)
- [Identity and authentication](#identity-and-authentication)
  - [CON-010 — IAM-based authentication and least-privilege roles](#con-010--iam-based-authentication-and-least-privilege-roles)
  - [CON-011 — Per-tenant workload identities](#con-011--per-tenant-workload-identities)
  - [CON-012 — No exclusive reliance on weak security proxies](#con-012--no-exclusive-reliance-on-weak-security-proxies)
- [Content handling and data exposure](#content-handling-and-data-exposure)
  - [CON-013 — Raw content must not leave a customer-managed runtime unless explicitly configured](#con-013--raw-content-must-not-leave-a-customer-managed-runtime-unless-explicitly-configured)
  - [CON-014 — No raw-content retention by default in the hosted runtime](#con-014--no-raw-content-retention-by-default-in-the-hosted-runtime)
- [Resource and operational limits](#resource-and-operational-limits)
  - [CON-015 — Input-size limits](#con-015--input-size-limits)
  - [CON-016 — Timeout limits](#con-016--timeout-limits)
- [Summary table](#summary-table)
- [Constraint interaction diagram](#constraint-interaction-diagram)
- [Related documents](#related-documents)
<!-- END GENERATED: toc -->
---

## Cloud placement and account model

### CON-001 — AWS as primary cloud

| Field | Detail |
|---|---|
| **Constraint** | Amazon Web Services is the primary and initial cloud platform for the AI Guardrails solution. All MVP infrastructure — evaluation runtime, API layer, policy storage, workload registry, audit pipeline — is designed and delivered on AWS. Cross-cloud deployment is out of scope for the MVP. |
| **Source** | [`DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) (AWS implementation direction), [`SCOPE.md`](../10-charter/SCOPE.md) (MVP non-goals: "Multi-cloud support"); [`AGREEMENTS.md` AGR-013](AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) |
| **Rationale** | A single, primary cloud platform lets the MVP validate the core reusable-policy hypothesis without the added design and operational cost of a cross-cloud abstraction layer that has not been justified by adoption or compliance need. |

### CON-002 — Minimal MVP account model

| Field | Detail |
|---|---|
| **Constraint** | The MVP account model is limited to exactly **one non-production AWS account and one production AWS account**. Separate management, production, and audit accounts are not required for the MVP. IAM, storage, and environment boundaries must remain explicit within this two-account model. Additional account separation may be introduced only when justified by: external customers, compliance requirements, independent security ownership, immutable audit requirements, production scale, or dedicated tenant deployments. |
| **Source** | [`DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) ("The MVP does not require separate management, production, and audit AWS accounts... A single non-production account and one production account are sufficient initially..."), [`SCOPE.md`](../10-charter/SCOPE.md) (MVP non-goals: "Three-account AWS separation"); [`AGREEMENTS.md` AGR-013](AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) |
| **Rationale** | A minimal account topology reduces MVP delivery complexity while still preserving explicit IAM, storage, and environment boundaries; account proliferation before a validated trigger would slow delivery without a corresponding benefit. |

---

## Product-contract independence

These constraints operationalize product principles 5.1 ("Policy portability"), 5.2 ("One contract across deployment modes"), 5.3 ("Policies are separate from integrations"), and 5.4 ("Explicit content-exposure model") from [`PRINCIPLES.md`](../10-charter/PRINCIPLES.md).

### CON-003 — No coupling to LiteLLM configuration structures

| Field | Detail |
|---|---|
| **Constraint** | The public product contract — policy schema, evaluation request/decision structures, reason codes, and connector invocation contract — must not depend on LiteLLM's internal configuration structures or request/response shapes. LiteLLM-specific logic must be isolated entirely inside the LiteLLM connector. |
| **Source** | [AGR-005](AGREEMENTS.md#agr-005--policy-portability) (Policy portability: "Guardrail policies must not depend directly on: LiteLLM configuration structures..."), [AGR-003](AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (LiteLLM integration); [`AGREEMENTS.md` AGR-003](AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-005](AGREEMENTS.md#agr-005--policy-portability) |
| **Rationale** | LiteLLM is the first supported connector, not the product boundary; coupling the contract to it would prevent onboarding teams that use a different gateway or invoke the Evaluation API directly. |

### CON-004 — No coupling to Bedrock Guardrails response formats

| Field | Detail |
|---|---|
| **Constraint** | The public product contract must not depend on Amazon Bedrock Guardrails' response format. Bedrock-specific responses must be normalized into the platform's own decision structure by an adapter before reaching policies, connectors, or audit records. |
| **Source** | [AGR-005](AGREEMENTS.md#agr-005--policy-portability) (Policy portability: "...or Bedrock Guardrails response formats"), [AGR-001](AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) (Decision summary: "Amazon Bedrock Guardrails will serve as an initial managed evaluation engine, but the public product contract will remain vendor-independent."); [`AGREEMENTS.md` AGR-004](AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](AGREEMENTS.md#agr-005--policy-portability) |
| **Rationale** | Bedrock Guardrails accelerates delivery of a working managed evaluator, but permanently coupling the contract to one vendor's response format would undermine policy portability and block future evaluation engines. |

### CON-005 — One logical evaluation contract across all modes

| Field | Detail |
|---|---|
| **Constraint** | Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime deployments must implement the same logical evaluation contract: policy definitions, request structures, decision structures, reason codes, enforcement actions, audit metadata, and policy-version semantics must remain compatible regardless of deployment location. Mode-specific optimizations (caching, telemetry) must not alter the externally observable contract. |
| **Source** | [AGR-006](AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) (One contract across deployment modes); [`AGREEMENTS.md` AGR-006](AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) |
| **Rationale** | A single reusable policy can only be applied consistently across deployment modes if the contract governing evaluation requests and decisions does not vary by where the runtime is deployed. |

### CON-006 — Policies separate from connectors

| Field | Detail |
|---|---|
| **Constraint** | A policy defines what must be evaluated and how violations are handled; a connector defines only how a specific gateway or application invokes and enforces that policy. Connectors must not embed policy logic, thresholds, or detection rules. This separation is mandatory in every implementation. |
| **Source** | [AGR-007](AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) (Policies are separate from integrations); [`AGREEMENTS.md` AGR-007](AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) |
| **Rationale** | Conflating policy intent with integration mechanics would recreate the coupling the platform exists to remove and would block reuse of one policy across multiple connectors. |

### CON-007 — Explicit content-exposure documentation per deployment

| Field | Detail |
|---|---|
| **Constraint** | Every deployment must explicitly document: where prompts are processed, where responses are processed, whether retrieved documents are transmitted, whether tool arguments are transmitted, whether content is persisted, which telemetry leaves the runtime, and which AWS accounts and Regions process the request. Operational ownership and data exposure must always be communicated as separate concerns — reduced operational ownership must never be implied to mean reduced data exposure, or vice versa. |
| **Source** | [AGR-008](AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns) (Explicit content-exposure model); [`AGREEMENTS.md` AGR-008](AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), [AGR-009](AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model) |
| **Rationale** | Stakeholders frequently conflate "who operates this component" with "where does my data go"; both questions require independently correct, explicit answers for security and compliance sign-off. |

---

## Transport and cryptographic security

### CON-008 — TLS for all runtime communication

| Field | Detail |
|---|---|
| **Constraint** | All runtime communication — between applications and connectors, connectors and the Evaluation API, the runtime and evaluation engines, and the runtime and the control plane — must use TLS. No unencrypted transport path is permitted. |
| **Source** | [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "The product must support: TLS for all runtime communication.") |
| **Rationale** | Evaluated content includes prompts, responses, and potentially sensitive tool arguments; unencrypted transport would expose this content and undermine every other data-handling constraint in this register. |

### CON-009 — KMS encryption in transit and at rest

| Field | Detail |
|---|---|
| **Constraint** | Data handled by the platform must be encrypted both in transit and at rest using AWS KMS-backed encryption. This applies to policy storage, the workload registry, audit event buffers and archives, and any cached policy data in customer-managed runtimes. |
| **Source** | [`DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md) (Data handling requirements: "Encrypt data in transit and at rest"), [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "KMS encryption"), [`DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) (AWS implementation direction — Encryption: AWS KMS) |
| **Rationale** | KMS-backed encryption is the baseline control for protecting policy data, audit records, and any cached or transiently processed tenant content across both hosted and customer-managed deployments. |

---

## Identity and authentication

### CON-010 — IAM-based authentication and least-privilege roles

| Field | Detail |
|---|---|
| **Constraint** | Authentication for AWS-facing consumers of the platform must be IAM-based. Runtime roles must be least-privilege and use short-lived credentials rather than long-lived static secrets. |
| **Source** | [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "IAM-based authentication for AWS consumers," "Short-lived credentials," "Least-privilege runtime roles") |
| **Rationale** | IAM-based, least-privilege, short-lived credentials limit the blast radius of a compromised role and align the platform's own runtime identity model with the per-tenant workload identity requirement (CON-011). |

### CON-011 — Per-tenant workload identities

| Field | Detail |
|---|---|
| **Constraint** | Each tenant's workloads must be issued distinct, per-tenant workload identities. A workload identity must not be shared across tenants, and tenant isolation must be enforceable through this identity boundary. |
| **Source** | [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "Per-tenant workload identities"), [AGR-017](AGREEMENTS.md#agr-017--core-domain-model) (Core domain model — Tenant, Workload) |
| **Rationale** | Per-tenant workload identity is the mechanism that ties every evaluation, decision, and audit record back to the correct tenant and workload, and is a prerequisite for policy authorization checks and audit integrity. |

### CON-012 — No exclusive reliance on weak security proxies

| Field | Detail |
|---|---|
| **Constraint** | The product must not rely **exclusively** on any of the following as a security mechanism: (a) API keys stored in LiteLLM configuration; (b) tenant-supplied identifiers without authentication; (c) client-provided policy versions without authorization; (d) connector telemetry as evidence of mandatory enforcement. Each of these may exist as a convenience or signal, but none may stand alone as the platform's sole security or enforcement-evidence control. |
| **Source** | [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "The product must not rely exclusively on: API keys stored in LiteLLM configuration; Tenant-supplied identifiers without authentication; Client-provided policy versions without authorization; Connector telemetry as evidence of mandatory enforcement."); [`AGREEMENTS.md` AGR-018](AGREEMENTS.md#agr-018--trust-and-enforcement-model) |
| **Rationale** | Each of these four items is easy to misconfigure, remove, or spoof; treating any one of them as sufficient would create a false sense of security and misstate the platform's actual enforcement guarantees (see AGR-018 on cooperative vs. mandatory enforcement). |

---

## Content handling and data exposure

### CON-013 — Raw content must not leave a customer-managed runtime unless explicitly configured

| Field | Detail |
|---|---|
| **Constraint** | In Customer-Managed Runtime deployments, raw prompt and response content must never leave the customer's AWS account or environment unless the customer has explicitly configured that transmission. By default, only customer-controlled telemetry categories (health status, product version, policy version, aggregated evaluation counts, reason-code totals, latency metrics, sanitized decision metadata) may reach the hosted control plane — or none at all, if the customer selects a "no telemetry" configuration. |
| **Source** | [`DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md) (Data handling requirements — Customer-managed telemetry: "Raw content must never leave a customer-managed runtime unless explicitly configured."); [`AGREEMENTS.md` AGR-016](AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime), [AGR-015](AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) |
| **Rationale** | Customers who adopt Customer-Managed Runtime do so specifically to keep content within their own account; any telemetry design that could carry raw content by construction would silently defeat the reason they chose that deployment mode. |

### CON-014 — No raw-content retention by default in the hosted runtime

| Field | Detail |
|---|---|
| **Constraint** | The hosted runtime (used in Remote Evaluation and Guarded Gateway modes) must process prompt and response content only for the purpose of evaluation and must not persist raw prompts and responses by default. By default it persists decision metadata only, hashes user and conversation identifiers where possible, and strips secrets from telemetry. Any retention of raw content requires explicit, tenant-level configuration — never a platform default. Audit metadata must record whether raw content was retained for a given evaluation. |
| **Source** | [`DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md) (Data handling requirements — Default behavior: "Avoid persisting raw prompts and responses by default... Persist decision metadata... Hash user and conversation identifiers where possible... Remove secrets from telemetry"), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) (Auditing: "Whether raw content was retained"); [`AGREEMENTS.md` AGR-014](AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default) |
| **Rationale** | Minimizing default retention of sensitive tenant content reduces breach impact, simplifies the platform's compliance posture, and keeps operational ownership and data exposure as deliberately managed, separate concerns (CON-007). |

---

## Resource and operational limits

### CON-015 — Input-size limits

| Field | Detail |
|---|---|
| **Constraint** | The Evaluation API and all evaluator adapters must enforce input-size limits on content submitted for evaluation. The specific size ceiling(s) are **TBD** and must be defined per evaluation stage before general availability; no unbounded input path is permitted. |
| **Source** | [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "Input-size limits.") |
| **Rationale** | Unbounded input size creates unpredictable evaluator latency, unbounded resource consumption, and a denial-of-service surface against the shared evaluation runtime. |

### CON-016 — Timeout limits

| Field | Detail |
|---|---|
| **Constraint** | Every evaluation stage (input, output, and any future stage) must enforce an explicit timeout. What happens when that timeout is reached must be defined as part of the workload's failure behavior — there is no undocumented default. Specific timeout values are **TBD** per stage and per detector. |
| **Source** | [original charter §14](PROVENANCE.md#3-section-by-section-map) (Security requirements: "Timeout limits."), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) (Operational requirements — Availability: "Timeout behavior"; Performance: "Timeout rate"); [`AGREEMENTS.md` AGR-012](AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) |
| **Rationale** | Synchronous evaluation sits on the request path of the consuming application; an unbounded or undefined timeout would let a stalled detector or unavailable dependency stall production traffic indefinitely. |

---

## Summary table

<!-- BEGIN GENERATED: con-summary -->
| ID | Constraint (short form) | Category |
|---|---|---|
| CON-001 | AWS is the primary and initial cloud platform | Cloud placement |
| CON-002 | MVP account model: one non-prod account + one prod account | Cloud placement |
| CON-003 | Public contract must not couple to LiteLLM configuration structures | Contract independence |
| CON-004 | Public contract must not couple to Bedrock Guardrails response formats | Contract independence |
| CON-005 | One logical evaluation contract across Remote, Gateway, and Customer-Managed modes | Contract independence |
| CON-006 | Policies and connectors are mandatorily separate | Contract independence |
| CON-007 | Every deployment must document content exposure explicitly | Contract independence |
| CON-008 | TLS required for all runtime communication | Transport & crypto |
| CON-009 | KMS encryption in transit and at rest | Transport & crypto |
| CON-010 | IAM-based authentication; least-privilege, short-lived roles | Identity & auth |
| CON-011 | Per-tenant workload identities; no cross-tenant sharing | Identity & auth |
| CON-012 | No exclusive reliance on API keys, unauthenticated identifiers, unauthenticated policy versions, or telemetry as security | Identity & auth |
| CON-013 | Raw content must not leave a customer-managed runtime unless explicitly configured | Content handling |
| CON-014 | No raw-content retention by default in the hosted runtime | Content handling |
| CON-015 | Input-size limits enforced on all evaluated content | Resource limits |
| CON-016 | Timeout limits enforced on every evaluation stage | Resource limits |
<!-- END GENERATED: con-summary -->
---

## Constraint interaction diagram

The diagram below shows how the content-handling constraints (CON-013, CON-014) bound what may cross the trust boundary of each deployment mode, and how they relate to the transport/crypto and identity constraints that protect whatever is permitted to cross it.

```text
┌───────────────────────────────────────────────────────────────────────┐
│ Mode A — Remote Evaluation ([AGR-002](AGREEMENTS.md#agr-002--three-deployment-modes), Mode A) │
│                                                                        │
│  Customer AWS account            │            Hosted (provider) side  │
│  ┌───────────────────┐           │           ┌─────────────────────┐  │
│  │ Application        │          │           │ Evaluation API      │  │
│  │       │             │  TLS +   │           │  - CON-014: no raw  │  │
│  │ Team-owned LiteLLM ─┼─ IAM ────┼──────────►│    retention by     │  │
│  │       (connector)   │ (CON-008 │           │    default          │  │
│  └───────────────────┘   /010)    │           └─────────────────────┘  │
│                                    │  Prompts + responses DO cross     │
│                                    │  this boundary (AGR-009: not      │
│                                    │  data-local)                      │
└───────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────┐
│ Mode B — Guarded Gateway ([AGR-002](AGREEMENTS.md#agr-002--three-deployment-modes), Mode B) │
│                                                                        │
│  Application ──TLS+IAM──► Provider-managed Guarded Gateway             │
│                             (guardrails + model routing, one          │
│                              provider-operated trust boundary;         │
│                              CON-009/010/011 apply throughout)         │
└───────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────┐
│ Mode C — Customer-Managed Runtime ([AGR-002](AGREEMENTS.md#agr-002--three-deployment-modes), Mode C) │
│                                                                        │
│  Customer AWS account (raw content stays here by default)             │
│  ┌────────────────────────────────────────────────────┐               │
│  │ Application → Customer LiteLLM → Guardrails Runtime │               │
│  │                                   (policy cache,     │               │
│  │                                    local evaluators)  │              │
│  └───────────────────────┬──────────────────────────────┘             │
│                           │ Only customer-selected telemetry           │
│                           │ categories cross (or none) — CON-013       │
│                           ▼                                            │
│              Hosted control plane (policy sync, optional telemetry)   │
└───────────────────────────────────────────────────────────────────────┘
```

All three trust boundaries above are crossed only over TLS (CON-008), authenticated via IAM and per-tenant workload identity (CON-010, CON-011), and encrypted at rest wherever data lands (CON-009). Modes A and B always transmit raw content to a provider-operated boundary; only Mode C is permitted to keep raw content local, and even there, no raw content crosses outward without explicit configuration (CON-013).

---

## Related documents

- [`AGREEMENTS.md`](AGREEMENTS.md) — the authoritative agreement register this document operationalizes.
- `10-charter/CHARTER.md` — product charter narrative (derived view; must reflect these constraints).
- `50-architecture/SECURITY-ARCHITECTURE.md` — implements CON-008 through CON-012.
- `50-architecture/DATA-ARCHITECTURE.md` — implements CON-007, CON-013, CON-014.
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md` — implements CON-001, CON-002, CON-005.
- `50-architecture/INTEGRATION-ARCHITECTURE.md` — implements CON-003, CON-004, CON-006.
- `70-governance/CLAIMS-REGISTER.md` — must reject any external claim that contradicts a constraint in this register.
