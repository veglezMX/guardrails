**Title: ADR-0008 — Connector Distribution and Integrity**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

# ADR-0008: Connector Distribution and Integrity

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Technical lead, Security and governance lead
**Related agreements:** [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import)
**Related ADRs:** [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md) (the version this distribution channel publishes)

While this ADR reads `Proposed` it binds no one.

## Context

[`DEPLOYMENT-ARCHITECTURE.md` §9](../DEPLOYMENT-ARCHITECTURE.md) specifies the Mode C runtime container's supply chain precisely: built from source shared with the hosted runtime, published to a provider-controlled registry, pulled and run by the customer under their own IAM.

**The connector has no equivalent.** No document names a distribution channel, a publication process, a versioning scheme, or an integrity mechanism for the artifact that consuming teams actually install. [AGR-020](../../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) lists "LiteLLM connector" as a Phase 1 deliverable; nothing says how a team obtains it.

The asymmetry runs opposite to the risk. The runtime container executes in the provider's account, or in a customer account the customer controls. **The connector executes inside the consuming team's gateway process, on the request path, with access to every prompt and response that workload handles.** It is the most privileged artifact this platform distributes and the only one with no defined supply chain.

Two further pressures make this a Phase 1 rather than Phase 4 concern:

- **A consumer-operated custom gateway is a near-term client** ([AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)). That team builds against a published SDK. "Published" currently means nothing specific.
- **[`PROOF-POINTS.md`](../../40-sales-deck/PROOF-POINTS.md) already commits publicly** that a connector "must still be installed and, in cooperative mode, maintained by the consuming team." A maintenance obligation presupposes something to maintain it *from*.

The question this ADR resolves: **how does a consuming team obtain a connector, and how does it know the artifact it received is the one the platform published?**

## Decision

### 1. Connectors are published artifacts with a defined channel

Every client the platform ships — the LiteLLM connector and the generic SDK — is published to a **named artifact channel appropriate to its ecosystem**, versioned independently of the platform's release cadence, with published release notes and a changelog.

"Appropriate to its ecosystem" is deliberate: a gateway plugin and a language SDK are consumed by different tooling, and forcing one distribution shape onto both produces an artifact neither ecosystem installs naturally. What is fixed is that a channel is **named, documented, and stable** — not which channel.

The channel must be reachable by teams outside the provider's AWS account. A consuming team is not necessarily inside the provider's network boundary, and [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) forbids onboarding requiring privileged access in either direction.

### 2. Connector versioning is independent of, and distinct from, three other versions

The connector version is separate from the evaluation contract version ([ADR-0005](./ADR-0005-evaluation-contract-wire-format.md)), the policy version, and the runtime/product version. Each may move without the others.

A connector release declares **which evaluation contract major versions it speaks**. That declaration is what makes [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md)'s compatibility checks meaningful — without it, "connector version 2.3" is an opaque token with no relationship to anything the platform serves.

### 3. Integrity is verifiable at the point of installation

Every published connector artifact carries an integrity mechanism a consuming team can verify **before** the artifact executes inside its gateway.

The specific mechanism follows the ecosystem's own convention rather than being invented here; what this ADR fixes is that verification must be **possible, documented, and part of the installation instructions** rather than an option a careful team might discover.

This is distinct from, and does not depend on, policy signing — which remains later-phase scope ([OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design), [`SCOPE.md`](../../10-charter/SCOPE.md)). Policy signing protects a *configuration artifact* distributed to runtimes. Connector integrity protects *executable code* distributed to consumers. The second is more urgent and structurally simpler, and deferring it because the first is deferred would be a category error.

### 4. The connector's privilege is documented, not implied

The published documentation must state plainly what a connector does inside the consuming team's process: it observes prompt and response content, calls the Evaluation API over the network, and applies returned decisions to the request.

A consuming team is being asked to run provider code on its request path with access to its users' content. That team's security review is a legitimate step in onboarding, and it needs a factual description to review. Leaving this to be inferred makes the review slower and the answers inconsistent.

### 5. A connector never carries policy, detection logic, or credentials

Consistent with [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), a published connector contains no policy content, no detection logic, and no embedded credential of any kind — neither the platform's nor the consuming team's.

Stating this as a property of the *distributed artifact* rather than only of the runtime design makes it checkable at publication: a connector release that embedded a policy or a credential would be detectable in the artifact itself, without reference to how the code is organised internally.

## Scope boundaries

This ADR does **not** decide:

