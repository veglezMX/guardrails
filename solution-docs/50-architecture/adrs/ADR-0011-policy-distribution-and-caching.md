**Title: ADR-0011 — Policy Distribution and Caching**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** seed.md + AGREEMENTS.md

---

# ADR-0011: Policy Distribution and Caching

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Architecture lead, Technical lead
**Related agreements:** [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)
**Related ADRs:** [ADR-0010](./ADR-0010-policy-storage-split.md) (the artifact distributed), [ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) (its immutability)

While this ADR reads `Proposed` it binds no one.

## Context

[`CONTAINER-ARCHITECTURE.md` §7.1](../CONTAINER-ARCHITECTURE.md) specifies policy distribution as one-way, asynchronous, and cached — then states plainly: *"The specific transport (push vs. pull, polling interval, or event-driven sync) is an implementation decision not specified in `seed.md` and is **TBD** at the component-design level."*

That TBD is load-bearing in four places:

- **[AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)** requires a customer-managed runtime to keep evaluating through a control-plane outage. Whether that is possible depends entirely on the distribution mechanism.
- **"Immediate rollback"** (`seed.md` §15) has a propagation delay determined by this choice, and nobody has stated what "immediate" means once a cache exists.
- **Policy-cache hit rate** is a tracked performance metric (`seed.md` §15) for a cache whose refresh behavior is undefined.
- **[`TECHNICAL-RISKS.md` R7](../TECHNICAL-RISKS.md)** — degraded-mode correctness — describes a stale cached policy as *"a correctness gap, not a visible failure."* Its severity is a direct function of this decision.

The question this ADR resolves: **how does published policy state reach a runtime, and what happens when it cannot?**

## Decision

### 1. Runtimes pull; the control plane does not push

Every runtime — hosted and customer-managed — **pulls** policy state from the control plane on a recurring interval. The control plane never initiates a connection to a runtime.

This is chosen primarily for Mode C. A push mechanism requires the control plane to reach into a customer's environment, which means inbound connectivity, endpoint registration, and a network path the customer must open. Pull inverts that: the customer's runtime makes an outbound call on its own schedule, through its own egress controls, and a customer-managed runtime behind a restrictive network boundary works the same way the hosted one does.

One mechanism serving all three deployment modes is also what keeps Phase 4 a packaging exercise rather than a redesign (`seed.md` §16).

### 2. Resolution reads local state only — never the control plane

Evaluation resolves policy and binding state from the runtime's **local cache only**. No evaluation makes a synchronous control-plane call, in any deployment mode.

Not just for Mode C. If the hosted runtime called the control plane per evaluation, the control plane would become a hot-path dependency whose availability bounded the evaluation path's — and the customer-managed path would then be structurally different from the hosted one, which is what [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) and the Phase 4 packaging claim both forbid.

Refresh runs independently of request handling. A refresh in progress, or a refresh failing, never blocks an evaluation.

### 3. A refresh failure is not an evaluation failure

When a runtime cannot refresh, it **continues evaluating against its last successfully retrieved state**. Control-plane unavailability is one of the failure modes [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) requires every policy to define behavior for, and it is handled by that configured behavior — not by an exception reaching the caller and not by an implicit fallback.

Because the cached artifact is immutable ([ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) §6), a cached policy version is never *wrong* for its version — it is only potentially *superseded*. That distinction is what makes continuing to evaluate defensible rather than reckless.

### 4. Staleness is measured, bounded, and visible

Every runtime tracks how long it has been since its last successful refresh and reports it. Two consequences follow:

- **Staleness is an operational signal**, surfaced to operators rather than inferred from a silent cache-hit metric. [R7](../TECHNICAL-RISKS.md)'s central complaint — that a stale-cache decision is *"a correctness gap, not a visible failure"* — is answered by making it visible.
- **Every decision records the policy version that produced it.** An operator reviewing a degraded window can determine exactly which version was applied, rather than inferring it from timestamps.

A deployment carries a configured maximum tolerable staleness. Exceeding it is reported and routed through the workload's configured control-plane-unavailable behavior — it does not silently continue as though nothing changed.

### 5. Rollback urgency is bounded by the refresh interval, and this is stated

"Immediate rollback" (`seed.md` §15) means **immediate at the control plane**: the new binding takes effect there at once, and every runtime converges within its refresh interval. It does not mean instantaneous global effect, and no product, sales, or operator material may imply otherwise.

The refresh interval is therefore a **safety parameter, not a performance tuning knob** — it sets the worst-case propagation delay for a rollback made in response to an incident. It is deployment configuration with an explicit value, not a default buried in code.

## Scope boundaries

This ADR does **not** decide:

