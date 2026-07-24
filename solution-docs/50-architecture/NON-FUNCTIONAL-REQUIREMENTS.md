**AI Guardrails — Non-Functional Requirements**

**Authority level:** L2
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

## Purpose and authority

This document specifies measurable non-functional requirements (NFRs) for the AI Guardrails runtime, control plane, and connectors, across all three deployment modes (Remote Evaluation, Guarded Gateway, Customer-Managed Runtime).

This is an **L2 — derived** document. It operationalizes, but never overrides, the L1 authoritative material in [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) and [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md). Where a constraint (`CON-###`) already states a hard boundary, the corresponding NFR here restates it as a measurable target, adds how it is validated, and identifies at which delivery stage that validation occurs. Where seed.md does not provide a concrete number, the target is marked **TBD** rather than invented, per source-of-truth policy — no metrics, SLAs, or performance numbers appear here that are not present in or directly inferable from `seed.md`.

Each requirement below follows a fixed shape:

```text
## NFR-<CATEGORY>-NNN — <short name>
Requirement:      the measurable statement, with a concrete target or TBD
**Measurement:**       how the target is observed or computed
**Validation stage:**  when/where this is checked (delivery phase, review gate, or ongoing operation)
**Source:**            seed.md section and/or AGR-/CON- reference
```

Categories are ordered: Availability, Reliability, Recovery, Performance, Scalability, Security, Privacy, Auditability, Maintainability, Portability, Operability, Cost, Compliance. Every category contains at least one requirement, per the scope of this document.

---

## Table of contents

