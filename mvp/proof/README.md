# Run the local technical proof

The proof uses a durable management API, a separate evaluation runtime, two independent real LiteLLM gateways and a deterministic HTTPS mock. The API-backed CLI registers workloads, publishes immutable policies, governs bindings and reads evidence. Phase 4 regression gates cover this integration; the two complete ten-row release reports and independent QA remain Phase 5. See the [implementation tracker](../../plans/local-technical-proof/progress.md).

Use Linux x86_64, Git, Python **3.14**, `just`, Docker Engine and the Compose plugin. The service images run pinned Python/LiteLLM environments; bootstrap prepares a separate ignored host environment for supervision and CLI commands. The host supervisor version and actual service artifacts are recorded in `.proof/artifacts/manifest.json`. The implementation host uses Compose 5.5.1; its recorded behavior, rather than the planned 5.6.0 candidate alone, is tested.

```sh
just proof-bootstrap
just proof-unit
just proof-contract
just proof-integration
just test
```

Bootstrap is the only preparation step that can download dependencies. It reuses the committed locks, verifies cached wheel hashes, builds both images, generates local trust and distinct credentials, and records exact versions/image identities. Rebuild after changing installed service code. Integration starts prepared images with `--pull never --no-build`. Unit/contract and earlier service integration profiles run in network-disabled containers. The full gateway gate uses a fresh internal Compose network and disposable run directory; the supervising host only invokes local APIs and Docker. A reachable external sentinel proves denied container egress. Gateway connection-attempt accounting detects unexpected external dependencies.

The default gates include every earlier profile and the closed inventory in `phase4-cases.json`; missing, skipped or unexpectedly changed cases fail. Optional profiles remain `foundation`, `management`, `runtime` for unit/contract and `smoke`, `management`, `runtime` for integration. `just proof-acceptance` fails explicitly until Phase 5 is implemented. A passing smoke or regression gate is not ten-row release acceptance.

## Start and stop

```sh
just proof-up
just proof-down
```

`proof-up` creates an isolated live run, starts management/runtime/mock, onboards two workloads through the public CLI/API, then starts both gateways. Initial bindings are Observe. `.proof/live.json` records the selected run directory, process and readiness without secrets. Repeating `proof-up` while ready reuses that run. `proof-down` stops the selected project and the legacy smoke project while preserving stores, credentials and logs. It does not stop unrelated containers.

| Service | Local endpoint | Application credential |
| --- | --- | --- |
| Management | `https://localhost:8443` | Run `secrets/management-admin.token` or `tenant-operator.token` |
| Runtime | `https://localhost:8444` | An API-issued workload credential |
| Gateway A | `https://localhost:8401` | Run `secrets/gateway-a.client.token`, alias `proof-a` |
| Gateway B | `https://localhost:8402` | Run `secrets/gateway-b.client.token`, alias `proof-b` |
| Mock/inspection | Internal network only | Separate inference/control credentials |

Always trust `.proof/pki/ca.crt`; never disable verification. Every service link is HTTPS. Private keys and tokens use restricted generated files. Gateways mount neither control nor audit databases. Docker engines that omit published ports for internal-only bridges use the launcher's loopback-only fixed-destination TLS passthrough. It does not terminate TLS, inspect content or provide an external proxy. Direct Compose startup on those engines does not provide working host ports; use the launcher.

`just proof-reset` stops and retires the selected disposable live run. The next `proof-up` starts fresh stores and issues new workload credentials. Previous live-run stores/logs remain under `.proof/state/live-stacks/` for inspection; exported reports and artifacts are preserved. Reset also clears only the historical smoke audit file, not unrelated state. Do not delete `.proof/` as routine cleanup.

## CLI lifecycle

The root `python -m guardrails` and installed `guardrails` entry point now use the versioned HTTPS APIs. Help is offline. Common options work before or after command groups: `--management-url`, `--runtime-url`, `--token-file`, `--ca-file`, `--json`, `--no-wait`, `--wait-timeout`.

```sh
# Read the selected run path without exposing a credential.
proof_run_dir=$(python3 -c 'import json; print(json.load(open(".proof/live.json"))["root"])')

PYTHONPATH=mvp .proof/host-venv/bin/python -m guardrails \
  --token-file "$proof_run_dir/secrets/management-admin.token" \
  --ca-file .proof/pki/ca.crt workloads list --tenant local-proof

PYTHONPATH=mvp .proof/host-venv/bin/python -m guardrails \
  --token-file "$proof_run_dir/secrets/tenant-operator.token" \
  --ca-file .proof/pki/ca.crt bindings get --tenant local-proof --workload gateway-a
```

