**Title: ADR-0015 — Runtime Deployment Target**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** seed.md + AGREEMENTS.md

---

# ADR-0015: Runtime Deployment Target

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Architecture lead, Platform operations lead
**Related agreements:** [AGR-002](../../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)
**Related ADRs:** [ADR-0010](./ADR-0010-policy-storage-split.md) (the stores this compute reads)

While this ADR reads `Proposed` it binds no one.

## Context

[`CONTAINER-ARCHITECTURE.md` §](../CONTAINER-ARCHITECTURE.md) describes the runtime's and control plane's compute as *"ECS Fargate or EKS"* and their entry point as *"API Gateway or an Application Load Balancer."* [`INVESTMENT-SUMMARY.md` §](../../30-executive/INVESTMENT-SUMMARY.md) carries the same pairs as cost line items.

Every downstream document therefore names two options and picks neither. That is appropriate for a cost estimate and insufficient for delivery: the choice determines the packaging path, and the packaging path is what the Phase 1–2 validation demonstration's final criterion depends on — *"the runtime can later be packaged for customer-managed deployment without changing the connector contract"* (`seed.md` §16).

The question this ADR resolves: **what does the runtime deploy onto, and what does that imply for Mode C?**

## Decision

### 1. The runtime and control plane deploy as containers on ECS Fargate, behind an Application Load Balancer