- [Availability](#availability)
- [Reliability](#reliability)
- [Recovery](#recovery)
- [Performance](#performance)
- [Scalability](#scalability)
- [Security](#security)
- [Privacy](#privacy)
- [Auditability](#auditability)
- [Maintainability](#maintainability)
- [Portability](#portability)
- [Operability](#operability)
- [Cost](#cost)
- [Compliance](#compliance)
- [Summary table](#summary-table)
- [Operability failure-path diagram](#operability-failure-path-diagram)
- [Related documents](#related-documents)

---

## Availability

seed.md §15 requires the guardrail runtime to define an availability objective and the operating behaviors that support it; success metrics in seed.md §19 name "Evaluation API availability" as a tracked reliability metric.

### NFR-AVAIL-001 — Evaluation API availability objective

Requirement: The hosted Evaluation API (Remote Evaluation and Guarded Gateway modes) must have a documented, monitored availability objective. The specific percentage is **TBD** — no target figure exists in the source material — and must be set and published before general availability.

**Measurement:** Uptime monitoring against the published objective, computed over a rolling window (window length TBD); reported as a success metric per seed.md §19 ("Evaluation API availability").
**Validation stage:** Objective defined during Phase 2 (Internal multi-team platform); measured continuously in production thereafter.
**Source:** seed.md §15 (Operational requirements — Availability: "Availability objective"), §19 (Reliability metrics); AGR-002.

### NFR-AVAIL-002 — Degraded-mode continuity

Requirement: When the control plane, a detector, or policy resolution is unavailable, the runtime must continue operating in a documented degraded mode rather than failing with undefined behavior. 100% of policies must declare an explicit degraded-mode behavior before they can be promoted out of Observe.

**Measurement:** Policy schema validation rejects any policy version lacking a failure-behavior/degraded-mode section; audit sampling confirms the declared behavior was the one actually taken during an observed outage.
**Validation stage:** Enforced at policy authoring/validation time (Phase 2 onward); exercised via chaos/failure-injection testing before promotion of any mode to general availability.
**Source:** seed.md §5.7 (Failure behavior is part of policy), §15 (Availability: "Degraded operating mode"); AGR-012, AGR-015.

---

## Reliability

seed.md §19 names four reliability metrics the platform must track; this section restates them as measurable NFRs.

### NFR-REL-001 — Evaluation timeout rate

Requirement: The proportion of evaluation requests that exceed their stage-specific timeout must be measured continuously. The acceptable ceiling is **TBD** pending timeout-value definition (see CON-016); until then, any non-zero, unexpected upward trend must trigger investigation.

**Measurement:** `timed_out_evaluations / total_evaluations` per stage, per detector, per workload, emitted as an operational metric.
**Validation stage:** Instrumented from Phase 1 (Integration proof); ceiling formalized once stage timeouts are finalized (Phase 2).
**Source:** seed.md §15 (Performance: "Timeout rate"), §19 (Reliability: "Evaluation timeout rate"); CON-016.

### NFR-REL-002 — Policy-resolution failure rate

Requirement: The rate at which a workload's effective policy cannot be resolved (version not found, incompatible runtime, cache miss with no fallback) must be measured and kept as close to zero as achievable; a specific ceiling is **TBD**.

**Measurement:** `policy_resolution_failures / policy_resolution_attempts`, tracked per workload and per deployment mode.
**Validation stage:** Phase 2 onward, as policy bindings and rollout controls come online; monitored continuously in production.
**Source:** seed.md §19 (Reliability: "Policy-resolution failure rate"); AGR-006, AGR-015.

### NFR-REL-003 — Audit delivery success rate

Requirement: The proportion of evaluations for which an audit record is successfully produced and delivered to the audit pipeline must be measured; a specific target is **TBD**, but audit-delivery failure must always be a defined, non-silent failure-behavior path (see NFR-AUDIT-002).

**Measurement:** `audit_records_delivered / evaluations_performed`, reported per tenant and per deployment mode.
**Validation stage:** Phase 1 (basic auditing) for instrumentation; Phase 2 for SLA-style tracking against a target once set.
**Source:** seed.md §15 (Auditing), §19 (Reliability: "Audit delivery success rate"); AGR-012.

---

## Recovery

seed.md does not provide numeric RTO/RPO figures; recovery requirements here are derived from the policy-rollback, control-plane-independence, and circuit-breaker behaviors specified in seed.md §5.6, §5.7, §15, and the MVP demonstration criteria in §16/AGR-020.

### NFR-REC-001 — Immediate policy rollback

Requirement: A promoted policy version must be reversible to its prior version through an explicit, auditable rollback action. The time required to complete a rollback is **TBD**, but rollback must not require a new deployment of runtime software — only a policy-version change.

**Measurement:** Time from rollback initiation to the prior version becoming effective for all bound workloads, measured in a controlled test; verified as part of the MVP demonstration ("a policy can be upgraded and rolled back").
**Validation stage:** Phase 1/2 (policy promotion and rollback tooling); re-verified at every subsequent policy-rollout release.
**Source:** seed.md §15 (Policy rollout: "Immediate rollback"); AGR-020.

### NFR-REC-002 — Customer-managed runtime reconnection recovery

Requirement: A Customer-Managed Runtime that has been operating disconnected from the hosted control plane must, upon reconnection, resume asynchronous policy synchronization without requiring a restart, without evaluation-path downtime, and without loss of locally buffered audit records.

**Measurement:** Controlled disconnect/reconnect test measuring (a) evaluation availability during the outage window, (b) successful resumption of policy sync, (c) completeness of audit records produced during the outage once flushed.
**Validation stage:** Phase 4 (Customer-managed runtime) delivery and acceptance testing.
**Source:** seed.md §3 (Mode C: "must be capable of executing policies without requiring a synchronous connection to the hosted control plane"); AGR-015.

### NFR-REC-003 — Circuit-breaker reset

Requirement: When a circuit breaker trips around an unavailable detector or dependency, the runtime must attempt automatic recovery (reset/half-open probing) rather than requiring manual intervention to restore normal operation. Specific probe intervals and thresholds are **TBD**.

**Measurement:** Time from underlying dependency recovery to circuit closing and normal evaluation resuming, measured in failure-injection tests.
**Validation stage:** Phase 2 onward, as part of runtime resiliency hardening; re-verified before each mode's general availability.
**Source:** seed.md §15 (Availability: "Circuit-breaker behavior"); AGR-012.

---

## Performance

seed.md §15 requires the platform to measure P50/P95/P99 evaluation latency, throughput, timeout rate, detector-specific latency, decision distribution, and policy-cache hit rate. No numeric SLA figures are present in the source material, so all targets below are marked TBD; what is fixed is that each metric must be measured, reported, and gated at defined stages.

### NFR-PERF-001 — P50 evaluation latency

Requirement: Median end-to-end evaluation latency (request received by the Evaluation API to decision returned) must be measured per stage (input/output) and per deployment mode. Target value is **TBD**.

**Measurement:** P50 of evaluation-latency distribution, computed continuously and reported per workload, per stage, per mode.
**Validation stage:** Instrumented from Phase 1; target thresholds set once representative load data exists (Phase 2).
**Source:** seed.md §15 (Performance: "P50 evaluation latency"), §19 ("P95 evaluation latency" as reliability metric implies the full percentile family is tracked).

### NFR-PERF-002 — P95 evaluation latency

Requirement: 95th-percentile evaluation latency must be measured per stage and per deployment mode. Target value is **TBD**.

**Measurement:** P95 of the same latency distribution as NFR-PERF-001.
**Validation stage:** Instrumented from Phase 1; used as the primary reliability-facing latency metric per seed.md §19.
**Source:** seed.md §15 (Performance: "P95 evaluation latency"), §19 (Reliability: "P95 evaluation latency"); AGR-006.

### NFR-PERF-003 — P99 evaluation latency

Requirement: 99th-percentile evaluation latency must be measured per stage and per deployment mode, to surface tail-latency behavior not visible at P50/P95. Target value is **TBD**.

**Measurement:** P99 of the same latency distribution.
**Validation stage:** Instrumented from Phase 1; reviewed at each load test and before general availability of each mode.
**Source:** seed.md §15 (Performance: "P99 evaluation latency").

### NFR-PERF-004 — Evaluation throughput

Requirement: The runtime must sustain the expected throughput declared per workload (a workload attribute in the domain model) without degrading latency beyond the targets above. Numeric throughput ceilings are **TBD**.

**Measurement:** Sustained evaluations-per-second under load test, compared against each workload's declared "Expected throughput" attribute.
**Validation stage:** Load testing prior to onboarding a workload whose expected throughput exceeds previously validated levels; re-validated at Phase 2/3 scale-up.
**Source:** seed.md §9 (Workload: "Expected throughput"), §15 (Performance: "Evaluation throughput").

### NFR-PERF-005 — Timeout rate as a performance signal

Requirement: Timeout rate must be tracked as a first-class performance metric, distinct from the reliability ceiling in NFR-REL-001, so that latency regressions are visible before they cross the timeout boundary.

**Measurement:** Same computation as NFR-REL-001, reviewed alongside latency percentiles in the same dashboard/report.
**Validation stage:** Continuous, from Phase 1 onward.
**Source:** seed.md §15 (Performance: "Timeout rate").

### NFR-PERF-006 — Detector-specific latency

Requirement: Latency must be measured per individual evaluator/detector (e.g., Bedrock Guardrails adapter, deterministic rules engine, PII evaluator), not only as an aggregate evaluation latency, so that a single slow detector can be identified and isolated.

**Measurement:** Per-detector latency histogram, attributed by evaluator identity in the decision/audit metadata.
**Validation stage:** Phase 1 (Bedrock adapter, deterministic evaluator) onward; required input to the "apply only relevant evaluators" latency mitigation in seed.md §20.
**Source:** seed.md §15 (Performance: "Detector-specific latency"), §20 (Key risks — Latency mitigation: "Measure per-evaluator latency").

### NFR-PERF-007 — Policy-cache hit rate

Requirement: The proportion of policy-resolution lookups served from a local/warm cache versus a cold lookup against the control plane or policy store must be measured. Target hit rate is **TBD**, but the metric must be visible per deployment mode, since Customer-Managed Runtime depends on the cache for control-plane-independent execution.

**Measurement:** `cache_hits / policy_resolution_attempts`, reported per mode; correlated with NFR-REL-002 (policy-resolution failure rate).
**Validation stage:** Phase 4 (local policy cache, policy synchronization) for Customer-Managed Runtime; Phase 2 onward for hosted modes.
**Source:** seed.md §15 (Performance: "Policy-cache hit rate"); AGR-015.

---

## Scalability

seed.md does not provide numeric capacity ceilings. Scalability requirements are derived from the domain model's "Expected throughput" workload attribute, the "Runtime scaling" and "capacity management" ownership items, and Mode C's stated fit for high-volume workloads.

### NFR-SCALE-001 — Horizontal scaling of the evaluation runtime

Requirement: The Evaluation Runtime must scale horizontally to accommodate a growing number of tenants, workloads, and aggregate evaluation volume without requiring architectural redesign. Specific capacity ceilings are **TBD**.

**Measurement:** Load tests that add tenants/workloads incrementally while holding per-workload latency targets (Performance section) constant; capacity headroom reported to product operators as "Runtime capacity management" telemetry.
**Validation stage:** Phase 2 (Internal multi-team platform) onward, as multiple tenants and workloads are onboarded.
**Source:** seed.md §7 (Product operators need "Runtime capacity management"), §3 (Mode A: provider owns "Runtime scaling").

### NFR-SCALE-002 — High-volume and independent-scale support in Customer-Managed Runtime

Requirement: The Customer-Managed Runtime packaging must support workloads explicitly characterized as high-volume, scaling independently of the hosted control plane and of other tenants' deployments.

**Measurement:** Deployment-mode-specific load test executed against the customer-managed runtime container/Helm/ECS artifacts, independent of hosted-mode infrastructure.
**Validation stage:** Phase 4 (Customer-managed runtime) delivery.
**Source:** seed.md §3 (Mode C: "intended for... High-volume workloads").

---

## Security

Security NFRs restate the CON-008 through CON-016 boundaries from `CONSTRAINTS.md` as measurable, verifiable targets. Where a constraint is already the boundary statement, this section adds measurement method and validation stage rather than re-deriving rationale.

### NFR-SEC-001 — TLS coverage

Requirement: 100% of runtime communication paths (application–connector, connector–Evaluation API, runtime–evaluation engines, runtime–control plane) must use TLS; zero unencrypted paths are permitted.

**Measurement:** Automated network/configuration scan confirming TLS termination on every listener; flagged as a release-blocking finding if any path is found in plaintext.
**Validation stage:** Security review at every release; continuous scanning in production.
**Source:** seed.md §14 (Security requirements: "TLS for all runtime communication"); CON-008.

### NFR-SEC-002 — IAM-based, short-lived, least-privilege authentication

Requirement: 100% of AWS-facing consumers must authenticate via IAM using short-lived credentials; zero long-lived static credentials may be used for runtime authentication.

**Measurement:** IAM policy and credential-lifetime audit; CI check rejecting infrastructure-as-code that provisions long-lived access keys for runtime roles.
**Validation stage:** Security review at Phase 2 (IAM authentication delivery) and at every subsequent release.
**Source:** seed.md §14 (Security requirements: "IAM-based authentication for AWS consumers," "Short-lived credentials," "Least-privilege runtime roles"); CON-010.

### NFR-SEC-003 — Rate limiting per workload identity

Requirement: The Evaluation API must enforce rate limits scoped to per-tenant workload identity. Specific numeric limits are **TBD**.

**Measurement:** Load test confirming requests beyond the configured limit are rejected with a defined response rather than degrading shared capacity; limits reviewed as part of usage-quota configuration (Phase 3, Guarded Gateway).
**Validation stage:** Phase 2 onward; formalized as part of Phase 3 "Cost and rate controls."
**Source:** seed.md §14 (Security requirements: "Rate limiting"); CON-011.

### NFR-SEC-004 — Input-size limits

Requirement: Every evaluation stage must enforce an input-size ceiling; no unbounded input path is permitted. Specific byte/character ceilings are **TBD** per stage.

**Measurement:** Automated test submitting oversized payloads per stage and confirming rejection with a defined error rather than resource exhaustion.
**Validation stage:** Defined before general availability of each stage; re-verified at every evaluator-adapter addition.
**Source:** seed.md §14 (Security requirements: "Input-size limits"); CON-015.

### NFR-SEC-005 — Audit-event integrity

Requirement: Audit events must be protected against undetected tampering between production and archival; 100% of audit records must carry integrity metadata sufficient to detect modification.

**Measurement:** Verification job confirming archived audit records match their integrity metadata (e.g., checksum/hash chain) on a sampling basis.
**Validation stage:** Phase 1 (basic auditing) for record structure; Phase 2 for integrity-verification tooling.
**Source:** seed.md §14 (Security requirements: "Audit-event integrity").

### NFR-SEC-006 — Dependency and container scanning

Requirement: 100% of runtime container images and declared dependencies must be scanned for known vulnerabilities before deployment; deployments with unresolved critical findings must be blocked pending an explicit exception.

**Measurement:** CI/CD scan gate results, tracked per release and per deployment artifact (including customer-managed packaging: container, Helm chart, ECS template).
**Validation stage:** Every build, from Phase 1 onward; re-verified for each new deployment artifact added in Phase 4.
**Source:** seed.md §14 (Security requirements: "Dependency and container scanning").

---

## Privacy

Privacy NFRs restate the default-behavior requirements in seed.md §13 (no raw retention by default, hashed identifiers, secret removal) as measurable targets, aligned with CON-013/CON-014 and AGR-014/AGR-016.

### NFR-PRIV-001 — No raw content retention by default

Requirement: 0% of raw prompts and responses are persisted by the hosted runtime unless a tenant has explicitly configured retention. Decision metadata is persisted for 100% of evaluations regardless of retention configuration.

**Measurement:** Storage audit confirming no raw-content fields exist in default-configuration persistence stores; audit metadata field "whether raw content was retained" checked against actual tenant configuration on a sampling basis.
**Validation stage:** Data-architecture review at Phase 1/2; re-verified at every schema change to storage or audit records.
**Source:** seed.md §13 (Default behavior: "Avoid persisting raw prompts and responses by default," "Persist decision metadata"), §15 (Auditing: "Whether raw content was retained"); AGR-014, CON-014.

### NFR-PRIV-002 — Identifier hashing

Requirement: User and conversation identifiers must be hashed before persistence wherever technically possible; the percentage of identifier fields hashed is targeted at 100% of fields where hashing is feasible without breaking required correlation.

**Measurement:** Schema review confirming identifier fields in persisted records are hashed, not raw; correlation-identifier design reviewed to confirm hashing does not break the required correlation-identifier function in the decision structure.
**Validation stage:** Data-architecture review at Phase 1/2.
**Source:** seed.md §13 (Default behavior: "Hash user and conversation identifiers where possible"); AGR-014.

### NFR-PRIV-003 — Secret removal from telemetry

Requirement: 100% of telemetry payloads emitted by the runtime must be free of secrets (credentials, API keys, tokens) by construction, not by post-hoc filtering alone.

**Measurement:** Telemetry schema/payload review confirming secret-bearing fields cannot be populated; automated secret-pattern scan on sampled telemetry as a secondary control.
**Validation stage:** Reviewed at Phase 1/2 telemetry design, and again whenever a new telemetry field is added.
**Source:** seed.md §13 (Default behavior: "Remove secrets from telemetry"); CON-009.

### NFR-PRIV-004 — Configurable customer-managed telemetry down to none

Requirement: Customer-Managed Runtime deployments must support telemetry configuration ranging from full sanctioned categories (health status, product version, policy version, aggregated evaluation counts, reason-code totals, latency metrics, sanitized decision metadata) down to a fully supported "no telemetry" setting, with raw content excluded from every configuration by construction.

**Measurement:** Configuration test matrix exercising each telemetry level, confirming payload contents match the selected level and that raw content cannot appear at any level.
**Validation stage:** Phase 4 (Customer-managed runtime) delivery and acceptance testing.
**Source:** seed.md §13 (Customer-managed telemetry); AGR-016, CON-013.

---

## Auditability

seed.md §15 enumerates the mandatory audit metadata fields; this section makes their completeness and delivery measurable.

### NFR-AUDIT-001 — Audit record field completeness

Requirement: 100% of audit records must contain all mandated fields: tenant, workload, policy version, evaluation stage, decision, reason codes, evaluator versions, timestamp, correlation identifier, and whether raw content was retained. No audit record may omit a mandated field.

**Measurement:** Schema validation on audit-record production; sampling-based completeness audit against the mandated field list.
**Validation stage:** Phase 1 (basic auditing) for initial fields; re-verified at every audit-schema change and before each mode's general availability.
**Source:** seed.md §15 (Auditing: full field list); AGR-006, AGR-017.

### NFR-AUDIT-002 — Defined behavior on audit-delivery failure

Requirement: 100% of workloads must have an explicit, non-silent failure behavior defined for the case where audit delivery fails (e.g., allow-and-log locally, block, buffer-and-retry); no workload may operate with an undefined audit-failure path.

**Measurement:** Policy/workload configuration review confirming an audit-failure behavior is set; failure-injection test confirming the configured behavior is actually taken when audit delivery is forced to fail.
**Validation stage:** Phase 1/2, as part of failure-behavior policy validation; re-verified at every policy-schema change.
**Source:** seed.md §5.7 (Failure behavior is part of policy: "Audit delivery fails"), §15 (Auditing); AGR-012.

---

## Maintainability

seed.md's adapter-isolation principles (§5.1) and policy-versioning rules (§15) are the basis for measurable maintainability targets.

### NFR-MAINT-001 — Vendor/framework isolation behind adapters

Requirement: 100% of LiteLLM-specific and Bedrock-Guardrails-specific logic must be isolated inside their respective connector/adapter; zero vendor-specific types, formats, or configuration structures may appear in the public policy schema or evaluation contract.

**Measurement:** Architecture-conformance check (static analysis or code review checklist) confirming no vendor-specific symbols cross the contract boundary; new evaluation engines or connectors added without modifying existing policies, as a functional test of isolation.
**Validation stage:** Reviewed at every connector/adapter addition; gated at Phase 1 (Bedrock adapter, LiteLLM connector) and re-verified at each subsequent connector (Phase 3+).
**Source:** seed.md §5.1 (Policy portability), §12 (LiteLLM integration: connector responsibilities); AGR-003, AGR-004, AGR-005.

### NFR-MAINT-002 — Immutable policy versions

Requirement: 100% of published policy versions are immutable; any change to a policy's checks, thresholds, or actions must produce a new version rather than mutating an existing one.

**Measurement:** Policy-store write-path check rejecting in-place modification of a published version; version-history audit confirming monotonic version creation.
**Validation stage:** Phase 1/2, as policy storage and rollout controls are delivered.
**Source:** seed.md §15 (Policy rollout: "Immutable versions").

---

## Portability

seed.md §19 (Product portability metrics) is the direct source for this section.

### NFR-PORT-001 — Deployment-mode-independent policy definitions

Requirement: The percentage of policy definitions that are deployment-mode independent (expressible and executable identically in Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime) is tracked as a product portability metric. Target percentage is **TBD**, with the design intent being as close to 100% as the evaluator ecosystem allows.

**Measurement:** Policy-catalog audit classifying each published policy as mode-independent or mode-restricted, with mode-restricted policies requiring an explicit justification.
**Validation stage:** Tracked from Phase 1 onward; reported as an ongoing product-portability metric per seed.md §19.
**Source:** seed.md §19 (Product portability: "Percentage of policy definitions that are deployment-mode independent"); AGR-006.

### NFR-PORT-002 — Shared runtime code across hosted and customer-managed modes

Requirement: The percentage of runtime code shared across hosted (Remote Evaluation/Guarded Gateway) and Customer-Managed Runtime deployments is tracked. Target percentage is **TBD**.

**Measurement:** Build/packaging analysis identifying shared modules versus mode-specific code, expressed as a percentage of total runtime code by an agreed unit (e.g., lines of code or module count).
**Validation stage:** Baseline established at Phase 4 (Customer-managed runtime packaging); re-measured at each subsequent release affecting the runtime core.
**Source:** seed.md §19 (Product portability: "Percentage of runtime code shared across hosted and customer-managed modes"); AGR-015.

### NFR-PORT-003 — Time to deploy a customer-managed runtime

Requirement: The elapsed time required to deploy a functioning Customer-Managed Runtime into a new customer AWS account is tracked as a portability/operability metric. Target duration is **TBD**.

**Measurement:** Timed deployment exercise using the supported artifacts (container, Helm chart, ECS deployment template, infrastructure module), measured from artifact acquisition to first successful evaluation.
**Validation stage:** Phase 4 delivery and acceptance testing; re-measured whenever deployment artifacts change materially.
**Source:** seed.md §19 (Product portability: "Time required to deploy a customer-managed runtime").

---

## Operability

seed.md §15 requires the runtime to define timeout, retry, cached-policy, degraded-mode, and circuit-breaker behavior. This section makes each one a discrete, verifiable requirement (see also the [operability failure-path diagram](#operability-failure-path-diagram) below).

### NFR-OPS-001 — Explicit, enforced timeout behavior

Requirement: Every evaluation stage must have an explicit timeout, and the behavior taken when that timeout is reached must be defined per workload (e.g., allow-and-log, safe fallback, block). No stage may operate with an undefined or unbounded timeout.

**Measurement:** Configuration audit confirming a timeout value and a mapped failure behavior exist for every stage/workload combination; failure-injection test confirming the defined behavior is what actually occurs.
**Validation stage:** Phase 1/2, as stage timeouts are finalized; re-verified at every new evaluation stage (future scope: retrieval, tool_call, etc.).
**Source:** seed.md §15 (Availability: "Timeout behavior"), §5.7 (Failure behavior is part of policy); CON-016, AGR-012.

### NFR-OPS-002 — Bounded retry behavior

Requirement: Retry behavior for evaluation calls must be explicit and bounded (maximum attempts, backoff); unbounded or undefined retry loops are not permitted, since they can stall the consuming application's request path indefinitely.

**Measurement:** Configuration review confirming a maximum retry count and backoff policy are set; load/failure test confirming retries terminate and hand off to the defined failure behavior once exhausted.
**Validation stage:** Phase 1/2, alongside timeout behavior.
**Source:** seed.md §15 (Availability: "Retry behavior"); AGR-012.

### NFR-OPS-003 — Cached-policy behavior

Requirement: The runtime must be able to serve the last-known-good policy version from a local cache when live policy resolution or the control plane is unavailable; the behavior when the cache itself is stale beyond an acceptable age must also be explicitly defined as part of failure behavior.

**Measurement:** Failure-injection test disconnecting the control plane and confirming the runtime continues evaluating using the cached policy; staleness-threshold test confirming the defined stale-cache behavior triggers correctly.
**Validation stage:** Phase 4 (local policy cache, policy synchronization) for Customer-Managed Runtime; Phase 2 for hosted-mode caching.
**Source:** seed.md §15 (Availability: "Cached-policy behavior"), §3 (Mode C: local policy cache); AGR-015, AGR-012.

### NFR-OPS-004 — Documented degraded operating mode

Requirement: Each workload must have an explicit degraded-mode definition describing runtime behavior when one or more dependencies (detector, control plane, audit pipeline) are unavailable, distinct from full outage and from normal operation.

**Measurement:** Policy/workload configuration review confirming a degraded-mode definition exists; scenario test exercising partial-dependency failure and confirming the documented degraded behavior is observed.
**Validation stage:** Phase 2 onward, as part of failure-behavior policy validation.
**Source:** seed.md §15 (Availability: "Degraded operating mode"); AGR-012.

### NFR-OPS-005 — Circuit-breaker behavior

Requirement: The runtime must implement circuit-breaker behavior around evaluators/detectors and the control-plane connection, tripping on sustained failure and reporting the tripped state as part of connector-health telemetry.

**Measurement:** Failure-injection test confirming the breaker trips after a defined failure threshold, that evaluation continues per the defined degraded/failure behavior while tripped, and that connector-health telemetry reflects the tripped state.
**Validation stage:** Phase 2 onward; re-verified before each mode's general availability.
**Source:** seed.md §15 (Availability: "Circuit-breaker behavior"), §12 (LiteLLM integration: "Emitting connector-health telemetry").

---

## Cost

seed.md does not provide dollar figures for the MVP; cost-related requirements here are qualitative or process-oriented, derived from the rate/quota and cost-control language in seed.md §3 and §18 (Phase 3).

### NFR-COST-001 — Cost model established before Guarded Gateway general availability

Requirement: A cost model covering runtime infrastructure cost and, for Guarded Gateway, model-provider inference cost, must be established before that mode reaches general availability. Specific dollar figures are **TBD** and out of scope for this document; see `../30-executive/INVESTMENT-SUMMARY.md` for financial detail where available.

**Measurement:** Existence and review sign-off of a documented cost model per deployment mode.
**Validation stage:** Phase 3 (Guarded Gateway: "Cost and rate controls").
**Source:** seed.md §18 (Phase 3 deliverables: "Cost and rate controls"), §3 (Mode A: consuming team owns "Model-provider costs"; Mode B: provider owns rate limits and usage quotas).

### NFR-COST-002 — Usage quotas and rate controls bound cost exposure

Requirement: The Guarded Gateway must enforce usage quotas and rate controls per tenant so that cost exposure from model-provider inference is bounded and predictable, independent of the specific dollar figures in NFR-COST-001.

**Measurement:** Configuration audit confirming quota/rate-control settings exist per tenant; load test confirming requests beyond quota are rejected or queued per the defined behavior rather than incurring unbounded inference cost.
**Validation stage:** Phase 3 (Guarded Gateway) delivery and acceptance testing.
**Source:** seed.md §18 (Phase 3 deliverables: "Cost and rate controls," "Managed credentials"); seed.md §14 (Security requirements: "Rate limiting," "Usage quotas" via §7 Product operators needs).

---

## Compliance

seed.md explicitly excludes "Full compliance certification" from the MVP (§6.3) while still requiring tenant-level retention configuration, regional documentation, and audit evidence — the compliance-relevant capabilities that must exist even though certification itself is out of scope.

### NFR-COMPL-001 — Tenant-specific retention configuration

Requirement: 100% of tenants must be able to configure their own content-retention behavior (within the platform's supported options), rather than being bound to a single global retention setting.

**Measurement:** Configuration schema review confirming retention is a per-tenant setting; audit sampling confirming actual retention behavior matches each tenant's configured setting.
**Validation stage:** Phase 1/2, alongside default no-retention behavior (NFR-PRIV-001).
**Source:** seed.md §9 (Tenant: "Retention configuration"), §13 (Default behavior: "Support tenant-specific retention configuration"); AGR-014.

### NFR-COMPL-002 — Full compliance certification explicitly out of MVP scope

Requirement: Full compliance certification (e.g., formal attestations or certifications) is explicitly not a deliverable of the MVP. This is a scope boundary, not a target to be silently pursued; any future certification effort requires an explicit scope-change decision.

**Measurement:** Not applicable as a measured target; tracked as a scope-boundary item in `../10-charter/SCOPE.md` and release criteria.
**Validation stage:** Confirmed at each MVP release-readiness review that no undocumented certification claim has been introduced into product or sales materials.
**Source:** seed.md §6.3 (Out of scope for the MVP: "Full compliance certification"); AGR-019.

### NFR-COMPL-003 — Regional processing boundary documented and enforced

Requirement: For every tenant, the AWS accounts and Regions that process that tenant's requests must be documented and, where the tenant has configured "Allowed Regions," enforced so that processing does not occur outside them.

**Measurement:** Configuration audit confirming allowed-Regions settings exist per tenant; request-routing trace confirming no evaluation traffic for a tenant is processed outside its configured Regions.
**Validation stage:** Phase 1/2, as part of the explicit content-exposure model.
**Source:** seed.md §9 (Tenant: "Allowed Regions"), §5.4 (Explicit content-exposure model: "Which AWS accounts and Regions process the request"); AGR-008, CON-007.

---

## Summary table

| ID | Requirement (short form) | Target | Source |
|---|---|---|---|
| NFR-AVAIL-001 | Evaluation API availability objective | TBD | seed §15, §19; AGR-002 |
| NFR-AVAIL-002 | Degraded-mode continuity declared per policy | 100% of promoted policies | seed §5.7, §15; AGR-012/015 |
| NFR-REL-001 | Evaluation timeout rate ceiling | TBD | seed §15, §19; CON-016 |
| NFR-REL-002 | Policy-resolution failure rate ceiling | TBD | seed §19; AGR-006/015 |
| NFR-REL-003 | Audit delivery success rate | TBD | seed §15, §19; AGR-012 |
| NFR-REC-001 | Policy rollback time | TBD | seed §15; AGR-020 |
| NFR-REC-002 | Control-plane reconnection recovery | No downtime / no audit loss | seed §3; AGR-015 |
| NFR-REC-003 | Circuit-breaker auto-reset | TBD interval | seed §15; AGR-012 |
| NFR-PERF-001 | P50 evaluation latency | TBD | seed §15 |
| NFR-PERF-002 | P95 evaluation latency | TBD | seed §15, §19 |
| NFR-PERF-003 | P99 evaluation latency | TBD | seed §15 |
| NFR-PERF-004 | Evaluation throughput | TBD, per workload | seed §9, §15 |
| NFR-PERF-005 | Timeout rate (performance signal) | TBD | seed §15 |
| NFR-PERF-006 | Detector-specific latency | Measured per evaluator | seed §15, §20 |
| NFR-PERF-007 | Policy-cache hit rate | TBD | seed §15; AGR-015 |
| NFR-SCALE-001 | Horizontal scaling of evaluation runtime | TBD capacity ceiling | seed §7, §3 |
| NFR-SCALE-002 | High-volume support in Customer-Managed Runtime | Qualitative | seed §3 |
| NFR-SEC-001 | TLS coverage | 100% | seed §14; CON-008 |
| NFR-SEC-002 | IAM-based, short-lived, least-privilege auth | 100% | seed §14; CON-010 |
| NFR-SEC-003 | Rate limiting per workload identity | TBD | seed §14; CON-011 |
| NFR-SEC-004 | Input-size limits per stage | TBD | seed §14; CON-015 |
| NFR-SEC-005 | Audit-event integrity | 100% of records | seed §14 |
| NFR-SEC-006 | Dependency/container scanning | 100% of builds | seed §14 |
| NFR-PRIV-001 | No raw content retention by default | 0% by default | seed §13; AGR-014/CON-014 |
| NFR-PRIV-002 | Identifier hashing | 100% where feasible | seed §13; AGR-014 |
| NFR-PRIV-003 | Secret removal from telemetry | 100% | seed §13; CON-009 |
| NFR-PRIV-004 | Configurable telemetry down to none (Mode C) | Full range supported | seed §13; AGR-016/CON-013 |
| NFR-AUDIT-001 | Audit record field completeness | 100% | seed §15; AGR-006/017 |
| NFR-AUDIT-002 | Defined behavior on audit-delivery failure | 100% of workloads | seed §5.7, §15; AGR-012 |
| NFR-MAINT-001 | Vendor/framework isolation behind adapters | 100% | seed §5.1, §12; AGR-003/004/005 |
| NFR-MAINT-002 | Immutable policy versions | 100% | seed §15 |
| NFR-PORT-001 | Deployment-mode-independent policy definitions | TBD %, goal ~100% | seed §19; AGR-006 |
| NFR-PORT-002 | Shared runtime code across modes | TBD % | seed §19; AGR-015 |
| NFR-PORT-003 | Time to deploy customer-managed runtime | TBD | seed §19 |
| NFR-OPS-001 | Explicit, enforced timeout behavior | 100% of stages | seed §15, §5.7; CON-016/AGR-012 |
| NFR-OPS-002 | Bounded retry behavior | 100% of stages | seed §15; AGR-012 |
| NFR-OPS-003 | Cached-policy behavior | Continues serving on outage | seed §15, §3; AGR-015/012 |
| NFR-OPS-004 | Documented degraded operating mode | 100% of workloads | seed §15; AGR-012 |
| NFR-OPS-005 | Circuit-breaker behavior | Trips + reports on failure | seed §15, §12 |
| NFR-COST-001 | Cost model before Guarded Gateway GA | TBD figures | seed §18, §3 |
| NFR-COST-002 | Usage quotas / rate controls bound cost | Enforced per tenant | seed §18, §14, §7 |
| NFR-COMPL-001 | Tenant-specific retention configuration | 100% of tenants | seed §9, §13; AGR-014 |
| NFR-COMPL-002 | Full compliance certification | Out of MVP scope | seed §6.3; AGR-019 |
| NFR-COMPL-003 | Regional processing boundary documented/enforced | 100% of tenants | seed §9, §5.4; AGR-008/CON-007 |

---

## Operability failure-path diagram

The diagram below shows how the five operability behaviors required by seed.md §15 (timeout, retry, cached-policy, degraded mode, circuit-breaker) compose along a single evaluation call, and where each corresponding NFR applies.

```mermaid
flowchart TD
    A[Evaluation request received] --> B{Detector/control-plane\nresponds in time?}
    B -- "Yes" --> C[Return normalized decision]
    B -- "No — NFR-OPS-001 timeout" --> D{Retry budget\nremaining?}
    D -- "Yes — NFR-OPS-002 retry" --> B
    D -- "No" --> E{Cached policy\navailable and fresh?}
    E -- "Yes — NFR-OPS-003 cached-policy" --> F[Evaluate against cached policy]
    F --> C
    E -- "No" --> G[Enter degraded mode\nNFR-OPS-004]
    G --> H{Failure threshold\nexceeded repeatedly?}
    H -- "Yes — NFR-OPS-005 circuit breaker" --> I[Trip breaker;\napply workload's\nconfigured failure\nbehavior — AGR-012]
    H -- "No" --> J[Apply workload's\nconfigured failure\nbehavior — AGR-012]
    I --> K[Emit connector-health\ntelemetry; audit the\nfailure path taken —\nNFR-AUDIT-002]
    J --> K
```

---

## Related documents

- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — L1 agreements this document operationalizes (notably AGR-006, AGR-012, AGR-014, AGR-015, AGR-016, AGR-020).
- [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — L1 boundary conditions (CON-001 through CON-016) that the Security, Privacy, and Compliance sections above restate as measurable targets.
- [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) — MVP scope boundaries referenced by NFR-COMPL-002 and the Cost section.
- [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) — product principles (portability, one contract, observe-before-enforce, explicit failure behavior) that motivate the Portability, Reliability, and Operability sections.
- `SECURITY-ARCHITECTURE.md` — implements the Security-section NFRs in concrete controls (planned).
- `DATA-ARCHITECTURE.md` — implements the Privacy- and Compliance-section NFRs (planned).
- `DEPLOYMENT-ARCHITECTURE.md` — implements the Availability, Scalability, and Recovery NFRs per deployment mode (planned).
- `OPERABILITY.md` — expands the Operability-section NFRs into concrete runbooks and dashboards (planned).
- `COMPONENT-ARCHITECTURE.md` — component-level design that must satisfy the Maintainability- and Portability-section NFRs (planned).
