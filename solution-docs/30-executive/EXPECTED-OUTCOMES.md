**AI Guardrails — Expected Outcomes**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

> This is a derived (L4) document. It introduces no new commitments and must not contradict the L1-authoritative [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md), or the L2 operationalization in [`10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md). Where a conflict is found, those documents win and this one must be corrected. Numeric targets not present in the source material are recorded as **TBD**, never estimated.

---

## Table of contents

<!-- BEGIN GENERATED: toc -->
- [1. Purpose of this document](#1-purpose-of-this-document)
- [2. Definitions: output, outcome, metric, business effect](#2-definitions-output-outcome-metric-business-effect)
- [3. Logic model](#3-logic-model)
- [4. The Phase 1–2 validation demonstration as the first proof point](#4-the-phase-12-validation-demonstration-as-the-first-proof-point)
- [5. Expected outcomes by cluster](#5-expected-outcomes-by-cluster)
- [6. On numeric targets](#6-on-numeric-targets)
- [7. What this document does not claim](#7-what-this-document-does-not-claim)
- [8. Related documents](#8-related-documents)
<!-- END GENERATED: toc -->
---

## 1. Purpose of this document

Several other documents in this set describe *what will be built* (the [charter](../10-charter/CHARTER.md), [product roadmap](../20-product/PRODUCT-ROADMAP.md)) and *how success will be measured* (the [success criteria](../10-charter/SUCCESS-CRITERIA.md)). This document exists to answer a narrower question that those documents leave implicit: **for each thing the platform delivers, what actually changes, how would we know, and why does it matter to the organization?**

It does so by separating four categories that are easy to conflate in executive communication:

* **Output** — a concrete thing the platform delivers (a component, capability, or artifact).
* **Outcome** — a change in behavior, capability, or state that the output makes possible.
* **Metric** — an observable, trackable indicator that the outcome is (or is not) occurring.
* **Business effect** — the organizational value that follows if the metric moves in the intended direction.

Every row in Section 5 is grounded in the success metrics enumerated in [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) and in the Phase 1–2 validation demonstration defined in [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) and [`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md). No metric, target, or business effect below is invented beyond what `AGREEMENTS.md` and `CONSTRAINTS.md` support; where a causal or quantitative claim is not present in the source material, it is stated qualitatively or marked **TBD** rather than estimated.

---

## 2. Definitions: output, outcome, metric, business effect

| Type | Definition | Test for the correct category |
|---|---|---|
| **Output** | A component, capability, or artifact the platform delivers. It exists once built and deployed, independent of whether anyone uses it well. | "Did we ship it?" |
| **Outcome** | A change in how teams, applications, or the organization actually operate as a result of the output existing. | "Did behavior change because the output exists?" |
| **Metric** | A number or rate the platform can observe to tell whether the outcome is occurring, and to what degree. | "How would we know, quantitatively?" |
| **Business effect** | The organizational consequence if the metric moves in the intended direction — expressed qualitatively where the L1 registers supply no figure. | "Why does leadership care?" |

An output can be delivered without producing its intended outcome (for example, a connector can be built but never installed). Distinguishing the four categories prevents "we shipped the Evaluation API" from being reported as if it were equivalent to "teams now apply consistent controls" — the latter requires adoption evidence, not just delivery.

---

## 3. Logic model

```mermaid
flowchart LR
    O["Output\nWhat is built and shipped"] --> C["Outcome\nWhat changes in how teams operate"]
    C --> M["Metric\nHow the change is observed"]
    M --> E["Business effect\nWhy it matters to the organization"]
```

