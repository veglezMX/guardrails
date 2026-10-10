# Architecture & Decisions

## System Overview

Evolve the completed local proof into a locally operated, multi-tenant guardrails service. Two application teams retain independent LiteLLM gateways and send content to the shared Evaluation API. The same policy and decision contracts also serve a provider-operated LiteLLM gateway and a customer-managed runtime.

The baseline is commit `ad59059f15b230e7fa28819f877d4836dc432e4b`. Its five implementation phases are complete. This plan has six new phases, numbered locally; they do not imply completion of the separate five-phase hosted product programme. Existing proof specifications and acceptance evidence remain historical records.

## Authority and Interview Decisions

The 2026-10-09 interview selected all nine workstreams: application integration, broader guardrail coverage, policy governance, shared service, operations, customer-managed runtime, administration UI, additional evaluation boundaries, and a provider-operated gateway option. It confirmed shared local processing, restrictions in all four allow/deny categories, distinct author/approver responsibilities, audited activation and rollback, and automated acceptance without named-team sign-off. These decisions are recorded as AGR-031 through AGR-036 and DEC-025 in the repository's source-of-truth registers.

Retain the charter's portability, explicit exposure, no raw retention by default, observe-before-enforce, customer-controlled telemetry and application authorization boundaries. Old Proposed decisions are not ratified by this plan. AWS remains a later hosting target; this plan's local gates do not certify AWS, multi-region availability or compliance.

## Architectural Decisions

### AD-1: Separate management, evaluation and integration

**Decision:** Extend the existing Python application with a versioned evolution contract and separate management and runtime processes. Share domain rules through ports; transport adapters contain no policy logic.
**Rationale:** Teams must adopt the service without transferring gateway administration or provider credentials.
**Implications:** Team gateways call the runtime over authenticated TLS. The management service cannot invoke their administration APIs. The optional operated gateway runs the existing LiteLLM product with our connector.

### AD-2: Shared local processing with default-deny egress

**Decision:** Raw evaluated content may cross from either team to the shared local runtime. No raw content may leave the declared local environment by default. Network destinations, provider/model identifiers, tool/action names and content terms/topics each have explicit allow and deny semantics.
**Rationale:** Residency is a processing constraint, not merely a log-retention setting.
**Implications:** Runtime, evaluator workers, model routes, browser assets, telemetry, backups and downloads have named destinations. Preparation may download locked artifacts before sensitive workloads start. Runtime egress uses registered endpoints plus network controls. An explicit deny wins. Disabling content enforcement does not disable residency, authentication or provider/tool eligibility.

### AD-3: Approval binds the exact executable policy

**Decision:** A candidate is immutable from creation. Compilation resolves pinned parents, mandatory checks, lists, residency profile and evaluator revisions into canonical bytes. A different principal approves their digest; an operator activates only that approved digest.
**Rationale:** Approval of a mutable name would not establish what runs.
**Implications:** Changes, including parent/list/provider/residency revisions, require a new candidate and approval. Rejection/revocation is an appended event. Rollback creates a new binding revision to an eligible approved version; it never restores revoked controls or bypasses current organizational minimums. Activation and runtime application are separate states.

### AD-4: Compose monotonically and publish one effective version

**Decision:** An organization baseline, tenant baseline and workload candidate compile to one immutable effective policy per binding. Inheritance references exact digests, never latest aliases.
**Rationale:** This adds composition without introducing ambiguous request-time policy selection.
**Implications:** Union mandatory checks and denylists; intersect enabled allowlists and allowed processing zones; choose stricter quotas and minimum enforcement state. Conflicting transforms or incompatible schemas fail compilation. An approved tenant policy may restrict but cannot weaken organizational controls. An empty intersection is an error, not allow-all.

### AD-5: PostgreSQL for shared state; local runtime cache

**Decision:** Shared management, audit and quota state use PostgreSQL with tenant keys, transaction-scoped identity and forced row security. Customer runtime snapshots and metadata spools use local SQLite.
**Rationale:** Multiple service replicas need transactional concurrency and independently tested tenant isolation; portable runtimes must survive control-plane loss.
**Implications:** Introduce repository ports and migration tooling. Never mount one SQLite file into multiple runtime pods. Preserve old SQLite proof mode separately during migration. No dual writes.

### AD-6: Version contracts and pin evaluation context

**Decision:** Add `/v2` management/runtime interfaces with `guardrails-v2` payloads. Keep the proof `/v1` service available only in the compatibility profile. Pin effective policy, residency configuration and check revisions for each turn.
**Rationale:** The existing local-proof decision supports only text input/output and cannot truthfully represent the selected surfaces.
**Implications:** Identity is derived from credentials, never trusted from caller metadata. Candidate revocation is checked on every stage, including already-open turns. Runtime freshness, revocation propagation and offline limits are explicit in the integration contract.

