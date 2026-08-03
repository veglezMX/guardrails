# AI Guardrails — MVP reference runtime

A working implementation of the Guardrails Runtime and one entry point, built
directly from the specifications in `solution-docs/`. Standard library only —
no AWS, no third-party packages, no gateway required to run it.

```
just test        # 67 tests
just demo        # one request through input eval -> model -> output eval
just serve       # POST /v1/evaluate, GET /v1/capabilities on :8080
just policies    # validate every policy document
just caps        # what this deployment can evaluate
```

## What is here, and what specifies it

| Component | File | Specified by |
|---|---|---|
| Wire types, action vocabulary, the clamp | `guardrails/contract.py` | ADR-0005, ADR-0004, ADR-0003 |
| Problem responses | `guardrails/errors.py` | ADR-0005 section 6 |
| Policy schema and validation | `guardrails/policy.py` | ADR-0009 |
| Bindings and workload identity | `guardrails/binding.py` | AGR-017, CON-011 |
| RT-1 intake, RT-7 correlation | `guardrails/intake.py` | COMPONENT-ARCHITECTURE RT-1, RT-7 |
| RT-2 policy resolver + local cache | `guardrails/store.py` | AGR-015, ADR-0011 |
| RT-3 decision engine | `guardrails/engine.py` | COMPONENT-ARCHITECTURE RT-3 |
| RT-4 evaluator adapters | `guardrails/evaluators/` | AGR-004, CON-004 |
| RT-5 audit producer | `guardrails/audit.py` | AGR-014, NFR auditing |
| RT-6 failure handler | `guardrails/failure.py` | AGR-012 |
| HTTP surface | `guardrails/app.py` | ADR-0005 section 4 |
| Entry-point enforcement | `connector/core.py` | AGR-003, AGR-007 |
| LiteLLM binding | `connector/litellm_hooks.py` | INTEGRATION-ARCHITECTURE section 4 |

`deployment/` holds what the control plane distributes: policy documents,
bindings, workload identities, and the deployment's own configuration. The
control plane itself is not built here — its output is hand-authored.

## The properties the tests hold

- A `block` is HTTP `200`. So is a substitute decision from the failure handler.
  `5xx` means no evaluation happened and the client's failure behavior applies.
- A policy missing any failure-behavior key is rejected at validation, naming the
  key. There are no defaults, because a default is undocumented fallback
  behavior from the author's point of view.
- A vendor name anywhere in an evaluator selection is a validation failure.
- The enforcement state clamps the action and nothing else: reason codes,
  guardrail category, and evaluator metadata are byte-identical across
  `observe`, `warn`, `redact`, and `block` for the same input.
- `disabled` skips the evaluators, returns `allow`, and still writes an audit
  record — so a deliberately disabled workload stays distinguishable from a
  removed connector.
- The same policy document produces the same decision whether sensitive-data
  capabilities are served in process or through the Bedrock adapter. Only
  `evaluator_metadata` differs.
- A `redact` ceiling that cannot be satisfied is `transform_failed`, never a
  silent downgrade to `allow` or upgrade to `block`.
- A client receiving an action it cannot enforce applies its configured failure
  behavior. It never treats the unknown value as `allow`.

## Deliberate deviations, and gaps found in the specifications

Each of these is a place where the documents are silent, circular, or in
conflict with themselves. They are implemented one way and recorded here rather
than resolved quietly.

1. **Reason-code middle segment.** ADR-0003 says the segment names the evaluator
   that produced the finding; INTEGRATION-ARCHITECTURE section 3.5 requires codes
   to be stable across engines covering the same category. Both cannot hold. This
   implementation uses the policy's detection family, so codes stay engine-stable,
   and records the implementation in `evaluator_metadata`.
2. **`policy_unresolved` is circular.** AGR-012 assigns its behavior to the
   policy, but the failure is that no policy resolved. Declared at deployment
   level instead (`runtime.json`), required at startup so it is still stated
   rather than defaulted.
3. **Client failure configuration.** ADR-0005 leaves open how a client learns its
   timeout and failure behavior, since that resolves server-side and a timeout is
   exactly when the server is unreachable. The connector reads it from local
   bootstrap configuration, which must be kept consistent with the policy by
   whoever operates the gateway.
4. **ADR-0005's example uses `GR-004:pii:email`.** `GR-004` is System Prompt
   Protection; sensitive data is `GR-003`. This implementation emits `GR-003`.
5. **Policy serialization.** No document states one. JSON is used here, matching
   the wire format's commitment.
6. **Authentication.** ADR-0005 specifies SigV4 against IAM. A bearer credential
   bound to one `(tenant, workload)` stands in locally. The rule it preserves is
   the one that matters: the identifiers in the request body are never trusted on
   their own.

## Not built

The control plane (CP-1 through CP-4), the Guarded Gateway, policy distribution
over the wire, the generated OpenAPI document and its golden-file gate, and the
cross-client conformance corpus.
