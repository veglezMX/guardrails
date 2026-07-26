# Phase 1 Code Design — AI Guardrails

**Status:** Design (approved in brainstorming; not yet promoted into the registers)
**Date:** 2026-07-25
**Author:** Product/technical lead (contact@veglez.com)
**Scope of this spec:** How the Phase 1 deliverables (`seed.md` §18) are shaped as code — repository topology, the contract package, the Runtime pipeline, the policy format and control plane, evaluator adapters, the two client packages, auditing, and the verification harness. This is a decision record produced through the brainstorming process. It does **not** edit the source-of-truth registers; promoting its load-bearing decisions into `AGREEMENTS.md` / `DECISIONS.md` / the ADR set is a separate, governed follow-on step (see §10).

---

## 1. The question this resolves

`solution-docs/` describes the platform completely at the architecture level and stops short of code. `seed.md` §18 lists seven Phase 1 deliverables; `seed.md` §16 lists twelve MVP components and one falsifiable demonstration. Neither says how any of it is structured as software.

This spec answers: **as a developer starting Phase 1 on an empty repository, what do I build and in what shape?**

It does not re-decide anything already settled in the registers. Where it picks something the registers leave open, that pick is listed in §10 as an ADR the implementation owes.

## 2. Inherited constraints

Settled before design work began, and treated as fixed here:

| Choice | Value | Why |
|---|---|---|
| Intent | Production-intent Phase 1, foundation for Phase 2+ | Not a throwaway prototype |
| Language | Python for every package | The LiteLLM connector runs inside the consuming team's LiteLLM process, so it is Python regardless; a single language keeps the contract defined once. No latency budget exists yet (VAL-001 exists to produce one), so optimising for an unknown number would be premature |
| Topology | Monorepo, `uv` workspace of independently publishable packages | Package boundaries make AGR-007 and CON-004 mechanically enforceable |
| Persistence | Ports and adapters, **plus** a minimal management API in Phase 1 | `seed.md` §15 permits "a minimal management API and version-controlled configuration" during the MVP; `seed.md` §16 lists workload registration and policy binding as MVP components 3 and 4 |
| Verification | The `seed.md` §16 demonstration written first, as an executable acceptance test | VAL-002 is worded as a pass/fail test, not a milestone; "Phase 1 complete" should be a CI result |
| Runtime orchestration | Explicit stage pipeline mirroring RT-1…RT-7 | Three load-bearing agreements (AGR-006, AGR-007, AGR-012) are structural rather than behavioural; structure a reviewer and a CI rule can check is worth the extra files |

### 2.1 Client-investment posture

Two facts established during design change the emphasis from what the architecture documents imply:

- **A non-LiteLLM client lands immediately** — a consumer-operated custom gateway. `EXTENSIBILITY-ARCHITECTURE.md` seam 4 ("new connector") is therefore a Phase 1 event, not a later one.
- **Agent workloads are near-future**, which makes the `retrieval` / `tool_call` stages — unreachable from any gateway — a live concern rather than Phase 5 theory.

Consequently the **SDK receives the documentation and DX budget**, while the LiteLLM connector is still built (VAL-002 requires it) and still ships connector-health telemetry in Phase 1.

The custom gateway is consumer-operated, so it is a cooperative client exactly like LiteLLM. Nothing about the enforcement posture changes: OQ-001 is Resolved, cooperative integration is the Phase 1–2 posture, and neither client may be described as an enforcement boundary (AGR-018).

## 3. Repository topology

```
guardrails/
├── pyproject.toml                    # uv workspace root
├── packages/
│   ├── contract/                     # guardrails-contract     → guardrails.contract
│   ├── runtime/                      # guardrails-runtime      → guardrails.runtime
│   ├── controlplane/                 # guardrails-controlplane → guardrails.controlplane
│   ├── sdk/                          # guardrails-sdk          → guardrails.sdk
│   ├── connector-litellm/            # guardrails-connector-litellm
│   └── testkit/                      # guardrails-testkit      → guardrails.testkit
├── policies/                         # git-authored policy YAML, schema-validated in CI
├── deploy/                           # container, IaC, evaluator wiring
└── tests/acceptance/                 # the VAL-002 harness
```

