**AI Guardrails — Functional Requirements**

**Authority level:** L2
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

## Purpose and authority

This document enumerates the **functional requirements** for the AI Guardrails solution: individually identified (`FR-001` …), measurable, testable statements of what the platform must do. It is a **derived (L2)** artifact and must not contradict [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both L1 — authoritative). Where a conflict is found, those documents win and this register must be corrected.

Every requirement below is derived from [`SCOPE.md`](../10-charter/SCOPE.md) (in-scope product scope), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (core domain model), [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md) (logical architecture), [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (LiteLLM integration), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (operational requirements), and §16 (MVP definition), read alongside the accepted agreements in [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md). No metric, dollar figure, SLA, or performance number is asserted beyond what the L1 registers state; where a numeric target would normally appear and is not present in the original charter, this document says the target is **TBD**, consistent with [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md).

This document is deliberately more granular than [`../20-product/CAPABILITIES.md`](../20-product/CAPABILITIES.md). `CAPABILITIES.md` catalogs *what the product does* at capability granularity (`CAP-001` …); this document states, for each capability, the individually verifiable *requirement statements* (`FR-001` …) that a test plan, an architecture document, or an implementation can be checked against. Every `FR-` below cross-references the `CAP-` ID(s) it operationalizes.

---

## How to read this document

| Column | Meaning |
|---|---|
| **ID** | Stable identifier (`FR-001` …), referenced by architecture documents, test plans, and the traceability matrix. |
| **Requirement** | A single, testable "must" / "must not" statement. Where a requirement is intentionally scoped narrower than a related future capability, that scoping is stated explicitly in the requirement text, not hidden in the Priority column. |
| **Rationale / Source** | The register entry the requirement is derived from, plus the `AGREEMENTS.md` (`AGR-`), `CONSTRAINTS.md` (`CON-`), `CAPABILITIES.md` (`CAP-`), or `OPEN-QUESTIONS.md` (`OQ-`) entry it is bound by or traces to. |
| **Priority** | `MVP` — Phases 1–2 (core platform) scope per the [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) component list and [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md). `Future` — later-phase scope: architecturally permitted but explicitly **not** built during Phases 1–2, per [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) and [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery). |

> **Change control:** A requirement's Priority may only move from `Future` to `MVP` (or vice versa) through the scope-change governance rule in [`../10-charter/SCOPE.md` §8](../10-charter/SCOPE.md#8-scope-change-governance-rule) — never as an incidental edit to this document.

---

## Table of contents

- [Purpose and authority](#purpose-and-authority)
- [How to read this document](#how-to-read-this-document)
- [Functional area map](#functional-area-map)
- [1. Policy definition and versioning](#1-policy-definition-and-versioning)
- [2. Tenant and workload registration](#2-tenant-and-workload-registration)
- [3. Policy-to-workload binding](#3-policy-to-workload-binding)
- [4. Evaluation stages: input and output](#4-evaluation-stages-input-and-output)
- [5. Decision normalization](#5-decision-normalization)
- [6. LiteLLM connector: pre-call and post-call](#6-litellm-connector-pre-call-and-post-call)
- [7. Generic HTTP Evaluation API](#7-generic-http-evaluation-api)
- [8. Evaluator adapters: Bedrock Guardrails, deterministic rules, and sensitive-data handling](#8-evaluator-adapters-bedrock-guardrails-deterministic-rules-and-sensitive-data-handling)
- [9. Observe and enforce modes](#9-observe-and-enforce-modes)
- [10. Decision-metadata audit](#10-decision-metadata-audit)
- [11. Evaluation latency and performance metrics](#11-evaluation-latency-and-performance-metrics)
- [12. Policy promotion and rollback](#12-policy-promotion-and-rollback)
- [13. Policy distribution and caching](#13-policy-distribution-and-caching)
- [14. Runtime capability manifest and bind-time compatibility](#14-runtime-capability-manifest-and-bind-time-compatibility)
- [15. Connector identity, installation verification, and liveness](#15-connector-identity-installation-verification-and-liveness)
- [16. Client distribution, configuration, and conformance](#16-client-distribution-configuration-and-conformance)
- [17. Future-scope functional requirements (not built in the MVP)](#17-future-scope-functional-requirements-not-built-in-the-mvp)
- [18. Traceability summary](#18-traceability-summary)
- [19. Related documents](#19-related-documents)

---

## Functional area map

The diagram below places each functional area (and its `FR-` range) against the logical architecture in [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md), showing how a request moves from connector, through evaluation and decision normalization, into mode enforcement, audit, and metrics.

```mermaid
flowchart TB
    subgraph POLICY["Policy & binding — §1-§3 (FR-001..FR-017, FR-095..FR-100)"]
        P1["Define versioned policy\nFR-001..FR-006"]
        P2["Register tenant & workload\nFR-007..FR-011"]
        P3["Bind policy version to workload\nFR-012..FR-017"]
        P4["Generate integration manifest\nFR-095..FR-100"]
    end

    subgraph CONNECT["Connectors & APIs — §6-§7 (FR-031..FR-042)"]
        C1["LiteLLM pre-call connector\nFR-031..FR-032, FR-035..FR-038"]
        C2["LiteLLM post-call connector\nFR-033..FR-038"]
        C3["Generic HTTP Evaluation API\nFR-039..FR-042"]
    end

    subgraph EVAL["Evaluation — §4-§5, §8 (FR-018..FR-030, FR-043..FR-050)"]
        E1["Input-stage evaluation\nFR-018, FR-020..FR-024"]
        E2["Output-stage evaluation\nFR-019..FR-024"]
        E3["Evaluator adapters:\nBedrock Guardrails - deterministic - PII\nFR-043..FR-050"]
        E4["Decision normalization\nallow / block / redact / log_only\nFR-025..FR-030"]
    end

    subgraph MODE["Observe / enforce — §9 (FR-051..FR-056)"]
        M1["Observe or Enforce per binding\nFR-051..FR-056"]
    end

    subgraph OBS["Audit & metrics — §10-§11 (FR-057..FR-070)"]
        O1["Decision-metadata audit\nFR-057..FR-063"]
        O2["Latency & performance metrics\nFR-064..FR-070"]
    end

    subgraph LIFECYCLE["Policy lifecycle — §12-§14 (FR-071..FR-077, FR-106..FR-112)"]
        L1["Promotion, rollback,\ncompatibility validation\nFR-071..FR-077"]
        L2["Distribution, caching,\nstaleness bounds\nFR-106..FR-109"]
        L3["Runtime capability manifest,\nbind-time refusal\nFR-110..FR-112"]
    end

    subgraph CLIENTLIFE["Client lifecycle — §15-§16 (FR-113..FR-127)"]
        K1["Identity, verification,\nliveness, decommission\nFR-113..FR-118"]
        K2["Distribution, configuration,\nconformance\nFR-119..FR-127"]
    end

    P2 --> P3
    P1 --> P3
    P3 --> P4
    P4 --> C1
    P4 --> C2
    P4 --> C3
    C1 --> E1 --> E3 --> E4
    C2 --> E2 --> E3
    E4 --> M1
    M1 --> C1
    M1 --> C2
    E4 --> O1
    E4 --> O2
    P1 -.-> L1
    L1 -.-> P3
    L2 -.-> L1
    L3 -.-> P3
    K2 --> K1
    K1 -.-> C1
    K1 -.-> C3
```

---

## 1. Policy definition and versioning

A policy defines what must be evaluated and how violations are handled ([`AGREEMENTS.md` AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)). It is the portable entity ([AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability)) that every deployment mode must resolve identically ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)). Implements [`CAPABILITIES.md` CAP-001](../20-product/CAPABILITIES.md#policy-governance).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-001 | The platform must allow defining a guardrail policy with, at minimum: policy name, an immutable version identifier, one or more evaluation stages, evaluator configuration, enforcement actions, thresholds, a failure-behavior section, audit behavior, and compatibility metadata. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy) | MVP |
| FR-002 | Once published, a policy version must be immutable; any change to checks, thresholds, or actions must be expressed as a new policy version, never as a modification of an existing one. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Policy rollout — "Immutable versions"); [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | MVP |
| FR-003 | A policy must not be accepted by the runtime unless its failure-behavior section explicitly addresses: Evaluation API timeout, detector unavailability, unresolved policy, control-plane unavailability, audit-delivery failure, and inability to safely transform a response. Policies missing any of these must be rejected or flagged, never defaulted silently. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) (Failure behavior is part of policy) | MVP |
| FR-004 | A policy definition must not encode LiteLLM configuration structures, Bedrock Guardrails response formats, a specific model provider, a specific programming language, or a specific application framework; any such vendor- or framework-specific logic must live in a connector or adapter, never in the policy itself. | [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) (Policy portability); [CON-003](../00-source-of-truth/CONSTRAINTS.md#con-003--no-coupling-to-litellm-configuration-structures); [CON-004](../00-source-of-truth/CONSTRAINTS.md#con-004--no-coupling-to-bedrock-guardrails-response-formats) | MVP |
| FR-005 | Policy storage must retain every previously published policy version in a resolvable form, so a binding can reference, compare against, or roll back to any prior version. | [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) (Policy storage), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Policy rollout) | MVP |
| FR-006 | A policy version's compatibility metadata must be evaluable by the runtime to determine whether that version is compatible with a given runtime or connector version before it can be bound to a workload. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy — compatibility metadata), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Compatibility validation) | MVP |
| FR-101 | Policy validation must occur at publication. A policy document that fails schema validation — including any missing failure-behavior key (FR-003) — must never be published, and therefore must never be resolvable by a runtime. Publishing an already-published name and version must be refused. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model); [ADR-0009](adrs/ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) §1, §4, §6 | MVP |
| FR-102 | A policy must select evaluators by detection family and capability, never by implementation, vendor, or engine name. Which implementation serves a family in a given deployment is deployment configuration, so the same policy document must resolve to different implementations in different deployments without modification. | [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [CON-004](../00-source-of-truth/CONSTRAINTS.md#con-004--no-coupling-to-bedrock-guardrails-response-formats); [ADR-0009](adrs/ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) §3 | MVP |

---

## 2. Tenant and workload registration

Tenant and Workload are the two identity entities that every policy binding, evaluation, and audit record must resolve to ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)). Per-tenant workload identity is also a security constraint, not only a domain-model convenience ([CON-011](../00-source-of-truth/CONSTRAINTS.md#con-011--per-tenant-workload-identities)). Implements [`CAPABILITIES.md` CAP-002](../20-product/CAPABILITIES.md#workload-and-binding).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-007 | The platform must represent a Tenant with, at minimum: tenant identifier, ownership information, AWS account information, data classification, allowed Regions, retention configuration, deployment mode, and authentication configuration, and must issue each tenant's workloads a distinct, non-shared workload identity. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Tenant); [CON-011](../00-source-of-truth/CONSTRAINTS.md#con-011--per-tenant-workload-identities) | MVP |
| FR-008 | The platform must allow registering a Workload — an application, assistant, agent, gateway, or inference service — with, at minimum: workload identifier, tenant identifier, owner, environment, risk classification, gateway type, deployment mode, expected throughput, streaming requirements, assigned policy, and enforcement mode. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Workload) | MVP |
| FR-009 | Workload registration must record a gateway-type field that is not restricted to LiteLLM, so a workload calling the generic HTTP Evaluation API directly, or through a future non-LiteLLM gateway, can be registered without a schema change. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (LiteLLM integration) | MVP |
| FR-010 | Registering a workload must not require copying another team's LiteLLM database, transferring model-provider credentials, exposing gateway administration endpoints, replacing the team's model-routing configuration, or granting the AI Guardrails provider administrative access to the team's gateway. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) (Definition of the import capability) | MVP |
| FR-011 | Workload registration must capture a "streaming requirements" attribute; the runtime must not represent, imply, or advertise streaming output evaluation as supported for a workload registered as streaming, since streaming evaluation remains future scope (see FR-080). | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Workload — streaming requirements), [`SCOPE.md`](../10-charter/SCOPE.md); [OQ-004](../00-source-of-truth/OPEN-QUESTIONS.md#oq-004--streaming-output-evaluation-timeline) | MVP |

---

## 3. Policy-to-workload binding

A binding associates a workload with a specific, immutable policy version and carries the mode, override, and rollback metadata needed to operate the observe-before-enforce progression ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)). The binding is also what an integration manifest is generated from — the "generate integration manifest" step of the onboarding flow ([AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import)), specified in [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md). Implements [`CAPABILITIES.md` CAP-003](../20-product/CAPABILITIES.md#workload-and-binding) and [CAP-038](../20-product/CAPABILITIES.md#workload-and-binding).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-012 | The platform must allow creating a binding that associates exactly one workload with exactly one specific, immutable policy version. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy binding) | MVP |
| FR-013 | Every binding must record whether it is currently operating in Observe or Enforce mode (see [Section 9](#9-observe-and-enforce-modes)). | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy binding); [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) | MVP |
| FR-014 | A binding may define workload-specific overrides only from a limited, explicitly tracked category list, and each such override must require a recorded owner. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy binding), [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) (Policy fragmentation mitigation) | MVP |
| FR-015 | Every binding must record an effective date and a designated rollback policy version. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy binding) | MVP |
| FR-016 | Every binding must record its runtime compatibility requirements, and the platform must validate those requirements against the resolving runtime/connector version before the binding is activated. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy binding), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Compatibility validation) | MVP |
| FR-017 | A newly created binding must default to Observe mode; enforcement must never be enabled as an implicit side effect of binding creation or of any other change. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) (implications) | MVP |
| FR-095 | The platform must generate, from a policy binding, an integration manifest: a machine-readable, version-controllable artifact a consuming team can commit to its own repository, review, and share with another team. It must not be a rendered page or a copy-paste snippet. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) (onboarding flow); [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md); CAP-038 | MVP |
| FR-096 | An integration manifest must carry, at minimum: manifest schema version, tenant identifier, workload identity, bound policy name and immutable version, enforcement mode, deployment mode, Evaluation API endpoint, evaluation contract major version, connector type and supported connector version range, per-stage evaluation timeouts, client-side failure behavior, required runtime capabilities, correlation-identifier propagation requirement, and the source binding reference with a generation timestamp. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary); [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import), [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [CON-016](../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits); [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md) §3 | MVP |
| FR-097 | An integration manifest must never contain model-provider credentials, gateway administration endpoints or credentials, any part of the consuming team's model-routing configuration, any reference requiring access to the team's LiteLLM database, or any AI Guardrails platform secret or long-lived bearer credential. The manifest schema must forbid these, and generated manifests must be validated against that prohibition. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import); [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md) §4 | MVP |
| FR-098 | Possession of an integration manifest must grant no access. The workload identity it carries is an identifier, not a secret; authentication to the Evaluation API remains IAM-based. | [CON-010](../00-source-of-truth/CONSTRAINTS.md#con-010--iam-based-authentication-and-least-privilege-roles), [CON-011](../00-source-of-truth/CONSTRAINTS.md#con-011--per-tenant-workload-identities); [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md) §5 | MVP |
| FR-099 | An integration manifest must not be authoritative at evaluation time: the runtime must resolve the effective policy version and enforcement mode server-side regardless of what any manifest asserts, and a manifest must be regenerable from its binding. | [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model); [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md) §2 | MVP |
| FR-100 | One integration-manifest structure must serve all three deployment modes; only field values, not the artifact's shape, may differ between Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime. | [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [ADR-0006](adrs/ADR-0006-integration-manifest-format-and-contents.md) §7 | MVP |

---

## 4. Evaluation stages: input and output

Evaluation represents a request to inspect content at a particular stage. The MVP requires exactly two stages — `input` and `output` — while the Evaluation entity's stage field must remain extensible to future stages without redesign ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)). Implements [`CAPABILITIES.md` CAP-004 and CAP-005](../20-product/CAPABILITIES.md#evaluation).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-018 | The Evaluation API must accept a request to evaluate content at the `input` stage, executed before the model is invoked. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Evaluation — supported initial stages) | MVP |
| FR-019 | The Evaluation API must accept a request to evaluate content at the `output` stage, executed after the model responds and before that response reaches the caller. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Evaluation — supported initial stages) | MVP |
| FR-020 | In the MVP, the Evaluation entity must support only the `input` and `output` stages, and evaluated content categories must be limited to user/model input and model output; the stage field must nonetheless be defined so future stages (`retrieval`, `tool_call`, `tool_result`, `memory_write`, `final_response`) can be added without redesigning the entity. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [`DATA-ARCHITECTURE.md`](DATA-ARCHITECTURE.md) ("The MVP should initially limit support to user/model input and model output"), [`SCOPE.md`](../10-charter/SCOPE.md) | MVP |
| FR-021 | Every evaluation request and its resulting decision must carry a correlation identifier that is propagated end-to-end from the originating request through the decision and into the audit record. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision), [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("Propagating correlation identifiers") | MVP |
| FR-022 | Every evaluation stage must enforce an explicit, configurable timeout; the behavior when that timeout is reached must be governed by the workload's configured failure behavior, never an undocumented default. | [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (Timeout limits), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Availability — timeout behavior); [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [CON-016](../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits) | MVP |
| FR-023 | The Evaluation API and every evaluator adapter must enforce an input-size limit on content submitted for evaluation; no evaluation path may accept unbounded input. | [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (Input-size limits); [CON-015](../00-source-of-truth/CONSTRAINTS.md#con-015--input-size-limits) | MVP |
| FR-024 | Every evaluation must record the exact, immutable policy version that was applied, so a decision is always traceable to one specific policy version rather than a policy name in the abstract. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision — "Policy name and version") | MVP |

---

## 5. Decision normalization

Every evaluator's output must be normalized into one vendor-independent decision structure, regardless of which engine produced it ([AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)). Implements [`CAPABILITIES.md` CAP-012](../20-product/CAPABILITIES.md#decisions-audit-and-observability).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-025 | Every evaluator's output — whether produced by the Bedrock Guardrails adapter, the deterministic evaluator, or the sensitive-data evaluator — must be normalized into one common platform decision structure before it reaches a policy, a connector, or an audit record. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision); [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract); [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) | MVP |
| FR-026 | In the MVP, a decision's action field must be restricted to exactly four values: `allow`, `block`, `redact`, `log_only`. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision — initial actions) | MVP |
| FR-027 | Every decision must contain: action, allowed status, reason codes, policy name and version, evaluator metadata, transformed content where applicable, correlation identifier, audit identifier, and evaluation latency. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision) | MVP |
| FR-028 | The decision schema must reserve, but need not operationally implement, the future actions `rewrite`, `discard`, `abstain`, and `require_approval`, so a later action can extend the schema without a breaking redesign. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision — future actions); [OQ-007](../00-source-of-truth/OPEN-QUESTIONS.md#oq-007--human-approval-workflows-introduction-timing-and-ownership) | Future |
| FR-029 | Reason codes must be part of the vendor-independent contract and must be identical in meaning regardless of which evaluation engine produced the decision or which deployment mode is executing it. | [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) (One contract across deployment modes); [CON-005](../00-source-of-truth/CONSTRAINTS.md#con-005--one-logical-evaluation-contract-across-all-modes) | MVP |
| FR-030 | When a decision's action is `redact`, the decision must include the transformed (redacted) content, and the invoking connector must apply that transformed content rather than the original. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision — "Transformed content where applicable") | MVP |

---

## 6. LiteLLM connector: pre-call and post-call

LiteLLM is the first supported gateway connector, not the product boundary ([AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)). The connector owns request mapping and enforcement invocation only, never policy definition or detection logic ([AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)). Implements [`CAPABILITIES.md` CAP-006 and CAP-007](../20-product/CAPABILITIES.md#connectors-and-apis).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-031 | The LiteLLM pre-call connector must map the LiteLLM request into the evaluation contract and invoke `input`-stage evaluation before model invocation proceeds. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (LiteLLM integration) | MVP |
| FR-032 | The LiteLLM pre-call connector must enforce the input-evaluation decision: a `block` decision must prevent model invocation, and a `redact` decision must cause the redacted content to be used for model invocation instead of the original. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("Enforcing block or redact decisions") | MVP |
| FR-033 | The LiteLLM post-call connector must invoke `output`-stage evaluation after model inference completes and before the response is returned to the calling application. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (LiteLLM integration) | MVP |
| FR-034 | The LiteLLM post-call connector must apply a safe output fallback when the output decision is `block`, and must return the transformed content when the output decision is `redact`. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("Applying safe output fallbacks") | MVP |
| FR-035 | Both the pre-call and post-call connectors must propagate the same correlation identifier for a given request across input evaluation, model invocation, and output evaluation. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("Propagating correlation identifiers") | MVP |
| FR-036 | Both connectors must handle an evaluation timeout according to the workload's configured failure behavior, not a fixed, undocumented default. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("Handling evaluation timeouts"); [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) | MVP |
| FR-037 | Both connectors must emit connector-health telemetry; this telemetry must never, by itself, be treated as evidence that guardrail enforcement is mandatory or bypass-proof. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("Emitting connector-health telemetry"); [CON-012](../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) | MVP |
| FR-038 | Neither connector may define organizational policy, implement detection models, manage model-provider credentials, persist audit records, perform user authorization, or manage policy versions; these responsibilities remain with the policy author, the Guardrails Runtime, and the consuming application, respectively. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("The connector is not responsible for"); [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) | MVP |

---

## 7. Generic HTTP Evaluation API

The generic HTTP Evaluation API is the proof that the evaluation contract does not depend on LiteLLM ([AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)) and is exercised directly by the Phase 1–2 validation demonstration ([`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md); [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md)). Implements [`CAPABILITIES.md` CAP-008](../20-product/CAPABILITIES.md#connectors-and-apis).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-039 | The platform must expose a generic, gateway-agnostic HTTP Evaluation API that implements the same evaluation contract used by the LiteLLM connector, callable directly by an application, SDK, or a non-LiteLLM gateway. | [`SCOPE.md`](../10-charter/SCOPE.md); [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary); [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) | MVP |
| FR-040 | The generic HTTP Evaluation API must accept both `input`- and `output`-stage evaluation requests using the identical request and decision schema used by the LiteLLM connector path, with no LiteLLM-specific fields required. | [`SCOPE.md`](../10-charter/SCOPE.md); [CON-003](../00-source-of-truth/CONSTRAINTS.md#con-003--no-coupling-to-litellm-configuration-structures); [CON-005](../00-source-of-truth/CONSTRAINTS.md#con-005--one-logical-evaluation-contract-across-all-modes) | MVP |
| FR-041 | The generic HTTP Evaluation API must authenticate callers via IAM-based authentication and must not treat a tenant-supplied identifier as sufficient authentication on its own. | [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (Security requirements); [CON-010](../00-source-of-truth/CONSTRAINTS.md#con-010--iam-based-authentication-and-least-privilege-roles); [CON-012](../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) | MVP |
| FR-042 | The generic HTTP Evaluation API must be independently demonstrable — callable and enforceable without any LiteLLM instance present — to prove that LiteLLM is a connector and not the product boundary. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary); [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md); [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md) | MVP |

---

## 8. Evaluator adapters: Bedrock Guardrails, deterministic rules, and sensitive-data handling

Amazon Bedrock Guardrails is the first managed evaluation engine, but the public contract remains vendor-independent ([AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)). The deterministic evaluator and sensitive-data evaluator run behind the same adapter pattern. Implements [`CAPABILITIES.md` CAP-009, CAP-010, and CAP-011](../20-product/CAPABILITIES.md#connectors-and-apis).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-043 | The platform must implement a Bedrock Guardrails adapter that invokes Amazon Bedrock Guardrails and normalizes its response into the platform's own decision structure before that result reaches any policy, connector, or audit record. | [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md) (Logical architecture), [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md); [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract) | MVP |
| FR-044 | Bedrock Guardrails' native response format must never be exposed directly to a policy author, a connector, or an audit consumer; only the normalized decision structure may cross that boundary. | [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract); [CON-004](../00-source-of-truth/CONSTRAINTS.md#con-004--no-coupling-to-bedrock-guardrails-response-formats) | MVP |
| FR-045 | The platform must implement a deterministic evaluator supporting organization-authored, non-ML rule-based checks (for example, pattern-, keyword-, or schema-based validation), running behind the same evaluator-adapter interface as the Bedrock Guardrails adapter. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) (MVP components, item 7); §9 ("Deterministic organization rules") | MVP |
| FR-046 | The deterministic evaluator's rules must be addable and versioned through policy configuration, without requiring a code change to any connector. | [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) (Policy portability) | MVP |
| FR-047 | The platform must implement a basic sensitive-data (PII) evaluator capable of detecting defined sensitive-data categories within evaluated content, sufficient to drive `redact`, `block`, or `log_only` decisions on recognized categories. | [`SCOPE.md`](../10-charter/SCOPE.md) (item 10), [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md) ("PII evaluators") | MVP |
| FR-048 | The sensitive-data evaluator's scope must remain bounded to basic detection; it must not be represented as, or expanded into, a complete data-loss-prevention (DLP) platform during the MVP. | [`SCOPE.md`](../10-charter/SCOPE.md) ("A complete DLP platform") | MVP |
| FR-049 | The evaluator-adapter interface must be designed so additional evaluation engines beyond the Bedrock Guardrails adapter, the deterministic evaluator, and the sensitive-data evaluator can be added later without changing the policy schema or the decision structure; no additional engine is required to ship in the MVP itself. | [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract) (implications); [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) | MVP |
| FR-050 | Every evaluator adapter must report its own evaluator version as part of the decision and audit metadata for each evaluation it produces. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Auditing — "Evaluator versions") | MVP |

---

## 9. Observe and enforce modes

Every policy must support a non-blocking evaluation mode and a defined progression from Disabled to Observe to Warn to Redact to Block ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)). The MVP requires Observe and Enforce to be fully operational end to end. Implements [`CAPABILITIES.md` CAP-013](../20-product/CAPABILITIES.md#decisions-audit-and-observability).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-051 | The policy and binding schema must represent all five states of the observe-before-enforce progression — Disabled, Observe, Warn, Redact, Block — per policy, per workload. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) (Observable before enforceable) | MVP |
| FR-052 | Phases 1–2 must implement Observe and Enforce as fully operational, end-to-end states; per the Phase 1–2 validation demonstration, one workload bound to a given policy must be able to run in Observe mode while a different workload bound to the same policy version runs in Enforce mode. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) (MVP components, item 10; MVP demonstration); [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md) | MVP |
| FR-053 | A binding operating in Observe mode must record every decision without blocking, redacting, or otherwise altering the request or response actually delivered to the caller or the model. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) | MVP |
| FR-054 | A binding operating in Enforce mode must apply the decision's action to the request or response before it is delivered to the caller or the model (a `block` decision must prevent delivery; a `redact` decision must substitute the transformed content). | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | MVP |
| FR-055 | A new binding must default to Observe mode; promotion to Enforce (or any other state) must be an explicit, separately recorded action, never an implicit side effect of another change such as a policy-version upgrade. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) (implications) | MVP |
| FR-056 | The platform must record, in audit metadata, which mode (Observe or Enforce) was in effect for a given workload at the moment each decision was produced. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Auditing); [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) | MVP |

---

## 10. Decision-metadata audit

Audit metadata is the evidence that policies are being applied, and the mechanism for tracing every decision back to a tenant, workload, and policy version ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)). Default behavior must not retain raw content ([AGR-014](../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default)). Implements [`CAPABILITIES.md` CAP-015](../20-product/CAPABILITIES.md#decisions-audit-and-observability).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-057 | Every evaluation decision must produce an audit record containing, at minimum: tenant, workload, policy version, evaluation stage, decision, reason codes, evaluator versions, timestamp, correlation identifier, and whether raw content was retained for that evaluation. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Auditing) | MVP |
| FR-058 | An audit record must be produced for every evaluation regardless of whether the governing binding is in Observe or Enforce mode. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) | MVP |
| FR-059 | By default, the hosted runtime must persist decision metadata only; it must not persist raw prompts or responses unless the tenant has explicitly configured raw-content retention. | [`DATA-ARCHITECTURE.md`](DATA-ARCHITECTURE.md) (Default behavior); [AGR-014](../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default); [CON-014](../00-source-of-truth/CONSTRAINTS.md#con-014--no-raw-content-retention-by-default-in-the-hosted-runtime) | MVP |
| FR-060 | Audit and telemetry pipelines must hash user and conversation identifiers where possible rather than storing them in cleartext, and must remove secrets from any emitted telemetry. | [`DATA-ARCHITECTURE.md`](DATA-ARCHITECTURE.md) (Default behavior) | MVP |
| FR-061 | Audit-delivery failure must be handled according to the workload's configured failure behavior; the platform must record that audit delivery did not complete normally and which failure-behavior path was taken. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) | MVP |
| FR-062 | Audit-event integrity must be preserved from the point a decision is generated through to durable storage — a decision must not be silently dropped between generation and archival. | [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (Audit-event integrity) | MVP |
| FR-063 | In a Customer-Managed Runtime deployment, audit records generated locally must remain local by default; only telemetry categories the customer has explicitly selected (or none) may reach the hosted control plane. | [`DATA-ARCHITECTURE.md`](DATA-ARCHITECTURE.md) (Customer-managed telemetry); [AGR-016](../00-source-of-truth/AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime); [CON-013](../00-source-of-truth/CONSTRAINTS.md#con-013--raw-content-must-not-leave-a-customer-managed-runtime-unless-explicitly-configured) | MVP |
| FR-103 | A request the runtime declines to evaluate — oversized content, an unrecognized stage, an unrecognized content kind, or an authentication or authorization refusal — must produce an audit record marked as a rejection, carrying the reason and correlation identifier. A rejection record must not carry the content that failed validation, and must not be required to carry a policy or an action. | [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [AGR-014](../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default); [ADR-0014](adrs/ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md) §1, §2 | MVP |
| FR-104 | A client that produces a decision without reaching the runtime must record it locally and deliver it to the platform once the runtime is reachable. The local store must be bounded, must carry no evaluated content, and must report the number of records discarded on overflow rather than discarding silently. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-014](../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default); [ADR-0014](adrs/ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md) §3 | MVP |
| FR-105 | Every audit record must identify whether it was produced by the runtime or self-reported by a client. Client-origin records must remain distinguishable in every downstream consumer, and no metric or report may aggregate the two without stating that it does. | [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [CON-012](../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies); [ADR-0014](adrs/ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md) §4 | MVP |

---

## 11. Evaluation latency and performance metrics

The platform must measure evaluation performance so a team can assess business impact before enforcing a policy ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)). Specific numeric latency targets are **TBD** and are not asserted in this document. Implements [`CAPABILITIES.md` CAP-014](../20-product/CAPABILITIES.md#decisions-audit-and-observability).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-064 | Every decision must include its own evaluation latency as a discrete field. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision) | MVP |
| FR-065 | The platform must measure and expose P50, P95, and P99 evaluation latency. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Performance) | MVP |
| FR-066 | The platform must measure evaluation throughput. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Performance) | MVP |
| FR-067 | The platform must measure the evaluation timeout rate. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Performance); [CON-016](../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits) | MVP |
| FR-068 | The platform must measure detector-specific (per-evaluator) latency, so a specific slow evaluator can be identified independent of overall evaluation latency. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Performance — "Detector-specific latency") | MVP |
| FR-069 | The platform must measure decision distribution — the relative proportion of `allow`, `block`, `redact`, and `log_only` decisions produced. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Performance — "Decision distribution") | MVP |
| FR-070 | The platform must measure policy-cache hit rate. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Performance — "Policy-cache hit rate") | MVP |

> Specific numeric SLA or latency targets (for example, a P95 ceiling in milliseconds) are **not** defined in the L1 registers and are therefore **TBD**; this document requires only that the measurements above be captured, per source-of-truth policy against inventing figures.

---

## 12. Policy promotion and rollback

Policies must support controlled promotion, workload-scoped rollout, and immediate rollback so a policy can be evolved safely across workloads ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)). This is exercised directly by the Phase 1–2 validation demonstration ([`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md); [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md)). Implements [`CAPABILITIES.md` CAP-001 and CAP-003](../20-product/CAPABILITIES.md#policy-governance).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-071 | The platform must support promoting a binding from a lower state to a higher state in the observe-before-enforce progression (for example, Observe → Enforce) only as an explicit, separately recorded action. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Controlled promotion); [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) | MVP |
| FR-072 | The platform must support publishing a new, immutable policy version without mutating any previously published version. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Immutable versions) | MVP |
| FR-073 | The platform must support workload-scoped rollout: promoting a subset of workloads bound to a policy to a new policy version while other workloads remain bound to a prior version. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Workload-scoped rollout) | MVP |
| FR-074 | The platform must support immediate rollback of a workload's binding to its designated rollback policy version. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Policy binding — rollback version), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Immediate rollback) | MVP |
| FR-075 | Before a new policy version is activated for a bound workload, the platform must validate that version's compatibility metadata against the resolving runtime/connector version. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Compatibility validation) | MVP |
| FR-076 | The platform must support comparing evaluation results produced under a candidate policy version against those produced under the currently bound version, to inform a promotion or rollback decision. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Evaluation-result comparison between versions) | MVP |
| FR-077 | The Phase 1–2 validation demonstration requires that a policy version can be upgraded and subsequently rolled back for a workload without changing that workload's connector integration or requiring any reconfiguration beyond the binding's policy-version reference. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) (MVP demonstration); [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md) | MVP |

---

## 13. Policy distribution and caching

Policy state reaches a runtime asynchronously and is resolved from local state, so that control-plane availability never bounds evaluation availability in any deployment mode ([AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)). Implements [`CAPABILITIES.md` CAP-001](../20-product/CAPABILITIES.md#policy-governance) and [CAP-017](../20-product/CAPABILITIES.md#deployment); design record: [ADR-0011](adrs/ADR-0011-policy-distribution-and-caching.md).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-106 | Runtimes must obtain policy and binding state by initiating retrieval from the control plane on a recurring interval. The control plane must not require inbound connectivity to a runtime, in any deployment mode. | [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md); [AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes); [ADR-0011](adrs/ADR-0011-policy-distribution-and-caching.md) §1 | MVP |
| FR-107 | Evaluation must resolve policy and binding state from local state only. No evaluation, in any deployment mode, may make a synchronous control-plane call, and a refresh in progress or failing must never block an evaluation. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md); [AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [ADR-0011](adrs/ADR-0011-policy-distribution-and-caching.md) §2 | MVP |
| FR-108 | A runtime that cannot refresh must continue evaluating against its last successfully retrieved state, and the control-plane-unavailable condition must be handled by the workload's configured failure behavior rather than by an implicit fallback or an exception reaching the caller. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [ADR-0011](adrs/ADR-0011-policy-distribution-and-caching.md) §3 | MVP |
| FR-109 | Every runtime must track and report the elapsed time since its last successful refresh, and a deployment must carry an explicit maximum tolerable staleness. Exceeding it must be reported and routed through the configured control-plane-unavailable behavior, never continued silently. Rollback and promotion take effect immediately at the control plane and converge at each runtime within its refresh interval; no material may describe them as instantaneous platform-wide. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Policy rollout, policy-cache hit rate); [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [ADR-0011](adrs/ADR-0011-policy-distribution-and-caching.md) §4, §5 | MVP |

---

## 14. Runtime capability manifest and bind-time compatibility

Because a policy selects evaluators by capability rather than by implementation (FR-102), a policy bound to a runtime lacking one of its required capabilities would produce different decisions in different deployments — the outcome [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) exists to prevent. This section activates the binding's "runtime compatibility requirements" attribute (FR-016). Design record: [ADR-0013](adrs/ADR-0013-runtime-capability-manifest.md).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-110 | Every runtime deployment must publish the set of detection capabilities it can serve, expressed in the same family-and-capability vocabulary policies select against. The manifest must state capabilities only — it must not disclose implementations, vendors, or engine names — and must be derived from the runtime's actual configuration rather than maintained separately. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md); [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [CON-004](../00-source-of-truth/CONSTRAINTS.md#con-004--no-coupling-to-bedrock-guardrails-response-formats); [ADR-0013](adrs/ADR-0013-runtime-capability-manifest.md) §1 | MVP |
| FR-111 | A runtime configured to serve a capability by an implementation that is unavailable, misconfigured, or unknown must fail to start, rather than starting with a silently reduced manifest. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [ADR-0013](adrs/ADR-0013-runtime-capability-manifest.md) §2 | MVP |
| FR-112 | Binding creation must validate every capability the policy version requires against the manifest of the runtime that will serve the workload, and refuse an unsatisfiable binding with a message naming the missing capabilities. Removing a capability from a running deployment must produce a report identifying every binding that depended on it. Capability shortfall encountered at request time despite these checks remains governed by the workload's configured failure behavior. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Compatibility validation); [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [ADR-0013](adrs/ADR-0013-runtime-capability-manifest.md) §3–§5 | MVP |

---

## 15. Connector identity, installation verification, and liveness

The connector is a cooperative component the platform does not operate ([AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)). These requirements make the connector half of compatibility validation (FR-006, FR-016, FR-075) implementable, make an incomplete installation visible, and give the cooperative posture the detection capability it can honestly provide. Implements [`CAPABILITIES.md` CAP-039](../20-product/CAPABILITIES.md#connectors-and-apis); design record: [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-113 | Compatibility validation must occur in two phases: declared compatibility at binding creation and policy promotion, validating the policy's requirements against the connector version range and runtime capabilities recorded for the workload; and observed compatibility at every evaluation, validating the identity the client actually presents. Neither phase alone satisfies FR-016 or FR-075. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) (Compatibility validation); [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §1 | MVP |
| FR-114 | An evaluation request must be able to carry the client's connector type, connector version, and the evaluation contract major version it speaks. The field is optional; a client not presenting identity must be recorded and reported as unknown, never treated as compatible. This identity is self-reported and must never be cited as evidence that a workload's guardrails cannot be bypassed. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [CON-012](../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies); [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §2 | MVP |
| FR-115 | A client presenting an evaluation contract major version the runtime does not serve must have its evaluation refused, and the client's configured failure behavior applies. A client whose connector version is outside the expected range but whose contract major is served must have its evaluation proceed, with the mismatch recorded on the workload and reported. | [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §3 | MVP |
| FR-116 | A bound workload must remain in an explicit pending-verification state, distinct from observing, until the platform has observed at least one evaluation at every stage the bound policy declares. Verification status must be reported to the workload owner and must be a precondition for promotion out of Observe. Verification state must be able to regress if a previously verified stage stops being exercised. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import); [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import); [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §4 | MVP |
| FR-117 | A workload with an active binding that has produced no evaluations for a configured per-workload interval must be reported as stale. The signal means only that no evaluations were observed; it must not be described as bypass detection or as evidence of enforcement, and it must not block, disable, or degrade anything. | [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [CON-012](../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies); [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §5 | MVP |
| FR-118 | The platform must support explicitly decommissioning a workload, retiring its bindings and ending staleness reporting, while retaining the workload, its bindings, and its audit history as queryable records. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model); [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §6 | MVP |

---

## 16. Client distribution, configuration, and conformance

The artifacts consuming teams install, how those clients learn the configuration they are required to apply, and how a client this team did not write proves it behaves identically. Implements [`CAPABILITIES.md` CAP-040](../20-product/CAPABILITIES.md#connectors-and-apis) and [CAP-041](../20-product/CAPABILITIES.md#connectors-and-apis); design records: [ADR-0008](adrs/ADR-0008-connector-distribution-and-integrity.md), [ADR-0012](adrs/ADR-0012-client-bootstrap-configuration.md).

| ID | Requirement | Rationale / Source | Priority |
|---|---|---|---|
| FR-119 | Every client the platform ships — the LiteLLM connector and the generic SDK — must be published to a named, documented distribution channel reachable by teams outside the provider's AWS account, with published release notes. Obtaining a client must not require privileged access in either direction. | [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery); [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import); [ADR-0008](adrs/ADR-0008-connector-distribution-and-integrity.md) §1 | MVP |
| FR-120 | A client's version must be independent of the evaluation contract, policy, and product versions, and each client release must declare which evaluation contract major versions it speaks. That declaration is what FR-113's compatibility validation reads. | [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [ADR-0008](adrs/ADR-0008-connector-distribution-and-integrity.md) §2 | MVP |
| FR-121 | Every published client artifact must carry an integrity mechanism a consuming team can verify before the artifact executes, and verification must be documented as a step in the installation instructions. The published documentation must state what the client does inside the consuming team's process, including that it observes prompt and response content on the request path. | [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [`DATA-ARCHITECTURE.md`](DATA-ARCHITECTURE.md); [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [CON-009](../00-source-of-truth/CONSTRAINTS.md#con-009--kms-encryption-in-transit-and-at-rest); [ADR-0008](adrs/ADR-0008-connector-distribution-and-integrity.md) §3, §4 | MVP |
| FR-122 | A published client artifact must contain no policy content, no detection logic, and no embedded credential — neither the platform's nor the consuming team's. | [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [CON-010](../00-source-of-truth/CONSTRAINTS.md#con-010--iam-based-authentication-and-least-privilege-roles); [ADR-0008](adrs/ADR-0008-connector-distribution-and-integrity.md) §5 | MVP |
| FR-123 | A client must be able to retrieve its per-stage timeouts and client-side failure behavior from the platform, keyed by workload identity, on a path separate from evaluation. The integration manifest (FR-096) seeds these values at install time; the retrieval path keeps them current when an operator changes them. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md); [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [CON-016](../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits); [ADR-0012](adrs/ADR-0012-client-bootstrap-configuration.md) §1, §5 | MVP |
| FR-124 | A client that has never successfully retrieved its configuration, and was given none explicitly at construction, must refuse to evaluate. It must not proceed with a built-in default and must not allow. A client that has previously retrieved its configuration must continue applying it when refresh fails. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy); [ADR-0012](adrs/ADR-0012-client-bootstrap-configuration.md) §2, §3 | MVP |
| FR-125 | Client configuration must carry timeouts and failure behavior only. It must not carry detection rules, thresholds, evaluator selection, or any policy content. | [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors); [ADR-0012](adrs/ADR-0012-client-bootstrap-configuration.md) §4 | MVP |
| FR-126 | The platform must publish a client conformance suite as a supported artifact, not an internal convenience, so that any client — including a consumer-operated custom gateway this team does not build — can demonstrate that it produces identical decisions and identical failure behavior. The suite must include, as mandatory cases: an unrecognized decision value, a never-configured client, concurrent interleaved requests, and a client-synthesized decision during runtime unavailability. | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [ADR-0005](adrs/ADR-0005-evaluation-contract-wire-format.md) §7, [ADR-0007](adrs/ADR-0007-connector-identity-verification-and-liveness.md) §4, [ADR-0012](adrs/ADR-0012-client-bootstrap-configuration.md) §2, [ADR-0014](adrs/ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md) §3 | MVP |
| FR-127 | The generic SDK must offer both a synchronous and an asynchronous calling surface, so that adopting guardrails does not require a consuming application to adopt an asynchronous runtime. The two surfaces must share one transport, one configuration source, and one failure-behavior path — they must not be independent implementations — and both must be registered in the conformance suite (FR-126) and produce identical decisions. | [`TARGET-USERS.md`](../20-product/TARGET-USERS.md) (minimal application changes), [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [ADR-0012](adrs/ADR-0012-client-bootstrap-configuration.md) | MVP |

---

## 17. Future-scope functional requirements (not built in the MVP)

[`SCOPE.md`](../10-charter/SCOPE.md) states the product architecture **should permit, but not initially require**, the capabilities below. Every requirement in this section carries `Priority = Future`: the architecture must not preclude them, but none may be scheduled, resourced, or silently built during Phases 1–2 ([AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery)). This section maps one-to-one onto the Future capabilities already cataloged in [`CAPABILITIES.md`](../20-product/CAPABILITIES.md#later-phase-capabilities--committed-and-estimated-phases-35) (`CAP-018`–`CAP-034`); see that document for fuller descriptions and [`../10-charter/SCOPE.md` §4](../10-charter/SCOPE.md#4-later-phase-scope--committed-and-estimated-phases-35) for the scope boundary these requirements sit inside.

Each future requirement also names the guardrail categor(y/ies) of [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) it would serve — classification only ([AGR-021](../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model)); a `—` means the requirement is deployment- or platform-scoped rather than guardrail-category-scoped.

| ID | Requirement | Rationale / Source | Guardrail category | Priority |
|---|---|---|---|---|
| FR-078 | The architecture must permit adding a `retrieval` evaluation stage that inspects retrieved documents before they are incorporated into a prompt, without redesigning the Evaluation entity. | [`SCOPE.md`](../10-charter/SCOPE.md), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model); CAP-018; [OQ-005](../00-source-of-truth/OPEN-QUESTIONS.md#oq-005--retrieval-content-evaluation-and-indirect-prompt-injection-detection-timeline) | GR-005 | Future |
| FR-079 | The architecture must permit adding an indirect-prompt-injection detector as a new evaluator behind the existing adapter pattern. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-019; [OQ-005](../00-source-of-truth/OPEN-QUESTIONS.md#oq-005--retrieval-content-evaluation-and-indirect-prompt-injection-detection-timeline) | GR-001, GR-005 | Future |
| FR-080 | The architecture must permit adding streaming output evaluation without changing the evaluation contract; until it ships, streaming workloads must be evaluated only on buffered, non-streamed content, or run in observe/log-only mode with the limitation documented (see FR-011). | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-020; [OQ-004](../00-source-of-truth/OPEN-QUESTIONS.md#oq-004--streaming-output-evaluation-timeline) | — (stage mechanics, cross-category) | Future |
| FR-081 | The architecture must permit adding a `tool_call` evaluation stage that inspects tool name and arguments before an agent executes a tool call, without requiring the contract to be redesigned. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-021; [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization); [OQ-006](../00-source-of-truth/OPEN-QUESTIONS.md#oq-006--tool-call-validation-tool-result-evaluation-and-mcp-authorization-scope) | GR-009 | Future |
| FR-082 | The architecture must permit adding MCP-specific authorization controls scoped to the platform's own tool-call evaluation, while remaining clearly distinguished from a general-purpose MCP security platform, which stays out of scope ([`SCOPE.md` §5](../10-charter/SCOPE.md#5-out-of-scope-for-the-solution)). | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-022; [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization); [OQ-006](../00-source-of-truth/OPEN-QUESTIONS.md#oq-006--tool-call-validation-tool-result-evaluation-and-mcp-authorization-scope) | GR-009 | Future |
| FR-083 | The architecture must permit adding a `memory_write` evaluation stage that inspects content before it is persisted to agent or assistant memory, using the same Evaluation/Decision entities as `input`/`output`. | [`SCOPE.md`](../10-charter/SCOPE.md), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model); CAP-023 | GR-011 | Future |
| FR-084 | The architecture must permit adding a groundedness evaluator (assessing whether a response is factually grounded in supplied or retrieved context) as a new evaluator, not a contract change. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-024 | GR-006 | Future |
| FR-085 | The architecture must permit adding automated-reasoning-based evaluation approaches as a new evaluator, not a contract change. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-025 | GR-006 | Future |
| FR-086 | The architecture must permit adding multilingual policy packs; until they ship, non-English workload owners must be informed explicitly during onboarding that MVP evaluators are assumed English-effective. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-026; [OQ-008](../00-source-of-truth/OPEN-QUESTIONS.md#oq-008--multilingual-policy-pack-timeline) | GR-001, GR-002 | Future |
| FR-087 | The decision schema must reserve a `require_approval` action (see FR-028); the architecture must permit adding a human-approval workflow and UI around that action without further schema change. | [`SCOPE.md`](../10-charter/SCOPE.md), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model); CAP-027; [OQ-007](../00-source-of-truth/OPEN-QUESTIONS.md#oq-007--human-approval-workflows-introduction-timing-and-ownership) | GR-013 | Future |
| FR-088 | The architecture must permit adding cryptographic policy signing that layers onto the existing versioned-policy model without replacing it. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-028; [OQ-012](../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design) | GR-015, GR-016 | Future |
| FR-089 | The architecture must permit adding structured policy inheritance or composition between policies, gated on a governance model that contains the policy-fragmentation risk ([`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md)). | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-029; [OQ-012](../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design) | GR-015 | Future |
| FR-090 | The architecture must permit adding a customer-specific evaluator plugin mechanism behind the standard evaluator-adapter contract, gated on a defined security and compatibility review model. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-030; [OQ-013](../00-source-of-truth/OPEN-QUESTIONS.md#oq-013--customer-specific-evaluator-plugin-governance) | GR-018, GR-016 | Future |
| FR-091 | The architecture must permit adding policy-marketplace discovery and distribution capabilities across organizations or tenants. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-031; [OQ-011](../00-source-of-truth/OPEN-QUESTIONS.md#oq-011--commercial-metering-and-policy-marketplace) | GR-015 | Future |
| FR-092 | The architecture must permit adding per-tenant dedicated hosted runtime instances, as opposed to a shared multi-tenant hosted runtime. | [`SCOPE.md`](../10-charter/SCOPE.md); CAP-032; [OQ-010](../00-source-of-truth/OPEN-QUESTIONS.md#oq-010--dedicated-tenant-runtimes-and-air-gapped-deployment-timeline) | — (deployment-scoped) | Future |
| FR-093 | The architecture must permit adding a fully air-gapped installation and policy-distribution model with no network connectivity to the hosted control plane at all, beyond the MVP's asynchronous, cache-tolerant Customer-Managed Runtime behavior. | [`SCOPE.md`](../10-charter/SCOPE.md), [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes); CAP-033; [AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection); [OQ-010](../00-source-of-truth/OPEN-QUESTIONS.md#oq-010--dedicated-tenant-runtimes-and-air-gapped-deployment-timeline) | — (deployment-scoped) | Future |
| FR-094 | The architecture must permit adding usage-based commercial metering suitable for customer-facing billing of evaluation volume, distinct from the internal cost/rate telemetry needed for Phase 3 quota and rate-limit enforcement. | [`SCOPE.md`](../10-charter/SCOPE.md), [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) (Phase 3); CAP-034; [OQ-011](../00-source-of-truth/OPEN-QUESTIONS.md#oq-011--commercial-metering-and-policy-marketplace) | GR-012 (metering-adjacent) | Future |

---

## 18. Traceability summary

| FR range | Functional area | Primary capability IDs | Primary agreements / constraints |
|---|---|---|---|
| FR-001–FR-006 | Policy definition and versioning | CAP-001 | AGR-005, AGR-006, AGR-017 |
| FR-007–FR-011 | Tenant and workload registration | CAP-002 | AGR-017, AGR-019, CON-011 |
| FR-012–FR-017 | Policy-to-workload binding | CAP-003 | AGR-011, AGR-017 |
| FR-018–FR-024 | Evaluation stages: input and output | CAP-004, CAP-005 | AGR-006, CON-015, CON-016; stage scope per 10-charter/SCOPE.md |
| FR-025–FR-030 | Decision normalization | CAP-012 | AGR-004, AGR-006, AGR-017 |
| FR-031–FR-038 | LiteLLM pre-call and post-call connectors | CAP-006, CAP-007 | AGR-003, AGR-007, AGR-012 |
| FR-039–FR-042 | Generic HTTP Evaluation API | CAP-008 | AGR-003, AGR-006, CON-010 |
| FR-043–FR-050 | Evaluator adapters (Bedrock, deterministic, PII) | CAP-009, CAP-010, CAP-011 | AGR-004, AGR-005, AGR-014 |
| FR-051–FR-056 | Observe and enforce modes | CAP-013 | AGR-011; 60-delivery/VALIDATION-PLAN.md |
| FR-057–FR-063 | Decision-metadata audit | CAP-015 | AGR-012, AGR-014, AGR-016, CON-013, CON-014 |
| FR-064–FR-070 | Evaluation latency and performance metrics | CAP-014 | — (no numeric target set; TBD) |
| FR-071–FR-077 | Policy promotion and rollback | CAP-001, CAP-003 | AGR-011; 60-delivery/VALIDATION-PLAN.md |
| FR-078–FR-094 | Future-scope functional requirements | CAP-018–CAP-034 | AGR-020, AGR-021; 10-charter/SCOPE.md |
| FR-095–FR-100 | Integration manifest generation | CAP-038 | AGR-002, AGR-006, AGR-018, AGR-019, CON-010, CON-011, CON-016; ADR-0006 |
| FR-101–FR-102 | Policy schema validation and capability-based evaluator selection | CAP-001 | AGR-005, AGR-012, AGR-017, CON-004; ADR-0009 |
| FR-103–FR-105 | Audit coverage — rejections, client-synthesized decisions, record origin | CAP-015 | AGR-012, AGR-014, AGR-018, CON-012; ADR-0014 |
| FR-106–FR-109 | Policy distribution and caching | CAP-001, CAP-017 | AGR-011, AGR-012, AGR-015; ADR-0011 |
| FR-110–FR-112 | Runtime capability manifest and bind-time compatibility | CAP-003 | AGR-004, AGR-005, AGR-012, CON-004; ADR-0013 |
| FR-113–FR-118 | Connector identity, installation verification, and liveness | CAP-039 | AGR-003, AGR-011, AGR-018, AGR-019, CON-012; ADR-0007 |
| FR-119–FR-127 | Client distribution, configuration, and conformance | CAP-040, CAP-041 | AGR-003, AGR-006, AGR-007, AGR-012, CON-010, CON-016; ADR-0008, ADR-0012 |

Guardrail-category traceability (GR-001–GR-018) for the future-scope requirements is carried in §13's `Guardrail category` column; the authoritative category definitions live in [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md).

**Requirement count summary**

| Category | Count |
|---|---|
| MVP requirements (`FR-001`–`FR-077`, minus FR-028; plus `FR-095`–`FR-127`) | 109 |
| Future requirements (`FR-028`, `FR-078`–`FR-094`) | 18 |
| **Total** | **127** |

---

## 19. Related documents

- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — authoritative agreements this requirements register operationalizes.
- [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — non-negotiable boundaries that bound how MVP requirements may be implemented.
- [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) — tracks unresolved timing/design decisions referenced by the Future-scope requirements in [Section 17](#17-future-scope-functional-requirements-not-built-in-the-mvp).
- [`../00-source-of-truth/DECISIONS.md`](../00-source-of-truth/DECISIONS.md) — decision log recording why the MVP was bounded as it is.
- [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) — the scope boundary (in-scope, future, out-of-scope, non-goals) this requirements register must not exceed.
- [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) — guiding principles these requirements operationalize.
- [`../20-product/CAPABILITIES.md`](../20-product/CAPABILITIES.md) — capability-level catalog; every `FR-` here implements one or more `CAP-` entries there.
- [`COMPONENT-ARCHITECTURE.md`](COMPONENT-ARCHITECTURE.md) — the component design (Policy model, Workload registry, Policy binding, Evaluation API, Policy resolver, Decision engine, Evaluator adapters) that satisfies Sections 1–3, 5, 8, and 9 of this document.
- [`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) — the connector and generic-SDK design that satisfies Sections 4, 6, and 7 of this document.
- [`DATA-ARCHITECTURE.md`](DATA-ARCHITECTURE.md) — the storage and retention design that satisfies Section 10 of this document.
- [`SECURITY-ARCHITECTURE.md`](SECURITY-ARCHITECTURE.md) — the authentication, authorization, and evaluator-plugin governance design referenced in Sections 7, 8, and 13.
- [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) — the hosted and customer-managed deployment design referenced in Sections 10 and 13.
- [`OPERABILITY.md`](OPERABILITY.md) — the availability, timeout, and performance-metric design that satisfies Section 11 of this document.
- [`../60-delivery/RELEASE-CRITERIA.md`](../60-delivery/RELEASE-CRITERIA.md) — gates MVP release on every `MVP`-priority requirement in this document, and on none of the `Future`-priority ones.
- [`../00-source-of-truth/TRACEABILITY-MATRIX.md`](../00-source-of-truth/TRACEABILITY-MATRIX.md) — cross-references each `FR-` ID to its implementing architecture section and test artifact.