### AD-7: Minikube is the local delivery target

**Decision:** Use a dedicated Linux Minikube profile with Docker driver and Calico, Helm packaging, per-team namespaces, one shared-service namespace and separate customer-runtime namespace. Retain Compose for predecessor regression.
**Rationale:** This host has sufficient observed CPU, available memory and disk for a bounded local environment, and Kubernetes permits real network-policy and rollout tests.
**Implications:** Start with 6 vCPU and 8 GiB, run heavyweight profiles sequentially, and enforce a preflight resource gate. Namespace separation proves only the tested controls on a shared trusted host. Do not call it physical, geographic or hostile-administrator isolation. Details are in deployment-topology.md.

### AD-8: Gateway and application responsibilities remain intact

**Decision:** LiteLLM owns provider routing, provider credentials, model transport and streaming protocol. Applications own retrieval, business authorization and tool execution. Guardrails inspects complete content boundaries and returns actions.
**Rationale:** Safe content does not imply authorization to act.
**Implications:** The connector may hold streamed output until a decision, but it does not implement a new LLM gateway. Tool allowlisting is a content/use restriction, never a grant of business access. Retrieval and tool-result evaluation need explicit application hooks.

### AD-9: Local engines with measured limits

**Decision:** Provide deterministic PII/secrets/rules/schema checks, a separately packaged pretrained injection classifier, and image text extraction. Exercise real local model traffic separately from deterministic acceptance fixtures. Implement a Bedrock adapter behind an external-processing gate.
**Rationale:** Local residency and reproducible tests must not depend on an external evaluator.
**Implications:** Bedrock contract tests run locally; live use requires approved destinations, region and operator-supplied credentials. Classifier artifacts are pinned and evaluated on held-out data; their training benchmarks are not our acceptance evidence. Supported media and categories are defined in guardrail-coverage.md.

## Scope Boundaries

### In Scope

| Workstream | Concrete completion | Main phases |
|---|---|---|
| A Integration | Two independent real LiteLLM gateways, sample application, real local inference, onboarding and compatibility tests | 2, 6 |
| B Coverage | PII, secrets, explicit terms/topics, schema validation, injection classifier, gated Bedrock adapter, benchmarks | 3, 6 |
| C Governance | Immutable versions, composition, separate approval, staged activation, revocation, rollback | 1, 2 |
| D Shared service | Tenant/workload identity, scoped roles, isolation, quotas, onboarding | 1, 2 |
| E Operations | Metrics, alerts, load/fault tests, backup/restore, upgrade and incident procedures | 4, 6 |
| F Customer runtime | Signed bundles, autonomous cached execution, controlled synchronization and telemetry including none | 5 |
| G Admin UI | Policies, review, rollout, workloads, restricted audit and operational state | 4 |
| H Boundaries | Buffered streaming, tool arguments/results, retrieval and PNG/JPEG text inspection | 6 |
| I Operated gateway | Optional separate LiteLLM deployment with credential isolation, tenant routing and usage budgets | 5 |

### Out of Scope

- Reimplementing a general-purpose LLM gateway, model training or a proprietary moderation/injection model.
- End-user authentication and business/resource authorization for consuming applications; service and administrator identity are included.
- Complete DLP, complete AI governance, autonomous agent governance, or a general MCP security platform.
- Cloud deployment, organization-wide AWS enforcement, multi-region availability, cross-cloud deployment and compliance certification in this local plan.
- Native audio/video evaluation, biometric recognition and general image moderation in this increment. Multimodal acceptance is bounded to PNG/JPEG text inspection; unsupported modalities must be rejected explicitly, never silently passed as inspected.
- Internet processing under the default local residency profile. The optional external profile is unusable until explicitly configured and approved.

## Error Handling Philosophy

Fail closed for identity, tenant mismatch, invalid policy, unsupported surface, residency/list violations, revoked policy, unsafe transformation and missing mandatory evidence. Content detector operational failures follow the approved policy only where allowed by organizational minimums. Observe records would-be actions but still enforces structural and residency restrictions. No retry can replay a model call or tool action automatically. Return bounded reason codes, not provider tracebacks or submitted content.

## Key Tradeoffs

| Chose | Over | Because |
|---|---|---|
| Buffered streaming in enforcing modes | Releasing tokens before complete evaluation | Prevents already-delivered sensitive output and cross-chunk evasion |
| Server-rendered administration | Separate SPA build system | Reuses API rules and keeps local installation small |
| Explicit eligibility lists and prepared endpoints | Arbitrary runtime URL fetching | Makes residency restrictions testable |
| Local acceptance plus optional external verification | Mandatory cloud credentials | Matches the selected local release |
