**Title: ADR-0009 — Policy Document Schema and Mandatory Failure Behavior**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

# ADR-0009: Policy Document Schema and Mandatory Failure Behavior

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Technical lead, Security and governance lead
**Related agreements:** [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)
**Related ADRs:** [ADR-0004](./ADR-0004-enforcement-ladder-states-and-decision-actions.md) (the action vocabulary a policy may name), [ADR-0003](./ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md) (the reason codes it produces)

While this ADR reads `Proposed` it binds no one.

## Context

"Policy format" is the second named Phase 1 deliverable in [AGR-020](../../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery). [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) requires a policy to be portable across every integration path and deployment mode. [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) requires failure behavior to be part of the policy with **no undocumented fallback**. [`FEASIBILITY-ASSESSMENT.md` §](../FEASIBILITY-ASSESSMENT.md) names "the policy schema's failure-behavior section" as design work that should start immediately.

**The schema does not exist.** `solution-docs/` describes what a policy must contain in prose — stages, evaluator configuration, thresholds, failure behavior, audit behavior, compatibility metadata ([CAP-001](../../20-product/CAPABILITIES.md)) — but no document states which fields are required, what a missing field means, or how evaluators are selected without naming a vendor.

Three consequences, all live:

- **AGR-012's "no undocumented fallback" is unenforceable.** If failure behavior is a section a policy author *may* provide, then a policy omitting it has undocumented fallback behavior by construction — which is the exact condition the agreement forbids. Whether a missing key is an error or a default is the whole agreement, and no document says.
- **AGR-005's portability has no mechanism.** A policy is portable only if nothing in it names a vendor, a gateway, or a deployment. Without a schema stating how evaluators are selected, the first author to write "bedrock" in a policy makes every policy non-portable and the agreement retroactively false.
- **[CON-004](../../00-source-of-truth/CONSTRAINTS.md) and [NFR-MAINT](../NON-FUNCTIONAL-REQUIREMENTS.md)'s 100%-isolation requirement have no checkable subject.** "Zero vendor-specific types may appear in the public policy schema" presupposes a schema.

The question this ADR resolves: **what does a policy document contain, what is required, and how does it name evaluators without naming vendors?**

## Decision

### 1. A policy is a versioned document with a published schema

A policy is authored as a structured document, validated against a **published schema**, and published as an immutable version. The schema is part of the platform's published contract surface alongside the evaluation contract ([ADR-0005](./ADR-0005-evaluation-contract-wire-format.md)), versioned under the same classification rules.

Validation happens **before publication**, not at evaluation time. An invalid policy is never published, so the runtime never resolves one.

### 2. Required contents

| Section | Required | Contents |
| --- | --- | --- |
| Identity | Yes | Policy name and immutable integer version |
| Stages | Yes | Which evaluation stages this policy applies to, drawn only from the reserved stage vocabulary |
| Evaluators | Yes | One or more evaluator selections — see §3 |
| Failure behavior | **Yes, all keys** | See §4 |
| Audit behavior | Yes | Content-retention posture — see §5 |
| Compatibility metadata | Yes | The runtime capabilities and contract version this policy requires |

A policy carries **no tenant, no workload, and no enforcement mode**. Those live on the binding ([AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)), which is what allows one policy version to serve many workloads at different points on the enforcement ladder — the property the Phase 1–2 validation demonstration exists to prove.

### 3. Evaluators are selected by capability, never by vendor

A policy selects an evaluator by **what it detects**, not by which product detects it: a detection family and a capability within that family, plus the policy-owned parameters — threshold, which stages it applies to, and which action a match produces.

Which implementation serves a given family in a given deployment is **deployment configuration, not policy content**. The same policy document, unchanged, resolves to different implementations in the hosted deployment and in a customer-managed runtime.

This is the mechanism behind [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) and [CON-004](../../00-source-of-truth/CONSTRAINTS.md): a vendor name appearing anywhere in a policy document is a schema violation, detectable by validation rather than by review.

The action a policy may name is bounded by [ADR-0004](./ADR-0004-enforcement-ladder-states-and-decision-actions.md)'s vocabulary. Reserved-but-inactive actions are not selectable.

### 4. Failure behavior has one required key per failure mode, and no defaults

[AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) enumerates the failure modes a policy must define behavior for. The schema requires **one key per mode, every key present, with no default value for any of them.**

A policy version missing any failure-behavior key **fails validation and is never published.**

This is the load-bearing decision in this record. A default value — any default, however conservative — would mean a policy author who omits the section still gets behavior, and that behavior would be undocumented from the author's point of view. "No undocumented fallback" and "sensible defaults" are mutually exclusive, and this ADR chooses the agreement.

The cost is real and accepted: every policy author writes every failure key, including for modes they consider unlikely. That verbosity **is** the documentation the agreement requires.

### 5. Audit behavior states content retention explicitly

A policy states whether evaluated content is retained in audit records. Retention is **off unless the policy opts in**, per [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default).

Unlike failure behavior, this section may carry a default — because the default *is* the agreement, and a policy that says nothing gets the behavior the register already mandates. The asymmetry is deliberate: AGR-014 states a default; AGR-012 forbids one.

