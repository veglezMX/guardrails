# Tech Stack & Configuration

## Runtime Dependencies

Existing exact pins are taken from the baseline pyproject/locks. New ranges below are implementation constraints, not a claim that every combination has already been tested. Phase 1 resolves compatible releases and commits hash locks; Phase 2 pins deployment images/binaries; owning phases pin model/OCR artifacts before their first gate. A lock conflict is a reported dependency decision, never an invitation to remove a required capability.

| Package / runtime | Version constraint | Purpose |
|---|---|---|
| Owned control/runtime Python | >=3.14,<3.15; baseline image 3.14.8 | Preserve current service runtime |
| FastAPI / Pydantic / Uvicorn / HTTPX | 0.143.0 / 2.14.0 / 0.54.0 / 0.28.1 initially | Existing transport/model stack |
| LiteLLM | 1.104.2 initially, exact locked image | Two independent gateways and optional operated gateway; inspect before advanced-surface upgrade |
| PostgreSQL | 18.x, exact image digest at prepare | Shared transactional metadata, quotas and row security |
| SQLAlchemy / psycopg / Alembic | >=2.0,<2.1 / >=3.2,<4 / >=1.16,<2 | Repository adapter and migrations |
| cryptography | >=46,<47 | Ed25519 bundle signing and encrypted backup primitives |
| jsonschema | >=4.25,<5 | Restricted schema validation; reject unsupported keywords before library call |
| Jinja2 / htmx | >=3.1.6,<3.2 / 2.0.11 | Server-rendered UI; htmx vendored with hash, no CDN |
| prometheus-client | >=0.22,<1 | Bounded-label local metrics |
| boto3 | >=1.40,<2, optional group | Bedrock adapter; default profile has no credentials/egress |
| Classifier/OCR Python | >=3.13,<3.14 | Isolated worker compatibility, independent service lock |
| transformers / torch / sentencepiece | >=4.51,<5 / >=2.6,<3 / >=0.2,<0.3 | CPU pretrained injection model; no model code execution |
| Tesseract / Pillow | >=5.5,<6 / >=11,<13 | Local bounded PNG/JPEG OCR and decoding |
| Ollama | >=0.12,<1, exact image/artifact lock | Real local inference; model qwen2.5:0.5b with actual digest |
| Caddy | >=2.10,<3, exact prepared image digest | TLS front for the loopback-only Ollama listener |
| Minikube / Kubernetes / Calico / Helm | >=1.37,<2 / >=1.34,<1.38 / >=3.30,<4 / >=3.18,<4 | Dedicated local cluster and policy enforcement; select a mutually supported combination and lock it |

No plugin, model, browser library or image may be resolved from an unpinned latest tag during a runtime/acceptance run. Preparation records ArtifactRef source, license, size and SHA-256, actual image IDs and tool versions. The local security/resource gate validates chosen versions; if the declared ranges prove incompatible, report the concrete conflict and revise the dependency decision before implementation proceeds.

## Dev Dependencies

Keep pytest 9.1.1 initially. Add pytest-asyncio >=1,<2, Hypothesis >=6,<7, Ruff >=0.12,<1, Playwright Python >=1.55,<2, pip-audit >=2.9,<3. Pin browser binaries during preparation. No Node build is required for the administration UI; a browser test asset may be vendored with a checked hash. Use the existing just task runner. Declare owned dependencies in pyproject.toml and separate worker requirements; build hashes into evolution-requirements.lock and worker lock files without altering frozen predecessor locks casually.

## Configuration Schema

ServiceConfig is authoritative (see data-schema.md §ServiceConfig). Environment variables are a strict prefix mapping: `GUARDRAILS_MODE`, `GUARDRAILS_DATABASE_URL_FILE`, `GUARDRAILS_TLS_CERT_FILE`, `GUARDRAILS_TLS_KEY_FILE`, `GUARDRAILS_TLS_CA_FILE`, `GUARDRAILS_SIGNING_KEY_FILE`, `GUARDRAILS_BACKUP_KEY_FILE`, `GUARDRAILS_RUNTIME_ID`, `GUARDRAILS_ZONE`, `GUARDRAILS_TELEMETRY`. Remaining budgets/limits come from a validated mounted `service.json`; arbitrary environment overrides are rejected. Never print effective secret values. Configuration changes that affect policy eligibility require a new capability manifest and compatible approved snapshot.

## DI / Wiring

Start management by loading validated configuration, credential hashes, PostgreSQL pool, repository ports, IdentityService, AuditStore, PolicyCompiler, PolicyGovernance, BindingService, then ManagementApi and AdminConsole. Start runtime by validating trust roots, registry/artifact hashes, worker readiness, snapshot, ResidencyGuard, QuotaService and AuditStore before marking EvaluationRuntime ready. Background snapshot polling and audit-spool replay have explicit startup/shutdown ownership. A worker crash changes affected capability health and admission; it cannot leave readiness true for unsupported mandatory checks.

CLI entry point is `guardrails-evolution = guardrails.evolution.cli:main`; management `guardrails.evolution.management_api:create_app`; runtime `guardrails.evolution.runtime_api:create_app`. Pure domain models and compiler rules do not import FastAPI, SQLAlchemy or LiteLLM. Inject clocks, ports and evaluators through constructors; no process-global tenant state.

## Task Runner

| Command | Action and owner phase |
|---|---|
| `just evolution-lock` | Resolve declared compatible dependencies, record hashes and run import/schema smoke tests; Phase 1 |
| `just evolution-test` | Owned Python unit and wire contract suite; Phase 1 onward |
| `just evolution-gate N` | Closed required registry for phase N; runner introduced Phase 1 |
| `just evolution-prepare` | Acquire/check binaries, images, model and browser artifacts; Phase 2 onward |
| `just evolution-up` | Dedicated offline-capable Minikube deployment, explicit context; Phase 2 |
| `just evolution-status` | Desired/applied digests, safe health and artifact state; Phase 2 |
| `just evolution-down` | Stop task-owned workloads, keep persistent data; Phase 2 |
| `just evolution-reset` | Explicit destructive reset of task-owned data after backup; never automatic on arbitrary namespace |
| `just evolution-migrate dry-run` | Read-only source mapping report; Phase 1 |
| `just evolution-migrate apply` | Transactional approved-profile import; Phase 1 |
| `just evolution-benchmark` | Frozen held-out detector metrics; Phase 3 |
| `just evolution-ui-test` | Real browser journeys/security/accessibility; Phase 4 |
| `just evolution-backup` / `just evolution-restore-drill` | Encrypted backup and isolated actual restore; Phase 4 |
| `just evolution-bundle` | Signed tenant-only portable bundle; Phase 5 |
| `just evolution-acceptance` | All local cases, source/artifact hashes and evidence; Phase 6 |
| `just evolution-bedrock-live` | Separate optional explicitly configured external integration |
| `just verify` | Existing governed-document validation and generated-region freshness |

Recipes dispatch to the single runner/deployment entry points, validate mode/profile arguments and propagate nonzero status. Unsupported commands/phase numbers fail explicitly. Recipe help states whether network preparation or destructive reset is involved.

## Docker

Use separate control/runtime, gateway, classifier and media images, each with immutable base digest and reproducible dependency lock. Build wheel/model assets in preparation stages; runtime images contain no package manager download path. Non-root UID, read-only root filesystem, tmpfs for bounded transient buffers, explicit writable metadata volumes, no Docker socket mounts. Use separate service accounts and secret mounts, TLS between content-bearing services and no host network. Helm resource/health/network settings are specified in deployment-topology.md. A local single-node PostgreSQL volume supports repeatable tests, not production HA.