### 3.1 The dependency rule

```
        contract  ←────────────────────────────────┐
           ↑  ↑  ↑                                 │
           │  │  └── runtime ── adapters ──────────┤
           │  └───── controlplane ─────────────────┤
           └──────── sdk ←── connector-litellm ────┘
                       ↑
                    testkit
```

- `contract` depends on nothing but `pydantic`. No `boto3`, no `litellm`, no `fastapi`.
- `sdk` and `connector-litellm` never import `runtime` or `controlplane`. A client that cannot import the policy engine cannot embed policy logic (AGR-007).
- `connector-litellm` depends on `sdk`. `INTEGRATION-ARCHITECTURE.md` §5 states the HTTP API "is the direct, framework-independent surface that the LiteLLM connector itself is built on top of" — so the connector is a LiteLLM-shaped hook wrapping the SDK, and the parity rule holds by construction.
- `testkit` depends only on `contract`, so it can exercise any client including ones this team does not own.

Enforced by `import-linter` in CI, plus a scan asserting no vendor token (`litellm`, `bedrock`, `boto3`) appears in `contract/` or the policy schema (CON-004, and the 100%-isolation requirement in `NON-FUNCTIONAL-REQUIREMENTS.md`).

### 3.2 The contract package

Four modules, nothing else:

```python
# guardrails/contract/models.py
class EvaluationRequest(BaseModel):
    tenant_id: str
    workload_id: str
    stage: Stage                       # Literal["input","output"] — reserved seven only
    content: Content
    correlation_id: str
    policy_ref: PolicyRef | None       # caller hint; never authoritative

class Decision(BaseModel):
    action: Action                     # allow | block | redact | log_only
    allowed: bool
    reason_codes: list[ReasonCode]
    policy_name: str
    policy_version: int
    evaluator_metadata: list[EvaluatorRef]
    transformed_content: Content | None
    correlation_id: str
    audit_id: str
    evaluation_latency_ms: float
```

```python
# guardrails/contract/ports.py — protocols, zero implementations
class PolicyStore(Protocol):    async def get(self, name, version) -> Policy: ...
class BindingStore(Protocol):   async def resolve(self, tenant, workload) -> Binding: ...
class Evaluator(Protocol):      async def evaluate(self, cfg, stage, content) -> Finding: ...
class AuditSink(Protocol):      async def emit(self, record: AuditRecord) -> str: ...
class PolicyDistribution(Protocol): async def refresh(self) -> None: ...
```

```python
# guardrails/contract/failures.py — the six AGR-012 modes as types
class EvaluationTimeout(GuardrailFailure): ...
class DetectorUnavailable(GuardrailFailure): ...
class PolicyUnresolvable(GuardrailFailure): ...
class ControlPlaneUnavailable(GuardrailFailure): ...
class AuditDeliveryFailed(GuardrailFailure): ...
class TransformFailed(GuardrailFailure): ...
```

Plus `reason_codes.py` holding the `GR-0NN:<evaluator-family>:<code>` taxonomy (DEC-020 / ADR-0003).

The OpenAPI document and the policy JSON Schema are **generated** from these models, never hand-written, so the published contract and the running code cannot drift.

### 3.3 `Content` is a discriminated union from day one

```python
class TextContent(BaseModel):
    kind: Literal["text"] = "text"
    text: str

# Future shapes — NOT implemented in Phase 1, but the tag makes them additive:
#   DocumentSetContent (retrieval): documents + source metadata
#   ToolCallContent    (tool_call): tool name + arguments
Content = Annotated[TextContent, Field(discriminator="kind")]
```

