**Title: ADR-0004 — Enforcement-Ladder States and Decision Actions**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-25
**Derived from:** AGREEMENTS.md

---

# ADR-0004: Enforcement-Ladder States and Decision Actions

**Status:** Proposed
**Date:** 2026-07-25
**Owners:** Technical lead, architecture lead, security and governance lead
**Related agreements:** [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy)
**Related ADRs:** none superseded

While this ADR reads `Proposed` it binds no one; [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) and [FR-051](../REQUIREMENTS.md) remain the standing L1 authority for the five-state requirement, and [INTEGRATION-ARCHITECTURE.md §3.4](../INTEGRATION-ARCHITECTURE.md) remains the standing description of the action set.

## Context

Two accepted requirements describe overlapping vocabulary, and a derived document has already noticed half the problem.

**The ladder.** [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) requires "a defined progression from Disabled to Observe to Warn to Redact to Block," and states that "the policy schema and runtime must support all five states per policy, per workload." [FR-051](../REQUIREMENTS.md) restates this as **MVP** scope.

**The action set.** [INTEGRATION-ARCHITECTURE.md §3.4](../INTEGRATION-ARCHITECTURE.md) defines four MVP decision actions: `allow`, `block`, `redact`, `log_only`.

**The collision already partly identified.** [EXTENSIBILITY-ARCHITECTURE.md §5](../EXTENSIBILITY-ARCHITECTURE.md) rejects introducing a `warn` action, reasoning that "`Warn` is a state on the AGR-011 enforcement ladder … not a per-decision instruction. Introducing a `warn` action would create two meanings for one word across the same contract."

That reasoning is correct and this ADR adopts it. What no document has yet observed is that **`Redact` and `Block` already have exactly that problem**: each is simultaneously a ladder state and a decision action. Three behaviours are therefore undefined across the entire document set:

1. **Ladder state `Redact`, policy evaluator configured with `action: block`.** Does the state cap the action at `redact`? Downgrade `block` to `redact`? Is the combination rejected at bind time? No document says.
2. **Ladder state `Warn`.** What action does the resulting decision carry? No `warn` action exists, and EXTENSIBILITY-ARCHITECTURE forbids adding one. If the answer is `log_only`, then `Warn` and `Observe` are indistinguishable in both the decision and the audit record — leaving the ladder with four usable states rather than the five AGR-011 requires.
3. **Ladder state `Disabled`.** Is the Evaluation API called at all? If it is not, no audit record exists, and there is no evidence distinguishing "guardrails were disabled for this workload" from "the connector was removed" — which matters directly to the connector-bypass risk ([TECHNICAL-RISKS.md](../TECHNICAL-RISKS.md) R1) and to [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)'s requirement that enforcement posture be "recorded and visible, not assumed."

The question this ADR resolves: **how do the five AGR-011 ladder states map onto the four decision actions, without introducing a second meaning for any word in the contract and without relaxing AGR-011?**

Per the [ADR authority model](./README.md), relaxing AGR-011 is not available to this ADR. The five states are fixed; only their expression is open.

## Decision

**A ladder state is a ceiling on the emitted action, held on the policy binding, and reported to the caller on the decision.**

### 1. The ladder state lives on the binding, and only there

The five states are an attribute of the `Policy binding` entity ([DATA-ARCHITECTURE.md §1.5](../DATA-ARCHITECTURE.md)), never of the `Policy` and never of an evaluator configuration. This is what AGR-011's "per policy, per workload" requires and what [DATA-ARCHITECTURE.md §1.4](../DATA-ARCHITECTURE.md) already implies by giving `Policy` no enforcement-mode attribute. It is also what preserves the core product hypothesis — one policy version bound to many workloads at different maturity levels — that the Phase 1–2 validation demonstration tests.

The binding attribute is named `enforcement_state` and takes exactly one of: `disabled`, `observe`, `warn`, `redact`, `block`.

### 2. The state caps the action; it never selects one

The decision engine (RT-3) computes the action the policy calls for from the findings and thresholds, then **clamps** that action to the ceiling the binding's state permits. Clamping is monotonic along the action-severity order `allow < log_only < redact < block`.

| `enforcement_state` | Evaluators run | Ceiling | Emitted action when the policy calls for `block` | Emitted action when the policy calls for `redact` |
| --- | --- | --- | --- | --- |
| `disabled` | **No** | `allow` | `allow` | `allow` |
| `observe` | Yes | `log_only` | `log_only` | `log_only` |
| `warn` | Yes | `log_only` | `log_only` | `log_only` |
| `redact` | Yes | `redact` | `redact` | `redact` |
| `block` | Yes | `block` | `block` | `redact` |

A policy that calls for a *less* severe action than the ceiling permits is not escalated — the ceiling is an upper bound, never a floor. A binding in state `block` whose policy calls for `redact` emits `redact`.

### 3. `Warn` is distinguished from `Observe` by a decision field, not by a fifth action

`enforcement_state` becomes an additive field on the `Decision` entity, carrying the state that produced this decision.