This chain runs left to right in each row of Section 5: a delivered output enables an outcome; the outcome is made observable by one or more metrics; movement in those metrics, if favorable, produces the stated business effect. None of the outputs listed below are asserted as already delivered — this document records the intended causal chain from the MVP components ([`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md)) and delivery phases ([AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery)) so that, once built, each can be evaluated against whether it produced its claimed effect.

---

## 4. The Phase 1–2 validation demonstration as the first proof point

Before any ongoing metric in [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) is meaningful, the platform must first clear the one-time, binary Phase 1–2 validation demonstration defined in [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) and [`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md): **one reusable policy applied consistently to two independent LiteLLM deployments**, with no shared model-provider credentials, consistent normalized decisions, one workload in observe mode and one in enforce mode, a policy upgrade-and-rollback cycle, disableable raw-content persistence, a configured failure policy exercised under failure, and a demonstrated path to customer-managed packaging. This demonstration is a delivery-plan milestone, not the definition of overall solution success ([DEC-008](../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases)).

The nine specific criteria (MVP-01 through MVP-09), their pass conditions, and the gating rule that all nine must hold simultaneously are defined in full in [`10-charter/SUCCESS-CRITERIA.md` §1](../10-charter/SUCCESS-CRITERIA.md#1-phase-12-validation-demonstration-criteria) and are not repeated here. This document treats that demonstration as **Cluster A** below — the foundational output/outcome pair that every other cluster's ongoing metrics build on.

---

## 5. Expected outcomes by cluster

Each cluster groups a related set of outputs, the outcome they jointly enable, the metric(s) from [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) that make that outcome observable, and the resulting business effect. Metric targets are **TBD** throughout — see [Section 6](#6-on-numeric-targets).

### Cluster A — Reusable policy across independent gateways (the reusable-policy hypothesis)

| ID | Type | Statement | Basis |
|---|---|---|---|
| EO-A1 | Output | Guardrail policy format, versioned policy storage, workload registry, and policy-to-workload binding, deployed and bound identically to two organizationally independent LiteLLM instances. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) MVP components 1–4; [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) |
| EO-A2 | Output | Central Evaluation API, Bedrock Guardrails adapter, basic deterministic evaluator, and LiteLLM pre-call/post-call connectors, deployed against both instances. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) MVP components 5, 6, 7, 8, 9 |
| EO-A3 | Outcome | Two independent teams apply the **same** versioned policy to their own LiteLLM gateways, without transferring model-provider credentials to the guardrail platform, and receive consistent normalized decisions — i.e., **consistent controls across teams and gateways** without standardizing on one gateway or provider. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md); [`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md), [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) |
| EO-A4 | Metric | Validation demonstration criteria MVP-01 through MVP-09 (binary, all-or-nothing per [`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md)). Target: pass/fail; verification method and owner **TBD** — see [`10-charter/SUCCESS-CRITERIA.md` §1.2](../10-charter/SUCCESS-CRITERIA.md#12-criteria-table). | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) |
| EO-A5 | Metric | Percentage of workloads using a shared policy. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Adoption |
| EO-A6 | Business effect | Central governance of AI safeguards does not require forcing every team onto one model provider, application framework, or LiteLLM deployment ([`CHARTER.md`](../10-charter/CHARTER.md)) — **lower engineering duplication** and lower risk of ungoverned, ad hoc safeguard implementations across teams. | [`CHARTER.md`](../10-charter/CHARTER.md), [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes) (Business impact) |

### Cluster B — Onboarding and connector adoption

| ID | Type | Statement | Basis |
|---|---|---|---|
| EO-B1 | Output | Onboarding flow implemented end to end: register workload → select policy → generate integration manifest → install connector → run in observe mode → review evaluation results → enable enforcement. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) |
| EO-B2 | Outcome | Application teams onboard to central guardrails through a simple process with minimal application changes, without transferring their LiteLLM database, model-provider credentials, or gateway administration access to the guardrail platform. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import), [`TARGET-USERS.md`](../20-product/TARGET-USERS.md) (Application teams needs); [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) |
| EO-B3 | Metric | Number of onboarded workloads. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Adoption |
| EO-B4 | Metric | Number of active tenants. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Adoption |
| EO-B5 | Metric | Time required to onboard a workload. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Adoption |
| EO-B6 | Metric | Percentage of supported LiteLLM instances using the connector. Target: **TBD**; denominator ("supported LiteLLM instances") must be operationally defined before this is reportable. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Adoption |
| EO-B7 | Business effect | Faster, lower-friction extension of governed AI safeguards to new teams and workloads, without the guardrail provider taking on gateway administration risk or credential custody. | [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import), [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) |

### Cluster C — Graduated rollout (observe before enforce)