Shipping `content: str` would make every future stage a breaking contract change that must then stay compatible across three deployment modes indefinitely (`TECHNICAL-RISKS.md` R8). A one-member tagged union costs one line now and keeps every future stage additive. With agent workloads in the near future, this is not speculative.

## 4. The Runtime pipeline

### 4.1 Stage mapping

| Doc component | Code | Shape |
|---|---|---|
| RT-1 Request intake | `stages/intake.py` | stage |
| RT-2 Policy resolver | `stages/resolve.py` | stage |
| RT-4 Evaluator adapters | `stages/evaluate.py` | stage |
| RT-3 Decision engine | `stages/normalize.py` | stage |
| RT-5 Audit producer | `audit.py` | pipeline epilogue — runs on success *and* failure paths |
| RT-6 Failure handler | `failures.py` | wrapper around the whole pipeline |
| RT-7 Correlation | `correlation.py` | collaborator used by intake |

RT-5, RT-6 and RT-7 are deliberately not sequential stages, because the architecture documents do not describe them as such: RT-6 is cross-cutting, RT-7 is invoked by RT-1 ("assigned by RT-7 if the caller did not supply one"), and RT-5 must fire on both the success and failure paths.

### 4.2 Core

```python
@dataclass(frozen=True)
class EvaluationContext:
    request:  EvaluationRequest
    binding:  Binding | None = None
    policy:   Policy  | None = None
    findings: tuple[Finding, ...] = ()
    decision: Decision | None = None
    def with_(self, **kw) -> "EvaluationContext": return replace(self, **kw)

class Stage(Protocol):
    name: ClassVar[str]
    async def __call__(self, ctx: EvaluationContext) -> EvaluationContext: ...

class EvaluationPipeline:
    async def run(self, req: EvaluationRequest) -> Decision:
        ctx, t0 = EvaluationContext(req), monotonic()
        try:
            for stage in self._stages:
                ctx = await stage(ctx)
        except GuardrailFailure as f:
            ctx = await self._failure.handle(f, ctx)
        decision = ctx.decision.with_latency(monotonic() - t0)
        return await self._audit.record(decision, ctx)   # stamps audit_id, returns Decision
```

`self._audit` is the audit **producer** (RT-5), not the `AuditSink` port: it builds the `AuditRecord`, calls `AuditSink.emit()` which returns the audit identifier, stamps that identifier onto the `Decision`, and returns the decision to the caller.

The context is frozen and rebuilt via `with_()` so each stage's contribution is visible and no stage can silently rewrite an earlier one's work.

The pipeline is **transport-agnostic**: it accepts a model and returns a model. The FastAPI application is a thin adapter over it. This is what lets Mode B call it in-process and Mode C call it locally with no contract change — the final bullet of the `seed.md` §16 demonstration.

### 4.3 Observe mode

Applied in `normalize.py` (RT-3), whose documented inputs already include "the workload's current binding mode":

```python
action = self._apply_policy(ctx.policy, ctx.findings)
emitted = Action.LOG_ONLY if ctx.binding.mode is Mode.OBSERVE else action
```

The would-have-been action is recorded in the **audit record, not the `Decision`**. The audience for "what would have happened" is the reviewer promoting observe→enforce, who reads audit records; widening the public contract instead would create a field that must stay compatible across all three modes forever (`TECHNICAL-RISKS.md` R8).

This is also what makes VAL-002 provable: one policy version, two bindings, divergent emitted actions, identical reason codes.

### 4.4 Failure handling

Six typed exceptions, one handler, one audit point.

```python
class FailureHandler:
    async def handle(self, f: GuardrailFailure, ctx) -> EvaluationContext:
        behavior = self._behavior_for(f, ctx)      # from the resolved policy
        decision = behavior.to_decision(f, ctx)    # never invented here
        return ctx.with_(decision=decision.annotated_with(failure_path=f.code))
```

