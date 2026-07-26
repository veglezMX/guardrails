**Title: ADR-0014 — Audit Coverage: Rejected Requests and Client-Synthesized Decisions**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

# ADR-0014: Audit Coverage — Rejected Requests and Client-Synthesized Decisions

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Security and governance lead, Technical lead
**Related agreements:** [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default), [AGR-016](../../00-source-of-truth/AGREEMENTS.md), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)
**Related ADRs:** [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) (the rejections this covers), [ADR-0012](./ADR-0012-client-bootstrap-configuration.md) (the client behavior that synthesizes decisions)

While this ADR reads `Proposed` it binds no one.

## Context

[`OPERABILITY.md` §](../OPERABILITY.md) and the decision-metadata requirements ([FR-057–FR-063](../REQUIREMENTS.md)) establish that every evaluation produces an audit record, including evaluations that took a failure path. That covers evaluations the runtime **completed**.

Two classes of event change what a workload was actually subject to and currently produce no record at all:

- **Requests the runtime declined to evaluate.** [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) §6 defines rejections — oversized content, an unrecognized stage, an unrecognized content kind, an authentication or authorization failure. Content was submitted and **not evaluated**. Treated as a transport error, it leaves no trace that the workload sent something the platform declined to look at. Under the cooperative posture ([AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)) that is indistinguishable from bypass.
- **Decisions a client produced on its own.** When the runtime is unreachable or an evaluation times out, the client applies its configured failure behavior ([ADR-0012](./ADR-0012-client-bootstrap-configuration.md)) and produces a decision locally. Nothing reaches the runtime, so nothing reaches the audit sink. **A fail-open synthesized allow is precisely the event most needing a record**, and it is the one event guaranteed not to have one. It is also unbounded: an outage produces one unaudited decision per request for its whole duration.

The audit design's own reasoning is the argument here — an evaluation that produced no audit record is indistinguishable from one that never happened. That reasoning does not stop at the runtime boundary.

The question this ADR resolves: **which events must produce an audit record, beyond evaluations the runtime completed?**

## Decision

### 1. Four classes of event produce an audit record

| Event | Produced by | Record |
| --- | --- | --- |
| Completed evaluation, any action | Runtime | As today |
| Failure-path substitute decision | Runtime | As today, recording which failure mode fired |
| **Request rejected before evaluation** | Runtime | Marked as a rejection: the reason, no findings, no action |
| **Client-synthesized decision** | Client | Marked as client-origin — see §3 |

The audit record gains two dimensions: **origin** (runtime or client) and **outcome** (evaluated or rejected). Every existing record is a runtime-origin, evaluated record, so the addition is additive to the record's meaning as well as its shape.

### 2. A rejected request has no policy, and the record says so

A rejection may occur before a policy was resolved — a malformed body cannot be attributed to a binding. The record therefore carries policy and action fields **only when resolution got far enough to know them**, and a record marked `evaluated` missing them is invalid rather than a variant.

The distinction being preserved: an evaluated record with no action would be a defect; a rejected record with no action is the accurate description of what happened.

Rejection records carry the reason and correlation identifier, never the content that failed validation — a body that failed validation has not been assessed for what it contains, and retaining it would breach [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default) on the least justifiable basis available.

### 3. Clients record synthesized decisions locally and deliver them when they can

A client that produces a decision without reaching the runtime records it locally and delivers it to the platform's audit path once the runtime is reachable again.

Three properties, each chosen against a specific failure:

- **Bounded, and lossy visibly.** The local store has a size ceiling. On overflow it discards oldest records and **reports the number discarded**. Unbounded growth inside a consuming team's process is not an acceptable failure mode for a distributed library — and neither is discarding silently, which would understate the outage in exactly the direction that flatters the platform.
- **Never any content.** The store holds reason and correlation only. It lives on infrastructure this platform does not operate, where [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default)'s default cannot be enforced or audited.
- **Best-effort, and described as such.** Records may be lost if the client process dies. This is accepted and must be stated rather than implied, in operator documentation and in any compliance discussion.

### 4. A client-origin record is never presented as equivalent to a runtime record

Client-origin records are self-reported by a cooperative component. They are **operationally valuable and not evidentiary**: they say what a client reports having done, not what the platform observed.

The distinction must survive into every downstream consumer — metrics, reports, compliance discussions. Aggregating the two into a single "decisions" figure would let a client's self-report inherit the credibility of a platform observation, which is precisely the confusion [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) and [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) exist to prevent.

### 5. Connector-health telemetry remains the aggregate signal

Nothing here replaces connector-health telemetry ([`INTEGRATION-ARCHITECTURE.md` §4.5](../INTEGRATION-ARCHITECTURE.md)). Telemetry reports aggregate health; §3 adds per-decision detail for the specific window in which a client was deciding alone. They answer different questions and both are needed.

