**Title: ADR-0003 — Reason-Code Taxonomy and Guardrail Category as an Audit Dimension**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

# ADR-0003: Reason-Code Taxonomy and Guardrail Category as an Audit Dimension

## Status

**Proposed**

This ADR records the design behind [DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension). Until this ADR's status reads `Accepted`, it does not itself carry L1 authority — DEC-013 does. Per [`00-source-of-truth/TRACEABILITY-MATRIX.md`](../../00-source-of-truth/TRACEABILITY-MATRIX.md), the [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) traceability row stays open on the ADR-track (currently held by [ADR-0002](./ADR-0002-guardrail-category-catalog-and-extension-model.md)) until the referenced ADR reads Accepted; this ADR does not itself close that row.

## Context

[ADR-0002](./ADR-0002-guardrail-category-catalog-and-extension-model.md) established a named, change-controlled taxonomy of guardrail categories — [`20-product/GUARDRAIL-CATALOG.md`](../../20-product/GUARDRAIL-CATALOG.md), `GR-001` through `GR-018` — and the four-seam extension model that lets the platform grow into that taxonomy without forking its runtime concepts. What the catalog did not resolve is how a guardrail category is expressed **at the point where an evaluation decision is actually recorded**.

Today the `DECISION` entity ([`50-architecture/DATA-ARCHITECTURE.md`](../DATA-ARCHITECTURE.md) §1) carries a `reason_codes` field, but the codes it holds are evaluator-local: they come from whatever vocabulary the underlying evaluation engine (Bedrock Guardrails today, other engines later per [AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)) happens to emit, normalized only to the extent the adapter chooses. Nothing in the decision record itself says which `GR-0NN` catalog category a reason code belongs to. This leaves two capabilities unreachable:

- **Per-category metrics.** There is no way to ask "how many block decisions this month were prompt-injection (GR-001) versus sensitive-data (GR-003)?" without an out-of-band lookup table mapping every evaluator-native code to a category — a table that drifts the moment a new evaluator or a new code is added.
- **Catalog→decision traceability**, which [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) requires: a durable, queryable link from a catalog category to the decisions it produced. Without a category-bearing field on the decision record, this traceability does not exist as a first-class capability.

The question this ADR resolves: **how should a guardrail category be represented in the reason code and the decision record, without forking the decision structure ([AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) — extend the domain model, never fork it) or the one logical evaluation contract shared across all three deployment modes ([AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes))?**

## Decision

Adopt a category-namespaced reason-code taxonomy, plus one new first-class field on the decision record:

1. **Canonical reason-code format.** Every reason code the platform emits — regardless of which evaluation engine produced it — is normalized by its adapter into the form `GR-0NN:<evaluator-family>:<code>`, where `GR-0NN` is the catalog category ID from `GUARDRAIL-CATALOG.md`, `<evaluator-family>` names the evaluator that produced the finding (e.g. `bedrock-guardrails`, a future deterministic-rules engine, a future PII evaluator), and `<code>` is that evaluator's own native code or a short stable slug for it. The category segment is mandatory; no reason code may be emitted without one.
2. **`guardrail_category` becomes a first-class field on the `DECISION` entity**, alongside the existing `reason_codes` field, in [`DATA-ARCHITECTURE.md`](../DATA-ARCHITECTURE.md) §1. It is populated from the category segment of the reason code(s) that drove the decision's `action`, so category is queryable directly on the decision record rather than only inside a compound string.
3. **Consequence for reporting and traceability.** With category namespaced into the code and mirrored onto its own field, per-category metrics (counts, rates, dashboards) and catalog→decision traceability (AGR-021) become directly implementable by grouping or filtering on `guardrail_category` — no side-table, no forked decision structure, no per-mode divergence of the evaluation contract.

This is a normalization rule applied inside the existing evaluator-adapter seam (AGR-004) and an additive field on an existing entity (AGR-017); it introduces no new domain entity and no new evaluation stage or decision action.

## Alternatives Considered

### Alternative 1 — Evaluator-local codes only (status quo)

Keep reason codes exactly as each evaluation engine's adapter happens to normalize them, with no mandatory category segment and no `guardrail_category` field on the decision record.

**Why rejected:**

- No cross-evaluator aggregation is possible: two evaluators' codes for conceptually the same category (e.g. both flagging prompt injection) have no shared vocabulary to group on.
- Catalog→decision traceability (AGR-021) stays unmet — the catalog remains a classification document with no queryable link to the decisions it describes.
- Any future per-category reporting would have to be reconstructed from an external, evaluator-code-to-category mapping table maintained outside the decision record — exactly the drift risk this decision avoids.

### Alternative 2 — Flat global code registry

Introduce a single flat namespace of reason codes (e.g. sequentially assigned `RC-0001`, `RC-0002`, …) independent of the guardrail-category catalog, with category looked up separately if needed.

**Why rejected:**

