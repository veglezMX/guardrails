# Quality Gates

## Status and gate rules

The proof commands and profile runner are implementation targets. They are not available merely because this plan names them. Phase 1 creates their entry points; later phases add the specified test inventory. Current documentation validation and the inspected 67-test prototype suite do not establish release acceptance.

Every gate returns nonzero on a failed assertion, missing prerequisite, uncollected required test, skipped required case or missing evidence. A selected profile has a fixed case inventory in source; it may not silently discover only the tests that happen to exist. Unknown profile names are errors. Optional developer filtering cannot be used as recorded gate evidence. A zero-test selection fails.

`just proof-unit`, `just proof-contract` and `just proof-integration` each accept an optional single profile name, default `all`. The profile list below is closed. Profile selection is an implementation staging mechanism; `all` and `proof-acceptance` require every applicable case, including the earlier profiles. Test case definitions and artifact meanings are authoritative in test-strategy.md; these gates schedule and enforce them.

## Named profiles

| Command profile | Required inventory and boundary | Earliest completion |
| --- | --- | --- |
| `just proof-unit foundation` | Immutable core values, strict scalar/unknown-key parsing, canonical serialization, safe decision identity and basic transformation rule tests | Phase 1 |
| `just proof-contract foundation` | Shared request/response/Problem model fixtures, generated OpenAPI projection consistency, smoke gateway request/metadata and TLS settings contracts | Phase 1 |
| `just proof-integration smoke` | Actual pinned gateway → HTTPS runtime → HTTPS mock path; one clean, one input-block, one input-redaction and one output-redaction case, native hook registration and invalid TLS trust rejection | Phase 1 |
| `just proof-unit management` | Transaction rules, credential/role boundaries, publication equality, single-binding governance/floors and snapshot serialization | Phase 2 |
| `just proof-contract management` | Management endpoint validation/status/authority/optimistic-concurrency cases, scoped audit-source errors and operation semantics; CLI parity remains in `all` | Phase 2 |
| `just proof-integration management` | Real management HTTPS and SQLite lifecycle/restart, failed mutation/audit transaction, immutable publication, pending generation and correctly authenticated test acknowledgement | Phase 2 |
| `just proof-unit runtime` | Detector format/span/failure matrix, bounded execution, immutable cache, age limits and turn-context cases | Phase 3 |
| `just proof-contract runtime` | Evaluation/client-config/client-event/internal-audit projections, workload/context ownership and unsupported payload rejections | Phase 3 |
| `just proof-integration runtime` | Actual management/runtime databases and TLS, snapshot apply/outage/restart, real worker deadline/queue recovery, retained-content authority, audit non-weakening and pinned-generation cases | Phase 3 |
| `just proof-unit` | Every required unit case, including connector/CLI helper behavior and all earlier profiles | Phase 4 |
| `just proof-contract` | Every contract case, including full CLI parity, complete gateway surface, fallback identity and all earlier profiles | Phase 4 |
| `just proof-integration` | Every integration case through actual gateways and local services, including API/CLI lifecycle, state/failure/privacy/offline/TLS/spool replay and all earlier profiles | Phase 4 |

The smoke profile uses explicit nonpersistent test configuration to isolate the native gateway integration risk before the management plane is built. A later management profile may submit a test runtime acknowledgement to test the endpoint's authorization and state semantics. Neither mechanism counts as real runtime application or any completed release row; the runtime/all profiles replace those boundaries with the real services.

## Phase gates

| Phase | Exact commands | Required reviewable result |
| --- | --- | --- |
| 1 | `just proof-bootstrap`; `just proof-unit foundation`; `just proof-contract foundation`; `just proof-integration smoke` | Prepared artifact manifest plus passing defined foundation/smoke inventories; compatibility result is empirical rather than a version listing. |
| 2 | `just proof-unit management`; `just proof-contract management`; `just proof-integration management` | Durable direct-API lifecycle and all authority/transaction/immutable-version rejection evidence. |
| 3 | `just proof-unit runtime`; `just proof-contract runtime`; `just proof-integration runtime` | Actual snapshot, context, process deadline, fault-precedence and separate audit-store evidence. |
| 4 | `just proof-unit`; `just proof-contract`; `just proof-integration`; `just test` | Full real-gateway/API/CLI regression result; no required case omitted. |
| 5 | `just proof-acceptance`; `just proof-down`; `just proof-reset`; `just proof-acceptance`; `just verify` | Two distinct preserved ten-row reports from clean runs, QA evidence and passing documentation validation. |

These are separate commands, listed in required order where one depends on state from the previous command. `proof-reset` is explicit destructive reset of disposable proof state only and preserves exported reports. A failed acceptance run retains its logs/state for inspection; run `proof-down` and reset only after preserving its useful evidence.

## Release evidence gate

`proof-acceptance` creates a fresh run ID, asserts network isolation, records the exact source/artifacts/settings, and runs all ten release rows. It must report each row independently and fail if any required case is absent, inconclusive or failed. Positive evidence includes both gateway identities, separate API and CLI lifecycles, the labeled PII corpus, state/model-call assertions, generation updates/rollback, every declared failure path and persisted-data privacy checks (see test-strategy.md §Acceptance matrix traceability).

The report records real service versions and digests, prepared lock hashes, source revision, effective test configuration and overrides, runtime/connector generations, fixture IDs, safe outcome summaries, timings, evidence locations and overall status. Never copy raw traffic, secrets, private keys or raw exception values into reports. Fixture-based results establish fixture coverage only.

If a gate fails, keep its result, repair the cause, rerun the affected gate, then rerun dependent gates whose inputs or behavior changed. Do not repeat unrelated suites solely to increase a test count. The final two acceptance runs occur after the last relevant fix and use the same resolved artifact set. A source/configuration/artifact change invalidates dependent earlier evidence.

## Review responsibilities

Engineering reviews model/contract changes across both management and gateway owners before implementation diverges. Review specifically checks that body/error/log handling cannot copy raw input by default, generation changes cannot mix a model turn, and audit failure cannot weaken a computed restriction. This is targeted review of documented behavior, not a separate production-security certification.

QA independently checks setup instructions, fixture labels, state and failure expectations, default privacy, repeated runs and the artifact manifest. Record a pass/fail observation and defect reference for each executed QA case. Technical acceptance requires all ten rows and resolved defects affecting those rows; product boundaries remain those in architecture.md.

## Planning checks versus implementation checks

During this planning task, use the implementation-plan skill checker and `just verify` to validate the documents, their required structure and repository-generated references. The loaded checker is `/home/veglez/.agents/skills/implementation-plan/scripts/check_plan.py`; invoke it with the final plan directory argument. A successful checker run says the plan is structurally consistent, not that its future service behavior works.

Runtime implementation begins from Phase 1 with zero completed implementation sessions. Only implemented, collected and passed proof gates can advance those phase statuses. AWS/Bedrock checks and production operational targets are not local-release gates (see architecture.md §Scope Boundaries).
