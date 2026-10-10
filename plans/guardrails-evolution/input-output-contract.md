# Input/Output Contract

## Input Modes

### Team Gateway Requests

Teams configure a versioned connector manifest in their existing LiteLLM deployment. Applications continue using the gateway's chat endpoint. The connector submits supported content parts to the shared runtime and enforces Decision.action. Team model-provider credentials and gateway admin endpoints are never inputs to guardrails. Initially support non-streaming text; activate advanced capabilities only after Phase 6 evidence.

Validate caller-controlled fields before model dispatch and again after gateway normalization. Unknown surfaces, missing role/content, unregistered provider/model, unsupported tools/images and oversized content fail explicitly. A gateway supporting a format does not imply that its guardrail connector supports it. Exact wire payloads and streaming errors are owned by (see api-endpoints.md).

### Direct Evaluation and Application Hooks

An authenticated workload may call v2 evaluation directly for input/output, retrieval before prompt construction, tool arguments before application execution and tool results before model reuse. Normalize into EvaluationRequest with ContentPart and optional ToolCall (see data-schema.md §EvaluationRequest). Subsequent stages require scoped context and pinned policy. Tool approval is followed by the application's own authorization check; guardrails does not execute the tool.

### Administrator API and CLI

All management commands use the same HTTPS API and roles. Command prefix `guardrails-evolution` takes `--url`, `--ca-file`, `--credential-file` and optional `--json`. Credential file must have owner-only permissions. Domain-command map:

| Command group | Commands | API purpose |
|---|---|---|
| tenant / principal / credential | create, list; issue, rotate, revoke | Scoped onboarding and service identity |
| workload | register, list, get, suspend, manifest | Integration lifecycle |
| policy | validate, submit, list, show, diff | Immutable candidates and inspection |
| policy review | approve, reject, revoke | Exact digest and reason, separate approver |
| release | canary, promote, show, wait | Desired/applied rollout |
| workload rollback | target revision, expected revision, reason | New eligible binding revision |
| config | create-version, list, show for lists/residency/destinations/providers/tools/quotas | Immutable referenced configuration |
| audit / evidence | list, show, export | Safe metadata/evidence |
| runtime / bundle | register, show; export, verify, import | Customer delivery |

Example: `guardrails-evolution policy review approve --tenant team-a --policy baseline --version 2 --digest-file candidate.sha256 --reason 'Validation passed' --credential-file approver.token`. Shell arguments must not include secrets or prompt content. Content is submitted through bounded stdin/file input, never logged. CLI exit codes: 0 success, 2 local usage/schema error, 3 authentication/authorization, 4 conflict/approval/floor, 5 unavailable/deadline, 6 blocked content or failed acceptance. Waiting for a rollout times out nonzero rather than treating pending as applied.

### Browser Administration

Same domain workflow through server-rendered pages; session and CSRF replace CLI bearer transport. UI permissions, role states and failure recovery are specified in (see admin-experience.md). Raw prompts/responses are not an administration input or preview feature in this increment.

### Prepared Artifacts and Offline Bundles

Preparation accepts declared tool/model sources, verifies hashes/licenses and creates ArtifactRef locks. Runtime bundle import accepts a signed Bundle for one runtime/tenant with monotonic generation, matching capabilities and unexpired lease. Invalid/tampered/foreign/old bundles leave last-known-good untouched; expired last-known-good cannot evaluate. Do not normalize unknown bundle schemas by dropping fields.

## Input Normalization

Gateway field paths become stable part IDs and provenance roles. JSON stays structured and numeric types remain numeric. Text normalization is evaluator-specific and never changes submitted bytes before a validated transform. Image input is inline decoded PNG/JPEG only. Tenant/workload scope comes from authenticated identity and is compared with request fields. Provider routing is attested only to the extent the installed connector and controlled gateway can observe; a request-declared provider name is not trusted evidence.

## Output

### Decisions

Return Decision with exact policy/version/digest, binding revision, snapshot generation, action, would-be action, bounded reason codes and safe finding locations. Transformed content exists only for valid redaction and is ephemeral. Structural errors use Error; clients must not treat missing/invalid decisions as allow. HTTP status alone does not answer whether content may proceed.

### Management and Evidence

Return immutable version references, audit events and explicit desired/applied rollout state. Secret issuance returns a random credential once; list/read views contain only safe IDs/expiry. EvidenceReport distinguishes deterministic fixtures, actual local inference and optional external verification; absence of external evidence does not imply it ran.

### Telemetry and Retention

No raw retention in supplied profiles. Metadata events contain IDs/digests/action/reasons/timing only. Customer telemetry none emits zero telemetry; aggregate and metadata follow their own schemas and declared endpoints. Logs, traces, backups and browser assets stay within residency rules. A future raw-retention extension needs an explicit separately approved retention policy; merely setting a request flag does not enable it.

## Configuration Effects

| Option | Effect |
|---|---|
| Enforcement state | Detector action mapping, subject to mandatory floors; never changes residency eligibility |
| Runtime mode | Ownership, cache/lease and telemetry behavior (see variance-matrix.md) |
| Enabled capabilities | Accepted surfaces and budget profile; missing required capability rejects activation |
| External processing | Necessary but insufficient for remote provider use; exact allowed destination and approved policy still required |
| Telemetry level | Changes safe outbound metadata only, never policy semantics |
| Requested deadline | May tighten, never expand the approved server budget |
