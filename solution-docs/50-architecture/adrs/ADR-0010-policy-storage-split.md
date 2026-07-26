**Title: ADR-0010 — Policy Storage Split: Bindings and Policy Documents**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

# ADR-0010: Policy Storage Split — Bindings and Policy Documents

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Architecture lead, Technical lead
**Related agreements:** [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model), [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)
**Related ADRs:** [ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) (the immutable document this stores), [ADR-0011](./ADR-0011-policy-distribution-and-caching.md) (what consumes the artifact)

While this ADR reads `Proposed` it binds no one.

## Context

[`DEPLOYMENT-ARCHITECTURE.md`](../DEPLOYMENT-ARCHITECTURE.md) names policy storage as **"DynamoDB or versioned S3"** and leaves the choice open. [`TRADE-OFFS.md` §6](../TRADE-OFFS.md) analyses both and reaches a recommendation, but records it explicitly as *"recommended, not yet an AGR,"* and states that if accepted it should become an agreement and its own ADR.

That recommendation has sat unratified while every downstream document assumes storage works. It is load-bearing in three places:

- **Policy resolution is on the evaluation hot path.** Which policy version a workload is bound to must be answerable per request, at low latency.
- **Mode C requires a distributable artifact.** [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) requires a customer-managed runtime to keep evaluating through a control-plane outage, which requires a policy artifact it can hold locally, checksum, and refresh asynchronously.
- **Immutability is a correctness property**, not a convention ([ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) §6). Rollback, audit traceability, and safe caching all rest on it.

This ADR ratifies the TRADE-OFFS §6 recommendation as a decision rather than leaving the code to settle it.

## Decision

### 1. Two stores, split by access shape

Policy state is divided by **how it is read**, not by what it is called:

| What | Store | Why |
| --- | --- | --- |
| **Bindings** — which policy version a workload is bound to, its mode, effective date, rollback version, compatibility requirements | Low-latency key-value store (**DynamoDB**), the same store already required for the workload registry ([`DEPLOYMENT-ARCHITECTURE.md`](../DEPLOYMENT-ARCHITECTURE.md)) | Read once per evaluation on the hot path, by workload identity. One store for both registry and binding lookups |
| **Policy documents** — the immutable versioned content itself | Object store with native versioning (**versioned S3**) | Immutability is native rather than schema-encoded; a versioned object is the natural artifact to distribute, checksum, and cache locally |

This is **not** an either/or between the two services [`DEPLOYMENT-ARCHITECTURE.md`](../DEPLOYMENT-ARCHITECTURE.md) names. It is a division of responsibility between them, and the division follows the two genuinely different access patterns the platform has.

### 2. Bindings are append-only

Rollback writes a **new binding record and supersedes the prior one**. Nothing is mutated in place and nothing is deleted.

This is what makes two required capabilities queryable rather than aspirational: "immediate rollback" ([`NON-FUNCTIONAL-REQUIREMENTS.md`](../NON-FUNCTIONAL-REQUIREMENTS.md)) becomes writing a record rather than restoring one, and "evaluation-result comparison between versions" ([`NON-FUNCTIONAL-REQUIREMENTS.md`](../NON-FUNCTIONAL-REQUIREMENTS.md)) becomes a query over history rather than an archaeology exercise. It also gives [ADR-0007](./ADR-0007-connector-identity-verification-and-liveness.md)'s workload lifecycle a durable place to record decommissioning without losing history.

### 3. The policy document is the distribution artifact

The same immutable versioned object that is the system of record for policy content is the artifact distributed to runtimes, including customer-managed ones. There is no separate export, transformation, or packaging step.

Making the stored artifact and the distributed artifact the same object removes an entire class of drift: a runtime cannot be evaluating a document that differs from the published one, because there is only one document. It also gives policy signing a natural anchor when that arrives ([OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design)) — an immutable object is signable in a way a mutable database row is not.

### 4. Regionality follows the tenant, not the store

Both stores are deployed as regional resources per [DEC-018](../../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga), inside the existing two-account model ([AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model)). A tenant's bindings and the policy documents its workloads resolve are served from that tenant's pinned processing Region. This decision adds no account and changes no account boundary.

## Scope boundaries

This ADR does **not** decide:

- **How policy state reaches a runtime, or the caching behavior there** — see [ADR-0011](./ADR-0011-policy-distribution-and-caching.md).
- **The policy document's schema or contents** — see [ADR-0009](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md).
- **Audit storage**, already settled as buffered-queue-then-archive in [`TRADE-OFFS.md` §](../TRADE-OFFS.md).
- **Policy signing** — deferred under [OQ-012](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design), though §3 is chosen partly to keep it cheap later.
- **Whether additional account separation is needed.** Unchanged; [OQ-009](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-009--trigger-point-for-three-account-aws-separation)'s triggers are untouched by this decision.

## Alternatives considered

- **Alternative: one store for everything (key-value only).**
  **Why not chosen:** Immutability must then be encoded in the schema rather than provided by the store, so "immutable version" becomes a convention any write path can violate. Distribution to Mode C also becomes a live read against the hosted control plane or a bespoke export, which is precisely the synchronous dependency [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) forbids.

- **Alternative: one store for everything (object store only).**
 **Why not chosen:** Binding resolution is a per-evaluation, hot-path lookup by workload identity. An object store is the wrong shape for it, and the workload registry already requires a key-value store ([`DEPLOYMENT-ARCHITECTURE.md`](../DEPLOYMENT-ARCHITECTURE.md)) — so this option adds a second store's latency without removing the first.

- **Alternative: defer the choice and let implementation settle it.**
  **Why not chosen:** This is the current state, and it is the reason this ADR exists. The choice determines whether immutability is structural or conventional and whether Mode C distribution is natural or bespoke — both too consequential to be settled by whoever writes the first storage adapter.

- **Alternative (chosen): key-value for bindings on the hot path, versioned object storage for immutable policy documents, append-only bindings, one artifact for storage and distribution.**
  **Why accepted:** Each store is used for the access pattern it fits; immutability becomes a property of the store rather than a rule the code must honour; and the Mode C distribution artifact exists by construction rather than as extra work in Phase 4.

## Consequences

### Positive

- Immutability is enforced by the storage layer rather than by discipline in every write path.
- The Mode C distribution artifact exists from Phase 1, so [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) is satisfiable by an approach already in use rather than by new Phase 4 machinery.
- Rollback and cross-version comparison become queries over append-only history.
- Policy signing has a natural anchor when OQ-012 resolves.
- [`TRADE-OFFS.md` §6](../TRADE-OFFS.md)'s standing "recommended, not yet an AGR" note is closed.

### Negative

- Two stores to operate, monitor, and back up rather than one.
- Resolution touches both: the binding names a version, the document supplies the content. Caching makes this one hop in the common case, but the two-store shape is inherent.
- Append-only bindings grow without bound and need a retention posture eventually.

### Risks

- **The document store becomes a hot-path dependency** if caching is weak, converting an archive into a latency contributor. This is why [ADR-0011](./ADR-0011-policy-distribution-and-caching.md) exists and why resolution must never make a synchronous fetch on the request path.
- **The split is treated as a vendor commitment** rather than an access-pattern decision, making the platform harder to reason about for non-AWS deployment discussions. The two roles — hot-path lookup, immutable versioned artifact — are what is being decided; the named services are how they are met under [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model).

## Validation required

This ADR is validated when:

1. Binding resolution for an evaluation reads only the low-latency store, with no request-path read of the document store.
2. Attempting to overwrite a published policy document is refused by the storage layer itself, not only by application logic.
3. A rollback produces a new binding record with the prior one superseded, and the prior record remains queryable.
4. A customer-managed runtime obtains a policy document as a versioned artifact it can checksum and cache locally, with no live query against the hosted control plane on the evaluation path.

## Related agreements

- [AGR-013](../../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) — AWS as primary cloud, minimal account model, unchanged by this decision.
- [AGR-015](../../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) — §3 is what makes this satisfiable.
- [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) — Policy and Policy binding are distinct entities; the storage split follows the domain model rather than cutting across it.

## Related documents

- [Trade-offs](../TRADE-OFFS.md) — §6, the analysis this ratifies
- [ADR-0009 — Policy document schema and mandatory failure behavior](./ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md)
- [ADR-0011 — Policy distribution and caching](./ADR-0011-policy-distribution-and-caching.md)
- [Data Architecture](../DATA-ARCHITECTURE.md), [Deployment Architecture](../DEPLOYMENT-ARCHITECTURE.md)