- **Numeric refresh intervals or staleness ceilings.** No source-defined figure exists; these are deployment configuration, set per environment and reviewed during pilot operation.
- **Where policy state is stored** — see [ADR-0010](./ADR-0010-policy-storage-split.md).
- **Policy signing or artifact authenticity** — [OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design).
- **How a client learns its own configuration** — a separate distribution path with different constraints; see [ADR-0012](./ADR-0012-client-bootstrap-configuration.md).
- **Air-gapped operation**, where no refresh path exists at all ([OQ-010](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-010--dedicated-tenant-runtimes-and-air-gapped-deployment-timeline)).

## Alternatives considered

- **Alternative: event-driven push from the control plane to each runtime.**
  **Why not chosen:** Lower propagation latency, but requires the control plane to reach into customer environments in Mode C — inbound connectivity, endpoint registration, and reachability the customer must maintain. It also fails exactly when it is most needed: a runtime that was unreachable during a push has no mechanism to discover what it missed, so a catch-up pull is required anyway. Pull is then the primary mechanism with push as an optimisation, and the optimisation is not worth a second code path across three modes at this stage.

- **Alternative: synchronous control-plane read per evaluation, no cache.**
  **Why not chosen:** Directly violates [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), makes control-plane availability an upper bound on evaluation availability, and puts a network hop on a path already carrying evaluator latency.

- **Alternative: cache with no staleness bound — evaluate on whatever is held, indefinitely.**
  **Why not chosen:** Makes R7 permanent and invisible. A runtime disconnected for a month would evaluate against month-old policy with no signal, and the operator reviewing an incident would have no way to know.

- **Alternative: fail closed when the cache is stale beyond the bound.**
  **Why not chosen as a fixed rule:** For some workloads that is correct; for others it converts a control-plane outage into a production outage. That is precisely the judgement [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) requires each policy to state rather than the platform to assume, so §4 routes it through the configured behavior instead of hardcoding either answer.

- **Alternative (chosen): runtime-initiated pull on a configured interval, local-only resolution, continued evaluation on refresh failure per configured behavior, measured and bounded staleness, and an explicit statement that rollback converges within the interval.**
  **Why accepted:** One mechanism across all three modes; the evaluation path never depends on the control plane; and R7 changes from an invisible correctness gap to a reported operational state.

## Consequences

### Positive

- [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) is satisfied by the same mechanism the hosted deployment uses, so Phase 4 stays a packaging exercise.
- Control-plane availability no longer bounds evaluation availability in any mode.
- R7's stale-cache correctness gap becomes visible and bounded rather than silent.
- `CONTAINER-ARCHITECTURE.md` §7.1's standing TBD is closed.
- The refresh interval is named as a safety parameter, so an operator tuning it for cost sees what they are trading.

### Negative

- Rollback is eventually consistent across runtimes. The propagation window is bounded and stated, but it is real, and incident response must account for it.
- Every runtime polls whether or not anything changed — steady background load proportional to runtime count.
- Staleness tracking and reporting is operational surface that must exist from Phase 1, not added when Mode C arrives.

### Risks

- **"Immediate rollback" is described externally as instantaneous**, and an incident review discovers the propagation window at the worst moment. Mitigated by §5 and by an entry in [`CLAIMS-REGISTER.md`](../../70-governance/CLAIMS-REGISTER.md) constraining the wording.
- **The refresh interval is lengthened for cost or load reasons** without recognising it as widening the rollback window. Mitigated by §5's framing, which is the whole reason that paragraph exists.
- **Staleness reporting exists but is not alerted on**, leaving the signal technically present and operationally unused.

## Validation required

This ADR is validated when:

1. Evaluation continues normally with the control plane fully unavailable, in both hosted and customer-managed deployments.
2. No evaluation path makes a synchronous control-plane call, verified by observing traffic during a load run.
3. A binding rollback at the control plane is observed to take effect at a runtime within the configured refresh interval.
4. A runtime exceeding its configured staleness ceiling reports it, and the workload's configured control-plane-unavailable behavior is the behavior actually observed.
5. Every decision produced during a degraded window records the policy version that produced it.

## Related agreements

- [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) — the constraint that selects pull over push.
- [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — refresh failure is a configured failure mode, not an implicit fallback.
- [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) — promotion and rollback both converge on the refresh interval.

## Related documents

- [Container Architecture](../CONTAINER-ARCHITECTURE.md) — §7.1, the TBD this closes
- [Technical Risks](../TECHNICAL-RISKS.md) — R7 degraded-mode correctness
- [ADR-0010 — Policy storage split](./ADR-0010-policy-storage-split.md)
- [ADR-0012 — Client bootstrap configuration](./ADR-0012-client-bootstrap-configuration.md)
