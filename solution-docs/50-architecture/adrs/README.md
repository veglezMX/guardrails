**AI Guardrails — Architecture Decision Records (ADR) Index**

**Authority level:** L1
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

## Purpose and authority

This directory holds the **Architecture Decision Records (ADRs)** for the AI Guardrails platform. An ADR captures a single deep, hard-to-reverse architectural decision — the kind that affects the shape of the system for years, not a product or delivery decision (those belong in [`00-source-of-truth/DECISIONS.md`](../../00-source-of-truth/DECISIONS.md)).

**Authority model — this is the important part:**

* This index document (the file you are reading) is a process document: it explains how ADRs work, provides the template, and lists what exists. It does not itself carry a technical decision.
* An individual ADR is **L1 — Authoritative**, on the same footing as [`AGREEMENTS.md`](../../00-source-of-truth/AGREEMENTS.md) and [`CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md), **once and only once its `Status` field reads `Accepted`**. A `Proposed` ADR is a draft under discussion and binds no one. A `Rejected` ADR is a historical record of a path considered and declined; it is not authoritative but must not be deleted. A `Superseded` ADR remains an authoritative historical record of what was decided and why at the time, but the current binding decision is whatever superseded it.
* No ADR — at any status — may contradict [`AGREEMENTS.md`](../../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md). Those two registers outrank every ADR. An ADR elaborates *how* an agreement or constraint is implemented at the architecture and component level; it must never relax, reinterpret, or work around one. If an ADR appears to require changing an agreement, the agreement must be amended first, through the same authority path used for `AGREEMENTS.md` itself, before the ADR can be Accepted.
* Where a derived document (architecture report, product materials, sales collateral, delivery plan) describes a design covered by an Accepted ADR, it must match that ADR. If a conflict is found, the ADR wins and the derived document must be corrected.

```mermaid
flowchart TB
    AGR["AGREEMENTS.md (L1)<br/>negotiated decisions"]
    CON["CONSTRAINTS.md (L1)<br/>checkable boundaries"]
    DEC["DECISIONS.md (L1)<br/>product / org / delivery decisions"]
    ADR["ADRs, once Accepted (L1)<br/>deep architecture decisions"]
    DERIVED["Derived documents (L2/L3)<br/>charter, product docs, architecture report,<br/>sales deck, delivery plan"]

    AGR --> DEC
    AGR --> ADR
    CON --> ADR
    DEC -. "notes a corresponding ADR<br/>where one exists" .-> ADR
    ADR --> DERIVED
    DEC --> DERIVED
    AGR --> DERIVED
```

---

## Table of contents

- [Purpose and authority](#purpose-and-authority)
- [When to write an ADR](#when-to-write-an-adr)
- [Numbering and naming convention](#numbering-and-naming-convention)
- [ADR lifecycle](#adr-lifecycle)
- [ADR template](#adr-template)
- [Current ADRs](#current-adrs)
- [Suggested backlog of future ADRs](#suggested-backlog-of-future-adrs)
- [Related documents](#related-documents)

---

## When to write an ADR

Write an ADR when a decision is architectural rather than product-, organizational-, or delivery-scoped — that is, when it fixes the shape of a component, an integration boundary, a data model, a security control, or a deployment topology in a way that would be expensive to reverse later. Typical triggers, drawn from the source material:

* A choice between materially different technical approaches to the same requirement (for example, how policies are distributed to a customer-managed runtime that cannot depend on a synchronous control-plane connection — [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)).
* A decision that touches the [core domain model](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (tenant, workload, policy, binding, evaluation, decision) or the [one logical evaluation contract](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) shared across all three deployment modes.
* A decision with real alternatives, real trade-offs, and consequences that a future reader would otherwise have to reconstruct from memory or a chat log.
* A decision that a component-level design document (for example [`COMPONENT-ARCHITECTURE.md`](../COMPONENT-ARCHITECTURE.md) or [`SECURITY-ARCHITECTURE.md`](../SECURITY-ARCHITECTURE.md), where present) needs to cite as its authority for a specific design choice.

Do **not** write an ADR for:

* Product, scope, or delivery-sequencing decisions — those belong in [`DECISIONS.md`](../../00-source-of-truth/DECISIONS.md).
* Restating an agreement or constraint without adding architectural design content — link to [`AGREEMENTS.md`](../../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md) instead.
* Routine implementation detail with no lasting design consequence.

---

## Numbering and naming convention

* Each ADR is a single Markdown file in this directory: `ADR-NNNN-kebab-case-title.md`.
* `NNNN` is a four-digit, zero-padded, strictly sequential integer starting at `0001`. Numbers are assigned in the order an ADR is first proposed (opened as a pull request or draft), not the order it is accepted.
* Numbers are **never reused and never renumbered**, even if the ADR is later rejected or superseded. A gap in the sequence (a rejected or abandoned draft) is expected and acceptable.
* The kebab-case title is a short, stable summary of the decision's subject (for example `ADR-0002-control-plane-data-plane-separation`), not of its outcome — the title should not need to change if the decision is later superseded.
* An ADR file is **immutable in substance** once Accepted. A later change of mind is recorded as a **new** ADR that states in its own front matter that it supersedes the prior number; the prior file's `Status` is then updated to `Superseded` with a pointer forward. Do not rewrite an Accepted ADR's Decision or Consequences sections in place.
* All ADRs live flat in `50-architecture/adrs/`; no subdirectories per phase or component. This index is the single place to browse them by topic.

---

## ADR lifecycle

```mermaid
stateDiagram-v2
    [*] --> Proposed
    Proposed --> Accepted : owners agree; becomes L1
    Proposed --> Rejected : owners decline
    Accepted --> Superseded : a later ADR replaces this decision
    Rejected --> [*]
    Superseded --> [*]
    Accepted --> [*]
```

| Status | Meaning | Authoritative? |
| --- | --- | --- |
| **Proposed** | Under discussion; alternatives and consequences are being evaluated. | No — informative only. |
| **Accepted** | Owners have agreed; the decision is binding on implementation and on every derived document. | **Yes — L1.** |
| **Superseded** | A later ADR (referenced by number) replaces this decision. Retained for historical traceability. | Historically yes, currently no — see the superseding ADR. |
| **Rejected** | Considered and explicitly declined. Retained so the alternative is not silently re-litigated. | No — informative only. |

---

## ADR template

Copy this template into a new file named per the [numbering convention](#numbering-and-naming-convention) above.

```markdown
# ADR-NNNN: <Short, decision-focused title>

**Status:** Proposed | Accepted | Superseded | Rejected
**Date:** <YYYY-MM-DD>
**Owners:** <decision owners — e.g. technical lead, architecture lead>
**Related agreements:** <AGR-NNN, AGR-NNN — link to AGREEMENTS.md anchors>
**Related ADRs:** <Supersedes ADR-NNNN / Superseded by ADR-NNNN, if applicable>

## Context

What forces led to this decision being necessary? What requirement, constraint,
or agreement is in tension, and why can it not be resolved without a deliberate
architectural choice? Cite the relevant register entries(s) and AGREEMENTS.md /
CONSTRAINTS.md entries.

## Decision

State the decision as a single, unambiguous position — what will be built,
where it lives, and what contract or interface it exposes. Avoid hedging;
a later ADR can always supersede this one if circumstances change.

## Alternatives considered

For each real alternative that was evaluated:

- **Alternative:** <name>
  **Why not chosen:** <reason — cost, coupling, contradicts an agreement,
  does not generalize across deployment modes, etc.>

## Consequences

### Positive

- <benefit this decision produces>

### Negative

- <cost, limitation, or capability given up>

### Risks

- <what could go wrong, and at what point this ADR should be revisited>

## Validation required

What must be true, measured, or demonstrated before this decision is
considered proven in production (e.g. a proof of concept, a conformance
test, a load test, a specific validation demonstration criterion)?

## Related agreements

- [AGR-NNN](../../00-source-of-truth/AGREEMENTS.md#agr-nnn--slug) — <one-line relevance>
```

---

## Current ADRs

| ID | Title | Status | Related agreements | File |
| --- | --- | --- | --- | --- |
| ADR-0001 | Standalone platform architecture (worked example) | Accepted | [AGR-001](../../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) | [`ADR-0001-example-decision.md`](./ADR-0001-example-decision.md) |
| ADR-0002 | Guardrail-category catalog and four-seam extension model | **Accepted** (pending owner ratification) | [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | [`ADR-0002-guardrail-category-catalog-and-extension-model.md`](./ADR-0002-guardrail-category-catalog-and-extension-model.md) |
| ADR-0003 | Reason-code taxonomy and guardrail category as an audit dimension | **Proposed** | [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) | [`ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md`](./ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md) |
| ADR-0004 | Enforcement-ladder states and decision actions | **Proposed** | [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) | [`ADR-0004-enforcement-ladder-states-and-decision-actions.md`](./ADR-0004-enforcement-ladder-states-and-decision-actions.md) |
| ADR-0005 | Evaluation contract wire format and versioning | **Proposed** | [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | [`ADR-0005-evaluation-contract-wire-format.md`](./ADR-0005-evaluation-contract-wire-format.md) |
| ADR-0006 | Integration manifest format and contents | **Proposed** | [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import), [AGR-002](../../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) | [`ADR-0006-integration-manifest-format-and-contents.md`](./ADR-0006-integration-manifest-format-and-contents.md) |
| ADR-0007 | Connector identity, installation verification, and liveness | **Proposed** | [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) | [`ADR-0007-connector-identity-verification-and-liveness.md`](./ADR-0007-connector-identity-verification-and-liveness.md) |
| ADR-0008 | Connector distribution and integrity | **Proposed** | [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) | [`ADR-0008-connector-distribution-and-integrity.md`](./ADR-0008-connector-distribution-and-integrity.md) |
| ADR-0009 | Policy document schema and mandatory failure behavior | **Proposed** | [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | [`ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md`](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) |
| ADR-0010 | Policy storage split — bindings and policy documents | **Proposed** | [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | [`ADR-0010-policy-storage-split.md`](./ADR-0010-policy-storage-split.md) |
| ADR-0011 | Policy distribution and caching | **Proposed** | [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) | [`ADR-0011-policy-distribution-and-caching.md`](./ADR-0011-policy-distribution-and-caching.md) |
| ADR-0012 | Client bootstrap configuration | **Proposed** | [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) | [`ADR-0012-client-bootstrap-configuration.md`](./ADR-0012-client-bootstrap-configuration.md) |
| ADR-0013 | Runtime capability manifest and bind-time compatibility | **Proposed** | [AGR-004](../../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | [`ADR-0013-runtime-capability-manifest.md`](./ADR-0013-runtime-capability-manifest.md) |
| ADR-0014 | Audit coverage — rejections and client-synthesized decisions | **Proposed** | [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-014](../../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) | [`ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md`](./ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md) |
| ADR-0015 | Runtime deployment target | **Proposed** | [AGR-002](../../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) | [`ADR-0015-runtime-deployment-target.md`](./ADR-0015-runtime-deployment-target.md) |

ADR-0001 records the architecture-level rationale behind the platform's foundational decision — building AI Guardrails as a standalone policy and enforcement platform rather than functionality embedded inside the existing internal LiteLLM instance (see [AGR-001](../../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) and [DEC-001](../../00-source-of-truth/DECISIONS.md#dec-001--build-as-a-standalone-platform-not-embedded-in-the-internal-litellm)). It also serves as the **worked example** that new ADR authors should model their own records on — hence its filename — since it is the only ADR filed as an affected document in [`AGREEMENTS.md`](../../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) to date.

ADR-0002 records the design behind the guardrail-category catalog ([`20-product/GUARDRAIL-CATALOG.md`](../../20-product/GUARDRAIL-CATALOG.md)) and the four-seam extension model ([`EXTENSIBILITY-ARCHITECTURE.md`](../EXTENSIBILITY-ARCHITECTURE.md)), per [AGR-021](../../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) and [DEC-009](../../00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision). Its status now reads **Accepted (pending owner ratification)**: the first graduation exercise (its Validation Required §2) has been executed and recorded as [DEC-020](../../00-source-of-truth/DECISIONS.md#dec-020--graduate-gr-017-per-category-decision-metrics-from-vision-to-committed-scope). On ratification (branch merge) it becomes the L1 authority for what it settles, closing the AGR-021 traceability gap; until then AGR-021 and DEC-009 remain the standing L1 authority.

ADR-0003 records the design behind category-namespaced reason codes (`GR-0NN:<evaluator-family>:<code>`) and the new `guardrail_category` first-class field on the decision record, per [DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension). While its status reads Proposed, it binds no one; DEC-013 remains the L1 authority.

ADR-0004 resolves a vocabulary collision between two accepted requirements. [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) and [FR-051](../REQUIREMENTS.md) require five enforcement-ladder states (Disabled → Observe → Warn → Redact → Block) per policy per workload, while [`INTEGRATION-ARCHITECTURE.md`](../INTEGRATION-ARCHITECTURE.md) §3.4 defines four decision actions — and `Redact` and `Block` appear in both vocabularies, with `Warn` having no action at all. The ADR makes the ladder state a **ceiling** held on the policy binding, reported to the caller as an additive `enforcement_state` field on the decision, with reason codes invariant across states. It does not relax AGR-011; all five states survive. While it reads Proposed, AGR-011 and §3.4 remain the standing authorities.

ADR-0005 defines the evaluation contract's wire format — JSON over HTTPS with SigV4, URI major versioning, an OpenAPI 3.1 document generated from the runtime's own models and gated by a CI golden diff, RFC 9457 error bodies, and a fail-closed rule for unrecognized enum values that keeps future actions addable without a major version. It closes the gap in which [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) required contract changes to be "versioned and evaluated for backward compatibility" while no versionable artifact existed. It partially fulfils the **API + SDK integration model** backlog candidate below; that candidate stays open for the client-side responsibilities half.

ADR-0006 defines the **integration manifest** — the artifact named in [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) and in the seven-step onboarding flow that no document previously described. It settles that the manifest is a generated, schema-validated, secret-free YAML **file** rather than a screen; that it is derived from a policy binding and never authoritative at evaluation time; that one shape serves all three deployment modes; and — the load-bearing part — that its **prohibited-contents list turns AGR-019's trust commitments into schema constraints that can be checked mechanically** instead of by review discipline. Where ADR-0005 made the evaluation contract versionable, ADR-0006 does the same one step earlier in the flow, for the artifact a consuming team actually receives.

ADR-0007 and ADR-0008 settle the **connector** side of onboarding, which AGR-019 names and no document described. ADR-0007 makes compatibility validation two-phase — declared at bind time, observed at request time — because FR-006, FR-016, and FR-075 all require validating against "the resolving runtime/**connector** version" while nothing stated how the platform learns it; it adds an installation-verification state so a half-wired connector stops being invisible, a reported staleness signal, and an explicit decommissioning action without which staleness becomes alert noise. ADR-0008 gives the connector the supply chain the Mode C runtime container already has — named channels, independent versioning that declares its supported contract majors, verifiable integrity, and documented privilege — on the argument that the connector is the *more* privileged artifact, since it runs inside the consuming team's process with access to prompts and responses.

ADR-0009 through ADR-0015 close the remaining structural gaps identified in a pre-implementation review, each of which would otherwise have been settled by whoever wrote code first:

- **ADR-0009** defines the policy document schema and makes every failure-behavior key required with **no defaults** — the only reading of AGR-012's "no undocumented fallback" that survives contact with a schema, and the record that makes AGR-005's vendor-neutral evaluator selection checkable at publication.
- **ADR-0010** ratifies [`TRADE-OFFS.md` §6](../TRADE-OFFS.md)'s standing "recommended, not yet an AGR" storage split: low-latency key-value for hot-path binding resolution, versioned object storage for immutable policy documents, with the stored artifact and the Mode C distribution artifact being the same object.
- **ADR-0011** closes [`CONTAINER-ARCHITECTURE.md` §7.1](../CONTAINER-ARCHITECTURE.md)'s explicit TBD: runtimes pull, resolution reads local state only, refresh failure never fails an evaluation, staleness is measured and bounded — and "immediate rollback" is stated to mean immediate *at the control plane*, converging within the refresh interval.
- **ADR-0012** closes the client-bootstrap gap ADR-0005 named in its own scope boundaries and the **client-side failure behaviour and configuration distribution** backlog candidate below: a dedicated retrieval path seeded by the integration manifest, with a client that has never been configured refusing to evaluate rather than defaulting to allow.
- **ADR-0013** activates the binding's previously unused "Runtime compatibility requirements" attribute: a runtime publishes what it can detect (capabilities, never implementations), fails to start if its configuration is unsatisfiable, and an unsatisfiable binding is refused at bind time instead of degrading quietly in production.
- **ADR-0014** extends audit coverage to the two events that currently produce no record — requests the runtime declined to evaluate, and decisions a client produced alone during an outage — while keeping self-reported client records distinguishable from platform observations.
- **ADR-0015** resolves the "ECS Fargate or EKS" and "API Gateway or ALB" pairs that every downstream document repeats without choosing, on the argument that Phase 4 ships this runtime into customer accounts as a container and a second packaging path would quietly falsify the [`SUCCESS-CRITERIA.md`](../../10-charter/SUCCESS-CRITERIA.md) criterion.

No ADR has yet been Superseded or Rejected.

---

## Suggested backlog of future ADRs

The following are **candidate topics**, not yet-numbered ADRs. They are architectural decisions implied by the source material and by the deployment-mode agreements that are likely to need a dedicated, deeply-reasoned record before or during the phase noted. A candidate is promoted to a real `ADR-NNNN` file — with a number assigned at the moment it is first proposed — when someone begins the design work; this table itself does not reserve numbers.

| Candidate topic | Why it needs an ADR | Grounding | Related agreements | Likely phase |
| --- | --- | --- | --- | --- |
| **API + SDK integration model** — *partially fulfilled by [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md); the client-side half remains open* | The platform must support invocation from a generic HTTP Evaluation API and a generic SDK in addition to the LiteLLM connector, without ever letting a connector-specific shape leak into the policy or decision contract. ADR-0005 settles the server-side wire format; what remains is the client's own contract — its non-responsibilities, its conformance obligations, and how a client obtains the configuration it needs. | [`SCOPE.md`](../../10-charter/SCOPE.md) (Generic HTTP Evaluation API in MVP scope), [`CONTAINER-ARCHITECTURE.md`](../CONTAINER-ARCHITECTURE.md) (Generic SDK shown as a peer of the LiteLLM Connector and Guarded Gateway in the logical architecture), [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) ("The product must later support additional integrations without changing policy semantics") | [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) | Phase 1–2 |
| **Client-side failure behaviour and configuration distribution** — *closed by [ADR-0012](./ADR-0012-client-bootstrap-configuration.md)* | [`INTEGRATION-ARCHITECTURE.md`](../INTEGRATION-ARCHITECTURE.md) §4.4 requires a connector to apply the workload's configured timeout and failure behaviour, and AGR-012 forbids any undocumented fallback — but that configuration resolves server-side, and the timeout case is precisely when the server is unreachable. No document states how a client obtains it. Until this is settled, every connector and SDK author improvises, which reintroduces at the client layer the divergence ADR-0005 removes at the wire layer. Named as an explicit open gap in [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md#scope-boundaries). | [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (connector responsibilities: timeout handling), [`CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md) (timeout limits) | [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [CON-016](../../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits) | Phase 1–2 |
| **Separate control plane and data plane** — *partially addressed by [ADR-0010](./ADR-0010-policy-storage-split.md) and [ADR-0011](./ADR-0011-policy-distribution-and-caching.md); the boundary's interface and failure-independence remain open* | The logical architecture already draws a Guardrails Control Plane (tenants, workloads, policies, bindings, rollouts) as distinct from the Guardrails Runtime (evaluation API, policy resolver, decision engine) connected only by policy distribution. The boundary, its interface, and its failure-independence need a deliberate record before component design proceeds. | [`CONTAINER-ARCHITECTURE.md`](../CONTAINER-ARCHITECTURE.md) (logical architecture diagram), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../NON-FUNCTIONAL-REQUIREMENTS.md) (policy rollout: immutable versions, controlled promotion, rollback) | [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | Phase 1–2 |
| **Support managed and standalone deployment** — *compute target settled by [ADR-0015](./ADR-0015-runtime-deployment-target.md); the Phase 4 artifact set remains open* | The same runtime software must run as the hosted Guarded Gateway path and as a customer-managed artifact (container, Helm chart, ECS template, infrastructure module) without diverging into two codebases or two contracts. | [AGR-002](../../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes) (Mode C — Customer-Managed Runtime), [AGR-020](../../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) Phase 4 deliverables (runtime container, Helm chart, ECS deployment template, infrastructure module), §16 (the Phase 1–2 validation demonstration requires later customer-managed packaging "without changing the connector contract"; see [`../../60-delivery/VALIDATION-PLAN.md`](../../60-delivery/VALIDATION-PLAN.md)) | [AGR-002](../../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) | Phase 4 |
| **Event-driven policy distribution** — *closed by [ADR-0011](./ADR-0011-policy-distribution-and-caching.md), which chooses pull over push* | Getting an immutable, versioned policy from the control plane to every runtime — including customer-managed runtimes that must keep evaluating during a control-plane outage — needs an explicit distribution mechanism (push/event vs. pull/poll, caching, staleness handling) rather than an assumed synchronous fetch. | [`CONTAINER-ARCHITECTURE.md`](../CONTAINER-ARCHITECTURE.md) ("Policy distribution" as the only arrow between control plane and runtime), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../NON-FUNCTIONAL-REQUIREMENTS.md) (policy-cache hit rate as a measured performance concern; immediate rollback requirement) | [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) | Phase 2 and 4 |
| **Tenant isolation model** | The tenant entity already carries AWS account information, allowed Regions, and retention configuration, and the MVP deliberately uses a minimal (two-account) topology with named triggers for adding separation later. The concrete isolation boundary (data, compute, keys, blast radius) per deployment mode needs a dedicated record before the Guarded Gateway multiplies tenants onto shared infrastructure. | [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) (Tenant domain entity), [`DEPLOYMENT-ARCHITECTURE.md`](../DEPLOYMENT-ARCHITECTURE.md) (AWS account model and its escalation triggers: external customers, compliance, independent security ownership, immutable audit, production scale, dedicated tenant deployments) | [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) | Phase 2–3 |
| **KMS for policy signing** | Policy signing is explicitly named as future scope and explicitly excluded from the MVP, but customer-managed and air-gapped deployments will eventually need integrity guarantees on distributed policy artifacts; the signing mechanism, key ownership, and verification point are architectural, not incidental. | [`SCOPE.md`](../../10-charter/SCOPE.md) (Policy signing listed under future scope; MVP non-goals explicitly exclude "a policy-signing infrastructure"), [`DEPLOYMENT-ARCHITECTURE.md`](../DEPLOYMENT-ARCHITECTURE.md) (AWS KMS as the encryption service), [`CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md) (KMS encryption; audit-event integrity) | [CON-009](../../00-source-of-truth/CONSTRAINTS.md#con-009--kms-encryption-in-transit-and-at-rest) | Phase 5 (post-MVP; design groundwork may start earlier for Mode C) |

Each candidate must be re-validated against [`AGREEMENTS.md`](../../00-source-of-truth/AGREEMENTS.md) and [`CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md) at the time it is actually proposed, since agreements may be amended between now and then.

---

## Related documents

- [`00-source-of-truth/AGREEMENTS.md`](../../00-source-of-truth/AGREEMENTS.md) — authoritative agreements (L1); no ADR may contradict these.
- [`00-source-of-truth/CONSTRAINTS.md`](../../00-source-of-truth/CONSTRAINTS.md) — authoritative checkable boundaries (L1); no ADR may contradict these.
- [`00-source-of-truth/DECISIONS.md`](../../00-source-of-truth/DECISIONS.md) — the product/organizational/delivery decision log; links to this index for the corresponding architecture-level records.
- [`50-architecture/ARCHITECTURE-REPORT.md`](../ARCHITECTURE-REPORT.md) — the consolidated technical report (L3); cites Accepted ADRs rather than re-deriving their reasoning.
- [`50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md`](../NON-FUNCTIONAL-REQUIREMENTS.md) — non-functional requirements that individual ADRs (availability, performance, tenant isolation) must satisfy.