This is what makes the two log-only states distinguishable without a `warn` action:

* **`observe`** — the caller proceeds silently. The decision is recorded for measurement; nothing is surfaced to the end user.
* **`warn`** — the caller proceeds, and the client surfaces the decision's reason codes to the caller or the operator. *How* it surfaces them is client-specific (a response header, a log line, an annotation on the response) and is deliberately not fixed by the contract, because the LiteLLM connector, the SDK, and a custom gateway each have different affordances.

This field is on the `Decision` and not only in the audit record because **a client cannot implement `warn` without it at request time**. That is the test this ADR applies for widening the contract, and it deliberately distinguishes this field from `would_be_action` (§5 below), whose audience is a reviewer reading audit records after the fact. [TECHNICAL-RISKS.md](../TECHNICAL-RISKS.md) R8 warns that every contract field must be maintained compatibly across all three deployment modes indefinitely; `enforcement_state` earns that cost, `would_be_action` does not.

### 4. `Disabled` evaluates nothing but still produces a decision and an audit record

A binding in state `disabled` is resolved normally by RT-2; RT-4 is then **skipped entirely** — no evaluator is invoked and no evaluation cost is incurred — and RT-3 emits `action: allow`, `enforcement_state: disabled`, with empty `reason_codes`. RT-5 audits it like any other evaluation.

The client is not asked to skip the call, because the client does not know the binding state until it calls. The consequence is that a disabled workload remains **provably** disabled rather than merely silent: the audit trail distinguishes "evaluation ran and was disabled by policy binding" from "no evaluation occurred," which is precisely the distinction AGR-018 requires enforcement posture to make.

### 5. Clamping changes the action only — never the findings or the reason codes

Reason codes, `guardrail_category` ([DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension) / [ADR-0003](./ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md)), evaluator metadata, and confidence are identical for identical input regardless of ladder state. Only `action` and `allowed` differ.

Two things depend on this:

* **The observe→enforce promotion decision** ([DEC-012](../../00-source-of-truth/DECISIONS.md)) is measurable, because observe-mode reason codes are exactly the codes enforce mode would have acted on.
* **The Phase 1–2 validation demonstration** asserts "consistent normalized decisions" across two independently operated gateways, one observing and one enforcing. Without this rule that assertion has no meaning.

The pre-clamp action is recorded on the **audit record** as `would_be_action`, not on the decision.

### 6. `warn` and `disabled` are permanently excluded from the action vocabulary

The four MVP actions of §3.4 are unchanged, and no future ADR may add `warn` or `disabled` as an action without superseding this one. The reasoning is [EXTENSIBILITY-ARCHITECTURE.md §5](../EXTENSIBILITY-ARCHITECTURE.md)'s, generalized: an identifier may name a binding state or a decision action, never both.

## Alternatives considered

- **Alternative: extend the action set to five, matching the five states (`allow`, `log_only`, `warn`, `redact`, `block`, plus a disabled path).**
  **Why not chosen:** This is the option [EXTENSIBILITY-ARCHITECTURE.md §5](../EXTENSIBILITY-ARCHITECTURE.md) already rejected, and its reasoning holds under scrutiny: one word would mean both "how strongly this binding is enforced" and "what happened to this request." It also conflates two independently varying things — a policy's severity judgment and a workload's rollout maturity — into a single field, so a decision record could no longer answer "what would enforce mode have done?" without a second field anyway. Finally, it widens the contract by two values across all three deployment modes permanently (R8) to express something a ceiling already expresses.

- **Alternative: hold the ladder state on the `Policy` rather than the binding.**
  **Why not chosen:** Directly contradicts AGR-011's "per policy, per workload" — a policy-held state is per policy only. It would also break the one-policy-many-workloads reuse that is the core product hypothesis: two workloads at different rollout maturity could no longer share a policy version, which is exactly what the Phase 1–2 validation demonstration must show. [DATA-ARCHITECTURE.md §1.4](../DATA-ARCHITECTURE.md) already excludes enforcement mode from the Policy entity for this reason.

- **Alternative: collapse the ladder to two states (`observe`, `enforce`) and treat `Disabled`, `Warn`, `Redact` as operational conventions rather than modelled states.**
  **Why not chosen:** Contradicts AGR-011 and FR-051, both of which require all five states to be represented in the policy and binding schema, and FR-051 marks it MVP. Per the [ADR authority model](./README.md), an ADR may not relax an agreement; AGR-011 would have to be amended first, through the authority path used for `AGREEMENTS.md` itself. No case has been made for such an amendment, and the graduated ladder is load-bearing for the product's observe-before-enforce differentiator.

- **Alternative: `Disabled` means the client skips the evaluation call entirely.**
  **Why not chosen:** The client cannot know the binding state without calling, so this would require distributing binding state to clients — enlarging the client's responsibilities in exactly the direction [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) forbids. It also destroys the audit distinction between a deliberately disabled workload and a removed connector, weakening the only bypass-detection signal available under the cooperative posture (AGR-018).

