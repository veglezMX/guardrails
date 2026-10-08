**AI Guardrails — Current Release Definition**

**Authority level:** L2
**Status:** Defined — implementation acceptance pending
**Date:** 2026-10-08
**Derived from:** AGREEMENTS.md, AGR-022 through AGR-030; DEC-022, DEC-023, DEC-024

---

## Release statement

Deliver a local technical proof of AI Guardrails for internal application teams. Teams operate the complete workload and policy lifecycle through an API and CLI. Two independently configured real LiteLLM instances use one versioned policy model and a locally running guardrails service. A mock model is the default inference backend, and the proof runs without internet access after dependencies are installed. Sensitive-data detection and redaction must actually work locally on input prompts and output responses.

Acceptance is a repeatable automated demonstration that internal teams can run. AWS integration and exposing the solution as a service to other teams are subsequent milestones. Completing this proof does not establish hosted readiness, the Customer-Managed Runtime deployment mode, or general availability.

Sources: AGR-022 through AGR-030; [DEC-022](../00-source-of-truth/DECISIONS.md#dec-022--define-a-separately-accepted-local-technical-proof), [DEC-023](../00-source-of-truth/DECISIONS.md#dec-023--establish-the-local-proof-topology-and-guardrail-scenario), [DEC-024](../00-source-of-truth/DECISIONS.md#dec-024--close-the-local-release-definition-and-acceptance-workflow).

## Accepted boundary

| Dimension | Current release | Source |
| --- | --- | --- |
| Outcome | Separately accepted local technical proof | AGR-022 |
| Consumers | Internal application teams | AGR-023 |
| User experience | API and CLI; no browser interface required | AGR-024 |
| Gateway proof | Two independently configured real local LiteLLM instances | AGR-025 |
| Inference | Local mock model by default; live provider credentials are not required | AGR-026 |
| Connectivity | Offline-capable after installation; communication among local services is allowed | AGR-026 |
| Working guardrail | Local sensitive-data detection/redaction on input and output | AGR-027 |
| Lifecycle | Register, publish, bind, inspect, promote, update and roll back | AGR-028 |
| Policy adoption | One configurable immutable published version per workload; no multi-pack composition | AGR-029 |
| Acceptance | Repeatable automated two-gateway proof; actual adoption by two teams is not a prerequisite | AGR-030 |

The broader product remains standalone, with portable policies and a common evaluation contract (AGR-001, AGR-005, AGR-006, AGR-007). Observe-before-enforce, explicitly configured failure behavior, workload identity boundaries and no raw-content retention by default apply locally (AGR-011, AGR-012, AGR-014, AGR-018). DEC-011's mandatory-policy minimums govern configuration of the single effective version.

## What an internal team can do

1. Install the documented dependencies, start the local stack, and verify both gateways and the runtime are ready.
2. Register two workloads with distinct local workload identities and gateway configurations.
3. Publish an immutable PII policy version and bind it to both workloads in Observe mode.
4. Send known clean and sensitive-content fixtures through either gateway, and inspect decisions and audit metadata through the API or CLI.
5. Promote a binding through the supported enforcement states and demonstrate masking and blocking on input and output.
6. Publish a changed policy as a new version, move a binding to it, then roll back to the prior version without rebuilding the runtime or editing its internal state.
7. Run the automated acceptance command and inspect its individual pass/fail results.

Both API and CLI cover this lifecycle and enforce the same validation and governance rules (AGR-028). Exact endpoint paths, command names, service topology and local credential mechanics are engineering decisions; they must implement these outcomes.

## Working PII scenario and evidence limits

Under the delegated selection in AGR-027 and its clarification in DEC-024, the minimum fixture baseline is email addresses and international-format phone numbers. Implementation must document the exact supported formats and include labeled positive, negative and transformation cases for each. The detector runs locally, independently of the mock inference backend.

Observe leaves content unchanged while recording findings. Redact replaces detected sensitive spans before model invocation for input and before application delivery for output. Block prevents model invocation for rejected input and prevents unsafe model output from reaching the application. Disabled and Warn retain the behavior established by AGR-011 and the chosen evaluation contract. A failure to transform safely uses the explicitly configured failure behavior (AGR-012).

The mock model must support clean and sensitive canned responses so output enforcement is tested even when input has already been redacted. Known fixtures establish the listed behaviors and supported formats, not comprehensive PII coverage, real-world false-positive rates, semantic prompt-injection detection, harmful-content detection, or real model quality.

Existing rule and JSON-validation implementations may remain; they do not expand the required PII scenario implicitly (AGR-027). Text input/output is the current proof surface. Streaming enforcement and agentic stages retain their later-phase scope under DEC-016; unsupported request shapes must receive an explicit supported-surface error rather than appear guarded.

## Local acceptance matrix

All rows must pass in a single repeatable run. The harness reports each result, records the versions/configuration used, and exits unsuccessfully if any row fails. Passing unit tests alone does not satisfy this matrix (AGR-030).

| No. | Required outcome | Evidence | Sources |
| --- | --- | --- | --- |
| 1 | The documented local stack starts, with two real LiteLLM gateways, the guardrails runtime and a mock model; the acceptance run completes with external network access disabled after installation. | Startup/health output, pinned dependency and image versions, and an offline test run. | AGR-022, AGR-025, AGR-026 |
| 2 | Both API and CLI can register workloads, publish/validate policies, bind, inspect, promote and roll back without editing internal deployment state. | Lifecycle runs through each surface, including validation failures and effective-state readback. | AGR-024, AGR-028 |
| 3 | Each workload uses its own local identity; identities cannot assert another workload's binding; each binding names one immutable policy version. | Two positive identity cases and cross-workload rejection; published-version overwrite rejection. | AGR-017, AGR-018, AGR-025, AGR-029 |
| 4 | With the same content, policy version, evaluator configuration and enforcement state, both gateways receive equivalent decision action, reasons and transformed content. | Shared fixtures through both gateways; comparison excludes workload/correlation/audit identifiers and measured latency. | AGR-005, AGR-006, AGR-025 |
| 5 | Real local PII detection recognizes the documented email/phone fixtures and leaves labeled clean fixtures unchanged. | Explicit expected findings and transformed content for input and output, including clean cases. | AGR-027, AGR-030 |
| 6 | Disabled, Observe, Warn, Redact and Block behave according to the contract; new bindings start in Observe and promotions are explicit and audited. | State-by-state fixtures; model-call spy proves input blocks skip inference and input redaction happens before it; output checks prove unsafe text is withheld or transformed before delivery. | AGR-011, AGR-012, AGR-027, AGR-028 |
| 7 | Publishing an update creates a new immutable version; changing and rolling back a binding affects subsequent evaluations without a rebuild. Required policy minimums remain enforced. | Version-1/version-2 evaluation records, rollback to version 1, actor/event records, and refusal to weaken configured mandatory controls. | AGR-005, AGR-017, AGR-028, AGR-029; DEC-011 |
| 8 | Declared failure behavior is observed under evaluation timeout/unavailability, unresolved policy, policy-management unavailability, audit failure and unsafe transformation. | Fault-injection cases distinguish runtime decisions from connector fallbacks. Where a policy declares allow-and-log, the model workflow actually continues; where it declares block, unsafe delivery is prevented. | AGR-012, AGR-030 |
| 9 | Decision evidence can be inspected without retaining raw prompts/responses by default; policy version, action, reasons, state, correlation and evaluation timing remain visible. | Audit/readback assertions and inspection of persisted logs/records using synthetic sensitive fixtures; strict rejection of invalid retention settings. | AGR-014, AGR-017, AGR-028 |
| 10 | The proof is reusable by an internal team and reports its scope and limitations accurately. | Setup/API/CLI instructions, one automated acceptance command, fixture labels, detector-format limits, and an evidence report. | AGR-023, AGR-026, AGR-027, AGR-030 |

Promotions in this proof use synthetic fixtures. Later promotion of live workloads remains subject to DEC-012's observation, quality and sign-off gates.

Local latency is measured and reported; no production latency or availability guarantee is inferred. Fixture pass rates are not labeled as population-level false-positive rates. Later operational and detector-quality targets remain governed by the hosted release criteria.

## Deferred delivery and unchanged programme scope

| Subsequent delivery | Why it does not gate this proof | Governing source |
| --- | --- | --- |
| AWS deployment, live Bedrock integration, IAM/KMS and regional control evidence | AWS integration was explicitly deferred; local substitutes do not establish those capabilities | AGR-022; AGR-004, AGR-013 |
| Exposure as a service to additional teams | Initial adoption is internal and local; service launch follows | AGR-023 |
| Hosted Phase 1–2 completion and organizational adoption | This release accepts an automated technical demonstration separately | AGR-022, AGR-030; AGR-020 |
| Managed gateway, customer-managed distribution and GA | The product's three-mode and phased commitments remain; a local container is not evidence of completing those modes | AGR-002, AGR-020, AGR-022 |

No browser UI, commercial launch, production SLA, live inference service, or comprehensive guardrail-catalog coverage is required by the accepted local boundary. Future scope changes require a recorded decision.

## Definition status and remaining engineering work

The product interview is closed for this local release. OQ-021, OQ-023 and OQ-024 are resolved for the current boundary by AGR-025 through AGR-030 and DEC-023/DEC-024. This definition is settled; implementation acceptance is unverified.

The derived [technical implementation plan](../../plans/local-technical-proof/index.md) now specifies the API/CLI contracts, local authentication, policy distribution and rollback, PII formats, deployment packaging, prototype repairs and acceptance gates. Its [remaining-effort estimate](../../plans/local-technical-proof/implementation-estimate.md) assumes the user-confirmed four full-time engineers, QA and scrum-master support. Dependency versions are researched candidates until the first real integration gate proves them. The plan implements this boundary; runtime acceptance remains pending and product choices are not reopened.

The existing [programme milestones](MILESTONES.md), [hosted release criteria](RELEASE-CRITERIA.md) and [validation plan](VALIDATION-PLAN.md) retain their later obligations. The circulated [local-first proposal](../../assets/AI-Guardrails-Local-First-MVP-Plan.docx) and [task survey](../../mvp/TASKS.md) predate this definition. Their cloud-dependent sign-off and effort estimates are historical for this release. The replacement engineering forecast is in the technical plan; it is a planning range, not a committed delivery date or acceptance result.

## Interview record

| Round | Date | Decisions | Record |
| --- | --- | --- | --- |
| 1 | 2026-10-08 | Local proof; AWS deferred; internal teams; later service exposure; API and CLI | AGR-022 through AGR-024; DEC-022 |
| 2 | 2026-10-08 | Two real gateways; offline-capable mock model; locally working PII scenario under delegated selection | AGR-025 through AGR-027; DEC-023 |
| 3 | 2026-10-08 | Full lifecycle; one published policy version per workload; automated demonstration sufficient | AGR-028 through AGR-030; DEC-024 |
