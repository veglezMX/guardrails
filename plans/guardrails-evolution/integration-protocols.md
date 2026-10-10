# Integration Protocols

## Trigger Condition

Guardrails connects independently operated LiteLLM gateways, model/evaluator services, management storage and customer runtimes.

## Scope Boundary

Protocols below cover local acceptance and the separately gated Bedrock adapter. They never transfer team gateway administration or model-provider credentials to the shared guardrails service.

## LiteLLM

Keep the existing native CustomGuardrail integration and protective request boundary. Pin and inspect the installed version's hook signatures; the baseline class implements async_pre_call_hook and async_post_call_success_hook with use_native_lifecycle_hooks true. Do not assume a beta generic API or newly documented streaming hook exists in baseline 1.104.2. Phase 6 must inspect the locked package and prove every selected surface; a necessary version upgrade is a deliberate lock change followed by the full predecessor and connector compatibility matrix.

Pre-call: normalize every supported content part, authenticate workload, match the effective provider alias, evaluate input, apply safe transforms and retain context. Post-call: evaluate complete assistant output, then return transformed/allowed response or block. Tool/retrieval stages use explicit application hooks. Never accept caller policy IDs or skip flags as authority. Team manifests name runtime endpoint, trust root, workload, contract version and compatibility evidence; contain no provider keys.

Source: [LiteLLM custom guardrails](https://docs.litellm.ai/docs/proxy/guardrails/custom_guardrail). The [generic API](https://docs.litellm.ai/docs/adding_provider/generic_guardrail_api) is useful upstream context but is not a substitute for testing the installed connector.

## Deadlines and Retries

| Operation | Server budget | Client total / connect | Retry |
|---|---|---|---|
| Deterministic text evaluation | 500 ms, including 100 ms audit reserve | 1500 / 250 ms | One retry only on connection failure before request delivery; same request ID |
| Classifier profile | 3000 ms including audit reserve | 4000 / 250 ms | None after body delivery |
| Image profile | 10000 ms including audit reserve | 11000 / 250 ms | None after body delivery |
| Management mutation | 5 s | 6 s / 250 ms | Idempotency-key replay, at most twice with 100/300 ms backoff |
| Snapshot sync | 2 s | 3 s / 250 ms | Next scheduled poll with bounded jitter |
| Deterministic mock model | 10 s | LiteLLM 10 s | No inference replay |
| Local real model | 45 s | LiteLLM 45 s | No inference replay |
| Buffered stream | 90 s and body cap | Gateway 95 s | None |

Queued work consumes the same budget. Reserve audit time before scheduling detectors. Expired work is cancelled at the process boundary. Online evaluation requires a signed snapshot/revocation lease no older than 60 seconds; a turn can retain old approved policy bytes while a refreshed lease proves that digest remains valid. Revocation or a stricter mandatory minimum invalidates the turn before further release. Customer mode uses bundle expiry and reports the disconnected revocation window honestly.

Transport circuit breaker: open after five consecutive connection/timeout failures in 30 seconds, cool down 10 seconds, admit one half-open probe. Structural denials, authentication failures and content blocks do not count as transport failures. Half-open uses metadata health, not replayed customer content. Breaker state cannot alter approved fail behavior.

## Local Inference

Ollama is a separate model service, accessed by LiteLLM through its supported local model adapter. Prepare a small CPU model, default `qwen2.5:0.5b`, then lock actual model digest and license. Set deterministic sampling where supported for reproducibility, but assert integration invariants rather than exact prose. The minimum real-traffic suite sends ten benign requests per gateway, verifies input/output evaluation attribution and confirms no forbidden destination receives traffic. Tool/stream protocol corner cases use a deterministic mock capable of reproducing malformed/fragmented responses; label those fixtures accordingly. Source: [Ollama API](https://docs.ollama.com/api/generate).

## Evaluator Workers

Classifier and OCR use internal HTTPS `POST /evaluate` with ContentPart/Rule-derived bounded payloads and return findings plus exact artifact revision. Credentials are runtime-service-specific; no tenant direct access. Workers receive only the parts needed by the rule. No retries after delivery; no downloads; no content logs. In-process deterministic engine has the same logical port. Requests include a monotonic remaining budget, bounded server-side by the approved profile.

## Bedrock

Use `bedrock-runtime.apply_guardrail(guardrailIdentifier=..., guardrailVersion=..., source="INPUT" or "OUTPUT", content=[{"text":{"text":...}}])`. Credential comes from an operator-mounted provider-specific secret/profile, never a tenant evaluation payload. Endpoint region must match ProviderProfile and ResidencyProfile. Map vendor action NONE or GUARDRAIL_INTERVENED plus assessments/output into local findings; include safe engine revision metadata, not provider trace data. One call per supported batch; SDK connect/read timeouts fit profile budget; disable hidden retries.

The default profile rejects the call before network I/O. `just evolution-bedrock-live` is separate, requires explicit approved external configuration and supplied credentials, and produces independent evidence. Offline protocol fixtures prove mapping, not that AWS was contacted. Source: [ApplyGuardrail](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html).

## Customer Synchronization

Pull signed per-tenant snapshots using runtime credential and pinned CA, verify Ed25519 signature/expiry/generation, stage, fsync and atomic rename, then acknowledge. Offline import performs identical checks. Online rotation delivers a new trust key signed by the old trusted key; emergency root replacement needs an operator-installed trust bundle. Reject downgrade and removed required engines. Last-known-good runs only until its signed expiry. Telemetry controls are independent of sync: manual offline mode sends neither; automatic sync with telemetry none sends only metadata required for policy synchronization.

Spool maximum 10 MiB or 24h of safe events, whichever first. Replay metadata by event ID, at most 100 per batch; aggregate loss is recorded locally. Required local decision-audit storage failure blocks according to the audit contract; optional telemetry delivery failure never secretly changes the guardrail decision.

## Cross-References

(see component-specs.md §LiteLLMConnector), (see component-specs.md §CustomerRuntime), (see variance-matrix.md), (see api-endpoints.md).
