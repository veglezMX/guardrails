**Title: ADR-0005 — Evaluation Contract Wire Format and Versioning**

**Authority level:** L1 (once Accepted)
**Status:** Draft
**Date:** 2026-07-25
**Derived from:** seed.md + AGREEMENTS.md

---

# ADR-0005: Evaluation Contract Wire Format and Versioning

**Status:** Proposed
**Date:** 2026-07-25
**Owners:** Technical lead, architecture lead
**Related agreements:** [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)
**Related ADRs:** depends on [ADR-0004](./ADR-0004-enforcement-ladder-states-and-decision-actions.md) (`enforcement_state`) and [ADR-0003](./ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md) (`guardrail_category`)

While this ADR reads `Proposed` it binds no one. It partially fulfils the **API + SDK integration model** candidate in the [ADR backlog](./README.md#suggested-backlog-of-future-adrs); the client-side responsibilities half of that candidate remains open (see [Scope boundaries](#scope-boundaries)).

## Context

[AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) and [CON-005](../../00-source-of-truth/CONSTRAINTS.md) require one logical evaluation contract across all three deployment modes, and require that "contract changes are versioned and evaluated for backward compatibility before release."

**There is no artifact to version.** [INTEGRATION-ARCHITECTURE.md §3.2 and §3.3](../INTEGRATION-ARCHITECTURE.md) define the request and decision as *field sets* — a table of what each must carry, explicitly labelled a "conceptual shape." No document in `solution-docs/` specifies a serialization, a field name, a URL path, an HTTP status mapping, an error envelope, or a version scheme.

Three consequences follow, and all three are live:

- **The central product claim is unfalsifiable.** "One contract across three modes" and "policy portability across every integration path" cannot be tested, demonstrated, or breached, because nothing states what the contract *is*. Every claim resting on it in [PROOF-POINTS.md](../../40-sales-deck/PROOF-POINTS.md) inherits that.
- **Three clients are about to invent three contracts.** The LiteLLM connector, the generic SDK, and a consumer-operated custom gateway are all Phase 1–2 concerns ([AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)). Each author will make the same set of undocumented choices, differently.
- **The compatibility discipline AGR-006 mandates has nothing to operate on.** "Evaluated for backward compatibility before release" presupposes a prior release to compare against.

"Evaluation contract" is the first named Phase 1 deliverable in `seed.md` §18. This ADR is that deliverable's design record.

The question this ADR resolves: **what concrete wire format does the Evaluation API expose, and by what rules does it change?**

## Decision

### 1. Transport and encoding

Synchronous **HTTPS** ([CON-008](../../00-source-of-truth/CONSTRAINTS.md)), request and response bodies **`application/json`**, resource-oriented paths, authenticated by **AWS SigV4** against IAM ([CON-010](../../00-source-of-truth/CONSTRAINTS.md), [SECURITY-ARCHITECTURE.md §5](../SECURITY-ARCHITECTURE.md)).

Field names are **`snake_case`**. Enumerated values are **lower `snake_case`**. Timestamps are RFC 3339 with an explicit offset. There is no field-name abbreviation: `correlation_id`, never `corr_id`.

### 2. Versioning

The contract version is a **major version in the URI path** — `/v1/evaluate`. It is distinct from, and never conflated with, the policy version (an integer on a policy document) or the product/container version.

| Change | Classification | Requires |
| --- | --- | --- |
| Add an optional request field | Minor | No client change |
| Add a response field | Minor | No client change; clients must ignore unknown response fields |
| Add a value to a response enum (a new action, a new stage) | **Major** — see §7 | New path prefix; plus bind-time compatibility refusal |
| Add a required request field | **Major** | New path prefix |
| Remove or rename any field | **Major** | New path prefix |
| Narrow validation on an existing field | **Major** | New path prefix |
| Change the meaning of an existing value | **Major** | New path prefix |

Two majors may be served concurrently during a migration window. There is no header-based or media-type negotiation.

### 3. The OpenAPI document is generated, and is the normative artifact

The **OpenAPI 3.1 document is generated from the runtime's own request and response models**, published as the contract's normative artifact, and committed to the repository as a golden file. A continuous-integration check fails when the generated document differs from the committed one.

This makes an unversioned contract change a build failure rather than a review omission, and it makes drift between the published contract and the running service structurally impossible — the runtime cannot serve a shape its published contract does not describe, because the contract is derived from the shape.

### 4. Phase 1 surface

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/evaluate` | Evaluate content at a stage; returns a `Decision` |
| `GET` | `/v1/capabilities` | The evaluator capabilities this deployment can serve |

Additional endpoints join the same `/v1` major and the same rules. The control plane's management endpoints are a separate surface with independent paths but the same versioning scheme and the same error envelope.

### 5. Request and decision bodies

The request carries the [§3.2](../INTEGRATION-ARCHITECTURE.md) field set exactly:

```json
{
  "tenant_id": "acme",
  "workload_id": "support-summarizer",
  "stage": "input",
  "content": { "kind": "text", "text": "…" },
  "correlation_id": "018f3a2b-…",
  "policy_ref": { "name": "pii-baseline", "version": 3 }
}
```

`policy_ref` is optional and **advisory only** — the runtime resolves and authorizes the effective policy server-side, per §3.2. A client cannot obtain a weaker policy by asserting one.

`content` is a **discriminated union tagged by `kind`**, with exactly one member in Phase 1: `text`. Reserved future stages carry structurally different payloads — `retrieval` a document set with source metadata, `tool_call` a tool name with arguments — and the tag is what makes adding them additive rather than a major version bump. Shipping `content` as a bare string would make every future stage a breaking change that must then remain compatible across three deployment modes indefinitely ([TECHNICAL-RISKS.md](../TECHNICAL-RISKS.md) R8).

The decision carries the [§3.3](../INTEGRATION-ARCHITECTURE.md) field set, plus `guardrail_category` from [DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension) and `enforcement_state` from [ADR-0004](./ADR-0004-enforcement-ladder-states-and-decision-actions.md):

```json
{
  "action": "redact",
  "allowed": true,
  "enforcement_state": "redact",
  "reason_codes": ["GR-004:pii:email"],
  "guardrail_category": "GR-004",
  "policy_name": "pii-baseline",
  "policy_version": 3,
  "evaluator_metadata": [{ "name": "bedrock-guardrails", "version": "2026-05-01" }],
  "transformed_content": { "kind": "text", "text": "mail me at [REDACTED]" },
  "correlation_id": "018f3a2b-…",
  "audit_id": "01J7…",
  "evaluation_latency_ms": 42.7
}
```

`transformed_content` is present when and only when `action` is `redact`.

### 6. Status codes: a `block` is a success

**Any completed evaluation returns `200`, whatever its action** — including `block`, and including a substitute decision produced by the failure handler under a configured AGR-012 behaviour. A blocked request is a successful evaluation with a restrictive outcome, not an error, and encoding it as one would make every client's error path also its enforcement path.

| Status | Meaning | Body |
| --- | --- | --- |
| `200` | Evaluation completed | `Decision` |
| `400` | Malformed body | Problem |
| `401` / `403` | Authentication or authorization failure | Problem |
| `413` | Content exceeds the deployment's size ceiling | Problem |
| `422` | Well-formed but invalid — unknown `stage`, unknown `content.kind` | Problem |
| `429` | Quota or rate limit (Phase 2) | Problem |
| `5xx` | **The runtime itself is unavailable** | Problem |

The `5xx` row carries a rule: the runtime never returns `5xx` to express an *evaluation* outcome. A detector outage, an evaluation timeout, or an unresolvable policy all produce a `200` with a substitute decision per the workload's configured failure behaviour. A `5xx` means no evaluation happened at all, and the **client's** configured failure behaviour applies.

Errors use **RFC 9457 `application/problem+json`**, extended with a `reason_code` member drawn from the same taxonomy as decisions:

```json
{
  "type": "https://guardrails.example.com/problems/content-too-large",
  "title": "Content exceeds the configured size ceiling",
  "status": 413,
  "detail": "Content of 2097152 bytes exceeds the ceiling of 1048576 bytes.",
  "reason_code": "GR-000:platform:content_too_large",
  "correlation_id": "018f3a2b-…"
}
```

`correlation_id` appears on every problem response so a rejected request is still traceable.

### 7. Closed vocabularies: prevented at bind time, failed closed at runtime

Closed vocabularies — `stage`, `action`, `enforcement_state`, `content.kind` — are validated strictly on input: an unrecognized value is `422`, never silently coerced or ignored.

On **output**, two mechanisms apply in order, and the first is primary.

**Primary — a client must never receive an action it cannot enforce.** [EXTENSIBILITY-ARCHITECTURE.md §5](../EXTENSIBILITY-ARCHITECTURE.md) is explicit that "unknown-action tolerance is **not** acceptable for enforcement clients": activating a reserved action requires defining its enforcement semantics per connector type first, and the control plane's compatibility validation must refuse to bind a policy using a new action to a workload whose client cannot enforce it. That refusal, not client tolerance, is how new actions are introduced safely. This ADR adopts that position unchanged.

**Backstop — if a client nevertheless receives an unrecognized `action` or `enforcement_state`, it must apply the workload's configured failure behaviour**, exactly as for an evaluation timeout. It must not ignore the value and must not treat it as `allow`. This is a defence against a bind-time check that was skipped, misconfigured, or raced — never a licence to skip it.

Because the primary mechanism is bind-time refusal rather than runtime tolerance, §2 classifies a new response-enum value as **major**. Introducing `rewrite`, `discard`, `abstain`, or `require_approval` therefore follows the full seam-3 process in EXTENSIBILITY-ARCHITECTURE §5, including the register-level governance gate — it is not a quiet additive change.

## Scope boundaries

This ADR does **not** decide:

- **The policy document schema.** Separate artifact, separate ADR; a policy is never transmitted on the evaluation path.
- **How a client learns its timeouts and failure behaviour.** [INTEGRATION-ARCHITECTURE.md §4.4](../INTEGRATION-ARCHITECTURE.md) requires a client to apply the workload's configured behaviour on timeout, but that configuration resolves server-side and the timeout case is precisely when the server is unreachable. §7 above depends on the client *having* that configuration. This gap is real, is not closed here, and needs its own record.
- **Authentication topology beyond SigV4**, which [SECURITY-ARCHITECTURE.md §5](../SECURITY-ARCHITECTURE.md) owns.
- **Control-plane management endpoints**, beyond stating that they inherit this versioning scheme and error envelope.

## Alternatives considered

- **Alternative: gRPC with Protocol Buffers.**
  **Why not chosen:** [INTEGRATION-ARCHITECTURE.md §5](../INTEGRATION-ARCHITECTURE.md) commits the generic surface to being callable by "any application, custom gateway, or agent framework capable of making an authenticated HTTP call." A protobuf toolchain is a real adoption barrier for a consumer-operated custom gateway and for Mode C customer environments, and the platform's latency budget is unmeasured ([VAL-001](../../60-delivery/VALIDATION-PLAN.md) exists to produce it) — so optimizing encoding before there is a number to optimize against inverts the evidence order this programme otherwise keeps.

- **Alternative: a hand-authored OpenAPI document as the source of truth, with server and clients generated from it.**
  **Why not chosen:** Creates two sources of truth that drift, and the drift is silent — the document says one thing while the running service does another, which is the precise failure AGR-006 exists to prevent. Generating the document from the models and gating it with a golden-file diff preserves the reviewable artifact and the deliberate-change gate without the drift.

- **Alternative: header or media-type versioning (`Accept: application/vnd.guardrails.v1+json`).**
  **Why not chosen:** Less visible in logs, traces, and audit records, where the contract version is genuinely useful diagnostic information; harder for the conformance suite to parameterize; and no benefit at a surface this small. URI versioning makes the version legible at every layer that will need it.

- **Alternative: express `block` as HTTP `403`.**
  **Why not chosen:** Conflates the enforcement path with the error path in every client, so an authentication failure and a policy block become indistinguishable without inspecting the body. It also loses the decision structure — reason codes, policy version, audit id — precisely on the outcomes that most need auditing.

- **Alternative (chosen): JSON over HTTPS, URI major versioning, generated OpenAPI gated by a golden diff, RFC 9457 errors, bind-time vocabulary refusal with a fail-closed runtime backstop.**
  **Why accepted:** Reachable by every client the architecture names, including ones this team does not build; makes an unversioned contract change a build failure; and keeps the action vocabulary governed by the existing seam-3 process rather than routing around it.

## Consequences

### Positive

- AGR-006 becomes enforceable rather than aspirational: there is now an artifact to version, a rule set for changing it, and a CI gate that fails when it changes without a version.
- The conformance suite becomes writable — "identical decisions across raw HTTP, SDK, and connector" is now a comparison of concrete JSON documents.
- The consumer-operated custom gateway team receives a specification rather than a description, removing the largest coordination risk in the Phase 1–2 client set.
- The unknown-value rules are stated once, in the contract, rather than reinvented per client: bind-time refusal is primary (preserving EXTENSIBILITY-ARCHITECTURE §5's position that enforcement clients must never meet an action they cannot enforce), with a fail-closed runtime backstop for when that check is skipped or raced.
- `guardrail_category` and `enforcement_state` are carried from the start, so [DEC-013](../../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension) and [ADR-0004](./ADR-0004-enforcement-ladder-states-and-decision-actions.md) do not each require a later contract revision.

### Negative

- The generated-OpenAPI approach couples the published contract to the runtime's model layer; a refactor of internal model classes can change the published document, and the golden diff will (correctly) block it until the change is deliberate.
- Committing to `snake_case` JSON means every client language does some naming translation at its boundary. This is accepted in exchange for one canonical spelling in the contract, the logs, and the audit record.
- Two concurrent majors during any migration window means the runtime carries two request-parsing paths and the conformance suite runs twice.

### Risks

- **A client ignores an unknown action instead of failing closed**, converting a future `discard` action into a silent allow. This is the most dangerous single misreading of this ADR; mitigated by making it an explicit, mandatory case in the client conformance suite rather than prose in a document.
- **`5xx` is used to express a fail-closed evaluation outcome** by an implementer who finds it more natural, which would make the client's transport error path and its enforcement path the same code again. Mitigated by a conformance case asserting `200` for a failure-handler substitute decision.
- **The client-config gap in [Scope boundaries](#scope-boundaries) is left open too long.** Until it is closed, each client will improvise its own timeout and failure-behaviour source, which reintroduces at the client layer exactly the divergence this ADR removes at the wire layer.

## Validation required

This ADR is validated when:

1. A generated OpenAPI 3.1 document is committed, and a CI job demonstrably fails on an unversioned change to any request or response model.
2. The same scenario corpus, executed through raw HTTP, the SDK, and the LiteLLM connector, yields byte-identical `Decision` documents modulo `audit_id`, `correlation_id`, and `evaluation_latency_ms`.
3. A failure-handler substitute decision — produced by fault-injecting an evaluator outage — returns `200` with a well-formed `Decision`, not a `5xx`.
4. A client presented with an unrecognized `action` value applies the workload's configured failure behaviour, verified by a conformance case using a synthetic response.
5. [INTEGRATION-ARCHITECTURE.md](../INTEGRATION-ARCHITECTURE.md) §3.2 and §3.3 cite this ADR as the normative serialization of the field sets they describe.

## Related agreements

- [AGR-006](../../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) — the contract this ADR makes concrete and versionable.
- [AGR-003](../../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) — the format must be reachable by clients other than LiteLLM, which is why HTTP/JSON wins over a toolchain-bound encoding.
- [AGR-005](../../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) — no gateway-specific or vendor-specific shape appears anywhere in this format.
- [AGR-007](../../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) — the wire format carries decisions, never policy; `policy_ref` is advisory only.
- [AGR-017](../../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) — request and decision bodies map onto the six domain entities and introduce no parallel vocabulary.
- [CON-005](../../00-source-of-truth/CONSTRAINTS.md), [CON-008](../../00-source-of-truth/CONSTRAINTS.md), [CON-010](../../00-source-of-truth/CONSTRAINTS.md) — one contract across modes; TLS; IAM authentication.

## Related documents

- [Integration Architecture](../INTEGRATION-ARCHITECTURE.md) — §3.1–§3.7, the field sets this ADR serializes
- [Security Architecture](../SECURITY-ARCHITECTURE.md) — §5, the identity substrate SigV4 sits on
- [ADR-0004 — Enforcement-ladder states and decision actions](./ADR-0004-enforcement-ladder-states-and-decision-actions.md)
- [ADR-0003 — Reason-code taxonomy and category audit dimension](./ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md)
- [Validation Plan](../../60-delivery/VALIDATION-PLAN.md) — VAL-001, VAL-002
