# Administration Experience

## Trigger Condition

The user selected a browser administration surface alongside API/CLI lifecycle management.

## Scope Boundary

This UI administers guardrails tenants, workloads, policies, approval and service operations. It is not the consuming application's chat UI or end-user identity service. Server-rendered FastAPI/Jinja pages and locally served CSS/JavaScript are the implementation default; no external CDN dependencies.

## Navigation and Journeys

| Route / view | Main information | Actions | Required states |
|---|---|---|---|
| /admin/login | Local administrator credential form | Start session | Empty, invalid, rate-limited, expired |
| /admin | Authorized tenant list, service status | Select tenant | No access, one tenant, many tenants, unavailable |
| /admin/t/{t}/workloads | Workloads, mode, required state, desired/applied revision | Register, suspend, open manifest | Empty, loading, ready, pending, failed, stale |
| /admin/t/{t}/policies | Policies and immutable version history | Create candidate, compare versions | Empty, validation error, candidate, approved, rejected, revoked |
| /admin/t/{t}/policies/{p}/{v} | Exact digest, resolved parents, rules/lists/residency, author, diff, validation evidence | Submit review; approve/reject by eligible principal; revoke | Self-approval forbidden, stale review, missing capability, baseline conflict |
| /admin/t/{t}/releases | Canary/full rollout, runtime receipts, failures | Activate approved candidate, promote canary, eligible rollback | Pending, partial receipt, applied, failed, superseded |
| /admin/t/{t}/configuration | Versioned lists, destinations, providers, tools, residency, quotas | Create next immutable version | Conflict, deny/allow overlap preview, invalid endpoint, no destinations |
| /admin/t/{t}/audit | Safe metadata timeline and filters | Inspect decision metadata, export scoped page | Empty, no permission, invalid date, expired cursor |
| /admin/t/{t}/runtimes | Installation mode, version, capabilities, sync age, expiry, telemetry | Register, export signed bundle, revoke | Offline valid, expired, stale, signature failure |
| /admin/t/{t}/operations | Latency, failures, quota pressure, rollback/recovery evidence | Read runbooks, inspect reports | Metrics unavailable, alert firing, recovery pending |

## Author, Review and Activation Flow

Author chooses policy ID and pinned parent baselines, edits validated fields, sees a semantic diff and submits immutable candidate. Version/digest and all resolved dependency versions are visible together. The author sees why they cannot approve their own work. A different approver sees candidate, validation/effectiveness evidence, changed destinations and effective minimums, enters reason and approves exact digest. Approval does not activate it.

Operator chooses approved version, target workloads and state, previews before/after desired state, and starts canary. Page shows each expected runtime receipt and never labels pending as active. Full rollout requires successful canary evidence. Rollback preview shows exact target digest, current eligibility and the new revision it will create. Revoked/incompatible targets are disabled with server-enforced reasons.

## Interaction and Accessibility Contract

Every async action has in-flight, success and recoverable error states; preserve entered fields on validation errors. Display field-level errors plus a summary linked to fields. Prevent double submission through idempotency, not only disabled buttons. Keyboard navigation, semantic headings, visible focus, labeled inputs and status announcements are required. Target WCAG 2.2 AA for implemented pages; automated checks plus keyboard-driven Playwright flows verify applicable criteria, without claiming a full accessibility certification.

Never hide a material restriction in a tooltip. Show declared processing zone and external-processing flag when reviewing destinations/providers. Mark lexical topic detection and image text inspection limitations next to capability selection. Distinguish desired state from applied state, unavailable metric from zero, and rejected content from evaluation failure. All product messages use plain language, no internal task IDs or architecture jargon.

## Security and Privacy

Server-side authorization protects every page and action. Autoescape all candidate/list strings, use strict CSP with self-hosted assets and no unsafe inline scripts, require CSRF and same-origin mutation, expire sessions and clear UI state on logout. Browser history/query strings never contain credentials or raw prompts. Audit views are metadata-only; policy configuration itself may include sensitive organization rules and is visible only to scoped roles. No telemetry scripts, external fonts or third-party analytics.

## Cross-References

(see component-specs.md §AdminConsole), (see api-endpoints.md §Browser Routes), (see tenancy-model.md §Permission Matrix), (see test-strategy.md).
