# Ecosystem reference

External interfaces were checked against official sources on **2026-10-08**. Exact selected versions and installation rules are in [tech-stack.md](tech-stack.md). This document records integration surfaces; it does not claim that the candidate images have been pulled or tested.

## LiteLLM gateway adapter

Use the public `CustomGuardrail` extension from `litellm.integrations.custom_guardrail`. Implement native asynchronous pre-call and post-call hooks for explicit request/response mapping; register the adapter in each gateway's server-owned configuration. Do not call a simulated wrapper and count it as gateway integration.

The pinned v1.104.2 base callback declares these event hook signatures:

```python
async def async_pre_call_hook(
    self,
    user_api_key_dict: UserAPIKeyAuth,
    cache: DualCache,
    data: dict,
    call_type: CallTypesLiteral,
) -> Exception | str | dict | None:
    ...

async def async_post_call_success_hook(
    self,
    data: dict,
    user_api_key_dict: UserAPIKeyAuth,
    response: LLMResponseTypes,
) -> Any:
    ...
```

These signatures are interface references, not implementation scaffolding. `UserAPIKeyAuth` is from `litellm.proxy._types`, `DualCache` from `litellm.caching.caching`, and the response/call annotations from `litellm.types.utils`. Input modifications return the modified request; output mapping returns the resulting response. [Pinned callback base source](https://raw.githubusercontent.com/BerriAI/litellm/v1.104.2/litellm/integrations/custom_logger.py)

The public documentation establishes `CustomGuardrail` as the extension point. Check its installed inheritance, dispatcher behavior, accepted kwargs and actual return propagation with `just proof-integration smoke` before expanding implementation; documentation examples alone do not establish the complete pinned integration. [Official custom guardrail documentation](https://docs.litellm.ai/docs/proxy/guardrails/custom_guardrail)

Use `pre_call`, not `during_call`, for input blocking/redaction: the latter runs alongside inference. Use the non-streaming post-call hook for response enforcement. Ordinary post-call hooks on streams cannot establish that unsafe output was withheld before delivery. Request shapes and explicit unsupported-operation rejection are authoritative in [input-output-contract.md](input-output-contract.md). [Proxy callback timing](https://docs.litellm.ai/docs/proxy/call_hooks)

The adapter maps the request and response representations to the canonical models in [data-schema.md](data-schema.md); it must preserve the relationship between individual message contents and evaluated segments. Gateway A and gateway B load independent configuration while using the same built adapter.

LiteLLM documentation shows exceptions rejecting calls, but it does not prove this project's precise HTTP status, safe error envelope, or decision-correlation fields. Test those outcomes through the real pinned gateways. Also prove that a blocked input never reaches the mock model, a redacted input reaches it only after transformation, and fail-open invokes it successfully.

Configure the two adapter stages in server-owned `guardrails` entries with `litellm_params.mode` set to `pre_call` and `post_call` respectively and `litellm_params.default_on: true`. LiteLLM documents this setting for invocation on every request. A caller omitting a `guardrails` field, or sending its own guardrail/workload metadata, must not bypass the configured adapter. Verify that requirement in the pinned-image tests. [Guardrail configuration](https://docs.litellm.ai/docs/proxy/guardrails/quick_start)

The required binding/authentication behavior belongs to [architecture.md](architecture.md) and [component-specs.md](component-specs.md).

## Offline operation

LiteLLM normally supports many outbound provider and integration calls. For this proof, configure only the local mock route and the local guardrails endpoint; leave cloud credentials and remote observability integrations unconfigured.

`LITELLM_LOCAL_MODEL_COST_MAP=True` selects the bundled cost map instead of the startup HTTP fetch. Set it before importing/starting the gateway. [Official model-cost-map instructions](https://docs.litellm.ai/docs/proxy/custom_model_cost_map)

The `--telemetry` CLI argument in LiteLLM v1.104.2 is explicitly a deprecated no-op. It must not be cited as proof that outbound network activity is disabled. [Pinned CLI source](https://raw.githubusercontent.com/BerriAI/litellm/v1.104.2/litellm/proxy/proxy_cli.py)

Bootstrap must discover and prepare any tokenizer/cache assets exercised by the selected route. The acceptance test runs with external connectivity blocked and prepared images only; a missing asset is a reproducibility defect, not permission to fetch it during acceptance. This also catches unanticipated startup and background network dependencies.

FastAPI's default interactive documentation can reference externally hosted assets. The accepted release experience is API and CLI, so keep the JSON OpenAPI endpoint available and disable interactive documentation in the proof configuration. [FastAPI offline documentation assets](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/)

## HTTPX and TLS

Use shared `httpx.AsyncClient` instances for async service calls; await request methods and close clients at application shutdown. Explicitly configure timeouts and limits. HTTPX response status and JSON parsing failures must enter the typed failure path in [input-output-contract.md](input-output-contract.md). [HTTPX async usage](https://www.python-httpx.org/async/)

Construct an SSL context trusting the generated local CA and supply it via `verify`; preserve hostname checking. Set `trust_env=False` for internal service clients so ambient proxy variables do not redirect calls. [HTTPX SSL configuration](https://www.python-httpx.org/advanced/ssl/), [environment variables](https://www.python-httpx.org/environment_variables/)

The gateway must also trust the generated CA for its calls to the mock upstream and guardrails runtime. Validate the effective pinned-gateway TLS settings through a successful trusted call and a rejected untrusted-certificate call; do not infer them from environment-variable names alone.

## Mock model boundary

The mock is a deterministic local HTTP upstream exercised by real LiteLLM routing. It is not a guardrails stub and does not make policy decisions. Its request/response shape and scenario controls are defined in [input-output-contract.md](input-output-contract.md), with models in [data-schema.md](data-schema.md).

Expose only the inference surface needed for the accepted chat contract and test-only scenario/inspection operations on the isolated proof network. Keep received-content inspection in volatile memory and exclude raw payloads from durable logs and acceptance artifacts. The test runner uses synthetic fixture content to prove model invocation and transformations.

TLS, identities, routing, and timeout behavior must remain real between the gateways and mock. A lightweight deterministic upstream avoids model downloads, GPU requirements, nondeterministic responses, and provider availability in the acceptance gate.

## Docker Compose interfaces

Use the plugin command `docker compose` from the repository root. `config --quiet` checks the resolved topology; `config --lock-image-digests` creates a digest override once registries are reachable. Do not retain expanded secret values from unrestricted `config` output as evidence. [Compose config reference](https://docs.docker.com/reference/cli/docker/compose/config/)

An `internal: true` network isolates external connectivity. Use `--pull never --no-build` for acceptance startup after bootstrap. Missing prepared artifacts must fail immediately; containers must not attach a second egress-enabled network for convenience. [Network isolation](https://docs.docker.com/reference/compose-file/networks/#internal), [image pull policy](https://docs.docker.com/reference/compose-file/services/#pull_policy)

Service health checks must check local readiness, not invoke a cloud provider. TLS certificate files and SQLite state use scoped local mounts/volumes. Lifecycle ownership, exposed endpoints, and persistent-state boundaries are canonical in [architecture.md](architecture.md).

## SQLite persistence

Use Python's stdlib `sqlite3` through the owned persistence boundary. Each process owns its connections; do not share a live connection across arbitrary request threads. Bind SQL parameters, explicitly control transaction boundaries, and record the runtime library version. [Python SQLite reference](https://docs.python.org/3.14/library/sqlite3.html)

Persistence schema, concurrency rules, recovery, and audit contents are defined once in [data-schema.md](data-schema.md) and [component-specs.md](component-specs.md). SQLite's presence does not establish durability or rollback acceptance: verify those behaviors by restarting the actual processes and querying their APIs.

## Evidence still required

Metadata compatibility and documentation establish candidate feasibility. Implementation must retain evidence of resolved dependency locks, actual image identities, trusted TLS, mandatory adapter invocation, safe error mapping, input/output transformations, restart recovery, and a complete acceptance run with external connectivity unavailable. Test ownership and pass/fail criteria are in [test-strategy.md](test-strategy.md).
