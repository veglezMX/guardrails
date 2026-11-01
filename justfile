set shell := ["bash", "-uc"]

docs := "solution-docs"
mvp := "mvp"
tools := "tools/docs"
deck_tools := "tools/deck"
xlsx_tools := "tools/xlsx"
docx_tools := "tools/docx"

# List available recipes.
default:
    @just --list

# Run every validator. Non-zero exit on any failure.
check:
    @python3 {{ tools }}/validate.py {{ docs }}

# Regenerate every machine-owned region in place.
gen:
    @python3 {{ tools }}/generate.py {{ docs }}

# Regenerate tables of contents only.
toc:
    @python3 {{ tools }}/generate.py {{ docs }} --only toc

# Reverse-index report. Optional single ID, e.g. `just trace AGR-009`.
trace id="":
    @python3 {{ tools }}/trace.py {{ docs }} {{ id }}

# Validate, then prove every generated region is already up to date.
verify: check
    @python3 {{ tools }}/generate.py {{ docs }} --check \
      || { echo "generated content is stale — run 'just gen'" >&2; exit 1; }

# Run the MVP runtime and connector test suite.
test:
    @cd {{ mvp }} && python3 -m unittest discover -s tests -t tests

# Validate every policy document in the deployment directory.
policies:
    @PYTHONPATH={{ mvp }} python3 -m guardrails.legacy_cli --root {{ mvp }}/deployment validate

# Serve the Evaluation API locally. e.g. `just serve 0.0.0.0 9000`.
serve host="127.0.0.1" port="8080":
    @PYTHONPATH={{ mvp }} python3 -m guardrails.legacy_cli --root {{ mvp }}/deployment \
      serve --host {{ host }} --port {{ port }}

# Walk one request through input eval, the model, and output eval.
demo:
    @python3 {{ mvp }}/demo.py

# Print the evaluator capabilities this deployment serves.
caps:
    @PYTHONPATH={{ mvp }} python3 -m guardrails.legacy_cli --root {{ mvp }}/deployment capabilities

# List the audience decks and the decision each one asks for.
decks:
    @python3 {{ deck_tools }}/registry.py

# Build one deck into presentations/. e.g. `just deck security`.
deck name="internal":
    @python3 {{ deck_tools }}/build.py {{ name }}

# Check a built deck: geometry, speaker notes, text fit, claims, source-ID leaks.
deck-check name="internal":
    @python3 {{ deck_tools }}/verify.py {{ name }}

# Build one deck and immediately check it.
deck-verify name="internal": (deck name) (deck-check name)

# Build and check every deck in the registry.
deck-all:
    @python3 {{ deck_tools }}/build.py --all
    @python3 {{ deck_tools }}/verify.py --all

# List the workbooks and the audience each one serves.
books:
    @python3 {{ xlsx_tools }}/registry.py

# Build one workbook into presentations/. e.g. `just xlsx delivery`.
xlsx name="delivery":
    @python3 {{ xlsx_tools }}/build.py {{ name }}

# Check a built workbook: structure, model integrity, formulas, anchors, dates.
xlsx-check name="delivery":
    @python3 {{ xlsx_tools }}/verify.py {{ name }}

# Build one workbook and immediately check it.
xlsx-verify name="delivery": (xlsx name) (xlsx-check name)

# Build and check every workbook in the registry.
xlsx-all:
    @python3 {{ xlsx_tools }}/build.py --all
    @python3 {{ xlsx_tools }}/verify.py --all

# List the stakeholder documents and the audience each one serves.
reports:
    @python3 {{ docx_tools }}/registry.py

# Build one document into presentations/. e.g. `just docx local-first`.
docx name="local-first":
    @python3 {{ docx_tools }}/build.py {{ name }}

# Check a built document: structure, placeholders, empty cells, metadata.
docx-check name="local-first":
    @python3 {{ docx_tools }}/verify.py {{ name }}

# Build one document and immediately check it.
docx-verify name="local-first": (docx name) (docx-check name)

# Build and check every document in the registry.
docx-all:
    @python3 {{ docx_tools }}/build.py --all
    @python3 {{ docx_tools }}/verify.py --all

