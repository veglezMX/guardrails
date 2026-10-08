# Implementation Estimate

## Basis and status

This estimate covers the accepted local proof in [RELEASE-DEFINITION.md](../../solution-docs/60-delivery/RELEASE-DEFINITION.md), implemented by this plan. It estimates remaining implementation from commit `eb68dd9`; planning has not built or accepted the runtime described here. The working boundary and architecture govern the estimate (see architecture.md). Current dependency candidates are researched, not compatibility-tested (see tech-stack.md).

**Remaining engineering effort: 28–43 engineer-days, plus 7–11 engineer-days of risk allowance: 35–54 engineer-days total.** An engineer-day means one allocated working day of implementation, engineering tests, review and integration; it is not eight uninterrupted coding hours. The allowance is explicit rather than additional feature scope.

The user confirmed **four full-time engineers, QA and a scrum master**. The schedule assumes one QA person is available full time when needed and the scrum master provides shared facilitation; their exact allocations were not separately specified. QA execution and independent verification require an estimated **5–8 person-days**, separate from engineering effort. Engineering owns automated test and harness construction already counted below; QA does not duplicate those implementation days. Scrum-master effort is not estimated as engineering or QA.

The conditional planning window is **12–18 working days, approximately 3–4 calendar weeks**, from implementation kickoff with the four engineers available, overlapping QA, a working development host, and the Phase 1 compatibility slice passing without an architecture replacement. This is a dependency-based forecast, not a promised completion date. No calendar start date was agreed. Reforecast after the first real LiteLLM/TLS/mock-provider slice passes.

The direct estimate includes local CA/TLS setup, tenant-bound management roles, complete supported-message inspection, generation pinning across a model turn, a bounded process worker with enforceable deadlines, and authorized content-retention/audit paths. These are planned implementation, not contingency.

## Evidence used

On 2026-10-08, `PYTHONDONTWRITEBYTECODE=1 just test` passed all **67 tests** in the isolated plan worktree at the above baseline. The suite took approximately 2.6 seconds on the inspection host. This supports reuse of the core, not release acceptance: the existing end-to-end class constructs `GuardedInvocation` against an in-process HTTP runtime and does not start LiteLLM ([test_connector.py](../../mvp/tests/test_connector.py)).

Read-only reproductions also confirmed:

| Reproduction | Observed result | Work that remains |
| --- | --- | --- |
| Parse a policy with `audit_behavior.retain_content` equal to the string `"false"` | Retention becomes `True` | Strict boolean validation and rejection evidence |
| Assign a new value into a parsed policy's `failure_behavior` mapping | Assignment succeeds | Deeply immutable published data, defensive copies, durable publication constraints |
| Redact `0123456789ABCDE` with spans `(0, 10)` and `(5, 15)` | Result is `[REDACTED]ABCDE` | Validate and merge overlapping spans before transformation |

These reproductions changed no repository files. The source locations are `mvp/guardrails/policy.py` (`parse_policy`, `Policy`, `EvaluatorSelection`) and `mvp/guardrails/engine.py` (`_redact`). The audit-failure and fail-open gaps below are supported by source and incomplete existing assertions; their fixes require regression tests, not merely preservation of the passing suite.

## Component inventory against release acceptance

The row numbers refer to the ten rows of the accepted release matrix. No row is currently accepted in full.

