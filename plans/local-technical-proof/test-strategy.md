# Test Strategy

## Framework and runners

Use `pytest` for new proof suites with the dependency pin in tech-stack.md. Retain the existing `unittest` suite as baseline regression evidence; new proof suites run through the proposed `just` recipes below. Domain models and wire fixtures use only the shapes in data-schema.md and input-output-contract.md. Acceptance behavior derives from component-specs.md and the ten rows in [RELEASE-DEFINITION.md](../../solution-docs/60-delivery/RELEASE-DEFINITION.md).

**Current evidence:** `PYTHONDONTWRITEBYTECODE=1 just test` passed all 67 baseline tests on 2026-10-08 at `eb68dd9`. The existing integration tests exercise `GuardedInvocation` and a local HTTP runtime, not LiteLLM. Planning did not implement the future commands or prove the proposed version combination. `just verify` and the plan checker validate documentation only; their success is not runtime acceptance.

## Test layers

| Layer | Test type and location | What it proves |
| --- | --- | --- |
| Policy, detector, action and failure rules | Unit, `mvp/tests/unit/` | Strict validation, immutable values, exact supported PII formats, span transformation, state ceilings and non-weakening failure precedence |
| Runtime cache and evaluation context | Unit plus integration | Atomic snapshots, identity/context binding, generation pinning, expiry and absence of a management lookup on the evaluation path |
| API and CLI | Contract, `mvp/tests/contract/` | Strict request/response models, problem responses, authorization, supported surfaces, equivalent validation and CLI exit behavior |
| Persistence and service boundaries | Integration, `mvp/tests/integration/` | Real SQLite files/restarts, HTTPS calls, snapshot polling, actual deadlines and durable actor/evaluation evidence |
| Gateways and model path | Integration through two real LiteLLM processes | Before/after enforcement across all supported text segments; no inference on rejected input and no unsafe delivery on rejected output |
| Complete local release | Acceptance, `mvp/tests/acceptance/` | All ten outcomes pass in one repeatable offline run with a complete manifest and no raw-content retention by default |

Test safety-relevant transformations, authorization, lifecycle decisions and fault paths directly. Do not add tests that merely reproduce a configuration value or assert internal helper calls where externally visible behavior supplies stronger evidence.

## Test locations

These paths are implementation targets, listed here for traceability and in folder-structure.md for ownership.

| File | Required test responsibilities |
| --- | --- |
| `mvp/tests/unit/test_policy_integrity.py` | Strict retention boolean; deep immutability; stable canonical content; changed-version publication rejection; mandatory minimum comparison |
| `mvp/tests/unit/test_pii_formats.py` | Exact supported positive/negative PII forms; independent per-segment offsets; overlapping/touching/invalid spans; safe redaction |
| `mvp/tests/unit/test_failure_precedence.py` | Existing block/redaction survives permissive audit fallback; actual continue/block behavior; unsafe transformation follows its declaration |
| `mvp/tests/unit/test_snapshot_cache.py` | Atomic apply, invalid generation rejection, complete referential consistency and last-known-valid snapshot |
| `mvp/tests/unit/test_deadline_executor.py` | Actual stalled process timeout, bounded admission, queued expiry, worker termination/replacement and healthy recovery |
| `mvp/tests/unit/test_turn_context.py` | Input-created context, workload/stage binding, pinned generation, expiry and malformed/unknown context handling |
| `mvp/tests/contract/test_api_validation.py` | Every documented rejecting case in input-output-contract.md; strict scalar types and supported endpoint response shapes |
| `mvp/tests/contract/test_authorization.py` | Security-role/workload-owner authority, tenant scope, credential ownership and cross-workload denial |
| `mvp/tests/contract/test_cli_api_parity.py` | Lifecycle equivalence, same rejected mutations, machine-readable output, safe credential output and nonzero failure exits |
| `mvp/tests/contract/test_gateway_surface.py` | Accepted roles/string messages and one output; explicit pre-inference rejection of unsupported options and shapes |
| `mvp/tests/integration/test_management_lifecycle.py` | Register, validate/publish, bind, inspect, promote, update and rollback through real HTTP; actor history and mandatory floors |
| `mvp/tests/integration/test_snapshot_distribution.py` | Polling/apply barrier, policy-management outage, corrupt snapshot rejection and concurrent update during model turn |
| `mvp/tests/integration/test_sqlite_restart.py` | Management state and separate runtime audit store survive restart; no shared SQLite writer; integrity after interrupted publication |
| `mvp/tests/integration/test_litellm_gateways.py` | Two real independent gateway configurations; model spy; all states and faults; correlation and context propagation |
| `mvp/tests/integration/test_offline_stack.py` | Prepared artifacts start with external egress disabled, local TLS trust works, and logs/volumes retain no raw fixture content by default |
| `mvp/tests/acceptance/test_release_matrix.py` | Orchestrate one run, account for every matrix row, create manifest/result report, fail on missing evidence and rerun cleanly |

