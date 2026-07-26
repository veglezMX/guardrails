**Title: ADR-0007 — Connector Identity, Installation Verification, and Liveness**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** seed.md + AGREEMENTS.md

---

# ADR-0007: Connector Identity, Installation Verification, and Liveness

**Status:** Proposed
**Date:** 2026-07-26
**Owners:** Technical lead, Platform operations lead
**Related agreements:** [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression), [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import)
**Related ADRs:** [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) (the request this identity travels on), [ADR-0006](./ADR-0006-integration-manifest-format-and-contents.md) (the manifest that declares the expected version)

While this ADR reads `Proposed` it binds no one.

## Context

Three accepted requirements — [FR-006](../REQUIREMENTS.md), [FR-016](../REQUIREMENTS.md), [FR-075](../REQUIREMENTS.md) — require the platform to validate a policy version's compatibility against *"the resolving runtime/**connector** version"* before a binding is activated or a new version is promoted. [`OPERABILITY.md` §](../OPERABILITY.md) records compatibility validation as an operator-facing control, and [`DATA-ARCHITECTURE.md` §1](../DATA-ARCHITECTURE.md) gives the binding a "Runtime compatibility requirements" attribute.

**Nothing states how the platform learns which connector version a workload is running.** The runtime half is knowable — the provider operates the runtime. The connector half runs inside a consuming team's process, and no document defines a channel by which its identity reaches the platform. The integration manifest ([ADR-0006](./ADR-0006-integration-manifest-format-and-contents.md) §3) carries the *supported* version range, which is what the platform expects, not what is installed. As written, the connector half of compatibility validation compares an expectation against itself.

Two adjacent gaps share the same root, and separating them would produce three records resting on one missing fact:

- **Nothing verifies the installation worked.** The onboarding flow ([AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import)) steps from "install connector" to "run in observe mode" with no gate between. A connector wired for the input stage but not the output stage emits no signal at all: the output-stage decisions simply never arrive, and **absence of evaluation is indistinguishable from absence of traffic**. This is the same reasoning the audit design already applies — an evaluation producing no record is indistinguishable from one that never happened — applied at the connector boundary.
- **Nothing defines what happens when a bound workload stops calling.** Under [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) a cooperative connector is always removable; that is the accepted posture, not a defect. But [`TECHNICAL-RISKS.md` §4.1](../TECHNICAL-RISKS.md) lists connector-health telemetry as the *first* mitigation for connector bypass — and telemetry can only report on a connector that is running and calling. The detection question that cooperative posture can actually answer is currently unowned.

The question this ADR resolves: **how does the platform know which connector a workload runs, that it was installed correctly, and that it is still there?**

## Decision

### 1. Compatibility validation is necessarily two-phase

The single requirement in FR-016 and FR-075 conflates two checks that happen at different times against different facts. This ADR separates them and states that **neither alone satisfies the requirement**:

| Phase | When | Validates | On failure |
| --- | --- | --- | --- |
| **Declared compatibility** | Binding creation or policy promotion | The policy version's requirements against the connector version range and runtime capabilities recorded for the workload | **Refuse the binding.** No manifest is generated |
| **Observed compatibility** | Every evaluation | The connector identity actually presented against what the binding expects | See §3 |

Bind-time validation is a check on *intent*, and it is the only check possible before a connector exists. Runtime validation is a check on *reality*, and it is the only one that can detect a team that installed something other than what was agreed.

### 2. Connector identity travels on the evaluation request

Every evaluation request carries a connector identity: **connector type, connector version, and the evaluation contract major version the client speaks.**

This is an **optional** request field in the current contract major, which makes it a minor change under [ADR-0005](./ADR-0005-evaluation-contract-wire-format.md) §2 rather than a breaking one. A workload whose client does not present identity is recorded as `unknown` and reported as such — not treated as compatible. Making it required would break every existing client on the day it lands, for a field whose purpose is diagnostic.

The identity is **self-reported and therefore not a security control**. It is diagnostic and operational: it answers "what is actually deployed," which nothing currently can. Under [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) and [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) it must never be cited as evidence that a workload's guardrails cannot be bypassed — a client that would lie about its version would equally decline to call at all.