| Release rows | Existing foundation and evidence | Remaining implementation |
| --- | --- | --- |
| 1, 10 | Standalone `serve` command and local deployment JSON; [README](../../mvp/README.md) describes hand-authored control-plane output | Pinned Compose stack, real gateways A/B, mock HTTP model, TLS bootstrap, health/readiness, offline execution, reusable acceptance report |
| 2 | [app.py](../../mvp/guardrails/app.py) handler exposes evaluation and capabilities only; [cli.py](../../mvp/guardrails/cli.py) exposes serve/validate/capabilities only | Durable management API and API-backed CLI for the complete lifecycle and inspection |
| 3 | [intake.py](../../mvp/guardrails/intake.py) binds bearer credentials to a workload and rejects body identity mismatch; [store.py](../../mvp/guardrails/store.py) rejects changed republishing into the in-memory cache | Generated credential lifecycle, hashed credential storage, tenant/role authorization, durable immutable publication, mutation resistance |
| 4 | [engine.py](../../mvp/guardrails/engine.py) normalizes actions and reasons; current vendor-equivalence tests use a fake Bedrock client | Real two-gateway comparison with equal policy generation, evaluator settings and state; compare all supported transformed text segments |
| 5 | [pii.py](../../mvp/guardrails/evaluators/pii.py) contains local email/phone regex detectors | Exact documented ASCII email and `+` international phone formats, labeled negatives, Unicode surroundings, span safety and deterministic transformations |
| 6 | Engine state ceilings and core connector model-call tests exist | Observe-only creation, explicit audited state changes, real gateway Warn evidence, all supported messages, model-call spy and unsupported-surface rejection |
| 7 | `PolicyCache` resolves a single binding; `load_runtime` loads deployment files once | SQLite publication/history, mandatory minimums for one effective version, periodic full snapshots, atomic application, per-turn generation pinning, applied-generation barrier and rollback |
| 8 | Failure vocabulary and injected exception cases exist | Actual timeouts, fail-open model continuation, safe transformation validation, management outage/cache evidence, non-weakening audit failure, separately visible connector fallback |
| 9 | [audit.py](../../mvp/guardrails/audit.py) emits policy/action/reasons/state/correlation and aggregate timing | Strict retention type, tenant-scoped query API/CLI, management events, separate runtime audit store, complete stack log privacy assertions |

Specific source repairs are material to the estimate: `GuardedInvocation.run` returns before inference on input `allow_and_log`; its output fallback can return the initially empty result. The LiteLLM hook inspects only the last user message and first choice, performs synchronous HTTP inside async hooks, and has no tested current LiteLLM registration. `DecisionEngine._audit_or_substitute` can replace an obtained restrictive action with an allow fallback. `_run_selection` catches a timeout exception but does not itself enforce a deadline. Existing tests do not establish the required outcomes for these cases.

Aggregate evaluation timing already supports the accepted local timing requirement. Additional production metrics, per-detector dashboards or hosted operational guarantees are not silently added to this estimate; scope remains defined in architecture.md.

## Work packages

Every range includes the package's engineering tests, review and documentation updates. The final integration package covers cross-component evidence, not a second allocation of unit-test construction. Dependencies refer to usable interfaces and incremental slices, not to waiting for every predecessor package to finish.

| ID | Remaining work | Engineer-days | Dependencies and exit evidence |
| --- | --- | ---: | --- |
| W1 | Lock the tested compatibility baseline; strict shared models, API contract and problem responses; prove one current LiteLLM hook path and HTTPS trust path | 3–4 | Start first. Exit: serialized contracts and minimal real gateway → runtime → mock slice work with candidate pins; final lock records exact artifacts. |
| W2 | Management API and its own SQLite database: tenant-bound actors/workloads, credential issuance, immutable publication, single binding, mandatory minimums, explicit promotion/history and rollback | 6–9 | Begins on W1's first contract slice. Exit: lifecycle and negative authority tests survive service restart. |
| W3 | Runtime snapshot consumer, atomic cache application and applied-generation reporting; management outage behavior; workload identity refresh; input-created evaluation contexts and output generation pinning | 3–5 | Begins when snapshot/context shapes in W1 exist; integrate against W2. Exit: 1-second polling, 5-second wait barrier, concurrent update and 120-second context expiry tests pass. |
| W4 | API-backed lifecycle/inspection CLI, credential handling, safe machine-readable output and explicit wait behavior | 2–3 | Begins against W1 contracts; integrates with W2/W3. Exit: complete API/CLI parity and error/exit-code cases pass. |
| W5 | Local PII format fixtures and redaction safety; strict policy immutability/authorized retention; failure precedence, bounded process workers and enforceable deadlines, separate audit persistence/federated queries and privacy repairs | 5–8 | Uses W1 contracts; can run alongside W2/W3. Exit: targeted regressions and real I/O fault cases pass. |
| W6 | Real LiteLLM adapter for all supported text messages; two independent gateway configurations; mock model/spies; Compose, local CA/TLS, dependency/image preparation and isolated offline networking | 5–8 | Begins with W1 integration findings; integrates W3/W5. Exit: both gateways enforce input/output with verified TLS and unsupported requests are rejected before inference. |
| W7 | Repeatable acceptance orchestration, independently callable API/CLI scenarios, ten-row evidence manifest, fault injection, network/privacy checks, setup/runbook and clean rerun | 4–6 | Fixture/report skeleton starts early; final integration requires W2–W6. Exit: all ten rows pass in one fresh offline run and a repeat run. |
| | **Direct total** | **28–43** | |
| R | Risk allowance for actual compatibility fixes, integration defects and environment variation | **7–11** | Approximately one quarter of direct effort; consumed only by identified work and recorded in progress. |
| | **Engineering planning total** | **35–54** | |

