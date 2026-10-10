# Agent Instructions — Guardrails Evolution

**Plan:** `guardrails-evolution`
**Location:** `plans/guardrails-evolution/`
**Scope:** Implement the six-phase local evolution of the standalone guardrails service, retaining gateway/application ownership and the completed proof's historical scope.

This folder is the build specification. Act only on this plan; do not modify sibling plans or their progress/session history. Product source, tests and deployment files are written in the repository at the locations specified here.

## How to Start

1. Read this file completely and inspect Git status, branch and worktree registrations under the user's standing rules.
2. Read `progress.md` for current state and the next task.
3. If prior sessions exist, read the latest numbered log in `sessions/`.
4. For the first implementation session, start with Phase 1; no new implementation is complete yet.
5. Use `index.md` for navigation, then read the task's specification and its referenced contracts.

## How to Record Progress

At the START of a work session, create the next numbered `sessions/NNN.md` (first is `001.md`) and record intended work. Maintain durable agent-session records under the user's required ~/.agent-sessions convention as well; plan logs are implementation history, not a substitute for cost records.

As each task finishes, update its checkbox, Last completed task and Next task in progress.md. At session end, fill the log, increment Sessions completed and update the phase Status only after its gate passed. The log sections are **Work Completed**, **Gaps Closed**, **Decisions Made**, **Issues / Blockers**, **Status at End of Session**. Record exact commands/results and safe evidence paths, never secrets or customer content.

If context is nearly exhausted, update progress.md and the durable HANDOFF.md before stopping. A missing log is not excused by a complete checkbox.

## How to Work

- Follow dependency and phase order; read referenced specs before implementation.
- Use data-schema.md as the domain authority and api-endpoints.md as the wire authority; do not invent incompatible fields.
- Use the installed codebase-memory skill/tools for structural exploration, indexing the assigned worktree first when necessary.
- Run appropriate component tests during work and the full phase gate before advancing. A missing command, skipped required case or zero-test pass is a failure.
- Preserve predecessor behavior through the compatibility profile; never rewrite its historical specifications to match new features.
- Do not claim an installed model, capability manifest or YAML policy proves operational behavior without the required executable evidence.
- Keep this planning task's Git scope: implementation/commit/push/integration requires the user's current task authorization. The old Phase 5 commit instruction does not authorize committing this plan automatically.

## When the Plan Has a Gap

A gap is work the plan needs but does not name: a missing task, dependency step or blocker before a phase gate. Never park it as out of scope. **Only architecture.md defines out-of-scope work.**

Close work gaps yourself within the established decisions and domain shapes. Add the missing task under its phase in progress.md, mark it complete only after validation, and record what was missing, what changed and which task it served under **Gaps Closed**.

Escalate a decision gap the plan cannot resolve: contradictory requirements, a new dependency outside the declared constraints, a scope change or a missing user-owned security decision. Ask the user and record the unresolved question; do not invent approval, weaken gates or relabel required work out of scope. Routine implementation choices already covered by these contracts do not need another approval.

## Document Map

| Document | What It Tells You |
|---|---|
| `index.md` | Which document answers each implementation question |
| `architecture.md` | Decisions, scope and ownership boundaries |
| `data-schema.md` | Domain records, constraints and state machines |
| `folder-structure.md` | Physical file locations and production actions |
| `component-specs.md` | Inputs, outputs, dependencies and behavior of 28 components |
| `tech-stack.md` | Dependency constraints, configuration, wiring and commands |
| `ecosystem-reference.md` | Verified baseline and external interfaces |
| `input-output-contract.md` | System boundaries and administrator/application workflows |
| `test-strategy.md` | Test layers, fixtures and mocking boundaries |
| `progress.md` | Six phases, tasks, gate state and next action |
| `sessions/` | Append-only implementation session history |
| `variance-matrix.md` | Behavior across modes, enforcement and telemetry |
| `migration-map.md` | SQLite/v1 transition, coexistence and rollback |
| `integration-protocols.md` | Gateway, worker, model and synchronization protocols |
| `plugin-contract.md` | Engine/stage/action/connector extension contracts |
| `quality-gates.md` | Executable acceptance, quality, operational and evidence thresholds |
| `api-endpoints.md` | HTTP routes, request/response schemas and errors |
| `tenancy-model.md` | Tenant scope, permissions and data isolation |
| `guardrail-coverage.md` | Exact detectors, list semantics and supported surfaces |
| `deployment-topology.md` | Minikube topology, traffic and resource constraints |
| `admin-experience.md` | Browser journeys, role states and accessibility |
| `delivery-estimate.md` | Staffing assumptions, effort ranges and critical path |

## Rules

Specification documents are read-only during implementation. Inside this plan folder, write only progress.md and sessions/ logs unless the user explicitly authorizes a specification revision. Build source/tests/configuration outside the plan folder. Do not modify sibling plans. If a gate fails, fix it before advancing. No required gap is silently skipped or reclassified as an exclusion. Protect existing Git changes and use the persistent worktree convention.
