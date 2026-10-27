# Pinned local classifier candidate

This implementation is a Phase 3 candidate. The model quality benchmark and
deployed acceptance gate must pass before enabling it. Neither the preparation
proposal nor the included local worker starts a deployment.

The model is `protectai/deberta-v3-base-prompt-injection-v2` at immutable revision
`90c9989b1a342275dd0d1a95aad283c04e075671`. `classifier_contract.py` fixes every
model/tokenizer file hash and the class mapping SAFE=0, INJECTION=1. Loading is
CPU-only, safetensors-only, local-files-only, without remote code. The upstream
model card declares English support and limitations for jailbreaks, other
languages and system prompts. System parts remain inspected. The separately
reported Spanish benchmark does not enable a Spanish operational profile.

## Runtime registry interface

The registry creates a finite set of endpoints from operator-mounted private
configuration at startup. Never add endpoints to the cache from request data.
The operator supplies exact versioned `ProviderProfile` and `Destination` bytes,
the configured runtime zone, `language_profile="en"`, and the credential resolved
from that provider's exact `credential_ref`.

```python
config = ClassifierEndpointConfig(
    provider=operator_provider,
    destination=operator_destination,
    runtime_zone=runtime_zone,
    language_profile="en",
    bearer_token=private_token,
)
endpoint = ClassifierEndpoint(config, pinned_transport)
configured_endpoints[config.binding_key] = endpoint

# One monitor per configured endpoint, at most 500 ms between refreshes:
await endpoint.refresh_readiness(monotonic() + 0.5)

# Safe from the synchronous metadata thread; no network or mutable policy:
health = endpoint.health_snapshot()
catalog = endpoint.capabilities()

# Lightweight per-policy/stage binding; no new endpoint or empty health cache:
adapter = endpoint.bind(current_eligible_policy, stage)
findings = await adapter.evaluate(rule, parts, detector_deadline)
await adapter.close()  # Does not close the shared endpoint.
```

The registry owns each endpoint's monitor and `await endpoint.close()`. A health
sample expires after one second. Failures invalidate the sample; an older
in-flight success cannot overwrite a newer failure. The snapshot uses a lock
and an immutable record, so synchronous eligibility checks need no event loop.
Tokens never appear in configuration `repr`. `binding_key` is a private opaque
cache identity, including exact resource bytes and a credential fingerprint;
it is not request-selected or a public capability identifier.

The `PinnedHttpsTransport` is operator-configured with the exact CA file and
local-service address registration. The policy adapter authenticates the
compiled policy and residency/provider/destination allowlists, then requires
both selected resources to match the operator bytes exactly. Classifier traffic
always uses `external_processing=False`, even if another policy provider is
approved for external processing. No fallback, redirects, proxy environment,
retries or discovery are added.

Phase 3 capability catalog advertises only input/output:

| Field | Value |
|---|---|
| capability_id | prompt-injection-en |
| evaluator_revision | deberta-v2-90c9989b1a34 |
| stages | input, output |
| part_kinds | text, json |
| actions | allow, warn, block |
| max_input_bytes | 65536 aggregate |
| timeout_ms | 2900, leaving 100 ms of the runtime's 3-second budget for audit |
| zone | exact operator runtime zone |
| status | enabled only while a verified health sample is current |

No redaction capability is advertised because injection findings have no safe
transformation. Findings contain a part ID and JSON
pointer when applicable, a finite score and revision, and no fabricated text
span or copied prompt. The pure adapter accepts later stage names for boundary
testing, but no retrieval/tool stage capability is advertised before Phase 6.

All roles are inspected. JSON object keys are not text leaves; every string
value is. Tokenization covers the complete input, then admits at most 16 windows
for the entire job. Each model window has at most 512 tokens including special
tokens, with a 256-content-token stride and explicit tail coverage. Excess work
returns `inspection_incomplete` before any forward pass, never partial clean.

## Service deployment boundary

`ClassifierApplication` is an ASGI parent with one supervised CPU process and at
most 32 queued requests. Parent admission, body reads, queueing, tokenization and
inference all consume the arrival-based budget. An expired or cancelled active
request kills and reaps the real worker. Readiness stays false during its one
replacement load. An idle-death monitor removes only the dead idle worker and
uses the same single replacement path; active jobs own their own cleanup. A
failed replacement stays unavailable; the failed request is never retried.
Shutdown cancels the monitor and any pending replacement load before reaping.
Uvicorn startup readiness occurs only after model/file/version
verification and successful model loading, with no warmup inference.

The deployment factory `services.classifier.app:create_app` requires:

- `CLASSIFIER_MODEL_DIRECTORY`: absolute, read-only nine-file model directory.
- `CLASSIFIER_TOKEN_FILE`: mounted private ASCII bearer credential, mode 0600 or
  stricter. A Kubernetes projection may use symlinks; the resolved final regular
  file is held by descriptor and checked across the read.
- `CLASSIFIER_LANGUAGE_PROFILE=en`: operator setting, never a request field.

The parent must install TLS with explicit certificate/key paths, disable access
logging, set HTTP connection/body limits, apply registered network restrictions,
and mount private credentials. Use the pinned CPython 3.13.16 image/wheel closure
in `preparation.proposal.json` and `requirements.lock`. `-B` prevents bytecode
writes and `-s` excludes ambient user-site packages. The image must include this service, the `guardrails` package init files,
and `mvp/guardrails/evolution/classifier_contract.py`; the worker never imports
the Python 3.14 policy/storage/runtime adapters. No model download occurs at
startup. The runtime client remains on the repository's Python 3.14 environment.

Shared lock/image/build/chart/registry/runtime edits and live deployment remain
parent-owned. Changed prepared artifacts require a new genuine cold proof.

## Frozen quality evidence

`mvp/tests/evolution/fixtures/injection-heldout-v1.json` has SHA-256
`baf953842a81a6b54640643b2e7fa6fe45ccb89149e7be09b196f27f59df99be`.
Labels were frozen before model loading or inference: English 200 benign and
200 malicious; Spanish 100 benign and 100 malicious. English includes 100
benign quoted-security cases and 150 indirect/retrieval cases. The synthetic
template families and their correlations are disclosed; this is an engineering
corpus, not a population-level accuracy estimate.

The explicit `services.classifier.benchmark` command requires the frozen corpus
and current service source digest. It creates a new private output, runs each
case once through the isolated worker without warmup, preserves every outcome,
and reports separate confusion matrices, operational errors, latency and child
CPU/memory usage. Operational errors fail the English quality gate. Threshold
0.90, recall >=0.90 and false-positive rate <=0.05 are fixed. Labels and thresholds
must not be changed after scoring. This worker benchmark does not claim the
separate HTTP/arrival/audit deployment SLA has passed.

Substantial model inference is currently held by the parent until the active
TLS maintenance cold proof finishes. Unit tests use synthetic tokenizers,
predictions and pipes; they do not load the model or perform live operations.