## Mocking boundary

Unit tests may inject clocks, evaluator failure ports, audit sinks and transport ports to enumerate deterministic cases. They must exercise the real policy parser, role/rule logic, state/action precedence, immutable data models and transformation implementation.

Integration and acceptance use real FastAPI services, real SQLite files, actual HTTPS with the local CA, two real LiteLLM instances, the real connector and real local PII detector. The **model** is deliberately mocked through its local HTTP endpoint. The mock supplies canned clean or sensitive responses and a bounded-delay mode; it does not perform guardrail detection or decide whether an assertion passes.

Acceptance must not replace either gateway or the runtime with an in-process fake. Fault injection may interrupt/delay boundary services or use a fault-enabled test adapter; it must drive the real connector/runtime decision path. Test-only fault controls are enabled only in the acceptance configuration and are not ordinary workload API privileges. A raised fake timeout alone is insufficient: at least one test uses an actual delayed request and checks elapsed time, the resulting action and model/delivery behavior. Stall a real evaluator worker; prove the deadline terminates/replaces it, queue capacity remains bounded and a later healthy job succeeds. Exercise queue-wait expiry and audit SQLite lock time against the same stage budget: evaluator cutoff at 400 ms, at most 100 ms reserved for audit, bounded asynchronous worker cleanup. Assert decision completion within 500 ms plus an explicit 100 ms test-scheduling tolerance on the supported local host, report observed elapsed time, and treat excess as a failure requiring investigation; this tolerance is not a production SLA.

## Fixtures and evidence privacy

| Fixture | Contents and purpose |
| --- | --- |
| `mvp/tests/fixtures/pii-cases.json` | Stable case IDs, synthetic input/output segments, supported format labels, expected finding types/spans/actions and expected safe transformation; includes positive and clean-negative cases |
| `mvp/tests/fixtures/lifecycle-cases.json` | Two tenant-bound actors/workloads and separate gateway identities, immutable policy versions, state transitions, floor constraints and API/CLI expectations |
| `mvp/tests/fixtures/failure-cases.json` | Failure location, declared behavior, state, starting decision and expected model/delivery/evidence result for each fault case |

Fixtures follow the contract models (see data-schema.md). They use synthetic addresses/numbers only. The fixture corpus is versioned test input; it is not runtime-retained traffic. The evidence report stores fixture IDs, configuration hashes, safe assertion names/results and metadata, not raw prompts, responses, credentials or generated private keys.

The mock model's input spy is in memory, bounded to the acceptance run, and supports an assertion that input redaction reached the model. It must not persist raw request bodies to logs, evidence files or audit databases. Retention scans cover generated application logs, gateway/model logs, both SQLite stores and files in generated runtime/evidence volumes. Run explicit-retention cases in a separate disposable store so they cannot contaminate the default-retention privacy scan. Separately prove only a security administrator can opt in to content retention, default queries omit retained content, and an explicitly authorized content query records its actor/event without copying the content into management audit. Static corpus source files are excluded from that scan because they are inputs, not retained observations; state this exclusion in the report.

PII format boundaries are the exact documented ASCII email subset and `+` followed by 8–15 ASCII digits for the international phone baseline. Test minimum/maximum lengths, invalid lengths, surrounding punctuation, Unicode surrounding text, adjacent detections, clean numeric identifiers and text in every accepted message role. Supported coverage is a fixture claim; the pass rate is not a population-level false-positive estimate.

## Required regressions and cross-component cases

### Publication, authority and lifecycle

Run the full lifecycle once through direct API requests and once through CLI commands against isolated test workloads. Neither may edit SQLite, deployment JSON or runtime internals. Both must reject the same invalid retention values, policy changes under an existing version, unsupported capabilities and unauthorized floor weakening.

Use two valid workload credentials and prove each works only for its own binding. Security-role and workload-owner credentials must enforce the permissions and tenant boundaries in the contracts. Include owner attempts to weaken mandatory controls, a wrong-tenant lookup/mutation and reuse of a workload credential for management operations. New bindings start in Observe; any conflict with configured mandatory constraints is handled by the specified validation, not implicit promotion.

