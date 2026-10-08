# Agent protocol — local technical proof

Implement this release using `plans/local-technical-proof/` as the build contract. The entry point is this file; the current work state is in progress.md.

## Startup

1. Read progress.md and the latest session log in sessions/. If none exists, start at its Next task.
2. Read index.md for navigation and assumptions, then architecture.md for the only plan-level scope boundary.
3. Read the current task's component specification, canonical data models and relevant interface/gate documents before editing code.
4. Inspect Git status, branch and worktrees; follow repository AGENTS.md and use the assigned persistent worktree. Preserve other tasks' changes.

## Working protocol

At session start update progress.md with the active task; at session end record completed tasks, evidence, blockers and the next concrete task. Add `sessions/NNN-YYYY-MM-DD.md` with these exact sections: Work Completed, Gaps Closed, Decisions Made, Issues/Blockers, Status at End. Reference phase/task IDs and tested commands. Do not mark a task complete because its document exists.

Build in dependency order. Independent owners may work concurrently as described in implementation-estimate.md, but each phase gate must pass before that phase becomes COMPLETE. Missing/skipped required checks are failures. Source changes belong in the repository paths in folder-structure.md, not in this plan folder. Evidence and local secrets belong only in their documented generated locations.

Specification documents are read-only during implementation. Only progress.md and sessions/ are routine mutable plan state. Do not rewrite the contract to match an incomplete implementation. Explicitly approved contract changes require a coherent plan update and rerun of its checks.

## Gap rule

Close work the plan needs but did not name: missing implementation tasks, setup steps, dependencies or blockers in front of a required gate. Add the work to progress.md and log it under Gaps Closed. Never relabel such work as out of scope. Only architecture.md declares out-of-scope work, subject to upstream authority. If a gap requires a product or conflicting-contract decision the plan cannot supply, ask the user to resolve the precise conflict while progressing independent work; do not invent a decision or waive acceptance.

Never claim that 67 baseline tests, this document set, researched dependency versions or a smoke test prove the release. All ten acceptance rows and retained evidence are required. Never implement cloud/streaming/other excluded scope merely to make a local test convenient.

## Document map

- `index.md` — Which document owns each decision?
- `architecture.md` — What is the end solution and its boundary?
- `data-schema.md` — What are the canonical entities, fields and invariants?
- `folder-structure.md` — Where does each implementation file go?
- `component-specs.md` — What must each component do?
- `tech-stack.md` — Which versions, tools and commands are used?
- `ecosystem-reference.md` — Which upstream interfaces were verified?
- `input-output-contract.md` — What can a user send and receive?
- `test-strategy.md` — What evidence proves the release?
- `progress.md` — What is the build order and current state?
- `variance-matrix.md` — How do state and failure combinations behave?
- `integration-protocols.md` — How do services, gateways and the mock communicate?
- `plugin-contract.md` — What is the provider-authored evaluator contract?
- `api-endpoints.md` — Which endpoint and CLI command implements each operation?
- `quality-gates.md` — What must pass before advancing each phase?
- `migration-map.md` — How is the prototype reused and migrated?
- `implementation-estimate.md` — What effort and team timeline remain?
- `sessions/` — implementation history with the required sections above.

## Session completion

Run the applicable phase gates, update status truthfully and report remaining work. Preserve failures as evidence and fix them; do not bypass checks. Commit, merge, push and worktree cleanup follow the user's Git authorization, not an implied permission from this plan.