| ID | Type | Statement | Basis |
|---|---|---|---|
| EO-C1 | Output | Observe and enforce modes, and the full Disabled → Observe → Warn → Redact → Block progression, implemented per policy per workload. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) |
| EO-C2 | Outcome | Teams measure false positives, latency, and business impact before a policy can block production traffic; enforcement posture evolves deliberately from cooperative integration toward mandatory enforcement rather than by default. | [`CHARTER.md`](../10-charter/CHARTER.md), [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) |
| EO-C3 | Metric | Percentage of policies promoted from observe to enforce. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Effectiveness |
| EO-C4 | Metric | Number of policy rollbacks caused by incorrect behavior. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Effectiveness |
| EO-C5 | Metric | Number of policy interventions; false-positive review rate. Target: **TBD** for both. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Effectiveness |
| EO-C6 | Business effect | Lower risk of guardrail-caused production incidents or broken user experiences during adoption, and increased organizational confidence to widen enforcement scope over time. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) (Latency, False confidence) |

### Cluster D — Decision evidence and auditing

| ID | Type | Statement | Basis |
|---|---|---|---|
| EO-D1 | Output | Normalized decision schema (action, allowed status, reason codes, policy name/version, evaluator metadata, transformed content, correlation identifier, audit identifier, evaluation latency) and decision-metadata auditing, implemented across both instances. | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Decision), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) (Auditing) |
| EO-D2 | Outcome | Security and compliance teams obtain policy-version traceability and decision evidence that policies are actually being applied consistently across teams, rather than assumed. | [`TARGET-USERS.md`](../20-product/TARGET-USERS.md) (Security and compliance teams needs) |
| EO-D3 | Metric | Audit delivery success rate. Target: **TBD**. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Reliability |
| EO-D4 | Metric | Confirmed harmful-input detection rate; confirmed sensitive-output prevention rate. Target: **TBD** for both; both depend on a risk-specific evaluation dataset not yet defined. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Effectiveness; §20 (False confidence) |
| EO-D5 | Business effect | **Lower compliance exposure** — evidence of consistent policy application is available to governance review on demand, rather than inferred from good faith. | [`TARGET-USERS.md`](../20-product/TARGET-USERS.md), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) |

### Cluster E — Reliability and predictable failure behavior

