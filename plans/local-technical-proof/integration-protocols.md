# Service and gateway protocols

## Local topology

Compose services: management, runtime, gateway-a, gateway-b, mock-model and an on-demand acceptance runner. All join one internal network. The two gateway configurations have distinct workload credentials and model aliases (`proof-a`, `proof-b`) but target the same mock. The derived gateway image is shared; process/configuration/credential instances are independent. Service DNS names are certificate SANs; host-facing SAN includes localhost. Management, runtime and gateway host ports bind to 127.0.0.1 at 8443, 8444, 8401 and 8402 respectively. Mock and internal diagnostic endpoints are not published. Service volumes are separate; gateways cannot mount control/runtime databases.

TLS is mandatory on every HTTP link. Mount CA trust and leaf key/cert files read-only; never disable verification. Local root/admin and service tokens are prepared by bootstrap into 0600 ignored files. Workload credentials are created/rotated through the API and written to restricted connector token files by the CLI; configuration reload through the documented launcher is permitted during onboarding. Policy/state changes subsequently require no file edits or rebuilds.

Startup order is explicit: prepare images/trust and local admin/service credentials; start management and runtime; register the two workloads and seed policy/bindings through their APIs using the CLI/harness; write returned workload secrets to restricted gateway token files; then start the two gateways and runner. Bootstrap does not hand-author workload/binding database rows. Acceptance uses its own disposable project/run directory and repeats onboarding through both interfaces, avoiding collisions with a developer's existing proof state.

## Management to runtime

The runtime authenticates to `GET /internal/v1/snapshot` using a dedicated runtime-service token and ETag. Poll every second with a 500 ms network deadline. A successful 304 refreshes the contact timestamp without changing generation. Parse/check the entire snapshot and swap only after references, floor predicates, schema, capabilities and digest pass. Keep the prior valid snapshot if parsing fails and mark degradation. After swap, `POST /internal/v1/applied` reports generation and digest. Only then can OperationStatus become applied.

Management never waits inside its mutation transaction for a runtime acknowledgement. CLI/API readback polls the operation endpoint with bounded backoff 100–500 ms, five seconds default. Pending operations survive service restart. Runtime acknowledgements cannot lower an applied generation. On runtime restart, readiness is false until a snapshot is fetched; no persisted snapshot is trusted automatically. Age/fallback semantics are in variance-matrix.md.

## Gateway to runtime

Use workload credentials and verified TLS for `GET /v1/client-config`, `POST /v1/evaluate`, and `POST /v1/client-events`. The gateway refreshes configuration in the background; no rules or thresholds enter the gateway. No configuration means no inference. Keep the last known configuration through loss of contact and expose its generation in diagnostics.

Before inference the connector validates the entire supported gateway request. Normalize every message's string content into a segment while retaining a private index-to-message mapping. Send one batch evaluation for input. Store context_id and the pinned ClientConfig in request-local state, not a process-global last-request variable. Apply transformed segments to the same message positions without changing roles. Do not mutate another request's data. A block terminates before model invocation.

After a non-streaming model response, require exactly one text choice and no tool/refusal/multimodal payload. Send output with the input context, same identity and correlation. Enforce it before sending any body bytes to the caller. The runtime consumes a context once. Invalid/expired/mismatched contexts cannot fall back to a newly selected policy. Failure behavior follows variance-matrix.md.

Use native asynchronous HTTPX; no synchronous HTTP in async hooks. No automatic content-evaluation retries. A transport error may occur after the runtime wrote an audit record: this is why each fallback identifies itself as connector-origin and never claims to be that runtime evaluation.

## LiteLLM adapter mechanism

Use the pinned upstream CustomGuardrail extension and mandatory server-configured pre_call/post_call stages (see ecosystem-reference.md). Register the adapter with `default_on=true`; client omission or attempted override cannot remove it. Use the native async pre-call and successful post-call hooks for explicit message mapping and per-turn state. If the upstream integration cannot preserve required error or response metadata, add a narrow connector-owned response adapter inside the derived gateway application; do not edit upstream site-packages or waive the contract. Prove this path in Phase 1 before expanding implementation.

The supported application route is `/v1/chat/completions`; reject other inference paths, guardrail override fields, direct provider keys and model selection outside the gateway's one configured alias. Health routes expose only safe readiness/configuration metadata. Upstream administration, UI and alternate inference routes are not exposed as supported application routes. Registration absence or callback failure must be visible, never an unguarded success.

## Model and fault protocol

The mock implements the minimal chat-completion protocol used by the pinned LiteLLM openai-compatible provider adapter. Its model response is deterministic: clean text, sensitive text or delayed/error response selected by a credentialed proof-only control endpoint. It records request count and received content only in memory, so the harness can assert input redaction and input-block non-invocation. No model API key or live provider is needed; use a local generated mock-service credential and local URL.

Proof-only fault controls support a stalled evaluator worker, runtime stop/restart, management stop/restart, read-only/full audit destination simulation, malformed spans and malformed runtime responses. They are enabled only in the isolated proof profile and require a harness credential, never caller prompt text. Pause/stop real services for availability cases; a pre-raised Python exception alone does not prove them.

## Client fallback delivery

Each gateway appends content-free fallback AuditEvent records to its own 0600 JSONL spool with fsync, then retries batch ingestion in the background using stable event IDs. Runtime deduplicates. Cap spool at 10 MiB and report failed durability if full; do not silently drop a record or recurse. Replay at most once per second, at most 100 events per batch, capped retries per iteration; retain unacknowledged rows. Raw content is never spooled, even with policy retention opt-in. Audit readback after runtime recovery includes these events and their origin. This is delivery resilience for proof evidence, not an immutable production audit service.

## Offline execution

Bootstrap may download/build the exact artifacts. Acceptance uses `--pull never --no-build` and cached packages/images on the internal network. Prepare all model-cost/tokenizer artifacts touched by the selected route. `LITELLM_LOCAL_MODEL_COST_MAP=True` avoids the documented cost-map fetch; a deprecated telemetry flag is not isolation evidence. Verify egress denial using a controlled reachable sentinel on another network, then run all ten rows. The supervising host may manage Docker but may not proxy an external dependency into the stack. Inspect connection attempts and fail if required behavior depends on an external endpoint (see test-strategy.md).
