# Ecosystem Reference

## External Dependencies

References were checked during planning on 2026-10-09/10 UTC. Vendor documentation may describe newer features than the baseline lock. The installed artifact and conformance tests determine compatibility.

### ExistingProof

**What:** Completed text-only local service and two native LiteLLM connectors at commit ad59059f15b230e7fa28819f877d4836dc432e4b.
**Where:** Repository paths `mvp/guardrails/local_contract.py`, `mvp/guardrails/management/`, `mvp/guardrails/runtime/`, `mvp/connector/native_guardrail.py`, `mvp/connector/core.py`, `mvp/proof/`.
**Produces:** Immutable policies/bindings, generation receipts, normalized PII decisions and offline evidence.
**Consumed by:** Compiler/migration adapters and v2 connector development; predecessor regression profile.

The old PolicyDraft.content_digest hashes canonical schema/tenant/policy/version/checks/failure/retention/detector fields. Its Decision enforces input/output only and one GR-003 category. Native LocalProofGuardrail rejects stream=true, non-string messages and tool/function/audio response fields. Do not change those definitions to imply advanced support; introduce v2 models/connector. Structural findings were verified against exact source snippets and the partial SQL graph range was read directly.

### LiteLLM

**What:** Existing LLM gateway; owns provider protocols/routing and inference SSE.
**Where:** [Custom guardrail reference](https://docs.litellm.ai/docs/proxy/guardrails/custom_guardrail).
**Produces:** Normalized chat requests and model responses; connector evaluates both.
**Consumed by:** LiteLLMConnector, OperatedGateway, StreamingGuard.

Verified baseline signatures:
```python
async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type): ...
async def async_post_call_success_hook(self, data, user_api_key_dict, response): ...
async def async_post_call_failure_hook(self, request_data, original_exception,
                                       user_api_key_dict, traceback_str=None): ...
```

Subclass CustomGuardrail, require pre_call/post_call and default_on=True, and use the protective boundary. Inspect locked source for streaming support; do not invent a hook name. Upgrade only with evidence and a changed lock. The beta generic endpoint is not selected as the primary contract.

### PostgreSQL

**What:** Shared transactional store.
**Where:** [Row security reference](https://www.postgresql.org/docs/current/ddl-rowsecurity.html), [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/).
**Produces:** Committed state, audit rows and atomic quota counters.
**Consumed by:** TenantRepository, AuditStore, QuotaService.

Use parameterized SQL and transaction-local tenant context, e.g. `SELECT set_config('app.tenant_id', :tenant_id, true)`. Policies compare tenant_id with current_setting(..., true); missing setting denies. Application role must not own tables or bypass RLS. Verify composite-key foreign references and pool reset behavior.

### Minikube

**What:** Local Kubernetes development cluster, not cloud production infrastructure.
**Where:** [Start](https://minikube.sigs.k8s.io/docs/start/), [Docker driver](https://minikube.sigs.k8s.io/docs/drivers/docker/), [network policies](https://minikube.sigs.k8s.io/docs/handbook/network_policy/).
**Produces:** Dedicated profile, API context, local image store and cluster networking.
**Consumed by:** LocalDeployment, AcceptanceRunner, RecoveryManager.

Use `minikube start -p guardrails-evolution --driver=docker --cni=calico --cpus=6 --memory=8192` with an additional exact Kubernetes version from the artifact lock. Load prepared images through `minikube image load -p guardrails-evolution`; every subsequent Kubernetes operation selects the explicit context. Verify Calico enforcement with traffic probes.

### Ollama

**What:** Real local model service for integration evidence.
**Where:** [API](https://docs.ollama.com/api/generate), [selected small model](https://ollama.com/library/qwen2.5:0.5b).
**Produces:** Actual inference and optional streaming, distinct from deterministic fixtures.
**Consumed by:** Team/operated LiteLLM; never directly by guardrail decision logic.

Native API: POST /api/generate with model, prompt, stream and options; response contains generated response text and completion metadata, with chunks when stream is true. LiteLLM's adapter owns conversion. Record the actual loaded model digest and disable pulls during acceptance. Do not infer deterministic tool correctness from a small model's presence.

### InjectionClassifier

**What:** Existing pretrained classification model, no new training or generative guardrail prompt.
**Where:** [Author model card](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2).
**Produces:** Labels/scores on tokenized input.
**Consumed by:** ClassifierWorker.

Load AutoTokenizer and AutoModelForSequenceClassification from a prepared local directory with local_files_only=True and trust_remote_code=False; inspect config.id2label before mapping injection class. Use eval/inference mode; record max sequence length, tokenizer/model commit hashes and license. The model card's benchmark does not replace our held-out corpus.

### Tesseract

**What:** Local image text extraction.
**Where:** [Command-line reference](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).
**Produces:** OCR text and TSV confidence/box information.
**Consumed by:** MediaInspector.

Invoke a fixed argv with a prepared raster path, stdout output, `-l eng+spa` and TSV output in a sandboxed temp directory. No user-derived shell command, URL fetch or dynamic language install. Normalize/decode first, enforce process deadline and delete transient files after the request. Returned confidence measures OCR extraction, not content safety.

### Bedrock

**What:** Optional external managed evaluator.
**Where:** [Independent ApplyGuardrail API](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html).
**Produces:** Intervention action, assessments and masked output.
**Consumed by:** BedrockAdapter only after residency/provider eligibility.

Protocol fields and mapping live in integration-protocols.md. Public Decision never copies vendor assessment JSON. Local mocks establish adapter shape compatibility, not live service behavior.

### BrowserStack

**What:** Jinja server rendering and self-hosted htmx interactions.
**Where:** [Jinja documentation](https://jinja.palletsprojects.com/en/stable/), [htmx documentation](https://htmx.org/docs/).
**Produces:** Escaped HTML pages/fragments.
**Consumed by:** AdminConsole.

Enable autoescaping and strict undefined variables, supply safe view models and CSRF values, disable htmx eval-dependent features and external requests under CSP. Use server-side authorization for fragment routes as for full pages. No frontend build service or remote assets are required.
