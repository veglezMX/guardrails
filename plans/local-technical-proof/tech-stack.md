# Technology stack

The user confirmed Python, FastAPI/Pydantic, SQLite, and Docker Compose for this local proof. Scope and deployment responsibilities are authoritative in [architecture.md](architecture.md); configuration and persistent models are authoritative in [data-schema.md](data-schema.md).

## Version baseline

Verified against official sources on **2026-10-08**. These are exact implementation candidates, not an installed or tested combined stack. The bootstrap phase must resolve and validate the selected versions before freezing the implementation lockfiles. No dependencies or images were installed for this plan.

| Component | Candidate | Published | Purpose and source |
|---|---|---|---|
| CPython | 3.14.8 | 2026-09-30 | Owned service runtime; [release](https://www.python.org/downloads/release/python-3148/) |
| FastAPI | 0.143.0 | 2026-10-08 | Management/evaluation HTTP applications; [PyPI](https://pypi.org/project/fastapi/0.143.0/) |
| Pydantic | 2.14.0 | 2026-10-08 | Strict request, policy, configuration, and response validation; [PyPI](https://pypi.org/project/pydantic/2.14.0/) |
| Uvicorn | 0.54.0 | 2026-09-25 | ASGI server; [PyPI](https://pypi.org/project/uvicorn/0.54.0/) |
| HTTPX | 0.28.1 | 2024-12-06 | Async connector/control-plane client and API test client; [PyPI](https://pypi.org/project/httpx/0.28.1/) |
| pytest | 9.1.1 | 2026-06-19 | New contract/integration/acceptance test orchestration; [PyPI](https://pypi.org/project/pytest/9.1.1/) |
| LiteLLM | 1.104.2 | 2026-10-08 | Two real gateway processes; [release](https://github.com/BerriAI/litellm/releases/tag/v1.104.2) |
| Docker Compose | 5.6.0 | 2026-10-02 | Local topology and isolated acceptance runs; [release](https://github.com/docker/compose/releases/tag/v5.6.0) |

Python 3.15 is still a release candidate on the verification date; its final release was postponed to October 9. Do not select prereleases for this proof. [Python release announcement](https://blog.python.org/2026/10/python-3150-rc3/)

FastAPI, Pydantic, Uvicorn, and pytest declare Python >=3.10. LiteLLM declares >=3.10,<3.15. HTTPX declares >=3.8; its classifiers alone do not establish Python 3.14 testing. Resolve the entire dependency graph and exercise the integration before claiming compatibility.

## Dependency and image isolation

Keep owned runtime dependencies to FastAPI, Pydantic, Uvicorn, and HTTPX plus the repository's existing requirements. Use stdlib `argparse`, `sqlite3`, `json`, and `unittest` where applicable; do not add an ORM, CLI framework, model SDK, or AWS integration for this release.

Install plain `fastapi`, not the optional `standard` bundle: that bundle adds unrelated CLI, form, template, and cloud tooling. [FastAPI dependencies](https://pypi.org/project/fastapi/)

Keep code in `mvp/guardrails` and the existing `mvp/connector` package. Preserve the existing 67 unittest tests and their current `just test` entry point. New tests may use pytest without rewriting working unittest coverage. File production belongs to [folder-structure.md](folder-structure.md).

Build the owned service image with `mvp/proof/Dockerfile`, using `python:3.14.8-slim` as the bootstrap candidate base. Verify tag availability/platform in Phase 1 and record its actual digest and OS; this plan does not claim an image pull or select an unverified Debian suite. Run `guardrails.management.app:create_app` and `guardrails.runtime.app:create_app` as separate Uvicorn processes with `--factory`; run `proof.mock_model:create_app` as the deterministic upstream. The CLI entry point in root `pyproject.toml` is `guardrails = "guardrails.cli:main"`. Explicit package discovery resolves `guardrails`, `connector`, and `proof` from the `mvp/` source tree; commands must work from the repository root without changing directories.

Derive a separate gateway image using `mvp/proof/gateway.Dockerfile` from `ghcr.io/berriai/litellm:v1.104.2`, adding only the connector and its necessary dependencies. Retain the upstream gateway's own Python/dependency environment; do not force it to equal the owned service image. The versioned image is documented by [LiteLLM](https://litellm.vercel.app/release_notes/v1.104.2/v1-104-2).

LiteLLM's pinned package metadata permits FastAPI >=0.136.3,<1, Pydantic >=2.12,<3 on Python 3.14, Uvicorn >=0.33,<1, and HTTPX >=0.28,<1. Its dependency tree includes Boto3; this does not authorize AWS calls or credentials. The owned service requires no new cloud SDK. [Pinned package definition](https://raw.githubusercontent.com/BerriAI/litellm/v1.104.2/pyproject.toml)

## Reproducible bootstrap

Host baseline is Linux x86_64, Docker Engine, the selected Compose plugin, and Python >=3.11 for the stdlib bootstrap/orchestration script. The owned service executes on the selected CPython image regardless of the host Python version. Record host, engine and Compose versions; this release does not claim a tested Windows, macOS, ARM, or rootless-Docker matrix.

`mvp/proof/bootstrap.py` runs online once to resolve compatible dependencies, download/build images, and prepare all assets subsequently exercised offline. Generate `requirements.lock` and `dev-requirements.lock` with `python -m pip freeze` from fresh, successfully resolved runtime/dev environments matching the image's CPython version and target platform. Freeze third-party packages before installing the local project, avoiding machine-specific editable paths. Exact direct constraints come from `pyproject.toml`; this plan adds no separate lockfile tool.

`pip freeze` reports installed versions; it does not itself solve or hash a dependency lock. Download the resulting pinned requirements with `python -m pip download`, retain the target-platform wheelhouse under `.proof/`, and generate a SHA-256 manifest of every retained artifact. Reinstalls verify that manifest and use only the prepared wheelhouse. Record the pip version actually used. [pip freeze](https://pip.pypa.io/en/stable/cli/pip_freeze/), [pip download](https://pip.pypa.io/en/stable/cli/pip_download/)

Record the gateway's installed-package snapshot independently. Record upstream and derived image digests, platform, Python versions, lockfile checksums, source commit, Docker Engine version, and Compose version in the proof manifest. Later normal bootstrap runs consume the recorded locks; a compatibility fix that changes pins must regenerate evidence and document the reason.

Resolve actual image digests from the registry during bootstrap; this plan does not supply invented digests. `docker compose config --lock-image-digests` can produce a digest override. Build and record locally derived image identities before acceptance. [Compose config](https://docs.docker.com/reference/cli/docker/compose/config/)

Record `sqlite3.sqlite_version` from the running image. Python ships the module, but the underlying SQLite library version is build-dependent. Use explicit connection/transaction ownership as specified in [component-specs.md](component-specs.md); no separate database service is required. [Python SQLite reference](https://docs.python.org/3.14/library/sqlite3.html)

During online image preparation, install the image distribution's OpenSSL package with its package manager; record the actual package and `openssl version` in the artifact manifest. `mvp/proof/bootstrap.py` invokes certificate generation in that prepared service image, avoiding a host OpenSSL dependency. Generate a local CA and service certificates with the service DNS names and exposed loopback names in their SANs. Store generated private material only in ignored `.proof/` state. Mount the CA into every caller and verify certificates; `verify=False` is not an acceptance configuration. Certificate generation is local proof setup, not production certificate lifecycle automation.

## Runtime wiring and configuration

`mvp/proof/compose.yaml` runs separate control-plane, runtime, mock-model, gateway-A, gateway-B, and runner processes. `mvp/proof/gateway-a.yaml` and `mvp/proof/gateway-b.yaml` independently supply gateway configuration. Configuration field names, defaults, identifiers, and timeout values are owned by [data-schema.md](data-schema.md), avoiding a second settings definition here.

Use a Compose network with `internal: true`, local TLS between services, and explicit loopback bindings for the endpoints required from the host. Keep state and certificate volumes scoped to this proof. [Compose networks](https://docs.docker.com/reference/compose-file/networks/)

Set `LITELLM_LOCAL_MODEL_COST_MAP=True` before gateway startup. Bootstrap must include tokenizer/cache assets actually required by the pinned gateway and mock route. The telemetry flag is not evidence of isolation; see [ecosystem-reference.md](ecosystem-reference.md).

Create shared HTTPX clients during application lifespan, with explicit CA trust, deadlines, connection limits, and `trust_env=False` for these internal service clients. Close them at shutdown. Inject repositories, evaluators, and clients at startup rather than instantiate clients in each request. Async handlers must not perform blocking network calls.

Use server-side validated settings, never client-supplied metadata, to select workload credentials or evaluation endpoints. Startup must fail on missing required secrets, invalid policy/configuration, unreadable CA files, or unavailable mandatory local state, following [input-output-contract.md](input-output-contract.md).

## Task runner contract

All commands run from the repository root. Existing targets remain operational; the proof targets below are **future implementation deliverables**, not commands claimed to exist today.

| Command | Required result |
|---|---|
| `just test` | Existing unittest regression suite |
| `just verify` | Existing repository documentation checks; not runtime acceptance |
| `just proof-bootstrap` | Dependency/image locks, build/pull, local TLS, and prepared disposable proof state |
| `just proof-unit` | Owned logic tests; optional profile `foundation`, `management`, `runtime`, or `all` (default) |
| `just proof-contract` | API/schema, adapter, and CLI checks; optional profile `foundation`, `management`, `runtime`, or `all` (default) |
| `just proof-integration smoke` | Phase 1 gate: installed hook signatures/dispatch, real LiteLLM route, local TLS trust, mock inference, and minimal before/after adapter invocation |
| `just proof-integration` | Real service integration; optional profile `smoke`, `management`, `runtime`, or `all` (default) |
| `just proof-acceptance` | Repeatable release demonstration, offline isolation, and retained acceptance evidence |
| `just proof-down` | Stop/remove only this proof's containers and network; preserve evidence and persistent state |
| `just proof-reset` | Explicitly reset disposable proof state; preserve committed sources and retained evidence |

Acceptance starts prepared images with `--pull never --no-build`; it must not silently run bootstrap or download missing dependencies. Compose `pull_policy: never` fails when a required image is absent. [Compose pull policy](https://docs.docker.com/reference/compose-file/services/#pull_policy)

Profiles select the exact required test lists defined in [quality-gates.md](quality-gates.md). Missing or skipped required tests fail the selected gate; unknown profiles fail argument validation. Omitting a profile selects `all`. The smoke gate uses prepared artifacts and real service processes; passing it does not satisfy lifecycle, fault, or release acceptance gates.

The runner first demonstrates that an external connection cannot succeed, then completes the required local checks and writes a machine-readable result. Failures return a nonzero exit status; logs and manifests remain available after `proof-down`. The authoritative checks and evidence semantics are in [test-strategy.md](test-strategy.md).