# Build and check every artifact, then copy them into assets/.
#
# presentations/ is gitignored, so a build alone leaves nothing anyone else can
# see. assets/ is the tracked, circulated copy — this is the only step that
# publishes one.
publish: deck-all xlsx-all docx-all
    @mkdir -p assets
    @cp presentations/*.pptx presentations/*.xlsx presentations/*.docx assets/
    @ls -1 assets/

# Prepare exact dependencies/images and local TLS; may access package registries.
proof-bootstrap:
    @python3 {{ mvp }}/proof/bootstrap.py

# Closed phase-specific gates. Unimplemented/all profiles fail explicitly.
proof-unit profile="all":
    @python3 {{ mvp }}/proof/run.py unit {{ profile }}

proof-contract profile="all":
    @python3 {{ mvp }}/proof/run.py contract {{ profile }}

proof-integration profile="all":
    @python3 {{ mvp }}/proof/run.py integration {{ profile }}

proof-acceptance:
    @python3 {{ mvp }}/proof/run.py acceptance

proof-down:
    @python3 {{ mvp }}/proof/run.py down

proof-reset:
    @python3 {{ mvp }}/proof/run.py reset

# Start the API-backed local proof and both gateways from prepared artifacts.
proof-up:
    @PYTHONPATH={{ mvp }} python3 -m proof.run up

# Evolution uses its separately hash-locked Python3.14 environment.
evolution_python := env_var_or_default("EVOLUTION_PYTHON", ".evolution/venv314/bin/python")

# Network preparation: resolve hashes when absent, install and verify local wheels.
evolution-lock *args:
    @PYTHONPATH={{ mvp }} python3 -m evolution.dependencies {{ args }}

# Owned evolution unit and wire tests; no artifact download.
evolution-test:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run test

# Start or verify the pinned task-owned PostgreSQL test fixture; never pulls images.
evolution-db:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.postgres

# Closed phase registry. Unknown or unimplemented phases fail explicitly.
evolution-gate phase:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run gate "{{ phase }}"

# Observe signed applied runtime caches and HTTPS receipts; optionally submit evidence.
evolution-canary *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.canary {{ args }}

# Keep all installed plan checks; validate a live session count against its logs.
evolution-plan-check checker:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.plan_check plans/guardrails-evolution --checker "{{ checker }}"

# Network preparation of exact deployment images, binaries and models (Phase 2).
evolution-prepare *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run prepare {{ args }}

# Start only the dedicated task-owned offline-capable deployment (Phase 2).
evolution-up *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run up {{ args }}

# Read safe deployment and artifact health (Phase 2).
evolution-status *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run status {{ args }}

# Stop task-owned workloads while retaining persistent data (Phase 2).
evolution-down *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run down {{ args }}

# Destructive task-profile reset requires its backup procedure (Phase 2).
evolution-reset *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run reset {{ args }}

# Read-only legacy mapping by default; apply requires the explicit approved profile.
evolution-migrate mode="dry-run" *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m guardrails.evolution.migrate "{{ mode }}" {{ args }}

# Frozen detector-quality benchmark (Phase 3).
evolution-benchmark *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run benchmark {{ args }}

# Prepared real-browser administration journeys (Phase 4).
evolution-ui-test:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run ui-test

# Encrypted metadata backup (Phase 4).
evolution-backup:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run backup

# Actual recovery into an isolated task-owned namespace (Phase 4).
evolution-restore-drill:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run restore-drill

# Signed tenant-only portable bundle (Phase 5).
evolution-bundle:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run bundle

# Complete closed local registry; never substitutes external optional tests.
evolution-acceptance:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run acceptance

# Optional externally configured Bedrock integration; may use paid external APIs.
evolution-bedrock-live *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run bedrock-live {{ args }}

# Verify the entire prepared deployment inventory offline.
evolution-verify-prepared *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run verify {{ args }}

# Run actual Calico and TLS network probes in the owned cluster.
evolution-probes *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run probes {{ args }}

# Assemble and start the owned two-tenant platform through real governance.
evolution-platform *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run platform {{ args }}

# Read local endpoints and the protected access-manifest path, without credentials.
evolution-access *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run access {{ args }}

# Renew existing identities through the authenticated API with durable recovery.
evolution-renew-credentials *args:
    @PYTHONPATH={{ mvp }} "{{ evolution_python }}" -m evolution.run renew-credentials {{ args }}