## Scope boundaries

This ADR does **not** decide:

- **Audit retention periods or storage**, governed by [AGR-016](../../00-source-of-truth/AGREEMENTS.md) and the tenant's retention configuration.
- **Numeric ceilings** for the client-side store or delivery intervals — deployment and client configuration.
- **Whether audit records are immutable or tamper-evident.** A separate concern, adjacent to [OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design)'s integrity work.
- **What a client does about the failure itself** — see [ADR-0012](./ADR-0012-client-bootstrap-configuration.md).
- **Liveness of a workload that stops evaluating entirely** — see [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md) §5, a different signal for a different absence.

## Alternatives considered

- **Alternative: rejections are transport errors and need no audit record.**
  **Why not chosen:** Content was submitted and not evaluated. That is a coverage gap in the workload's protection, and treating it as a client-side problem means the platform holds no evidence that it declined to assess something. Under cooperative posture, the platform's records are the only account of what it did and did not evaluate.

- **Alternative: clients do not record synthesized decisions; connector-health telemetry is sufficient.**
  **Why not chosen:** Telemetry is aggregate and time-bucketed. It can report that a client had a bad ten minutes; it cannot answer "which requests were allowed without evaluation, and can they be correlated with what those requests were." That question is asked after incidents, and aggregate metrics cannot answer it.

- **Alternative: clients buffer synthesized records without a ceiling, to guarantee none are lost.**
  **Why not chosen:** An unbounded buffer in someone else's process is a memory or disk exhaustion risk this platform would be introducing into a consuming application. A bounded store that reports its losses is honest; an unbounded one that takes down a consuming service during a long outage is not.

- **Alternative: merge client-origin records into the main audit stream without distinguishing them.**
  **Why not chosen:** Grants self-reported data the standing of observed data. Every downstream metric and compliance statement would then rest on an unverifiable claim, without anyone having decided that.

- **Alternative (chosen): rejections and client-synthesized decisions both produce records; origin and outcome are first-class; the client store is bounded, content-free, visibly lossy; client records are never presented as equivalent.**
  **Why accepted:** Extends the audit design's own principle to the two places it currently stops, without overstating what a client's self-report proves, and without introducing an unbounded resource into a consuming team's process.

## Consequences

### Positive

- A declined request stops being indistinguishable from a request that was never made.
- Fail-open windows — the highest-risk periods the platform has — gain per-decision evidence instead of only aggregate health.
- The audit record's origin and outcome dimensions make "what did the platform actually observe" answerable, which is the question every incident review starts with.
- The self-reported/observed distinction is decided once, here, rather than in each downstream metric.

### Negative

- The audit record grows two dimensions, and every consumer must handle records with absent policy and action fields.
- Clients gain a local store — state in a library that previously had none, and an operational surface consuming teams must understand.
- Client-origin records arrive late and out of order relative to the runtime stream, so any time-ordered view must tolerate backfill.

### Risks

- **Client-origin records are aggregated into headline decision counts**, quietly converting self-reports into platform observations. The most likely misuse; needs a [`CLAIMS-REGISTER.md`](../../70-governance/CLAIMS-REGISTER.md) entry, not prose here.
- **The client store is implemented but never delivered**, because the delivery path is the least-exercised code in the client. It must be a conformance case, not an implementation detail.
- **Discarded-record counts are collected and not surfaced**, making the store silently lossy — the exact failure §3 was written to prevent.

## Validation required

This ADR is validated when:

1. A request rejected for oversized content produces an audit record marked as a rejection, carrying the reason and correlation identifier and no content.
2. A record marked as evaluated but missing its policy or action is rejected as invalid.
3. A client that produced decisions during a simulated runtime outage delivers records for them once the runtime returns, correlated to the original requests.
4. Overflowing the client store produces a reported discard count rather than silent loss.
5. Runtime-origin and client-origin records are distinguishable in every consumer, and no report aggregates them without stating that it does.

## Related agreements

- [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — client-synthesized decisions are the visible result of configured failure behavior.
- [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default) — no content in rejection records or the client store.
- [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) — §4's self-reported/observed distinction.

## Related documents

- [ADR-0005 — Evaluation contract wire format](./ADR-0005-evaluation-contract-wire-format.md) — §6, the rejections this covers
- [ADR-0012 — Client bootstrap configuration](./ADR-0012-client-bootstrap-configuration.md)
- [ADR-0007 — Connector identity, verification, and liveness](./ADR-0007-connector-identity-verification-and-liveness.md)
- [Operability](../OPERABILITY.md), [Integration Architecture](../INTEGRATION-ARCHITECTURE.md) — §4.5