- Loses the category mapping at the point of use: a code alone does not say what category it belongs to without a side lookup, reproducing the same drift risk as Alternative 1 in a different shape.
- Invites collisions and governance overhead as more evaluator families are added — a second registry to keep synchronized with the catalog, with no structural guarantee the two stay aligned.
- Duplicates governance machinery `GUARDRAIL-CATALOG.md` and its category IDs already provide; there is no reason to invent a parallel identifier space.

### Alternative 3 — Category-namespaced reason code (chosen)

Namespace the category directly into the reason code (`GR-0NN:<evaluator-family>:<code>`) and add `guardrail_category` as a first-class decision-record field.

**Why accepted:**

- Reuses the catalog's own category IDs (`GR-001`–`GR-018`) as the namespace, so the mapping from code to category is structural, not a maintained side table — it cannot drift independently of the catalog.
- Adds one field to one existing entity (AGR-017's extend-don't-fork discipline) rather than introducing a new entity, a new evaluation stage, or a mode-specific variant of the evaluation contract (AGR-006).
- Makes per-category metrics and AGR-021 catalog→decision traceability directly queryable from the decision record itself.

## Consequences

### Positive

- Per-category dashboards and reporting (block/warn/redact counts by `GR-0NN`, trend over time, per-tenant category mix) become directly implementable from decision records, with no external mapping table to maintain.
- Supplies the catalog→decision traceability [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) requires, closing a gap `ADR-0002` left open.
- The one logical evaluation contract (AGR-006) and the core domain model (AGR-017) are both preserved unchanged in shape — this is a normalization rule inside the adapter seam plus one additive field, not a new concept.
- New evaluator families (per the evaluator-adapter extension seam, `EXTENSIBILITY-ARCHITECTURE.md` §4) inherit the namespacing rule automatically: any new adapter must emit `GR-0NN:`-prefixed codes from day one, so category coverage never lags behind evaluator coverage.

### Negative

- Every evaluator adapter — the existing Bedrock Guardrails adapter and every future engine adapter — must map its native codes into the `GR-0NN:<evaluator-family>:<code>` namespace before a decision is recorded; this is translation logic that must be written, tested, and kept current as each engine's own code vocabulary evolves.
- A reason code that plausibly spans more than one catalog category forces an adapter-level judgment call about primary category attribution; the taxonomy does not itself resolve multi-category findings.

### Risks

- **Miscategorization at the adapter boundary** — an adapter maps a native code to the wrong `GR-0NN`, silently corrupting per-category metrics and traceability. Mitigated by making category-mapping conformance a required part of the evaluator-adapter contract-conformance tests referenced in `EXTENSIBILITY-ARCHITECTURE.md` §4 and by DEC-015's requirement that every new engine adapter pass a compatibility review gate.
- **Namespace drift as categories change** — if a catalog category is ever renamed or reclassified (a Path A, register-level change per `70-governance/DOCUMENT-GOVERNANCE.md` §6), previously recorded reason codes remain stamped with the category ID that was current at evaluation time; historical decision records are not rewritten. This is expected (decision records are append-only audit facts) but must be understood by anyone querying across a category rename boundary.
- **Multi-category findings simplified to one category** — a finding that legitimately touches two categories is recorded under one primary `guardrail_category`; this should be revisited if a category-spanning reporting need emerges that a single-valued field cannot satisfy.

## Validation Required

This ADR is validated when:

1. A decision record produced by a real evaluation (not a synthetic example) carries a reason code in the canonical `GR-0NN:<evaluator-family>:<code>` form and a populated `guardrail_category` field that matches the code's category segment.
2. A per-category count — for example, "decisions with `action = block` grouped by `guardrail_category`" — can be produced directly from a set of decision records, with no external mapping table involved.
3. `DATA-ARCHITECTURE.md` §1 reflects `guardrail_category` as a field on the `DECISION` entity, and at least one evaluator adapter's contract-conformance tests assert the namespacing rule.

## Related Agreements

| Agreement | Relevance to this ADR |
|---|---|
| [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) | The reason-code and decision-record shape defined here is part of the one logical evaluation contract; it must hold identically across all three deployment modes. |
| [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | Extend-don't-fork rule: `guardrail_category` is an additive field on the existing `DECISION` entity, not a new domain entity. |
| [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) | The catalog whose category IDs this ADR reuses as the reason-code namespace, and the traceability requirement this decision fulfills. |
| [DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension) | The decision-log entry this ADR provides the design and alternatives for. |

## Related Documents

- [Guardrail-Category Catalog](../../20-product/GUARDRAIL-CATALOG.md)
- [Data Architecture](../DATA-ARCHITECTURE.md)
- [Extensibility Architecture](../EXTENSIBILITY-ARCHITECTURE.md)
- [ADR-0002 — Guardrail-Category Catalog and Four-Seam Extension Model](./ADR-0002-guardrail-category-catalog-and-extension-model.md)
- [Decision Log](../../00-source-of-truth/DECISIONS.md)
- [Traceability Matrix](../../00-source-of-truth/TRACEABILITY-MATRIX.md)