### 3. An observed mismatch is graded, not uniformly fatal

Two different mismatches carry different consequences, and treating them alike would either under-react or cause outages:

- **Contract major mismatch** — the client speaks a contract version the runtime does not serve. The runtime **refuses the evaluation** with an error response, and the client's configured failure behaviour applies ([AGR-012](../../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy)). Serving a request whose shape is not understood is how silently wrong decisions are produced.
- **Connector version outside the expected range, contract major intact** — the evaluation **proceeds**, and the mismatch is recorded on the workload and reported to both the workload owner and the platform operator. Blocking here would convert a metadata drift into a production outage, which is a worse failure than the drift.

### 4. A workload is not onboarded until its installation is verified

A bound workload occupies an explicit **`pending verification`** state, distinct from `observing`, until the platform has observed at least one evaluation **at every stage the bound policy declares**.

This is what makes the half-wired connector detectable. A policy declaring `input` and `output` whose workload has only ever produced `input` evaluations is not a quiet success; it is an incomplete installation, and it is visible as one.

Verification status is reported to the workload owner and is a precondition for promotion out of Observe ([AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)): a workload whose output stage was never confirmed must not be promoted to enforce, because the enforcement it would gain does not exist on that stage.

### 5. A bound workload that stops evaluating is marked stale

A workload with an active binding that has produced no evaluations for a configured interval is marked **stale** and reported. The interval is per-workload configuration, because a batch workload evaluating weekly and an interactive workload evaluating continuously cannot share a threshold.

Three constraints on how this may be described, all load-bearing:

- **Stale means "no evaluations observed," never "bypassed."** The platform cannot distinguish a removed connector from a workload with no traffic, and must not claim to. [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) forbids treating this signal as evidence of enforcement.
- The signal is **reported, not enforced**. Nothing about staleness blocks, disables, or degrades anything.
- Resolution is an **attestation by the workload owner**, not an inference by the platform.

### 6. Decommissioning is an explicit action

A workload may be explicitly decommissioned, retiring its bindings and stopping staleness reporting.

Without this, every legitimately retired workload becomes a permanent stale alert, and within a few months the staleness signal is noise that operators filter out — destroying the one detection capability cooperative posture actually provides. **An offboarding path is a prerequisite for the liveness signal being useful, not a convenience.**

Decommissioning is recorded, not deleted: the workload, its bindings, and its audit history remain queryable, consistent with the append-only treatment bindings already receive.

## Scope boundaries

This ADR does **not** decide:

- **How long a connector version is supported**, or when an out-of-range connector stops being tolerated. That is a product-governance question with no architectural answer; tracked as [OQ-022](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-022--connector-version-support-window-and-deprecation-policy).
- **How the connector is obtained or verified for integrity** — see [ADR-0008](./ADR-0008-connector-distribution-and-integrity.md).
- **Numeric staleness thresholds or verification timeouts.** No source-defined figure exists; these are per-workload configuration with values set during pilot operation.
- **Whether enforcement posture should change.** [OQ-001](../../00-source-of-truth/OPEN-QUESTIONS.md#oq-001--enforcement-model-required-for-the-first-external-customers) is resolved by [DEC-010](../../00-source-of-truth/DECISIONS.md#dec-010--serve-ai-guardrails-as-a-developer-service-with-mode-a-as-the-front-door); nothing here alters it.

## Alternatives considered

- **Alternative: infer the connector version from behaviour** — which fields it sends, which endpoints it calls.
  **Why not chosen:** Fragile, silently wrong on any client that happens to match an older shape, and impossible to report to a workload owner as a fact. A diagnostic signal that cannot be stated plainly is not usable in an operational conversation.

- **Alternative: require connector identity on every request from the first release.**
  **Why not chosen:** A required request field is a **major** contract change under ADR-0005 §2, breaking every client for a diagnostic field. Optional-and-reported achieves the same operational visibility without a version bump, and `unknown` is itself an actionable state.

- **Alternative: block evaluation on any connector-version mismatch.**
  **Why not chosen:** Converts a metadata drift into a production outage. A connector one patch behind its expected range is not a correctness hazard; a contract-major mismatch is, and §3 blocks precisely that case and no more.

- **Alternative: treat prolonged silence as a bypass and alert on it as a security event.**
  **Why not chosen:** The platform cannot distinguish silence from absence of traffic, and [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) forbids the claim. Reporting it as a security event would also be self-defeating: without §6's decommissioning path, retired workloads would flood the channel until it was ignored.

- **Alternative (chosen): optional self-reported identity on the request, two-phase compatibility validation, an explicit verification state gating promotion, a reported staleness signal, and an explicit decommissioning action.**
  **Why accepted:** Makes FR-016 and FR-075's connector half implementable for the first time; makes a half-installed connector visible instead of silently partial; and gives the cooperative posture the one thing it can honestly offer — detection — without overclaiming it as prevention.

## Consequences

### Positive

- FR-006, FR-016, and FR-075 become implementable on the connector side. Their two-phase nature is now stated rather than discovered during implementation.
- A partially wired connector — the most likely and least visible installation error — becomes a reported state instead of a silent gap in coverage.
- [`TECHNICAL-RISKS.md` §4.1](../TECHNICAL-RISKS.md)'s first mitigation gains substance: connector-health telemetry now has a companion signal covering the case where the connector is not running at all, which telemetry structurally cannot report.
- Promotion out of Observe gains a factual precondition, strengthening [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) with evidence rather than review discipline.
- A retired workload stops looking like an incident.

### Negative

- The workload entity gains lifecycle state (`pending verification`, `observing`, `stale`, `decommissioned`) that must be modelled, transitioned, and surfaced — more control-plane surface in Phase 2 than a registry alone.
- Self-reported identity is unverifiable, so the platform holds a field it must repeatedly caveat in operator and sales contexts.
- Per-workload staleness intervals are configuration nobody has data to set well until pilot workloads have run.

### Risks

- **The staleness signal is cited as bypass detection** in sales or compliance material, contradicting [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) and [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model). This is the most likely misuse; mitigated by an explicit entry in [`CLAIMS-REGISTER.md`](../../70-governance/CLAIMS-REGISTER.md) rather than prose here.
- **Verification is treated as a one-time gate**, so a connector that later loses its output-stage wiring is never re-detected. Verification state must be able to regress, not only advance.
- **Alert fatigue defeats §5** if decommissioning is not implemented alongside it. These two decisions must ship together or neither is worth shipping.

## Validation required

This ADR is validated when:

1. A binding whose policy requires a capability or contract major the workload's recorded connector cannot serve is refused at creation, with no manifest generated.
2. An evaluation presenting an unserved contract major is refused, and the client's configured failure behaviour is observed to apply.
3. An evaluation presenting an out-of-range connector version but a valid contract major succeeds, and the mismatch appears in the workload's reported state.
4. A workload whose client exercises only the input stage of a two-stage policy remains in `pending verification` and cannot be promoted to enforce.
5. A bound workload silent beyond its configured interval is reported stale, and an explicitly decommissioned workload is not.

## Related agreements

- [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) — identity must describe any client, not only LiteLLM.
- [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) — the identity field is additive and does not fork the contract.
- [AGR-011](../../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) — verification becomes a precondition for promotion.
- [AGR-018](../../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [CON-012](../../00-source-of-truth/CONSTRAINTS.md#con-012--no-exclusive-reliance-on-weak-security-proxies) — none of these signals is evidence of enforcement.
- [AGR-019](../../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) — verification is the missing gate between "install connector" and "run in observe mode."

## Related documents

- [ADR-0006 — Integration manifest format and contents](./ADR-0006-integration-manifest-format-and-contents.md)
- [ADR-0008 — Connector distribution and integrity](./ADR-0008-connector-distribution-and-integrity.md)
- [Integration Architecture](../INTEGRATION-ARCHITECTURE.md) — §4.4, §4.5
- [Technical Risks](../TECHNICAL-RISKS.md) — §4.1 connector bypass
- [Operability](../OPERABILITY.md) — compatibility validation, connector-health telemetry