Two cases the documents force:

- **`PolicyUnresolvable` has no policy to consult.** The deployment carries an explicit `unresolvable_policy_behavior` setting with **no default**; the runtime refuses to start if it is unset. An unset value is a configuration error, never a silent `allow` — the only honest reading of "no undocumented fallback" (AGR-012).
- **`AuditDeliveryFailed` is raised by the handler's own epilogue.** It is caught separately and never re-enters the handler, so a failing audit sink cannot recurse.

### 4.5 Policy resolution and caching

`resolve.py` reads a **local cache only** and never makes a synchronous control-plane call (AGR-015). Refresh runs as a background task behind the `PolicyDistribution` port. Phase 1 ships an in-process TTL cache; Phase 4's Mode C swaps in a disk-backed one as an adapter, not a rewrite.

## 5. Policy format and control plane

### 5.1 The policy document

Authored as YAML in `policies/`, validated in CI, published as an immutable version. The schema is generated from the Pydantic model in `guardrails-contract`.

```yaml
# policies/pii-baseline/v3.yaml
name: pii-baseline
version: 3

stages: [input, output]

evaluators:
  - family: prompt-attack          # vendor-neutral selector — never "bedrock"
    capability: injection
    stages: [input]
    threshold: 0.7
    action: block

  - family: pii
    capability: email
    stages: [input, output]
    action: redact

failure_behavior:                  # all six keys required — no defaults exist
  evaluation_timeout:      allow_and_log
  detector_unavailable:    degrade_and_log
  policy_unresolvable:     block
  control_plane_down:      use_cached_policy
  audit_delivery_failed:   allow_and_log_locally
  transform_failed:        safe_fallback

audit_behavior:
  retain_content:      false       # AGR-014 default
  retain_reason_codes: true
```

Two structural properties:

- **Evaluator selection is `family` + `capability`, never a vendor name.** The runtime maps `prompt-attack` to whichever adapter is configured for it. That mapping lives in deployment configuration, not in the policy (CON-004, AGR-005).
- **`failure_behavior` has six required fields and no defaults.** A missing key is a parse error, implementing the requirement that "policy schema validation rejects any policy version lacking a failure-behavior section".

The policy carries no tenant, no workload and no enforcement mode. Those live on the binding, which is what lets one version serve many workloads at different maturity levels — the hypothesis VAL-002 tests.

### 5.2 The binding

```python
class Binding(BaseModel):
    tenant_id: str
    workload_id: str
    policy_name: str
    policy_version: int
    mode: Literal["observe", "enforce"]
    effective_from: datetime
    stage_timeouts_ms: dict[Stage, int]     # timeouts are configuration, not code
    superseded_at: datetime | None = None
```

Rollback writes a new binding row and supersedes the old one. Nothing is mutated or deleted, which makes "immediate rollback" and "evaluation-result comparison between versions" queryable rather than aspirational.

### 5.3 The minimal control plane

Git is the authoring surface, the API is the system of record, CI is the bridge — satisfying both halves of `seed.md` §15's allowance rather than choosing one.

```
policies/*.yaml ──[CI: validate → publish]──▶ POST /v1/policies
developer ──────────────────────────────────▶ POST /v1/workloads
                                              POST /v1/bindings
                                              POST /v1/bindings/{id}/rollback
```

`POST /v1/policies` with an existing `(name, version)` returns **409, always**, with the content hash stored alongside. Immutability is enforced at the only write path.

`tenant_id` is carried end to end from day one — request, binding, decision, audit — but tenant *management* (registry CRUD, per-tenant workload identity, quotas) is Phase 2. Carrying the field early is free; retrofitting it through five models later is not.

### 5.4 Storage adapters