- **Which specific registry, package index, or repository is used.** That is an implementation and procurement choice; the requirement is a named, documented, externally reachable channel.
- **How long a connector version is supported.** Tracked as [OQ-022](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-022--connector-version-support-window-and-deprecation-policy).
- **How the platform learns which version is installed** — see [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md).
- **Policy signing**, which remains later-phase scope under [OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design).
- **Distribution of the Mode C runtime container**, already specified in [`DEPLOYMENT-ARCHITECTURE.md` §9](../DEPLOYMENT-ARCHITECTURE.md).

## Alternatives considered

- **Alternative: distribute the connector as source, for each team to vendor into its own build.**
  **Why not chosen:** No stable version identity, so [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md)'s compatibility validation has nothing to validate; no upgrade path, so a contract change requires contacting every team individually; and every consumer's vendored copy drifts independently, which is [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)'s divergence problem reintroduced at the distribution layer.

- **Alternative: defer connector distribution to Phase 4 alongside the Mode C packaging work.**
  **Why not chosen:** Phase 1 requires two independent LiteLLM instances to install the connector; Phase 4 is years of sequencing away. The artifact is needed before the first validation demonstration can run, and an undocumented ad-hoc channel used for the pilot becomes the de facto channel by default.

- **Alternative: publish only the SDK, and let each team write its own gateway integration.**
 **Why not chosen:** [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) names the LiteLLM connector as a Phase 1 deliverable, and [AGR-020](../../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) lists it explicitly. It also pushes enforcement mechanics into consumer code, which is precisely the divergence the connector exists to prevent.

- **Alternative (chosen): named per-ecosystem channels, independent connector versioning that declares its supported contract majors, verifiable integrity at installation, documented privilege, and a policy/credential-free artifact.**
  **Why accepted:** Gives compatibility validation something real to check, gives a consuming team's security review something factual to assess, and closes a supply-chain gap on the most privileged artifact the platform distributes — without over-specifying tooling that will differ per ecosystem anyway.

## Consequences

### Positive

- The most privileged artifact the platform distributes gains the supply-chain treatment the least privileged one already has.
- [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md)'s compatibility checks become meaningful, because a connector version now declares a relationship to the contract versions it speaks.
- A consuming team's security review has a documented subject rather than an inference exercise, removing a predictable onboarding delay.
- The custom-gateway team receives a published, versioned SDK rather than a shared branch.

### Negative

- Publication, release notes, and integrity verification are ongoing release-engineering obligations from Phase 1, not deferred work.
- Per-ecosystem channels mean more than one publication path to operate as client languages are added.
- An externally reachable channel is a public surface that must be operated and monitored.

### Risks

- **Integrity verification is documented but not performed** by consuming teams, leaving the mechanism present and unused. Mitigated by putting verification in the installation instructions as a step rather than an appendix.
- **The pilot uses an ad-hoc channel** — a shared archive, a branch — and that becomes permanent by default. This is the most likely failure and the reason this decision is Phase 1 rather than later.
- **Connector versioning drifts into lockstep with the contract version**, so a contract patch forces a connector release and the independence in §2 erodes. Mitigated by the explicit declaration of supported contract majors, which only changes when the relationship actually changes.

## Validation required

This ADR is validated when:

1. Both the LiteLLM connector and the generic SDK are installable by a team outside the provider's AWS account, following published instructions, with no privileged access in either direction.
2. A published connector release declares the evaluation contract major versions it speaks, and that declaration is what the [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md) compatibility check reads.
3. Integrity verification is documented as an installation step and demonstrably fails on a tampered artifact.
4. An inspection of a published connector artifact finds no policy content, no detection logic, and no embedded credential.
5. The Phase 1–2 validation demonstration installs the connector into both LiteLLM instances through the published channel, not by an out-of-band copy.

## Related agreements

- [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) — the connector is one client among several; distribution must serve the SDK equally.
- [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) — §5 makes this checkable at the artifact level.
- [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) — the connector is cooperative; a supply chain does not change that, and integrity verification must not be described as making enforcement mandatory.
- [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) — obtaining a connector must not require privileged access in either direction.

## Related documents

- [ADR-0007 — Connector identity, installation verification, and liveness](./ADR-0007-connector-identity-verification-and-liveness.md)
- [Deployment Architecture](../DEPLOYMENT-ARCHITECTURE.md) — §9, the runtime container supply chain this mirrors
- [Security Architecture](../SECURITY-ARCHITECTURE.md) — the onboarding-flow annotation and enforcement-posture controls
- [Integration Architecture](../INTEGRATION-ARCHITECTURE.md) — §4, connector responsibilities
