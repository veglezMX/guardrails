**Title: ADR-0012 — Client Bootstrap Configuration**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

# ADR-0012: Client Bootstrap Configuration

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Technical lead
**Related agreements:** [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy)
**Related ADRs:** [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) (names this gap in its scope boundaries), [ADR-0006](./ADR-0006-integration-manifest-format-and-contents.md) (the artifact that seeds a client)

While this ADR reads `Proposed` it binds no one.

## Context

[`INTEGRATION-ARCHITECTURE.md` §4.4](../INTEGRATION-ARCHITECTURE.md) requires a connector to apply the workload's configured per-stage timeouts and, on timeout, its documented failure behavior — *"rather than an implicit default; there must be no undocumented fallback behavior"* ([AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy)). §4.4 also states that timeouts are configured on the policy binding, **not hardcoded in the connector**.

Those two requirements combine into a problem no document resolves: **the configuration resolves server-side, and the moment a client most needs it is exactly the moment the server is unreachable.** A client that has never contacted the platform has no timeouts and no failure behavior — and "no failure behavior" is the one state AGR-012 forbids.

[ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) names this gap explicitly in its scope boundaries and declines to close it. The [ADR backlog](./README.md#suggested-backlog-of-future-adrs) carries it as **Client-side failure behaviour and configuration distribution**, noting that until it is settled *"every connector and SDK author improvises, which reintroduces at the client layer the divergence ADR-0005 removes at the wire layer."*

This is not theoretical. Three clients are in Phase 1–2 scope — the LiteLLM connector, the generic SDK, and a consumer-operated custom gateway ([AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)) — and the third is written by a team this one does not control.

The question this ADR resolves: **how does a client obtain the timeouts and failure behavior it is required to apply, including when the platform is unreachable?**

## Decision

### 1. Client configuration is served by the platform, on its own retrieval path

A client retrieves its configuration — **per-stage timeouts and client-side failure behavior** — from the platform, keyed by workload identity, on a path separate from evaluation.

Separate because the two have opposite requirements. Evaluation is synchronous, per-request, and latency-critical. Configuration is retrieved once, cached, and refreshed in the background. Folding configuration into the evaluation response would put bytes on every request to carry state that changes rarely, and would leave a client with nothing before its first successful call.

### 2. A client with no configuration refuses to evaluate

If a client has never successfully retrieved its configuration, **and no explicit configuration was supplied when it was constructed, it refuses to evaluate.** It does not proceed with a built-in default, and it does not allow.

Same rule the runtime applies to an unresolvable policy: an unset failure behavior is a **configuration error, not a permissive default.** Any built-in default would be undocumented fallback behavior, which is exactly what AGR-012 forbids — and defaulting to allow would silently disable guardrails for a workload whose owner believes they are on.

The explicit-configuration escape hatch exists because a client must be able to start in an environment where the platform is not yet reachable — but it makes the values a deliberate, visible choice rather than a hidden one.

### 3. Losing contact never un-configures a client

A client that has previously retrieved its configuration keeps applying it when refresh fails. A refresh failure is not a configuration failure.

Without this, a control-plane outage would convert every working client into a refusing one — turning a platform availability event into a consuming application outage, which is far worse than briefly stale timeouts. This mirrors [ADR-0011](./ADR-0011-policy-distribution-and-caching.md) §3 for the same reason.

### 4. Configuration carries no policy

Client configuration contains **timeouts and failure behavior only**. It carries no detection rules, no thresholds, no evaluator selection, and no policy content.

[AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) requires policy to stay out of connectors. A configuration channel from platform to client is precisely where policy would leak if the boundary were not stated — it would be locally convenient and permanently corrosive, because a client holding policy fragments is a client that can drift from the runtime's decisions.

The client learns *how long to wait* and *what to do when it cannot wait longer*. It never learns *what to detect* or *what to do about a finding*.

### 5. The manifest seeds the client; the retrieval path keeps it current

The integration manifest ([ADR-0006](./ADR-0006-integration-manifest-format-and-contents.md) §3) already carries timeouts and client failure behavior. That is the **seed**: it lets a client be correctly configured at install time, before its first retrieval, and it makes the values reviewable in the consuming team's own repository.

The retrieval path is what keeps them **current** when an operator changes a binding's timeouts without regenerating and redistributing manifests.

Neither alone is sufficient. Manifest-only means a timeout change requires redistributing a file to every consumer. Retrieval-only means a client is unconfigured until its first successful call — precisely the bootstrap gap this ADR exists to close.

## Scope boundaries

This ADR does **not** decide:

- **Numeric timeout or refresh values.** Per-workload configuration; no source-defined figure exists ([CON-016](../../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits) requires them to be explicit, not any particular number).
- **What the client does with an unrecognized decision value** — already settled in [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) §7, which routes it through the same configured failure behavior this ADR distributes.
- **Policy distribution to runtimes** — see [ADR-0011](./ADR-0011-policy-distribution-and-caching.md). Different consumer, different constraints, deliberately separate.
- **Authentication**, which follows the same IAM basis as the evaluation path ([CON-010](../../00-source-of-truth/CONSTRAINTS.md#con-010--iam-based-authentication-and-least-privilege-roles)).
- **Whether a client-side failure produces an audit record** — see [ADR-0014](./ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md).

## Alternatives considered

- **Alternative: ship defaults in each client library.**
  **Why not chosen:** The definition of undocumented fallback behavior, forbidden by [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy). It also guarantees divergence — three client authors will choose three different defaults, and the custom-gateway team a fourth — reintroducing at the client layer exactly the inconsistency the evaluation contract removes at the wire layer.

- **Alternative: carry configuration on every evaluation response.**
  **Why not chosen:** Solves nothing at the moment it matters. A client with no prior response has no configuration, and a client whose call just timed out did not receive a response — the two cases where the configuration is actually needed. It also puts rarely-changing state on every request.

- **Alternative: manifest only, no retrieval path.**
  **Why not chosen:** Makes every timeout adjustment a redistribution exercise across every consuming team, which under cooperative posture means the operator cannot change a timeout without each team's cooperation. Configuration would ossify at whatever was set on install day.

- **Alternative: allow evaluation to proceed unconfigured, using a conservative built-in fallback.**
  **Why not chosen:** "Conservative" is not universal — fail-closed protects content and breaks traffic; fail-open does the reverse. Choosing either on the workload owner's behalf is the assumption AGR-012 exists to prevent.

- **Alternative (chosen): a dedicated retrieval path, seeded by the manifest, refusing to evaluate when never configured, retaining configuration through refresh failure, and carrying no policy.**
  **Why accepted:** Closes the bootstrap gap ADR-0005 named; keeps three independently authored clients on identical behavior; and keeps policy out of clients while still giving them what AGR-012 requires them to apply.

## Consequences

### Positive

- The [ADR backlog](./README.md#suggested-backlog-of-future-adrs)'s **client-side failure behaviour and configuration distribution** candidate is closed, and ADR-0005's named scope gap with it.
- Three independently authored clients — including one this team does not write — share one specified behavior instead of three improvised ones.
- Timeouts become operator-adjustable without redistributing files to consuming teams.
- An unconfigured client fails visibly at startup rather than silently allowing at runtime.

### Negative

- One more retrieval path to serve, authenticate, and version.
- Timeout and failure-behavior values exist in two places — the manifest and the live configuration — so a committed manifest can disagree with current configuration. Live values win; the manifest's generation timestamp is what makes the divergence detectable.
- A client refusing to start because it was never configured is a visible failure a consuming team must diagnose during onboarding. Intended, but it is friction at exactly the moment a team is forming its first impression.

### Risks

- **A client author adds a "temporary" default** to smooth local development, and it ships. This is the single most likely erosion of AGR-012 in the client layer; the conformance suite must include the never-configured case as a mandatory scenario rather than leaving it to review.
- **Configuration is quietly widened** to carry thresholds or evaluator hints because it is a convenient existing channel, breaching §4 and [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors).
- **The custom-gateway team implements retrieval but not the refusal rule**, producing a client that allows when unconfigured. Mitigated by making it a conformance case, not documentation.

## Validation required

This ADR is validated when:

1. A client that has never retrieved configuration, and was given none explicitly, refuses to evaluate — and does not allow.
2. A client that has previously retrieved configuration continues operating normally through a total platform outage, applying its last known values.
3. Changing a binding's per-stage timeout is observed to reach a running client without redistributing its manifest.
4. Client configuration contains no thresholds, no evaluator selection, and no policy content.
5. All registered clients — connector, SDK, and a stand-in for a custom gateway — exhibit identical behavior in the never-configured and lost-contact cases.

## Related agreements

- [AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — §2 is the enforcement of "no undocumented fallback" at the client boundary.
- [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) — §4 keeps policy out of the configuration channel.
- [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) — the mechanism must serve a client this team does not write.
- [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) — one configuration behavior across all clients and modes.
- [CON-016](../../00-source-of-truth/CONSTRAINTS.md#con-016--timeout-limits) — timeouts explicit, never implicit.

## Related documents

- [ADR-0005 — Evaluation contract wire format](./ADR-0005-evaluation-contract-wire-format.md) — names this gap
- [ADR-0006 — Integration manifest format and contents](./ADR-0006-integration-manifest-format-and-contents.md) — the seed
- [ADR-0011 — Policy distribution and caching](./ADR-0011-policy-distribution-and-caching.md) — the runtime analogue
- [Integration Architecture](../INTEGRATION-ARCHITECTURE.md) — §4.4, §4.6