### 6. Published versions are immutable

Publishing a policy under an existing name and version is refused, always. A change is a new version.

Immutability is what makes rollback meaningful, makes "which policy version produced this decision" answerable from an audit record, and makes a policy artifact safe to cache and distribute ([AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)).

## Scope boundaries

This ADR does **not** decide:

- **Policy composition or inheritance** — whether a workload may be bound to more than one policy ([OQ-021](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-021--policy-selection-cardinality-in-the-onboarding-flow), [OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design)).
- **Policy signing** — later-phase scope under OQ-012.
- **Where policy documents are stored or how they reach a runtime** — see [ADR-0010](./ADR-0010-policy-storage-split.md) and [ADR-0011](./ADR-0011-policy-distribution-and-caching.md).
- **Which evaluator implementations exist**, or the deployment configuration mapping families to them — see [ADR-0013](./ADR-0013-runtime-capability-manifest.md).
- **The authoring surface.** Whether policies are authored in a repository, through a management API, or both is an operating-model choice; the schema is the same either way.

## Alternatives considered

- **Alternative: failure behavior optional, with conservative defaults.**
  **Why not chosen:** Directly contradicts [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy). A default is undocumented fallback behavior from the author's perspective regardless of how safe it is, and "safe" is not universal — fail-closed protects content and breaks production traffic; fail-open does the reverse. The right answer is workload-specific, which is why the agreement requires it to be stated.

- **Alternative: allow a policy to name an evaluator implementation directly, for cases where a specific engine's behavior is wanted.**
  **Why not chosen:** Makes that policy non-portable, breaking [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) for every deployment that lacks the named engine, and violating [CON-004](../../00-source-of-truth/CONSTRAINTS.md). Where an implementation's behavior genuinely differs, the difference belongs in the capability vocabulary, not in an escape hatch.

- **Alternative: mutable policies with a change history rather than immutable versions.**
  **Why not chosen:** Breaks rollback, breaks decision-to-policy traceability in audit records, and makes cached policy artifacts unsafe — a cached copy could differ from the current content under the same identifier, which is exactly the degraded-mode correctness risk ([`TECHNICAL-RISKS.md`](../TECHNICAL-RISKS.md) R7) made structural.

- **Alternative (chosen): a schema-validated, immutable versioned document; capability-based evaluator selection; every failure-behavior key required with no defaults; retention off by default.**
  **Why accepted:** Makes AGR-005, AGR-012, AGR-014, and CON-004 checkable at publication rather than by review, and keeps the policy free of tenant, workload, and mode so one version can serve many workloads at different maturity levels.

## Consequences

### Positive

- AGR-012 becomes mechanically enforced: a policy that would have had undocumented fallback behavior cannot be published.
- AGR-005 and CON-004 gain a validation point — a vendor name in a policy is a schema violation, not a review finding.
- The policy artifact is safe to cache, distribute, and sign later, because it is immutable and self-contained.
- One policy version serving many workloads at different ladder positions is structural rather than conventional, which is what the Phase 1–2 demonstration tests.

### Negative

- Policy documents are verbose. Every author writes every failure key.
- Compatibility metadata must be maintained per policy version, and a policy requiring a capability no deployment serves is only detectable at bind time.
- The schema is another versioned contract artifact with its own compatibility discipline.

### Risks

- **Defaults are reintroduced for author convenience** after the first complaints about verbosity. This is the most likely erosion; it is a contradiction of AGR-012 and must be treated as a register-level change, not a schema tweak.
- **The capability vocabulary grows to mirror one vendor's feature list**, making it nominally vendor-neutral and practically vendor-shaped. Mitigated by the guardrail-category catalog governing the vocabulary rather than adapter authors.

## Validation required

This ADR is validated when:

1. A policy document missing any failure-behavior key is rejected at publication with a message naming the missing key.
2. A policy document containing a vendor name in any evaluator selection is rejected at publication.
3. The same policy document, unchanged, produces identical decisions in two deployments whose evaluator implementations differ.
4. Publishing an existing name and version is refused.
5. A policy that omits audit behavior retains no content.

## Related agreements

- [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) — §3 is the portability mechanism.
- [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — §4 is the enforcement of "no undocumented fallback."
- [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default) — §5.
- [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) — a policy carries no tenant, workload, or mode.
- [CON-004](../../00-source-of-truth/CONSTRAINTS.md) — no vendor-specific structure in the policy schema.

## Related documents

- [ADR-0004 — Enforcement-ladder states and decision actions](./ADR-0004-enforcement-ladder-states-and-decision-actions.md)
- [ADR-0010 — Policy storage split](./ADR-0010-policy-storage-split.md)
- [ADR-0011 — Policy distribution and caching](./ADR-0011-policy-distribution-and-caching.md)
- [ADR-0013 — Runtime capability manifest and bind-time compatibility](./ADR-0013-runtime-capability-manifest.md)
- [Component Architecture](../COMPONENT-ARCHITECTURE.md), [Data Architecture](../DATA-ARCHITECTURE.md) — the Policy entity this schema expresses