A published version survives restart unchanged. Binding updates and rollback produce actor/event history and affect subsequent model turns after the applied-generation barrier. Existing turns remain pinned to the earlier version. A rollback is a new audited binding generation, not rewriting published content or erasing history.

### Runtime snapshot and turn consistency

Prove 1-second full-snapshot polling without placing a management HTTP/database lookup in the evaluation request. A valid applied generation becomes observable within the CLI's 5-second wait deadline under the normal local fixture load; expiration of that wait returns failure and the requested/current generation, rather than claiming success.

With a delayed mock response, create input under generation A, apply generation B, and evaluate output using the input's `EvaluationContext`: output must still use A. A new turn then uses B. Test the 120-second context TTL with an injected clock at unit level; test a live context at integration level. Wrong-workload, unknown, expired or invalid contexts follow the specified rejecting/failure behavior; they must never silently select the latest policy.

During management unavailability, valid cached policy/identity data continues serving according to the contract and readiness exposes the relevant state. Invalid partial snapshots do not replace the complete active snapshot. A cold runtime without a usable snapshot does not invent a policy or claim ready. Exercise the configured maximum snapshot age and revocation lag; lower its 60-second default only through the explicit fault-test profile, then record the override in evidence.

### Input, output and supported gateway surface

Send equivalent fixtures through independently configured gateways A and B with equal policy/evaluator configuration and enforcement state. Compare action, reason codes and transformed segments. Exclude workload/correlation/audit/context identifiers and measured latency. Identity and audit records remain distinct.

Cover string content for every accepted `system`, `developer`, `user` and `assistant` message in one request, including sensitive text outside the last user message. Preserve segment identity and order through transformations. Cover Disabled, Observe, Warn, Redact and Block with both clean and sensitive input/output. A model-call spy proves input blocks skip inference; redacted input contains no original matched spans when received by the model. Independently canned sensitive output proves output enforcement even after input has been redacted.

Reject streaming, `n` other than 1, multipart content, unsupported roles, tool/function options or calls, and unsupported response shapes explicitly. Rejected input requests must not reach the model. An unsupported output must not be returned as though it were inspected text. Warn must be distinguishable from Observe in the contracted visible evidence. All invocation paths use the same enforcement/fallback rules.

### Fault matrix

For every allowed declaration of the following fault, assert the action, content delivered or withheld, model invocation count, failure origin, reason and evidence destination. Exercise both input and output where the fault can occur in either stage; apply state-ceiling behavior exactly as the contracts specify.

| Fault | How it is driven | Essential evidence |
| --- | --- | --- |
| Runtime unavailable or timed out | Stop/unroute the service or delay beyond the connector deadline | Connector fallback is distinct from a runtime decision; `allow_and_log` actually calls the model/returns the available permitted response; block prevents unsafe progress |
| Evaluator unavailable or timed out | Failed evaluator port and real delay through its execution boundary | Declared runtime path, elapsed bounded deadline, remaining-detector behavior where applicable |
| Unresolved policy/context | Missing valid binding/snapshot or invalid/expired turn context through documented inputs | Explicit decision/error/fallback with no invented policy version and no silent latest-version substitution |
| Policy-management unavailable | Stop management after applying a valid snapshot; separately test cold start | Cached evaluation and pending changes are distinguished; no claimed applied generation without receipt |
| Runtime audit storage failure | Fail its dedicated store without failing management storage | Existing block or safe redaction is never weakened by a permissive audit declaration; failure remains visible in returned metadata and safe operational evidence; connector spool replay deduplicates without content |
| Unsafe transformation | Invalid/non-localizable spans or inconsistent transformed segments | Declared transform-failed behavior, no unredacted unsafe delivery through an implicit downgrade |

Regression tests must cover the verified overlap example, mutable policy dictionaries and the string `"false"` retention case. Add the missing fail-open assertion that the model was called with the permitted original input, and that output fail-open returns the permitted model response rather than an empty initialized result. Preserve a prior restrictive content decision when a later audit operation fails.

## Acceptance matrix traceability

All rows are required within the same acceptance run. A skipped, unimplemented or missing row fails the run.