The [endpoint contract](../../plans/local-technical-proof/api-endpoints.md) lists every command and argument. [API examples](api-examples.md) show writable policy documents. Public commands cover tenant registration; workload registration/read/list/credential rotation; policy validation/publication/read/list; binding creation/read/history/state/update/rollback/removal; floors; operation status; audit queries; capabilities; direct diagnostic evaluation. Diagnostic `evaluate` does not invoke a model.

Mutations wait up to five seconds for an authenticated applied-generation receipt by default. `--no-wait` reports committed desired state only. A wait timeout returns exit 6 and the committed operation; it does not undo or automatically repeat the write. Read the operation/resource before deciding whether another mutation is necessary. All revision-sensitive commands send the prescribed preconditions.

Issued tokens require `--credential-out PATH`. The CLI creates a new 0600 file atomically, refuses to replace existing files, and prints only its path. It never accepts bearer secrets on the command line. If issuance committed but credential delivery failed, read the workload and explicitly rotate its current credential revision.

| Exit | Meaning |
| --- | --- |
| 0 | Read success, applied mutation, or explicitly requested committed/no-wait result |
| 2 | Syntax/schema rejection |
| 3 | Authentication/authorization rejection |
| 4 | Conflict, not found, stale revision or governance rejection |
| 5 | Network/service/file failure |
| 6 | Committed mutation did not apply within the wait deadline |

Errors contain safe codes/correlation, never input values or credentials. Audit queries disclose source completeness. Use `--allow-partial` to request explicitly incomplete results; only security administrators may publish retention-enabled policies or use `--include-content`.

## Supported model path and failures

Only `/v1/chat/completions` is supported. Each gateway accepts its one alias, up to 32 system/developer/user/assistant string messages (including a user), aggregate content up to 1 MiB, `stream=false`, `n=1`, `temperature=0`, and `max_tokens` 1–256. Every message is inspected separately. Tools, multipart content, other routes, arbitrary models and guardrail/identity/provider overrides reject before inference. The deterministic mock uses a native role-preserving OpenAI-compatible adapter; its explicitly registered `o3-proof-mock` capability record selects role-preserving transport to the local endpoint and is not a live model integration. Sampling options have no effect on canned mock responses.

Disabled skips detection; Observe logs; Warn also returns namespaced warning codes; Redact replaces validated spans before inference/delivery; Block stops unsafe progress. Supported detection is the documented ASCII email subset and `+` followed by 8–15 ASCII digits, without separators/extensions. These fixture results do not establish comprehensive PII coverage or live model quality.

Runtime snapshots refresh every second. Input pins policy, generation and failure settings for output even across updates. New turns see applied updates. A never-configured gateway refuses inference. A configured gateway uses its explicit cached/pinned failure action during an outage; fail-open really invokes the model or returns the permitted output. An input fallback without a runtime context keeps output explicitly unpinned/connector-origin. Authentication and unsupported shapes cannot fail open.

The runtime enforces a 500 ms stage budget with a 100 ms audit reserve. Client stage deadlines come from the binding (1,500 ms by default), with 250 ms connection timeout and no content retries. A stalled detector is killed/replaced. Audit failure cannot weaken an existing block/redaction. Gateway fallback events are content-free, fsynced to separate 0600 JSONL spools capped at 10 MiB, and replayed at most 100 at a time, once per iteration no faster than one second. Stable IDs deduplicate at the runtime; failed durability remains visible.

## Evidence and compatibility

Full gateway runs retain configuration, separate stores, logs and offline observations under `.proof/state/gateway-tests/<run-id>/`. Earlier profiles use `.proof/state/management-tests/` and `runtime-tests/`. Default privacy scans cover actual stores, spools and service logs. Authored fixture inputs, restricted credential files and explicit-retention tests are deliberately separate. Volatile mock captures are bounded and cleared between scenarios; raw captures are not exported as evidence.

The live runtime has no worker/response fault endpoints. Integration enables fixed, separately credentialed controls and real service pause/stop/restart. No caller content selects faults. Generated schemas are `openapi.json`, `management-openapi.json` and `runtime-openapi.json`.

The old unversioned single-text server remains only as `guardrails.legacy_cli` for `just serve`, `just policies`, `just caps`, the reference demo and retained baseline assertions. New routes reject those old wire shapes. Legacy files/Bedrock/schema evaluators are not proof-supported capabilities or lifecycle shortcuts. Current progress and remaining release work are in the plan tracker.
