# Quality Gates

## Trigger Condition

Correctness needs executable acceptance evidence across policy governance, multiple gateways, residency restrictions and deployment modes.

## Scope Boundary

These are local engineering acceptance gates, not hosted programme sign-off or production SLAs. Automated acceptance is sufficient. No named team must manually approve the release. The policy approval workflow itself is exercised using distinct authenticated principals.

## Gate Commands and Required Evidence

All evolution commands are planned recipes, introduced by Phase 1 and extended with their owning phase. Until implemented, a command must fail explicitly; absence is never a pass.

| Phase | Command | Required case families |
|---|---|---|
| 1 Governance foundation | `just evolution-gate 1` | Storage/RLS; credential/session roles; compilation and immutable bytes; self-approval rejection; digest-bound approval/rejection/revocation; activation/rollback eligibility; API/CLI parity; dry-run migration; closed evidence registry |
| 2 Shared service and integration | `just evolution-gate 2` | Minikube preflight/network denial; TLS; two real independent gateways/tenants; snapshots and applied receipts; deterministic input/output controls; quotas and noisy neighbor; all destination/provider negative cases; prepared offline run; 20 real local model requests |
| 3 Broader guardrail coverage | `just evolution-gate 3` | Exact PII/secrets/terms/topics/schema cases; transform aggregation; injection held-out benchmark; worker deadline/restart; Bedrock request/response contract and forbidden-egress tests |
| 4 Administration and operations | `just evolution-gate 4` | Browser author→different approver→operator journey; every UI failure/role state; metadata privacy; metrics and firing alerts; bounded load; backup plus actual restore; schema upgrade/rollback |
| 5 Portable delivery modes | `just evolution-gate 5` | Customer signed bundle, offline execution until expiry, key rotation/replay rejection, telemetry none/aggregate/metadata; operated LiteLLM route/credential/quota/cost tests; A/B independence retained |
| 6 Additional boundaries and release | `just evolution-gate 6` | Tool arguments/results and retrieval hooks; application authorization remains separate; image text cases; buffered SSE cross-chunk and cancellation cases; all three modes; two fresh full acceptance passes with matching executable/lock/fixture digests |

`just evolution-acceptance` executes the complete required registry from phases 1–6 with phase/source/lock evidence. Phase 6 runs it twice after clean state reset through the documented task-owned profile reset procedure. `just evolution-bedrock-live` remains a separate optional profile and never supplies missing local cases. `just verify` and the skill plan checker also remain required documentation gates.

## Closed Acceptance Registry

Registry entries use stable IDs: GOV, TEN, NET, INT, DET, UI, OPS, CMR, GW, BND prefixes. Each contains requirement ID, expected profile/mode, exact assertions and timeout. Required variants are expanded before execution: two tenants; all five enforcement states where valid; input/output; each enabled engine; three deployment modes; every supported stage/part/action combination. Impossible combinations are rejected explicitly by a capability test, not skipped silently.

The runner compares expected and executed IDs, disallows duplicate IDs, missing/zero-assertion cases and required skips, and verifies report source hashes against tested executable files. Use fresh test identities to prevent a previous run's state satisfying a new assertion. At least one negative case proves each security control by observing forbidden dispatch did not occur; a 403 alone without downstream observation is insufficient for egress/bypass claims.

## Governance Gate Details

Test identical policy bytes from two teams produce equivalent normalized findings while preserving distinct tenant identity. Change a dependency after approval and prove activation cannot use it. Exercise self-approval with two credentials belonging to the same principal, concurrent approve/reject, stale If-Match, canary receipt loss, old-version revocation, rollback under stricter current baseline and control-plane outage. A passing mock receipt cannot replace a real runtime digest acknowledgment.

## Residency and No-Bypass Gate

Include all four list categories, allow/deny overlap, empty enabled allow, inherited intersections, case/Unicode semantics and unsupported IDs. Run an internal denied-destination sink and external-egress probe with sensitive synthetic canaries; assert zero prohibited dispatches and no raw canary in logs, audit, metrics, browser cache, backup or telemetry. Inspect actual network enforcement with the installed CNI. Prove provider alias remapping cannot skip eligibility, image URLs are rejected and retrieval/tool hooks cannot execute a denied toy action.

## Capacity and Failure Gate

Use the declared 6-CPU/8-GiB profile, deterministic 1-KiB text, 10 evaluations/second total split equally between tenants for five minutes; configure quotas above that test load. Target client p95 ≤750 ms, no incorrect actions, no lost required audit and <1% operational errors. Record p50/p95/p99 and throughput by tenant. Saturate A beyond its allowed concurrency while B remains within quota; B must retain at least 90% of its baseline throughput. Thresholds are defaults for this fixture, not a production SLA.

Run classifier and OCR separately and assert their declared budget/cancellation behavior. Kill runtime/evaluator pods, interrupt DB, expire credentials, stop control plane, fill metadata spool and cancel streams. No scenario may produce uninspected content or a success claim without required audit. Restore drill target: recover last completed local backup within 10 minutes on this fixture; RPO is that backup's timestamp, never an invented zero-data-loss guarantee.

## Severity and Failure Handling

Any unauthorized cross-tenant access, unapproved policy activation, raw-data egress/retention, uninspected content release, missing mandatory case or corrupted evidence is a blocking defect. Any failing defined functional/performance threshold also blocks its phase. Fix cause, rerun affected suite, then rerun the owning gate; never delete expected IDs or broaden exclusions to make it pass. Record failed attempts as diagnostics and final passes separately. Source/lock changes invalidate earlier whole-release passes and require two new matching runs.

## Evidence Bundle

EvidenceReport includes source commit plus dirty-source digest, all tested file hashes, dependency/image/model/fixture locks, cluster/CNI/runtime versions, safe configuration, exact cases/assertions/durations and outcome. Store ignored per-run reports under `.evolution/reports/{run_id}/`, durable safe summaries in agent-session records and implementation session logs. No prompt bodies, token secrets or raw captures. Export machine-readable JSON and concise Markdown matrix. A source commit alone is insufficient when the tested tree is dirty.

## Cross-References

(see test-strategy.md), (see progress.md), (see component-specs.md §AcceptanceRunner), (see data-schema.md §EvidenceReport).