| ID | Type | Statement | Basis |
|---|---|---|---|
| EO-E1 | Output | Evaluation latency metrics (P50/P95/P99), timeout rate, detector-specific latency, and policy-cache hit rate instrumented; explicit, per-workload failure behavior defined for every failure mode in [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) (API timeout, detector unavailability, unresolved policy, control-plane unavailability, audit-delivery failure, unsafe transformation). | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md); [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) |
| EO-E2 | Outcome | Consuming applications experience predictable evaluation latency and a documented, agreed failure path — never an undocumented fallback — when the Evaluation API, a detector, or the control plane is unavailable. | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) |
| EO-E3 | Metric | P95 evaluation latency; evaluation timeout rate. Target: **TBD** for both. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Reliability |
| EO-E4 | Metric | Evaluation API availability; policy-resolution failure rate. Target: **TBD** for both; an explicit availability objective ([`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md)) must be set first. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Reliability |
| EO-E5 | Business effect | **Lower operational exposure** — guardrail integration does not itself become an unpredictable source of production incidents or unbounded request-path stalls. | [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md), [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) (Latency) |

### Cluster F — Deployment-mode portability (path beyond hosted)

| ID | Type | Statement | Basis |
|---|---|---|---|
| EO-F1 | Output | One logical evaluation contract implemented identically across Remote Evaluation and Guarded Gateway modes, plus a demonstrated packaging path for Customer-Managed Runtime that does not change the connector contract. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) MVP demonstration condition 9; [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) |
| EO-F2 | Outcome | Workloads with data-residency, regulatory, or high-volume requirements have a defined future path (Mode C) that reuses the same policy model, rather than requiring a separately forked product. | [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes) (Mode C) |
| EO-F3 | Metric | Percentage of policy definitions that are deployment-mode independent; percentage of runtime code shared across hosted and customer-managed modes. Target: **TBD** for both. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Product portability |
| EO-F4 | Metric | Number of supported connectors; time required to deploy a customer-managed runtime. Target: **TBD** for both; the latter is only measurable once Phase 4 deliverables exist. | [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) Product portability; §18 Phase 4 |
| EO-F5 | Business effect | Broader addressable set of regulated or data-sensitive workloads without maintaining a separate governance model or duplicating engineering investment per deployment mode — **lower compliance exposure** for the organization's most sensitive workloads specifically. | [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) (Hosted data exposure) |

---

## 6. On numeric targets

Every metric target in Section 5 is marked **TBD**, consistent with [`10-charter/SUCCESS-CRITERIA.md` §2](../10-charter/SUCCESS-CRITERIA.md#2-ongoing-success-metrics), which records the same metrics with the same TBD status and assigns target-setting to an accountable owner per metric. This document does not set, estimate, or imply any numeric target, baseline, cadence, or dollar figure beyond what `AGREEMENTS.md` and `CONSTRAINTS.md` state. Any numeric target eventually adopted must be recorded first in `SUCCESS-CRITERIA.md` (or a successor `RELEASE-CRITERIA.md`), not introduced here as a new commitment.

Per [`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) and [`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md), the Phase 1–2 validation demonstration result (Cluster A) becomes the primary early evidence cited in executive and sales materials about product portability — but only the demonstration result itself, referenced against the nine-criteria table in `SUCCESS-CRITERIA.md`, not a paraphrase or a subset. The demonstration is a delivery-plan milestone, not the register-level definition of overall solution success ([DEC-008](../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases)).

---

## 7. What this document does not claim

Consistent with the authoritative agreements this document derives from:

* **No dollar-value ROI, cost-of-incident, or engineering-hours-saved figure is asserted.** None exists in the source material (`AGREEMENTS.md`, `CONSTRAINTS.md`); see [`00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md) for what remains open.
* **Remote Evaluation (Mode A) is not described as reducing data exposure.** "Reduced operational ownership" (Cluster B/E business effects) is never conflated with "reduced content exposure" — per [AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model) and [AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), operational ownership and data exposure are always separate questions; only Mode C (Cluster F) changes the data-exposure answer.
* **No outcome in this document implies business authorization.** Guardrail decisions (Cluster D) evaluate content safety; they do not determine whether a user is authorized to take an action — per [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization).
* **No outcome implies bypass-proof enforcement in cooperative mode.** Cluster A and C outcomes hold for cooperative integration; a cooperative connector remains removable or misconfigurable and must never be represented as a hard security boundary — per [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model).
* **Phase 5 capabilities (retrieval evaluation, tool-call validation, MCP integration) do not yet have outcome rows here.** They are part of the full solution scope defined and estimated under [DEC-008](../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases), delivered last per [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery); their outcome and metric rows will be added to Section 5 when Phase 5 planning begins. Product-category exclusions in [`SCOPE.md`](../10-charter/SCOPE.md) — including a general MCP security platform and business authorization — remain out of scope entirely (see [`10-charter/SCOPE.md`](../10-charter/SCOPE.md)).
* **This document does not gate release.** The binary demonstration gate is defined in [`10-charter/SUCCESS-CRITERIA.md` §1.3](../10-charter/SUCCESS-CRITERIA.md#13-gating-rule); this document may be cited as context but must not be read as an alternative or looser gate.

---

## 8. Related documents

* [`00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — authoritative agreement register (L1); every business-effect claim above traces to an agreement listed here.
* [`00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — non-negotiable technical and security boundaries (L1) that bound how each output may be built.
* [`00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md) — unvalidated beliefs, including the two independent teams needed for the Cluster A demonstration.
* [`00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) — unresolved decisions that could change which outcomes are achievable and when.
* [`10-charter/CHARTER.md`](../10-charter/CHARTER.md) — full product charter narrative underlying every cluster in Section 5.
* [`10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) — the authoritative validation demonstration criteria (MVP-01–09) and the full ongoing-metrics tables this document's Metric rows summarize by cluster.
* [`60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md) — the Phase 1–2 validation demonstration (Section 4) as a delivery-plan milestone.
* [`10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) — the product principles (portability, one contract, observe-before-enforce, failure behavior) each cluster's outcome operationalizes.
* [`20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) — the phase sequencing that determines when each cluster's outputs become deliverable.
* [`30-executive/EXECUTIVE-BRIEF.md`](EXECUTIVE-BRIEF.md) — the executive ask this document's outcomes support; Section 5 (Expected outcomes) of that brief summarizes the demonstration-and-outcomes view expanded here.
* [`70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) — the risks (connector bypass, false confidence, latency, hosted data exposure, policy fragmentation, over-expansion) that could prevent a cluster's outcome or business effect from materializing.
* [`70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) — must reject any external claim that a business effect or metric in Section 5 has already been achieved unless recorded as met in that register or in `SUCCESS-CRITERIA.md`.
