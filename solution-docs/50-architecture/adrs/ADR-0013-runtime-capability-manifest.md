**Title: ADR-0013 — Runtime Capability Manifest and Bind-Time Compatibility**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

# ADR-0013: Runtime Capability Manifest and Bind-Time Compatibility

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Technical lead, Architecture lead
**Related agreements:** [AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)
**Related ADRs:** [ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) (policies declare required capabilities), [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md) (the connector half of the same check)

While this ADR reads `Proposed` it binds no one.

## Context

[`DATA-ARCHITECTURE.md` §1](../DATA-ARCHITECTURE.md) gives the Policy binding an attribute — *"Runtime compatibility requirements: whether the runtime executing the workload's evaluations supports the bound policy version"* — and [FR-016](../REQUIREMENTS.md) requires the platform to validate it before a binding is activated. [`OPERABILITY.md` §](../OPERABILITY.md) lists compatibility validation as an operator-facing control.

**Nothing states what a runtime's capabilities are, or how the control plane learns them.** The attribute exists and nothing populates or reads it.

This matters because [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) and [ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) §3 deliberately decouple a policy from any specific evaluator implementation: a policy names a detection family and capability, and each deployment maps that to whatever implementation serves it. That decoupling is the portability mechanism — and it creates a failure mode with it.

**Two deployments can serve the same family with different implementations, and one can serve it with none at all.** A policy bound to a runtime lacking one of its required capabilities produces different decisions in different modes, which is precisely the outcome AGR-005 exists to prevent. Worse, the failure is quiet: the missing evaluator surfaces as a detector-unavailable condition at request time, handled by whatever degraded behavior was configured — so a workload can run for weeks with a guardrail its policy declares and its runtime never had.

The question this ADR resolves: **how does the platform know what a runtime can evaluate, and how does it prevent binding a policy the runtime cannot satisfy?**

## Decision

### 1. Every runtime publishes a capability manifest

Each runtime deployment publishes the set of detection capabilities it can serve — the same family-and-capability vocabulary policies select against ([ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) §3).