- **Alternative (chosen): ceiling model, state on the binding, `enforcement_state` reported on the decision.**
  **Why accepted:** Preserves all five AGR-011 states without adding an action; keeps one meaning per identifier; keeps reason codes invariant across states so promotion is measurable and VAL-002's consistency assertion is meaningful; adds exactly one field to one existing entity, consistent with [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)'s extend-don't-fork discipline.

## Consequences

### Positive

- The five states of AGR-011 and the four actions of §3.4 stop competing for the same words. Every identifier in the contract has one meaning.
- `Warn` becomes implementable and distinguishable from `Observe` for the first time, satisfying FR-051's five-state requirement in the decision contract rather than only in the binding schema.
- A disabled workload leaves an audit trail, turning "is this workload actually guarded?" into a query rather than an assumption — a direct improvement to the AGR-018 posture-visibility requirement.
- Reason-code invariance across ladder states makes the observe→enforce promotion criteria of [DEC-012](../../00-source-of-truth/DECISIONS.md) computable from audit data, and gives the Phase 1–2 validation demonstration's "consistent normalized decisions" a precise, testable meaning.
- Clamping is one function over a total order, testable exhaustively — five states times four policy actions is twenty cases, all enumerable in a conformance suite.

### Negative

- One additional field (`enforcement_state`) is added to the public decision contract and must be maintained compatibly across all three deployment modes indefinitely.
- Every client must now understand five states, not two, to be conformant — including the distinction between `observe` and `warn`, whose surfacing behaviour the contract deliberately leaves to the client.
- The `redact` ceiling has a subtlety implementers will meet: a binding in state `redact` whose policy calls for `block` emits `redact`, which requires the evaluator to have produced usable spans. Where it has not, the `transform_failed` failure mode (AGR-012) applies rather than a silent fallback to `block` or `allow`.

### Risks

- **Clients treat `warn` as `observe` and surface nothing**, silently reducing the ladder to four states in practice. Mitigated by making `warn` surfacing an explicit item in the client conformance suite, and by the connector-health telemetry already required by [INTEGRATION-ARCHITECTURE.md §4.5](../INTEGRATION-ARCHITECTURE.md).
- **The `redact` ceiling is misread as an escalation rule** — an implementer assumes state `redact` forces redaction on a policy that called for `allow`. Mitigated by the explicit statement in §2 that the ceiling is an upper bound only, and by conformance cases covering the full five-by-four matrix.
- **`enforcement_state` is used by a client as an authorization signal** — for example, treating `block` state as proof that a request was authorized. This is the [AGR-010](../../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization) / [RSK-002](../../70-governance/RISK-REGISTER.md) false-confidence risk in a new location, and belongs in the client documentation's non-responsibilities section.

## Validation required

This ADR is validated when:

1. The full five-by-four clamping matrix of §2 is covered by conformance tests, executed identically against every client (raw HTTP, SDK, LiteLLM connector), demonstrating that ladder state affects `action` and `allowed` only.
2. One policy version bound to two workloads — one in `observe`, one in `block` — produces identical `reason_codes` and `guardrail_category` values for identical input, with divergent `action` values. This is the [VAL-002](../../60-delivery/VALIDATION-PLAN.md#val-002--two-litellm-one-policy-consistency) assertion, made precise.
3. A workload in state `disabled` produces an audit record with `action: allow`, `enforcement_state: disabled`, empty `reason_codes`, and no evaluator metadata — demonstrably distinguishable from a workload whose connector was removed, which produces no record at all.
4. `DATA-ARCHITECTURE.md` §1.5 records `enforcement_state` as the binding's five-valued attribute, and §1.7 records `enforcement_state` on the `DECISION` entity.

## Related agreements

- [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) — the five-state progression this ADR expresses without relaxing.
- [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) — clamping and `enforcement_state` are part of the one logical contract and hold identically in all three deployment modes.
- [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) — extend-don't-fork: one additive attribute on `Policy binding`, one on `Decision`; no new entity.
- [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — a `redact` ceiling that cannot be satisfied routes to the `transform_failed` behaviour rather than an invented fallback.
- [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) — enforcement posture must be recorded and visible; the `disabled` audit record is what makes that true for the lowest ladder state.
- [AGR-010](../../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization) — `enforcement_state` must not be read as an authorization signal.

## Related documents

- [Integration Architecture](../INTEGRATION-ARCHITECTURE.md) — §3.4 (actions) and §3.8 (this reconciliation, applied)
- [Extensibility Architecture](../EXTENSIBILITY-ARCHITECTURE.md) — §5, the reserved-word reasoning this ADR generalizes
- [Data Architecture](../DATA-ARCHITECTURE.md) — the `Policy binding` and `Decision` entities amended by this decision
- [ADR-0003 — Reason-code taxonomy and category audit dimension](./ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md)
- [ADR-0005 — Evaluation contract wire format and versioning](./ADR-0005-evaluation-contract-wire-format.md)
- [Requirements](../REQUIREMENTS.md) — FR-051
- [Validation Plan](../../60-delivery/VALIDATION-PLAN.md) — VAL-002