Within the existing two-account model ([AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model)), with IAM-based authentication ([CON-010](../../00-source-of-truth/CONSTRAINTS.md#con-010--iam-based-authentication-and-least-privilege-roles)), TLS in transit ([CON-008](../../00-source-of-truth/CONSTRAINTS.md)), and KMS encryption at rest ([CON-009](../../00-source-of-truth/CONSTRAINTS.md#con-009--kms-encryption-in-transit-and-at-rest)).

Regional deployment follows [DEC-018](../../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga): regional instances inside the same two accounts, not an account per Region.

### 2. Fargate over EKS, because the operational cost is not yet justified

Both were named. Fargate is chosen because the platform's Phase 1–2 workload is two long-running services with straightforward scaling needs, and EKS adds a cluster to operate, upgrade, and secure for capability this platform does not yet use. If a later phase needs what EKS provides, migrating a containerised service is a deployment change rather than a rewrite — which is exactly the property that makes deferring the decision safe.

### 3. Container-based compute, because Phase 4 ships the same artifact into customer accounts

This is the decisive argument, and it is the reason a serverless function target was not chosen despite being a reasonable fit for a request/response API.

[`DEPLOYMENT-ARCHITECTURE.md` §9](../DEPLOYMENT-ARCHITECTURE.md) already specifies the Mode C artifact as a container image built from source shared with the hosted runtime, published to a provider-controlled registry, pulled and run by the customer. [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) requires it to run in environments this team does not operate.

**One packaging path serves the hosted deployment, Mode C, and later Mode B.** A function-based hosted deployment would mean maintaining a second packaging path for the same code, and the two would drift — which is how "packaged for customer-managed deployment **without changing the connector contract**" quietly becomes false. Choosing containers everywhere makes Phase 4 the packaging exercise [`IMPLEMENTATION-STRATEGY.md` §](../../60-delivery/IMPLEMENTATION-STRATEGY.md) already claims it is.

[`CONTAINER-ARCHITECTURE.md` §](../CONTAINER-ARCHITECTURE.md) also specifies Fargate or EKS for the Phase 3 Guarded Gateway. Choosing containers here keeps all three modes on one runtime deployment story.

### 4. ALB over API Gateway at this stage

The Evaluation API is called by IAM-authenticated clients on a small, stable surface. The features API Gateway adds beyond load balancing — usage plans, per-key throttling, request transformation — map to capabilities that are Phase 2 or later (quotas) or explicitly not wanted (transformation, which would put a request-shaping layer in front of a contract [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) defines precisely).

This is the weakest-held decision in this record. If Phase 2 quota work or a future external-facing posture makes API Gateway's features load-bearing, revisiting it changes an entry point, not an architecture.

### 5. This decision does not change any account boundary

No new AWS account, and [OQ-009](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-009--trigger-point-for-three-account-aws-separation)'s triggers for additional separation are untouched.

## Scope boundaries

This ADR does **not** decide:

- **Scaling parameters, task sizing, or cost targets.** Operational configuration, informed by [VAL-001](../../60-delivery/VALIDATION-PLAN.md)'s latency measurements, which do not yet exist.
- **The customer's deployment target in Mode C.** The customer receives a container image and runs it under their own orchestration; [`DEPLOYMENT-ARCHITECTURE.md` §9](../DEPLOYMENT-ARCHITECTURE.md) lists the supporting artifacts.
- **The Phase 3 Guarded Gateway's own topology**, beyond noting it shares this compute story.
- **Network exposure and private connectivity** — [OQ-002](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-002--privatelink--private-connectivity), resolved by [DEC-017](../../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture).

## Alternatives considered

- **Alternative: serverless functions for the Evaluation API.**
  **Why not chosen:** A good fit for a synchronous request/response API in isolation, and rejected on the Mode C argument alone: Phase 4 ships this runtime into customer accounts as a container. A function-based hosted path plus a container-based customer path is two packaging paths for one codebase, and their drift is precisely what would falsify the `seed.md` §16 criterion. Cold-start behavior on a latency-sensitive synchronous path is a secondary concern, unquantified until [VAL-001](../../60-delivery/VALIDATION-PLAN.md) produces figures.

- **Alternative: EKS.**
  **Why not chosen:** Adds cluster operations, upgrade cycles, and security surface for capability the Phase 1–2 workload does not use. Some customers will run the Mode C container on their own Kubernetes — which the container image supports regardless of what the hosted side runs on.

- **Alternative: defer and let deployment tooling settle it.**
  **Why not chosen:** The choice determines whether one packaging path or two exist, and that determines whether a load-bearing validation criterion is satisfiable. Too consequential to be settled by whoever writes the first deployment configuration.

- **Alternative (chosen): containers on ECS Fargate behind an ALB, within the existing two-account model.**
  **Why accepted:** One packaging path across all three modes; no cluster to operate before there is a reason; and the Phase 4 claim stays structurally true rather than requiring discipline to keep true.

## Consequences

### Positive

- One container artifact serves the hosted deployment, Mode C, and later Mode B — making Phase 4 a packaging exercise as [`IMPLEMENTATION-STRATEGY.md` §](../../60-delivery/IMPLEMENTATION-STRATEGY.md) already claims.
- `CONTAINER-ARCHITECTURE.md`'s and `INVESTMENT-SUMMARY.md`'s standing "or" is resolved, so cost modelling and deployment work reference one target.
- No new operational surface: no cluster, no second packaging path.
- Regional deployment fits [DEC-018](../../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga) without account changes.

### Negative

- Always-on tasks cost more than per-invocation compute at low volume — a real cost during pilot, accepted for packaging coherence.
- Container image lifecycle — building, scanning, publishing, patching — is an obligation from Phase 1.
- Choosing ALB defers quota and throttling capability to the application layer or to a later revisit.

### Risks

- **Cost pressure at low pilot volume prompts a move to functions for the hosted path only**, reintroducing the two-packaging-path problem this decision exists to avoid. The Mode C argument, not the cost argument, is what should govern any revisit.
- **The ALB choice is treated as settled** when §4 records it as the weakest-held part of this ADR; Phase 2 quota work should revisit it deliberately.

## Validation required

This ADR is validated when:

1. The same container image runs as the hosted runtime and as a customer-managed runtime, differing only in configuration.
2. A client configured against a customer-managed runtime differs from one configured against the hosted runtime **only by endpoint** — the Phase 1–2 demonstration's packaging criterion.
3. Regional deployment adds no AWS account.
4. No component requires a second packaging path.

## Related agreements

- [AGR-002](../../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes) — one compute story across three modes.
- [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) — the runtime must run where this team does not operate.
- [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) — account model unchanged.
- [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) — one artifact, so the contract cannot fork by deployment target.

## Related documents

- [Container Architecture](../CONTAINER-ARCHITECTURE.md) — the "Fargate or EKS" this resolves
- [Deployment Architecture](../DEPLOYMENT-ARCHITECTURE.md) — §9, Mode C artifacts
- [Implementation Strategy](../../60-delivery/IMPLEMENTATION-STRATEGY.md) — the build-once-package-per-mode claim
- [Investment Summary](../../30-executive/INVESTMENT-SUMMARY.md) — the cost line items this fixes