| Port | Local / CI | Internal deployment |
|---|---|---|
| `PolicyStore` | filesystem YAML | versioned S3 |
| `BindingStore` | in-memory / SQLite | DynamoDB |
| `AuditSink` | JSONL file | SQS → S3 |
| `PolicyDistribution` | direct read | poll + TTL cache |

The full test suite runs with zero AWS; the same suite runs against the AWS adapters as a scheduled integration job.

This adopts the `TRADE-OFFS.md` recommendation — DynamoDB for binding lookups on the hot path, versioned S3 for immutable policy documents and Mode C distribution — which is currently flagged "recommended, not yet an AGR". It needs an ADR (§10) rather than being settled silently by the code.

## 6. Evaluator adapters

### 6.1 The adapter boundary

```python
class Evaluator(Protocol):
    impl: ClassVar[str]                      # stable registry key
    family: ClassVar[str]
    capabilities: ClassVar[frozenset[str]]
    async def evaluate(self, cfg: EvaluatorConfig,
                       stage: Stage, content: Content) -> Finding: ...
```

Two different configuration objects reach an evaluator, and keeping them distinct matters:

- **`EvaluatorConfig`** comes from the **policy** — threshold, action, which stages. It is portable across deployments and is what AGR-005 protects.
- **`EvaluatorWiring`** (§6.2) comes from the **deployment** — which implementation serves this family, its credentials, endpoints and timeout. It is deployment-specific by design and never travels with a policy.

**An adapter returns a `Finding`, never an `Action`:**

```python
class Finding(BaseModel):
    evaluator: EvaluatorRef          # name + version
    matched: bool
    confidence: float | None
    reason_codes: list[ReasonCode]
    spans: list[Span]                # for redaction
    latency_ms: float
```

The adapter reports what it saw; `normalize.py` decides what happens by applying the policy's thresholds and actions. That split is the AGR-007 separation — detection is vendor territory, action is policy territory — and it is what lets the same policy produce identical decisions whether Bedrock or a deterministic rule matched.

Phase 1 ships two implementations (`seed.md` §16 items 6 and 7): `BedrockGuardrailsEvaluator` and `DeterministicEvaluator`.

The Bedrock adapter's entire job is translation; no Bedrock-native field escapes it. Guarded by a test asserting no Bedrock-native key ever appears in a serialised `Finding` or `Decision`, replayed over recorded responses — the concrete mitigation for `TECHNICAL-RISKS.md` R2 (contract leakage).

Evaluators run concurrently per stage with a per-evaluator timeout, each recording start and end timestamps. That is precisely the instrumentation VAL-001 needs to produce the latency figures that do not yet exist.

### 6.2 Evaluator wiring is configuration, not code

Which implementations exist is declared in code (discovered via entry points, so a custom evaluator is a package you install). Which of them are enabled *in this deployment*, with what parameters, is YAML:

```yaml
# deploy/evaluators.hosted.yaml
evaluators:
  - impl: bedrock-guardrails          # registry KEY, never an import path
    serves:
      - {family: prompt-attack, capability: injection}
      - {family: pii,           capability: email}
    params:
      guardrail_id: gr-abc123
      guardrail_version: "3"
      region: us-east-1
    timeout_ms: 800

  - impl: deterministic
    serves:
      - {family: pii, capability: email}
    params:
      rules_path: /etc/guardrails/rules.yaml
    timeout_ms: 50
```

```yaml
# deploy/evaluators.local.yaml — full test suite, zero AWS
evaluators:
  - impl: deterministic
    serves: [{family: prompt-attack, capability: injection},
             {family: pii, capability: email}]
    params: {rules_path: tests/fixtures/rules.yaml}
    timeout_ms: 50
```

Rationale: Phase 4 ships this same container into customer accounts with different guardrail identifiers, regions and possibly custom evaluators. If wiring lived in Python, every deployment variant would be a code change — breaking "packaged for customer-managed deployment without changing the connector contract".

`impl` is a registry key rather than an import path, because an import path in configuration is code execution from configuration, and in hosted mode that configuration crosses a trust boundary.