The manifest states **capabilities, never implementations.** A runtime declares that it can evaluate a given family and capability; it does not disclose which engine does so. That keeps the vendor boundary intact ([AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [CON-004](../../00-source-of-truth/CONSTRAINTS.md)) at the one interface where it would be most tempting to leak — and it means a deployment can change implementations without changing what it advertises, which is the portability property being protected.

The manifest is **derived from the runtime's actual configuration**, not authored separately. A hand-maintained list would drift from what the runtime can really do, and a compatibility check against a drifted list is worse than no check, because it reports confidence it does not have.

### 2. A runtime refuses to start if its configuration is unsatisfiable

If a runtime is configured to serve a capability by an implementation that is unavailable, misconfigured, or unknown, it **fails to start** rather than starting with a silently reduced manifest.

A container that boots successfully while missing a detector is the same class of failure as a policy with undocumented fallback behavior: something is quietly not happening, and nothing says so. Failing at startup makes it a deployment error, which is where it is cheapest to find.

### 3. Binding creation validates the policy's requirements against the target runtime's manifest

Before a binding is created, the control plane checks every capability the policy version requires against the manifest of the runtime that will serve that workload. **An unsatisfiable binding is refused, with a message naming the missing capabilities.**

This converts a production-time degradation into a bind-time error. The difference is not cosmetic: a bind-time refusal is seen by the operator making the change, at the moment they are making it, with the context to fix it. A detector-unavailable condition at request time is seen by nobody in particular, weeks later, in a metric.

This is the **runtime half** of the two-phase compatibility validation [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md) §1 describes. The connector half validates what the client can enforce; this half validates what the runtime can detect. Both are required, and FR-016 is satisfied by neither alone.

### 4. Capability changes are reported, and existing bindings are re-checked

Removing a capability from a running deployment does not retroactively refuse the bindings that depend on it — a running deployment cannot un-create a binding. It **reports the affected bindings** so an operator sees which workloads are now degraded and can act.

Without this, capability removal is a one-way silent degradation: the bind-time check passed once, and nothing revisits it. The check must be repeatable against current state, not a one-time gate.

### 5. Capability shortfall at request time remains a configured failure mode

If, despite §2 and §3, an evaluation encounters an unavailable evaluator, that is one of the failure modes [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) requires every policy to define behavior for, and it is handled by that behavior.

The mechanisms above are **prevention**, not replacement. Bind-time refusal is primary; the configured failure behavior remains the backstop for the cases prevention misses — a race, a partial outage, a skipped check.

## Scope boundaries

This ADR does **not** decide:

- **The capability vocabulary itself.** Governed by the guardrail-category catalog and its extension process ([AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model), [ADR-0002](./ADR-0002-guardrail-category-catalog-and-extension-model.md)), not invented here.
- **Which evaluator implementations exist**, or how a deployment is configured to serve a family. That is deployment configuration; this ADR fixes only that the resulting capabilities are published and checkable.
- **Third-party or customer-supplied evaluators** — [OQ-013](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-013--customer-specific-evaluator-plugin-governance).
- **Connector-side compatibility** — see [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md).
- **Whether a policy may require capabilities no deployment yet serves.** It may; the binding is simply refused until one does.

## Alternatives considered

- **Alternative: no manifest — let a missing evaluator surface as a request-time failure.**
  **Why not chosen:** This is the current state. It makes a configuration error look like an availability event, routes it through degraded behavior that may permit traffic, and leaves the workload owner believing a guardrail is active that never was. It also silently breaks [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability): the same policy genuinely does produce different decisions in the two deployments.

- **Alternative: the manifest lists implementations rather than capabilities.**
  **Why not chosen:** Leaks vendor identity across the one interface the control plane and every deployment both read, violating [CON-004](../../00-source-of-truth/CONSTRAINTS.md), and couples bindings to implementations so that swapping an engine invalidates bindings that were never about that engine.

- **Alternative: hand-maintain the capability list as deployment metadata.**
  **Why not chosen:** Drifts from reality, and a compatibility check against a drifted list asserts a guarantee it cannot make — worse than no check, because it is trusted.

- **Alternative: block startup *and* hard-fail existing bindings when a capability is removed.**
  **Why not chosen:** Converts a capability change into an outage for every dependent workload. §4's report-and-surface keeps the operator informed without making a configuration change a production incident.

- **Alternative (chosen): a derived capability manifest, startup failure on unsatisfiable configuration, bind-time refusal of unsatisfiable bindings, re-checking on capability change, and configured failure behavior as the backstop.**
  **Why accepted:** Activates an existing but unused binding attribute, makes FR-016's runtime half implementable, and converts a quiet production degradation into a loud deployment or bind-time error — without leaking vendor identity or making capability changes into outages.

## Consequences

### Positive

- [`DATA-ARCHITECTURE.md` §1](../DATA-ARCHITECTURE.md)'s "Runtime compatibility requirements" attribute stops being decorative.
- [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) gains a mechanism: a policy cannot be bound where it would behave differently for lack of a detector.
- A missing evaluator becomes a startup or bind-time error with a clear message, rather than weeks of quiet degradation.
- The integration manifest's "required runtime capabilities" field ([ADR-0006](./ADR-0006-integration-manifest-format-and-contents.md) §3) has a source.

### Negative

- Binding creation now depends on knowing which runtime will serve a workload, which is straightforward in the hosted modes and requires the customer-managed runtime to report its manifest in Mode C.
- Capability changes require an operator workflow to review affected bindings, not just a configuration edit.
- Startup-fails-closed means a misconfigured deployment does not start — correct, but it makes evaluator configuration errors deployment-blocking.

### Risks

- **The manifest is treated as a security boundary.** It is not; it describes what a deployment can detect, not what a caller is entitled to. It must not be used for authorization.
- **Capability granularity is too coarse**, so two deployments both advertising a capability produce materially different results within it — satisfying the check while breaching AGR-005 in substance. Mitigated by the catalog governing the vocabulary, but coarseness is a standing pressure as implementations diverge.
- **Mode C manifests go stale** if a customer-managed runtime's reporting lapses, leaving bind-time checks validating against an old picture.

## Validation required

This ADR is validated when:

1. A runtime configured to serve a capability by an unavailable implementation fails to start, with a message naming the capability.
2. A published capability manifest contains no implementation, vendor, or engine identifiers.
3. Creating a binding whose policy requires a capability the target runtime does not serve is refused, naming the missing capability, and no integration manifest is generated.
4. Removing a capability from a running deployment produces a report identifying every binding that depended on it.
5. An evaluator that becomes unavailable at request time is handled by the workload's configured failure behavior, unchanged by any of the above.

## Related agreements

- [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) — the property this protects.
- [AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [CON-004](../../00-source-of-truth/CONSTRAINTS.md) — §1's capabilities-not-implementations rule.
- [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — §5, the backstop.
- [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) — the binding attribute this activates.

## Related documents

- [ADR-0007 — Connector identity, installation verification, and liveness](./ADR-0007-connector-identity-verification-and-liveness.md) — the connector half of the same check
- [ADR-0009 — Policy document schema and mandatory failure behavior](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md)
- [Data Architecture](../DATA-ARCHITECTURE.md) — §1, the binding attribute
- [Extensibility Architecture](../EXTENSIBILITY-ARCHITECTURE.md) — the evaluator seam