| Release row | Automated evidence source | Pass condition |
| --- | --- | --- |
| 1 | Offline-stack integration plus acceptance manifest | Prepared pinned services start healthy with two actual gateway processes; all acceptance requests run with external network disabled. |
| 2 | Management lifecycle and CLI/API parity suites | Both public surfaces complete registration, validation/publication, binding, inspection, promotion and rollback; validation failures are demonstrated. |
| 3 | Authorization, policy-integrity and lifecycle suites | Two positive identities; cross-workload/tenant rejection; one version per binding; changed overwrite and illegal mandatory weakening rejected. |
| 4 | Two-gateway comparison | Equal fixtures/config/state produce equivalent action, reasons and transformed segments while distinct identifiers remain distinct. |
| 5 | PII corpus through both gateways | All documented positive/negative and input/output transformation expectations pass for the exact format baseline. |
| 6 | State matrix and model spy | All five states conform; Observe creation and explicit promotion are audited; blocked input never invokes; unsafe output never escapes. |
| 7 | Lifecycle, restart and snapshot/context suites | New immutable version, live binding update and rollback are observable without rebuild; generations and actor events are recorded; minimums persist. |
| 8 | Fault matrix through real boundaries | Every declared fault behavior is observed and labeled correctly, including actual fail-open continuation and non-weakening audit failure. |
| 9 | Audit query/parity, retention validation and stack scan | Required metadata/timing can be inspected; invalid retention is rejected; default persistence contains no raw traffic or secrets. |
| 10 | Fresh setup and repeat-run orchestration with QA review | Instructions reproduce the proof; one command emits ten individual results, fixture/config/version manifest and accurate coverage limitations. |

## Offline and TLS evidence

`proof-bootstrap` may access registries and package sources to prepare the selected dependency locks, images and local CA. After preparation, integration/acceptance use only the local service network, trust that CA and never disable certificate verification. Negative TLS cases reject an untrusted certificate or hostname mismatch. Published host ports follow the loopback binding contract.

Run acceptance in an environment that has no external egress for the participating containers and runner, while preserving required local service traffic. Record the effective Compose/network configuration. A controlled egress probe must demonstrate a connection is blocked; use a reachable sentinel outside the isolated stack network and verify its reachability from the supervising test process so an absent external server is not misreported as network isolation. Do not contact a live model or AWS as acceptance evidence. No dependency download is allowed in the acceptance phase.

Inspect gateway telemetry, update checks, optional callbacks and mock/runtime logging settings before the offline run. Successful behavior despite hidden retry timeouts is insufficient if the stack still attempts external dependencies or the report cannot account for them. The evidence records network checks and versions, not packet payloads containing traffic.

## Coverage standard

No numeric line-coverage percentage is a release gate. Every component's documented logic, safety-relevant edge case and rejecting validation rule has a test at the lowest meaningful layer, and every release matrix row has real integrated evidence. The requirements-to-test matrix above is enforced by the acceptance runner's complete row accounting and exit status. Unit tests cannot replace real gateway, SQLite, TLS, timeout or offline tests.

There are no exception-based waivers for missing release rows. Existing tests may be adapted when contracts intentionally change, but only after equivalent or stronger behavior assertions exist and the change is recorded in the implementation session log. Imported version lists and plan validation are not compatibility evidence.

## Commands and gates

The `proof-*` commands below are **planned implementation targets**, not commands already available in the baseline. Their implementation belongs to the build work. `progress.md` assigns them to phases. The unit/contract/integration recipes accept one optional profile argument; exact profile membership and missing-test rules are defined in quality-gates.md. Only the bootstrap gate may require internet access.

| Command | Required behavior and pass/fail purpose |
| --- | --- |
| `just proof-bootstrap` | Prepare exact dependencies/images, local certificates and disposable test configuration; validate pins and trust setup. Fail if required artifacts are unavailable. |
| `just proof-unit` | Run new deterministic rule/cache/context regression suites; fail on any assertion. |
| `just proof-contract` | Validate API/OpenAPI models, CLI parity, authority and supported gateway-shape cases; fail on schema drift or a rejected-case mismatch. |
| `just proof-integration` | Start/use real prepared services and databases; run TLS/lifecycle/snapshot/two-gateway/fault/offline integration cases; fail on unavailable prerequisites or skipped required cases. |
| `just proof-acceptance` | Create an isolated run, execute all ten release rows offline, emit a safe manifest/results report and return nonzero if any row or required evidence fails. |
| `just proof-down` | Stop the proof stack and retain evidence/state for inspection; report cleanup errors. |
| `just proof-reset` | Explicitly reset only the disposable proof project state for a clean rerun; preserve named exported evidence reports and never delete unrelated local data. |
| `just test` | Existing prototype regression suite until superseded by equivalent proof assertions. |
| `just verify` | Existing repository documentation validation; does not establish runtime release acceptance. |

The plan checker validates the fixed plan document set and cross-references. Run it during planning and after contract changes using the absolute checker path from the loaded implementation-plan skill. Its exit code is a documentation gate only. Final implementation acceptance requires `proof-acceptance` plus the reviewable evidence, regardless of planning-checker success.