Validated like everything else, and unknown `impl`, unknown capability, or a parameter mismatch fails the boot rather than starting a container silently missing a detector:

```python
class EvaluatorWiring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impl: str
    serves: list[Capability]
    params: dict[str, Any] = {}
    timeout_ms: int                      # required — no default
```

### 6.3 Capability manifest and bind-time compatibility

Because two deployments can serve the same `family` with different implementations, a policy bound to a runtime lacking one of its evaluators would produce different decisions in different modes, violating AGR-005.

`DATA-ARCHITECTURE.md` §1.5 already gives the binding a "Runtime compatibility requirements" field that nothing currently uses. This design activates it:

```
GET /v1/capabilities → {"serves": [{"family": "pii", "capability": "email"}, ...]}
```

Binding creation checks the target runtime's manifest against the policy's required capabilities and **rejects the binding** if unsatisfiable. A missing evaluator becomes a bind-time error with a clear message instead of a `DetectorUnavailable` in production that silently degrades under whatever behaviour was configured.

## 7. Clients

### 7.1 SDK, and the bootstrap gap

The SDK must apply the workload's configured timeouts and failure behaviour. But policy resolves server-side, and the moment the client most needs that configuration is exactly when the server is unreachable. The architecture documents require the behaviour (`INTEGRATION-ARCHITECTURE.md` §4.4, AGR-012) without specifying how a client learns it. This design fills the gap:

```
GET /v1/workloads/{id}/client-config → stage timeouts + client-side failure behavior
```

```python
client = GuardrailsClient(endpoint=..., tenant_id=..., workload_id=...)
await client.bootstrap()        # fetched and cached; refreshed periodically
```

If bootstrap has never succeeded and no explicit override was supplied at construction, the client **refuses to evaluate**. Same rule as the runtime's `unresolvable_policy_behavior`: an unset failure behaviour is a configuration error, never a silent `allow`.

The rest of the SDK is transport and ergonomics only — a request context owning the correlation identifier, `evaluate(stage, text)`, and a `Decision` exposing `blocked` and `effective_text`. No detection, no decision caching, no policy, no authorization.

### 7.2 LiteLLM connector

Roughly 150 lines, and the only package permitted to `import litellm`.

```python
class GuardrailsHook(CustomLogger):
    async def async_pre_call_hook(self, data, ...):
        self._ctx = self._client.request(correlation_id=data.get("x-correlation-id"))
        d = await self._ctx.evaluate(stage="input", text=self._extract(data))
        if d.blocked:  raise LiteLLMBlocked(self._safe_rejection(d))
        return self._substitute(data, d.effective_text)

    async def async_post_call_success_hook(self, response, ...):
        d = await self._ctx.evaluate(stage="output", text=self._extract(response))
        if d.blocked:  return self._safe_fallback(d)
        return self._substitute(response, d.effective_text)
```

Everything LiteLLM-shaped is confined to `_extract` and `_substitute`. Connector-health telemetry — reachability, timeout rate, error rate — is emitted separately from decisions and is **Phase 1 scope**, because under a cooperative posture it is the only bypass *detection* available.

## 8. Audit

```python
class AuditRecord(BaseModel):
    audit_id: str
    correlation_id: str
    tenant_id: str
    workload_id: str
    stage: Stage
    policy_name: str
    policy_version: int
    emitted_action: Action
    would_be_action: Action          # observe-mode measurement
    reason_codes: list[ReasonCode]
    evaluator_metadata: list[EvaluatorRef]
    failure_path: str | None         # which AGR-012 mode fired, if any
    latency_ms: float
    content_digest: str | None       # hash + length when retain_content is false
    content: Content | None          # populated only when the policy opts in
```

Emitted for **every** evaluation including failure paths — the failure handler's substitute decision is audited exactly like a normal one, with `failure_path` set. An evaluation that produced no audit record would be indistinguishable from one that never happened.

