**AI Guardrails — Serving Model**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md + DEC-010 + DEC-011

---

## About this document

This document describes how AI Guardrails is **served to, and consumed by**, the teams that adopt it: what the product is at its core, which deployment mode is the default front door, how operational responsibility splits between the provider and the consumer, the shape of the integration contract, and the governance model that lets a security team pin controls an application team may strengthen but never weaken.

It is a **derived, L4** view. It must not contradict [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md), both L1-authoritative, or an `Accepted` entry in [`DECISIONS.md`](../00-source-of-truth/DECISIONS.md), which is also L1. **If a conflict is ever found between this document and an L1 register, the L1 register wins and this document must be corrected — never the reverse.**

Two of the decisions this document is derived from — [`DECISIONS.md` DEC-010](../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door) (serving model / front door) and [`DECISIONS.md` DEC-011](../00-source-of-truth/DECISIONS.md#dec-011--policy-governance-classes-and-mandatory-policy-authority) (policy governance classes) — currently carry **Status: Proposed** in the decision log, not yet `Accepted`. This document describes that proposed shape; if either decision is revised or rejected at ratification, this document must be corrected to match.

This document does not restate the three deployment modes from first principles — see [`20-product/PRODUCT-OVERVIEW.md`](PRODUCT-OVERVIEW.md) §4 for that, and [`50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) for the full per-mode ownership breakdown. This document instead answers a narrower question those documents leave open: of the three modes, which one is the default path a new consumer is guided through, and what exactly does adopting it commit each side to.

---

## Table of contents

- [1. Product identity](#1-product-identity)
- [2. The front door: Mode A as a developer service](#2-the-front-door-mode-a-as-a-developer-service)
- [3. The managed / self-serve split](#3-the-managed--self-serve-split)
- [4. The integration contract](#4-the-integration-contract)
- [5. Policy governance classes](#5-policy-governance-classes)
- [6. Graduation paths: Mode B and Mode C](#6-graduation-paths-mode-b-and-mode-c)
- [Related documents](#related-documents)

---

## 1. Product identity

AI Guardrails is a **hosted policy and evaluation service that attaches to a consumer's existing LLM gateway.** The consumer keeps their gateway, their model provider, and their data path; the platform hosts policy storage, evaluation, and the audited decision.

"Attach to any gateway" is the delivered direction: LiteLLM is the first supported connector, but the evaluation contract and policy model are gateway-independent by design ([`AGREEMENTS.md` AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)). **"Attach to any agent runtime" is stated only as directional vision** (Phase 5, `Vision — uncommitted` in the guardrail catalog) — it is not a delivered capability and must not be described as one in any product or sales material. See [`20-product/PRODUCT-ROADMAP.md`](PRODUCT-ROADMAP.md) and [`70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) for what may and may not be claimed today.

*([`DECISIONS.md` DEC-010](../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door); [`AGREEMENTS.md` AGR-001](../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm))*

---

## 2. The front door: Mode A as a developer service

Of the [three deployment modes](PRODUCT-OVERVIEW.md#4-the-three-operating-models) ([`AGREEMENTS.md` AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes)), **Mode A (Remote Evaluation) is the default adoption path.** A new consumer is guided to Mode A first; Modes B and C are graduation paths that answer specific objections (see §6), not alternatives competing for the same first conversation.

Mode A is offered as a **developer service**: a team keeps its own gateway and installs a cooperative connector, or calls the hosted HTTP Evaluation API directly, to import a policy onto a workload. This is the path that proves the platform's core thesis — that one reusable policy applies consistently across independently operated gateways — without requiring the provider to first stand up a managed gateway (Mode B) or package a customer-hosted runtime (Mode C). It is also the smallest operational, security, and compliance footprint for the provider among the three modes.

*([`DECISIONS.md` DEC-010](../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door); [`AGREEMENTS.md` AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery))*

> **Reminder carried forward from [`PRODUCT-OVERVIEW.md`](PRODUCT-OVERVIEW.md) §4:** Mode A must never be described as a private-data or data-local deployment model. Prompts, responses, and any other evaluated content reach the hosted Evaluation API regardless of who operates the gateway. Teams with data-residency requirements are routed to Mode C, not told Mode A already satisfies them. ([`AGREEMENTS.md` AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model))

---

## 3. The managed / self-serve split

Mode A is neither "the consumer builds everything" nor "the provider runs everything." It is a defined split, and naming that split is the expectation this section sets.

| Provider operates (managed) | Consumer does (self-serve) |
|---|---|
| Hosted Evaluation API — the product surface itself | Install a connector, or call the SDK/API directly, at pre-call and post-call |
| Policy store, versioning, and rollback | Register their workload; import/select a policy |
| Workload and policy-binding registry | Configure the failure behavior they choose for their workload |
| Normalized decision and audit metadata | Run in observe mode, review results, and promote to enforce when ready |
| Control plane and dashboards | Keep their own gateway, model credentials, and request path |

**In short: the provider manages the policy brain; the consumer owns the request-path plumbing.** Neither side inherits responsibility for what the other retains.

*([`DECISIONS.md` DEC-010](../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door); [`AGREEMENTS.md` AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import); [`50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) §2)*

---

## 4. The integration contract

The **HTTP Evaluation API is the real product surface** — one logical evaluation contract shared across all three deployment modes ([`AGREEMENTS.md` AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)). Everything else a consumer touches rides over it:

* An **official LiteLLM connector** — the paved path for the gateway AI Guardrails already supports.
* **Thin Python and TypeScript SDKs** — for teams calling the Evaluation API directly rather than through a supported gateway connector.
* The **raw HTTP API stays public** for any stack that has neither, so no consumer is locked out of the platform for lacking a supported connector.

**Parity rule:** an SDK or connector must never add evaluation semantics the underlying API does not itself expose. This preserves policy portability across every integration path ([`AGREEMENTS.md` AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability)) — a policy behaves identically whether it is reached through the connector, an SDK, or a raw API call.

*([`DECISIONS.md` DEC-010](../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door); [`AGREEMENTS.md` AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes))*

---

## 5. Policy governance classes

The provider ships **curated policy packs** — starter policies mapped to the guardrail catalog — and consumers author their own policies on top, in the same portable format. Every policy in play, curated or consumer-authored, carries one of three **governance classes**:

| Class | Consumer may | Consumer may not |
|---|---|---|
| **Mandatory (pinned)** | Strengthen — promote Observe → Block earlier, tighten thresholds, add stages | Disable, unbind, select a version below the pinned floor, or weaken below the minimum required enforcement |
| **Recommended / overridable** | Adopt as-is, override thresholds, extend, or skip | — |
| **Own (consumer-authored)** | Anything expressible in the portable policy format | Contradict a mandatory policy layered above it |

**Effective policy = mandatory base (locked) ⊕ consumer overrides and additions.** This layering is the first, MVP-simple form of policy inheritance.

**Authority to mark a policy Mandatory rests with the security/governance role** — never the application or workload team. Application teams may only strengthen a mandatory policy; they may never weaken it. This separation of authority — one role sets the floor, another may only raise it — is the platform's core value to a security buyer, and mirrors the ownership split already recorded for security and compliance teams. ([`20-product/TARGET-USERS.md`](TARGET-USERS.md) §4)

**Honesty caveat.** In cooperative Mode A, "mandatory" is a **governance guarantee, not a physical one.** The platform records the binding as required and refuses to attest compliance when it is absent — it does not, and cannot, physically prevent an application from running without it while running in cooperative posture. A cooperatively installed connector can always be disabled, removed, or misconfigured by whoever operates the gateway ([`AGREEMENTS.md` AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)). Bypass-proof mandatory enforcement requires graduating to Mode B or a network/IAM-enforced posture — mandatory-policy *strength* climbs the same posture ladder as enforcement bypass-resistance does generally. This caveat must be stated alongside any "mandatory" claim made to a prospect or customer; it is what keeps the claim consistent with [`AGREEMENTS.md` AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model).

*([`DECISIONS.md` DEC-011](../00-source-of-truth/DECISIONS.md#dec-011--policy-governance-classes-and-mandatory-policy-authority); [`AGREEMENTS.md` AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import))*

---

## 6. Graduation paths: Mode B and Mode C

Modes B and C exist to answer specific buyer objections, not to compete with Mode A for the front door. A consumer is routed off Mode A only when Mode A genuinely cannot satisfy the objection raised:

| Objection | Path |
|---|---|
| "We have no gateway." | **Mode B — Guarded Gateway.** A fully managed, OpenAI-compatible inference + guardrail path for teams without their own gateway. |
| "Content can't leave our account." | **Mode C — Customer-Managed Runtime.** The guardrail runtime deploys inside the consumer's own AWS account, keeping raw content local by default. |
| "Mandatory controls must be bypass-proof." | **Mode B, or a network/IAM-enforced posture** — never a Mode A promise (see the honesty caveat in §5). |

Neither graduation path is a delivered capability at the Mode A launch; they are named here so that a Mode A conversation can point to a credible next step rather than over-building Mode A to cover a case it was never meant to.

*([`DECISIONS.md` DEC-010](../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door); [`AGREEMENTS.md` AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model), [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery))*

---

## Related documents

- [`20-product/PRODUCT-OVERVIEW.md`](PRODUCT-OVERVIEW.md) — the three deployment modes this serving model builds on
- [`00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — authoritative agreement register (L1)
- [`00-source-of-truth/DECISIONS.md`](../00-source-of-truth/DECISIONS.md) — decision log, including DEC-010 and DEC-011
- [`20-product/TARGET-USERS.md`](TARGET-USERS.md) — the four stakeholder groups, including the security/governance authority described in §5 above
- [`50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) — full per-mode ownership and topology detail
- [`50-architecture/INTEGRATION-ARCHITECTURE.md`](../50-architecture/INTEGRATION-ARCHITECTURE.md) — the evaluation contract and how connectors invoke it
- [`50-architecture/SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md) — the trust and enforcement posture ladder referenced in §5–§6
- [`70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) — approved and rejected external claims, including the agent-runtime and data-locality caveats above