## Four-engineer execution scenario

Use component ownership to limit conflicts while reviewing interfaces together. These are proposed assignments, not named staff commitments.

| Owner | Primary responsibility | Useful concurrent contribution |
| --- | --- | --- |
| Engineer 1 | W1 contracts and W2 management/storage | Publish minimal usable lifecycle and snapshot interfaces early; review authorization and migration behavior. |
| Engineer 2 | W3 runtime/context/cache and W5 detector/failure correctness | Review decision safety, audit precedence and content-retention paths. |
| Engineer 3 | W6 gateway/mock/TLS/Compose | Own the initial real LiteLLM compatibility slice with Engineer 1. |
| Engineer 4 | W4 CLI and W7 acceptance automation | Start corpus/report scaffolding immediately; integrate each service slice as it becomes available. |
| QA | Independent scenario review, setup execution, negative/edge cases, evidence inspection and retest | Work from the accepted matrix and contracts rather than reproducing implementation assertions. |
| Scrum master | Dependencies, access/readiness follow-up, review scheduling and impediment tracking | Protect availability of integration owners and QA at handoff points. |

| Overlapping working-day window | Deliverable and dependency milestone |
| --- | --- |
| Days 1–2 | Shared contract slice, first actual LiteLLM/TLS/mock feasibility result, fixture IDs and report skeleton. W1 continues with locks and contract checks. |
| Days 2–8 | Management/storage, snapshot/context runtime, detector repairs and gateway packaging progress concurrently. CLI develops against frozen request/response shapes. |
| Days 6–12 | Integrate registration → publication → Observe → evaluation; add promotion/update/rollback and generation wait. Run API and CLI workflows separately. |
| Days 10–15 | Full two-gateway acceptance, management/runtime/audit/transform faults, retention scans and offline rerun; QA validates setup and evidence. |
| Days 14–18 | Resolve integration defects from the explicit allowance, complete QA retest and produce the passing release evidence. Earlier completion is possible if the lower ranges hold. |

The dependency bottleneck is: usable contract and actual LiteLLM hook → management publication and valid runtime snapshot → gateway enforcement pinned to that snapshot → complete offline/fault evidence. W2, W3 and W6 must exchange functioning slices before they are individually finished. Starting W7 after all coding completes would lengthen this forecast.

Dividing 35–54 engineer-days by four gives only an **8.75–13.5 working-day capacity lower bound**. It ignores dependent work, review and shared integration. For a changed allocation, compute `capacity_days = remaining_engineer_days / sum(each_engineer_allocation_fraction)`, then schedule the dependency graph; the forecast must be at least the longer of capacity days and dependency duration, plus any non-overlapping QA or external wait. Adding people does not proportionally shorten the critical integration path.

## QA effort and acceptance ownership

The 5–8 QA person-days comprise 1–2 days reviewing the fixture/failure/authority matrix and independently reproducing setup, 2–3 days executing negative and offline scenarios while inspecting evidence and persisted logs, and 2–3 days retesting fixes and checking the final repeatable run. QA may use the harness but does not count its engineering implementation again. QA is assumed available within the same 12–18-day window; restricted QA availability is a calendar risk, not hidden engineering effort.

The technical owner accepts implementation only against all ten release rows. A documentation checker, passing prototype unit tests or a successful live-provider request cannot substitute for that result (see test-strategy.md).

## Risk allowance and reforecast triggers

The largest risks are current LiteLLM callback behavior and packaging, TLS/CA handling between all local services, off-network execution after installation, and consistency of policy generation while a model turn is in progress. Reserve also covers a defect found by the non-weakening failure matrix or content-retention scan.

Reforecast explicitly if any of the following occurs: W1 cannot establish a supported hook path with the selected version; the real stack needs a new infrastructure component; actor/governance rules require an additional workflow; the accepted surface expands beyond the contracted non-streaming text shape; the number or allocation of engineers changes; or offline execution requires a different supported host platform. Do not consume allowance to add AWS integration or other work absent from architecture.md.

Checkpoints: after W1's real integration result; after the first end-to-end lifecycle; and after the first complete acceptance run. At each, report remaining packages, actual effort consumed, concrete blockers, and reserve remaining. No runtime release is declared complete during this planning task.