Buffered SQS → S3 so archive latency stays off the request path, and audit-delivery failure is one of the six configured behaviours rather than an exception reaching the caller.

`retain_content` defaults to false (AGR-014), and the model makes the two fields mutually exclusive by construction.

## 9. Verification, CI and deployment

### 9.1 The VAL-002 acceptance harness

`docker-compose`, written on day one, failing until Phase 1 is genuinely complete:

```
mock-provider          # stands in for the model; makes credential isolation assertable
litellm-a  ─┐          # team A configuration, provider credentials A
litellm-b  ─┼─▶ runtime ─▶ controlplane
gateway-stub┘          # the custom gateway — added, not substituted
```

One test per bullet of the `seed.md` §16 demonstration:

| §16 bullet | Assertion |
|---|---|
| Two instances, same policy | Both decisions cite identical `policy_name` and `policy_version` |
| No shared provider credentials | Runtime configuration contains zero provider credentials; A and B hold different ones |
| Consistent normalized decisions | Same input produces identical `reason_codes` and `would_be_action` |
| One workload observes | `emitted_action == log_only`; the request is byte-identical downstream |
| One workload enforces | Blocked, or content substituted |
| Upgrade and rollback | Bind v4, decisions change; roll back, v3 decisions return exactly |
| Raw persistence disabled | Scan the whole audit sink for input fragments; zero hits (VAL-006) |
| Failures follow configured policy | Fault-inject each of the six modes; assert the configured outcome (VAL-005) |
| Packageable for Mode C | Same conformance suite against a runtime at a different endpoint; client configuration differs **only** by URL |

The last row is a cheap Phase 1 proxy for the Phase 4 claim. If any assertion requires a code change rather than a URL change, the claim is already false.

### 9.2 Conformance suite

`guardrails-testkit` is a **shipped artifact**, not an internal convenience — the custom-gateway team is not this team, and "one logical contract" enforced by cross-team code review is not enforced.

```
guardrails-testkit
├── scenarios/          # (policy, stage, content) → expected Decision
├── conformance.py      # point at ANY client; asserts identical decisions
└── fixtures/           # a runnable Evaluation API stub
```

```python
@pytest.mark.parametrize("client", [raw_http, sdk, litellm_connector, gateway_stub])
def test_identical_decisions(client, scenario):
    assert client.evaluate(scenario).normalized() == scenario.expected
```

### 9.3 Test layers

| Layer | Speed | Needs AWS |
|---|---|---|
| Unit, per package | ms | no |
| Contract (schema round-trip, OpenAPI golden) | ms | no |
| Conformance (testkit) | s | no |
| Acceptance (VAL-002 compose) | min | no |
| Integration (real DynamoDB / S3 / SQS) | min | yes, scheduled |

### 9.4 CI gates

- `import-linter` — dependency direction (AGR-007)
- vendor-token scan of `contract/` and the policy schema (CON-004)
- OpenAPI and JSON Schema golden diff — a contract change appears as a reviewable diff and fails until the version is bumped (AGR-006)
- policy YAML validation for everything under `policies/`
- no-vendor-leak serialisation test over recorded Bedrock responses

### 9.5 Internal deployment

Runtime and control plane as containers on **ECS Fargate behind an ALB**, single AWS account (AGR-013), IAM/SigV4 authentication, TLS, KMS.

Fargate rather than Lambda because Phase 4 ships this runtime into customer accounts and `CONTAINER-ARCHITECTURE.md` already specifies Fargate/EKS for the Guarded Gateway. One container artifact serves the internal deployment, Mode C and later Mode B; Lambda would mean maintaining a second packaging path for the same code.

## 10. Decisions this design owes the registers

Seven ADRs, each a Phase 1 deliverable — otherwise the code silently becomes the decision:

