# Local technical proof implementation plan

Derived on 2026-10-08 from the [accepted release definition](../../solution-docs/60-delivery/RELEASE-DEFINITION.md), at source baseline `eb68dd9`. This plan turns the agreed internal-team API/CLI proof into implementation contracts and an effort forecast. Runtime implementation and acceptance have not started under this plan.

## Document map

| No. | Document | Question answered |
| --- | --- | --- |
| 0 | `AGENT.md` | Where do I start and how do I execute this plan? |
| 1 | `index.md` | Which document owns each decision? |
| 2 | `architecture.md` | What is the end solution and its boundary? |
| 3 | `data-schema.md` | What are the canonical entities, fields and invariants? |
| 4 | `folder-structure.md` | Where does each implementation file go? |
| 5 | `component-specs.md` | What must each component do? |
| 6 | `tech-stack.md` | Which versions, tools and commands are used? |
| 7 | `ecosystem-reference.md` | Which upstream interfaces were verified? |
| 8 | `input-output-contract.md` | What can a user send and receive? |
| 9 | `test-strategy.md` | What evidence proves the release? |
| 10 | `progress.md` | What is the build order and current state? |
| 11 | `sessions/` | Where is implementation session history recorded? |
| 12 | `variance-matrix.md` | How do state and failure combinations behave? |
| 13 | `integration-protocols.md` | How do services, gateways and the mock communicate? |
| 14 | `plugin-contract.md` | What is the provider-authored evaluator contract? |
| 15 | `api-endpoints.md` | Which endpoint and CLI command implements each operation? |
| 16 | `quality-gates.md` | What must pass before advancing each phase? |
| 17 | `migration-map.md` | How is the prototype reused and migrated? |
| 18 | `implementation-estimate.md` | What effort and team timeline remain? |

## Assumptions and Open Questions

| Topic | Status and choice | Affected document |
| --- | --- | --- |
| Product outcome and acceptance | Confirmed in AGR-022–030/DEC-022–024; no product re-interview required | architecture.md |
| Python, FastAPI/Pydantic, SQLite, Compose | Explicitly confirmed by user in this planning interview | tech-stack.md |
| Staffing | User specified four engineers full time + QA + scrum master | implementation-estimate.md |
| QA and scrum-master allocation | Assumption: one QA available full time when needed; scrum-master facilitation shared. QA effort estimated separately, exact allocations not specified | implementation-estimate.md |
| Supported host | Engineering baseline: Linux x86_64 Docker host; compatible Docker Desktop/ARM setups may work but are not additional acceptance platforms | tech-stack.md |
| Interfaces and security | Engineering choice: strict versioned JSON, local TLS/bearer roles, separate CP/runtime, SQLite per owner, API-backed CLI | architecture.md, api-endpoints.md |
| Chat surface and PII format | Engineering choice within text-only proof: all supported string-message roles, non-streaming single choice; exact ASCII email and +8–15 digit phone format | input-output-contract.md |
| Mandatory floor states | Engineering limit: local floors permit Disabled or Observe; all five workload states still work above that minimum. Higher mandatory floors need later staged onboarding | data-schema.md |
| Timing/capacity defaults | Engineering defaults, not SLAs: explicit budgets, context TTL, cache staleness and convergence wait | data-schema.md |
| Current dependency versions | Latest stable candidates verified from primary sources on 2026-10-08; combined compatibility/image digests remain implementation evidence to obtain in Phase 1 | tech-stack.md |
| Calendar start date | Not specified; estimate is relative to kickoff, not a committed date | implementation-estimate.md |
| Product-blocking open questions | None. Integration feasibility and pin resolution are planned engineering gates, not missing product decisions | progress.md |

These defaults are reviewable engineering choices; they do not claim additional user approvals or amend the programme's L1 agreements. Selected Proposed ADR mechanics are adopted only as named in architecture.md. An implementation contradiction with an authoritative agreement must be raised and resolved, not hidden in code.

## Plan History

| Date | Change |
| --- | --- |
| 2026-10-08 | Initial technical design and remaining-effort estimate for the separately accepted local proof; user-confirmed stack and four-engineer staffing. |
