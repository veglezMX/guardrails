**AI Guardrails — Differentiators**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

## Purpose and scope

This document states what makes AI Guardrails distinct from a single embedded gateway feature, a single-vendor moderation add-on, or a point detection model. It is written for product, sales-engineering, and architecture audiences who need a precise, non-overclaiming description of the platform's differentiation.

This is an **L4** document: a derived product artifact. It must not contradict [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both L1), and every wording choice below is written to align with the restricted phrasing that [`CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) will enforce. Where this document and the claims register appear to diverge, the claims register governs external-facing wording.

Each differentiator below is stated as: what it is, why it is not trivial to replicate informally, and what it deliberately does **not** claim.

---

## Summary

| # | Differentiator | One-line framing |
|---|---|---|
| 1 | [Model- and gateway-independent contract](#1-model--and-gateway-independent-contract) | Policies are defined once and do not depend on a specific model provider or gateway product. |
| 2 | [Policy portability across three deployment modes](#2-policy-portability-across-three-deployment-modes) | The same policy, evaluation contract, and decision semantics hold across Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime. |
| 3 | [Operational ownership separated from data exposure](#3-operational-ownership-separated-from-data-exposure) | "Who runs this component" and "where does my content go" are answered independently for every mode. |
| 4 | [Observe-before-enforce as a structural default](#4-observe-before-enforce-as-a-structural-default) | Every policy ships with a non-blocking evaluation path and a graduated promotion route, not just an on/off switch. |
| 5 | [Honest scoping of guardrails](#5-honest-scoping-of-guardrails) | The platform states plainly what it is not: not authorization, not a complete security boundary, not a full DLP or governance platform. |
| 6 | [One evaluation contract, hosted or customer-managed](#6-one-evaluation-contract-hosted-or-customer-managed) | The policy schema, request/decision structures, reason codes, and audit metadata are identical whether the runtime is hosted by the provider or run inside the customer's own AWS account. |

---

## 1. Model- and gateway-independent contract

**What it is.** The policy schema, evaluation request structure, decision structure, and reason codes do not depend on LiteLLM's configuration structures, on Amazon Bedrock Guardrails' response format, on a specific model provider, on a specific programming language, or on a specific application framework. Vendor- and gateway-specific behavior is isolated behind adapters and connectors — a Bedrock Guardrails adapter, a LiteLLM connector — so that a policy author writes intent ("detect prompt injection and block it"), not integration mechanics ("call this SDK method with this payload shape").

**Why it matters.** LiteLLM is the platform's first supported gateway connector, and Amazon Bedrock Guardrails is its first managed evaluation engine — but neither defines the boundary of the product. A team that operates a different gateway, or that wants to invoke the Evaluation API directly, is not shut out of the policy model. This is what allows the same organizational policy to be reused across gateways and providers that the platform has not yet integrated.

**What this does not claim.** This is not a claim that every model provider or gateway is already supported out of the box — LiteLLM and Bedrock Guardrails are the initial, validated integrations. It is a claim about where the seams are: new connectors and evaluation engines can be added without changing policy semantics, because the contract was designed independently of any one of them from the start.

**Related:** [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability); [CON-003, CON-004](../00-source-of-truth/CONSTRAINTS.md#product-contract-independence).

---

## 2. Policy portability across three deployment modes

**What it is.** AI Guardrails supports exactly three deployment modes, and a policy defined once is designed to be bindable to a workload in any of them:

```text
                     ┌─────────────────────────────┐
                     │   One policy definition      │
                     │   One evaluation contract     │
                     └──────────────┬───────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────────┐
│ Mode A         │           │ Mode B         │           │ Mode C             │
│ Remote          │           │ Guarded         │           │ Customer-Managed    │
│ Evaluation      │           │ Gateway         │           │ Runtime             │
│                 │           │                 │           │                     │
│ Team-owned      │           │ Provider-        │           │ Runtime deployed    │
│ LiteLLM calls    │           │ managed gateway │           │ inside the          │
│ the hosted       │           │ + guardrails     │           │ customer's own AWS  │
│ Evaluation API   │           │ (fully managed)  │           │ account             │
└───────────────┘           └───────────────┘           └───────────────────┘
```

A team is not forced into a single topology to get the benefit of centrally governed policy. The mode a workload uses is chosen based on the operational-ownership and data-residency needs of that workload — not on which mode the platform happens to support best.

**Why it matters.** Most guardrail or moderation add-ons are shipped as a feature of one gateway product or one hosted API. AI Guardrails treats the deployment topology itself as a first-class choice, while committing to a single logical evaluation contract underneath all three: policy definitions, request structures, decision structures, reason codes, enforcement actions, audit metadata, and policy-version semantics remain compatible regardless of where the runtime executes.

**What this does not claim.** Portability across modes does not mean feature parity is automatic or effortless — feature-parity gaps between modes must be explicitly tracked and justified, not assumed away. It is a design commitment enforced through conformance testing of the shared contract, not a claim that every mode is equally mature at every point in delivery (see [Phased delivery](#note-on-delivery-phasing) below).

**Related:** [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes); [CON-005](../00-source-of-truth/CONSTRAINTS.md#con-005--one-logical-evaluation-contract-across-all-modes).

---

## 3. Operational ownership separated from data exposure

**What it is.** For every deployment mode, the platform answers two questions independently, rather than letting one imply the other:

1. **Who operates this component?** (infrastructure, availability, credentials, upgrades)
2. **Where does my content go?** (which accounts and Regions process prompts and responses, whether content is persisted, what telemetry leaves the runtime)

| Mode | Who operates the inference path | Where content is processed |
|---|---|---|
| A — Remote Evaluation | The consuming team operates its own gateway; the provider operates only the Evaluation API. | Prompts, responses, and any additional evaluated content are transmitted to the hosted Evaluation API. **This is not a private-data or data-local model.** |
| B — Guarded Gateway | The provider operates the gateway and the guardrail runtime end to end. | Content passes through the provider-managed gateway as part of the complete inference path. |
| C — Customer-Managed Runtime | The consuming team operates the runtime inside its own AWS account (with optional managed support). | Raw content stays inside the customer's account by default; only customer-selected telemetry categories — or none — reach the hosted control plane. |

**Why it matters.** These two questions are frequently conflated in practice: a team may assume that a hosted, provider-run service implies the provider now holds their data (not necessarily true in ways that matter for compliance framing), or conversely that "we still run the gateway" means content never left their environment (not true in Mode A, where content is transmitted to the hosted Evaluation API precisely because that is the mode's function). AI Guardrails treats this as a documentation and design requirement, not an afterthought: every deployment's data-flow story is stated explicitly, and reduced operational ownership is never used to imply reduced data exposure, or vice versa.

**What this does not claim.** Mode A does not reduce how much tenant content reaches the hosted guardrail service — its benefit is reduced operational ownership of the inference path, full stop. Teams with data-residency or non-transmission requirements are directed to Mode C, not told that Mode A already satisfies them.

**Related:** [AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), [AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model); [CON-007, CON-013, CON-014](../00-source-of-truth/CONSTRAINTS.md#content-handling-and-data-exposure).

---

## 4. Observe-before-enforce as a structural default

**What it is.** Every policy supports a graduated state progression rather than a binary on/off switch:

```text
Disabled → Observe → Warn → Redact → Block
```

A workload's binding defaults to Observe, not Block. A team can measure false positives, latency, and business impact against real traffic before any policy is allowed to affect end users. Promotion between states is an explicit, auditable action — never an implicit side effect of another change.

**Why it matters.** A policy that can only be turned fully on or fully off forces teams to either accept unvalidated risk of breaking production traffic, or delay adoption indefinitely out of caution. Building the graduated path into the policy schema and runtime — rather than leaving it to each team to build their own shadow-mode tooling — is what makes "evolve from cooperative integration to mandatory enforcement" a supported product path rather than an aspiration.

**What this does not claim.** Observe mode does not detect or prevent anything by itself; it produces evaluation results for review. The value is in the measured, auditable transition to enforcement it enables, not in observation being a substitute for enforcement.

**Related:** [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression).

---

## 5. Honest scoping of guardrails

**What it is.** The platform explicitly states what guardrail evaluation is not, in three specific directions:

* **Guardrails are not authorization.** A guardrail decision that a tool call or piece of content "appears safe" does not determine whether the current user is permitted to execute that action against a given resource. Authentication, business authorization, tenant isolation, resource ownership, transaction limits, and approval workflows remain the responsibility of the consuming application. Tool and agent authorization is a distinct, later-phase product capability (Phase 5 of the delivery plan) — not part of the Phase 1–2 core platform and not available today.
* **A cooperative connector is not a hard security boundary.** The trust and enforcement model distinguishes cooperative integration (a team installs and maintains its own connector, which can be disabled, removed, or misconfigured) from platform-managed enforcement and from a network/IAM-enforced gateway (where bypass is structurally prevented). A cooperative connector must never be represented as bypass-proof.
* **Guardrails are not a complete AI security boundary or governance platform.** The solution's feature set explicitly excludes a general-purpose LLM gateway replacement, a complete AI governance platform, a proprietary prompt-injection or moderation model, a complete DLP platform, end-user authentication, application business authorization, fully autonomous agent governance, and a general MCP security platform (seed.md §6.3).

**Why it matters.** The single largest risk named in the solution charter is false confidence — teams interpreting a successful guardrail evaluation as complete AI security. Stating the scope boundary explicitly, in product materials and not only in internal risk logs, is itself part of the product's value: it tells adopting teams exactly which risks the platform addresses and which remain theirs to own.

**What this does not claim.** This is not a claim of limited usefulness — it is a claim of precise usefulness. Content evaluation, policy enforcement, and audit evidence are what the platform delivers; user authentication, authorization, and complete security posture are deliberately left to the consuming application and its existing security controls.

**Related:** [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model); [CON-012](../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies).

---

## 6. One evaluation contract, hosted or customer-managed

**What it is.** The same policy definitions, request structures, decision structures, reason codes, enforcement actions, audit metadata, and policy-version semantics apply whether the runtime executing them is the provider-hosted service (Modes A and B) or a runtime deployed inside the customer's own AWS account (Mode C). The customer-managed runtime is additionally capable of executing policies without requiring a synchronous connection to the hosted control plane — it maintains a local policy cache and synchronizes asynchronously, so policy evaluation continues through a control-plane outage.

**Why it matters.** A common pattern in this space is to offer a hosted product and a materially different "on-premises" or "private" variant with its own contract, its own decision format, and its own operational model. That pattern reintroduces exactly the coupling this platform is built to avoid: a policy written for the hosted service would need to be rewritten for the customer-managed one. AI Guardrails instead treats "where does the runtime execute" as a deployment decision layered on top of one contract, validated by conformance tests that check hosted and customer-managed runtimes identically.

**What this does not claim.** This does not claim that hosted and customer-managed runtimes are operationally identical — ownership of infrastructure, availability, upgrades, and telemetry configuration differs by design (see [Differentiator 3](#3-operational-ownership-separated-from-data-exposure)). The claim is narrower and more concrete: the *evaluation contract* — what a policy looks like, what a decision looks like, what a reason code means — does not change based on where the runtime happens to run.

**Related:** [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), [AGR-016](../00-source-of-truth/AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime); [CON-005, CON-013](../00-source-of-truth/CONSTRAINTS.md#product-contract-independence).

---

## Note on delivery phasing

These differentiators describe the target product architecture and the agreements that govern it, not a claim that every mode and connector is generally available today. Delivery proceeds through five phases — integration proof, internal multi-team platform, guarded gateway, customer-managed runtime, and advanced agent protection — with each phase's deliverables validated before the next is committed. Statements about "three deployment modes" or "one evaluation contract" describe the platform's architectural commitment across the full delivery arc, not a same-day feature-parity guarantee across all phases.

---

## What these differentiators are not

To avoid overclaiming, this document deliberately does not assert:

* That AI Guardrails eliminates the need for authorization, authentication, or existing application security controls (see [Differentiator 5](#5-honest-scoping-of-guardrails)).
* That Remote Evaluation (Mode A) keeps content local — it does not (see [Differentiator 3](#3-operational-ownership-separated-from-data-exposure)).
* That a cooperative connector prevents bypass — it does not, absent platform-managed or network/IAM-enforced controls.
* That the platform has a proprietary detection model, a complete DLP platform, or full compliance certification — none of these are part of the solution's feature set (seed.md §6.3).
* Any specific performance figure, adoption number, or SLA — none are established in the source material at this stage; where a figure is needed, it is marked TBD pending measurement.

---

## Related documents

- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — authoritative agreements this document derives from.
- [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — checkable boundary conditions underlying these differentiators.
- [`../00-source-of-truth/DECISIONS.md`](../00-source-of-truth/DECISIONS.md) — decision log recording when and why these positions were adopted.
- [`CAPABILITIES.md`](CAPABILITIES.md) — the corresponding in-scope capability list.
- [`PRODUCT-OVERVIEW.md`](PRODUCT-OVERVIEW.md) — the broader product narrative this document supports.
- [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) — the restricted external-facing wording that governs how these differentiators may be stated in sales and executive materials.
- [`../50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) — architectural detail behind the three deployment modes.