| # | Decision | Current state in `solution-docs/` |
|---|---|---|
| 1 | Evaluation contract wire format — paths, field names, error envelope, version scheme | Absent; only a conceptual field list (§3.2, §3.3) |
| 2 | Policy document schema, with failure-behaviour required-field enforcement | "Policy format" is a Phase 1 deliverable, undefined |
| 3 | Policy storage split — DynamoDB bindings + versioned S3 documents | `TRADE-OFFS.md` recommends it, flagged "not yet an AGR" |
| 4 | Policy distribution mechanism — poll + TTL cache | `CONTAINER-ARCHITECTURE.md` §7.1 records it as TBD |
| 5 | Client bootstrap configuration endpoint | New — fills a gap implied by §4.4 and AGR-012 |
| 6 | Runtime capability manifest and bind-time compatibility check | New — activates the unused binding field in `DATA-ARCHITECTURE.md` §1.5 |
| 7 | Deployment target — ECS Fargate | Not specified; `INVESTMENT-SUMMARY.md` names "API Gateway or ALB" |

Two notes to file against existing open questions:

- **OQ-018 (stage vocabulary).** A five-stage product vocabulary is circulating — INPUT / CONVERSATION / CONTEXT / ACTION / OUTPUT. `INPUT`, `CONTEXT` and `ACTION` map to the reserved `input`, `retrieval` and `tool_call`. `CONVERSATION` has no reserved equivalent, and `INTEGRATION-ARCHITECTURE.md` §3.1 already scopes conversation history *inside* `input`, so it is a content-scoping concern rather than a stage. `ACTION` as described ("permissions, rate limits") crosses into authorization, which AGR-010 excludes and RSK-002 actively watches for. With agent workloads near-future, OQ-018 needs resolving during Phase 1–2 rather than at Phase 5.
- **VAL-002 has a stronger available variant.** As written it is two LiteLLM instances, proving *instance* independence. LiteLLM plus the custom gateway on one policy version would prove *gateway* independence — the actual AGR-003 / PP-006 claim. Proposed as an **addition**, not a substitution; VAL-002 is a defined gate in an L3 document and changing it belongs to its owner.

## 11. Out of scope for Phase 1

Named explicitly to keep `TECHNICAL-RISKS.md` R8 (product over-expansion) from arriving through the side door:

Admin portal or UI · policy signing · marketplace or metering · quotas · tenant registry CRUD and per-tenant workload identity (Phase 2) · Guarded Gateway (Phase 3) · Helm and ECS customer templates (Phase 4) · evaluation stages beyond `input` and `output` (Phase 5, gated on OQ-018) · the `rewrite`, `discard`, `abstain` and `require_approval` actions · platform-managed and network/IAM-enforced postures (OQ-001, deferred by DEC-010).

## 12. Related documents

| Document | Relationship |
|---|---|
| `seed.md` §16, §18 | The MVP component list, demonstration, and Phase 1 deliverables this design implements |
| `50-architecture/COMPONENT-ARCHITECTURE.md` | RT-1…RT-7 and CP-1…CP-4, mapped to modules in §4.1 |
| `50-architecture/INTEGRATION-ARCHITECTURE.md` | The evaluation contract, connector responsibilities, and the SDK's non-responsibilities |
| `50-architecture/DATA-ARCHITECTURE.md` | The six-entity domain model the code's types mirror |
| `50-architecture/EXTENSIBILITY-ARCHITECTURE.md` | The four extension seams; seam 4 fires in Phase 1 |
| `60-delivery/VALIDATION-PLAN.md` | VAL-001, VAL-002, VAL-005, VAL-006 — the tests §9 implements |
| `00-source-of-truth/AGREEMENTS.md` | AGR-003, 005, 006, 007, 010, 012, 014, 015, 018 — the agreements this design makes structural |
| `docs/superpowers/specs/2026-07-24-serving-model-design.md` | The serving model this code serves |
