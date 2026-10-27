# Guardrails evolution local operations

Implementation status and required gates are tracked in
[`progress.md`](../../plans/guardrails-evolution/progress.md). A passing component
test is not a phase or release acceptance result. The predecessor proof remains
a separate profile; its original locks and interfaces are preserved.

## Optional evaluator preparation and governance

Phase 3 acceptance remains pending until its complete signed gate passes. The
classifier quality benchmark, isolated worker tests and declared resource limits
are separate evidence; none establishes the deployed evaluation deadline alone.

Classifier preparation takes an explicit sealed offline context directory and its
seal SHA-256 through `python -m evolution.run prepare --classifier-context PATH
--classifier-seal-sha256 SHA256`. The context builder in
`services/classifier/build_context.py` accepts only the pinned public source,
standalone Python, CPU wheels and model artifacts. Its 46-entry TAR is verified
before the native Docker build, which disables build networking and automatic
image pulls. The resulting immutable image is bound to both preparation locks.
Retain the context, seal and build receipts. Changed prepared artifacts require
fresh preparation and cold-start evidence before acceptance.

The classifier runs separately from Ollama. Apply the updated owned probe network
policies before selecting its profile: the shared DNS grant excludes classifier
pods, and the classifier receives no egress grant. Run
`python -m evolution.run classifier-service --state PATH` after preparing its image and TLS
leaf. This stages the owned Service and local bearer, returning its exact
ClusterIP and private token/CA file paths. This result means the endpoint exists;
it does not mean a policy is approved or a worker is ready.

Create immutable evaluator ProviderProfile and exact Destination versions through
the existing management API, then submit a policy containing their returned
ID/version/digest references. Existing allowlists match resource IDs; when
repurposing an already allowed resource, create new versions and preserve every
earlier version and approval. A changed endpoint, purpose or model requires new
policy approval and release. The workload's inference provider must remain the
separately approved model route. Distinct author and approver principals, current
mandatory floors and genuine runtime receipts remain required.

The operator endpoint file uses `evolution-evaluator-endpoints-v1` with explicit
`classifier` and `bedrock` arrays. Classifier rows contain tenant ID, complete
approved provider/destination resources, `language_profile: "en"`, and absolute
`bearer_token_file` and `ca_file` paths. Keep operator configuration and credentials
owned and mode 0600. CA files may be 0600 or 0644. The loader validates and snapshots
their bytes; it never discovers credentials or trusts an ambient endpoint.
`python -m evolution.run platform --profile classifier --evaluator-config-file PATH`
requires the distinct team-a/team-b resources and the exact staged local route.

Profile changes retain a private transition journal, drain only owned heavy
workloads, and restore the selected deployment within the declared application
resource envelope. Resume an interrupted transition with the same profile and
input file; preserve its journal and original values. Readiness and immutable
image verification precede its completion marker. Do not create a replacement
journal or infer successful recovery from a still-running process.

Run the explicit frozen benchmark through `just evolution-benchmark --python PATH
--model-directory PATH --output NEW_DIRECTORY --source-digest SHA256`. It scores
all 600 frozen cases once, without warmup or retries, using the prepared Python
3.13.16 CPU environment. English requires at least 90% recall, at most 5% false
positives and no operational errors at threshold 0.90. Spanish results are
reported separately and do not enable Spanish enforcement. Keep failed reports
and the original invocation receipt. The classifier's 3-second evaluation budget
and 4-second connector budget still need separate arrival-through-audit evidence;
the default text evaluation budget remains 500 ms.

`just evolution-bedrock-live` is optional and requires `--allow-external` plus
explicit configuration, credential and CA file arguments. A numbered guardrail
version, exact regional endpoint, current credentials and approved external
processing are required. Its results cannot replace the required local Bedrock
contract and forbidden-dispatch cases.

## Prepare the governance environment

Run from the assigned persistent worktree with Python 3.14, `uv`, Docker and
`just` available. `just evolution-lock` resolves the declared exact lock when
absent, installs it into `.evolution/venv314`, and prepares verified local wheel
bytes. This is a network preparation operation. Acceptance does not download
packages, images or models.

The PostgreSQL artifact is pinned in
[`artifacts.lock.json`](../../deploy/evolution/artifacts.lock.json). Prepare that
exact Docker image before the first governance gate. `just evolution-db` starts
or verifies one task-owned loopback PostgreSQL instance with a serving role that
does not own tables and has neither superuser nor RLS bypass. Container ownership
is bound to the physical worktree path. Application and migration DSNs are separate
mode-0600 files in `.evolution/postgres/`; do not print them or copy them to reports.
The serving role cannot execute audit retention deletion.
Explicit database preparation reapplies the current idempotent schema migration
using the separate owner credential, including on a reused container; it does not
reset tenant data.

`just evolution-test` runs domain and wire checks. `just evolution-gate 1` runs
the closed governance registry against fresh test identities, with real storage,
HTTPS CLI and signed runtime application boundaries. A phase whose registry is
unavailable exits nonzero. The gate verifies local wheel/image bytes and imported
dependencies, hashes executable sources before and after the run, and writes a
signed report, source manifest and assertion matrix under `.evolution/reports/`.
Previous reports are never overwritten. Source changes invalidate their use as
evidence for the new tree.

## Credentials and management commands

The `guardrails-evolution` client uses HTTPS with a supplied CA and reads bearer
credentials from an owned mode-0600 file. Use `--file` or bounded stdin for request
documents. Never place credentials or policy content in shell arguments.

Issuance and rotation require a new `--secret-output` path. The secret is written
once with mode 0600 and excluded from standard output and idempotency storage.
The client reserves that file before issuing the request; a failed request removes
its own empty reservation. An existing file is never overwritten.
Replaying an issuance key must not issue another credential or disclose the old
secret. If the first secret was lost, revoke that credential and issue a new one
with a new idempotency key. Policy review always uses a distinct principal;
another credential for the author is still self-approval.

`config create-version --resource quotas --file quota.json` creates a new resource
at version 1. Add `--id UUID` to create the next immutable version of that resource.
The same commands support lists, residency, destinations, providers and tools.
Keep the same `--idempotency-key UUID` when retrying an unchanged submission;
changing its body requires a new key. `config show --resource quotas --id UUID
--version 1` retrieves the unchanged earlier version. Creating a version does not
activate it: compile a policy pinned to its returned ID, version and digest, obtain
independent approval, then release it through the normal workflow.

`audit list` exposes one signed cursor page. `audit export` follows all pages,
preserving `--workload`, `--action`, `--from` and `--until` filters. Export is bounded
by `--timeout` and 100,000 records; an incomplete export exits nonzero without
writing a success file. Use `--output` for a new mode-0600 JSON file or `--json`
for machine-readable standard output. The default display contains safe IDs and
state summaries.

Release creation means desired state is pending. `release wait` exits nonzero
until every required runtime has durably applied and acknowledged the expected
digest. A timeout never promotes pending state. Rollback appends a new binding
revision and rechecks approval, revocation, current baselines and compare-and-swap
revision. It never rewrites binding history.

Management mutations have a five-second server budget; synchronization has two
seconds. The same absolute deadline covers request-body reception and database
work. A timed-out transaction rolls back; retry an unchanged mutation with its
original idempotency key. Release admission, revocation and baseline publication
serialize per tenant through transaction commit so their eligibility checks
cannot become stale before the desired binding is written.

The governance gate also runs the pinned existing email/phone detector in bounded
worker processes to compare normalized findings across two authenticated tenants.
This prerequisite proves detector execution and separate audit attribution. The
complete evaluation endpoint, gateway integration and broader detector coverage
remain subject to their later phase gates.

## Runtime enrollment and canary evidence

Register runtime records through the management API. Before the first workload
release, an authenticated deployment operator calls
`RuntimeEnrollment.enroll` from `guardrails.evolution.enrollment` to associate
each install with an active runtime-only principal and its exact workload set.
Supply the current runtime and target revisions. Enrollment records are private,
immutable and tenant-scoped; registration alone does not create a ready runtime.
Retargeting an already released workload is rejected.

Provision the runner's raw 32-byte Ed25519 **public** key through the deployment
helper `EvidenceTrust.provision(platform_token, key_id=..., public_key=...)`.
The helper authenticates the platform administrator and audits the new trust root.
For the included acceptance runner, the public key is
`.evolution/secrets/acceptance-ed25519.pub` and its ID is the SHA-256 of those bytes.
The corresponding private key stays with the runner. The assembled Management
API resolves currently active roots from protected platform records. Rotate to a
new unique ID, then revoke the old root with its expected revision; revoked IDs
cannot be reused.

After the canary release is applied, run `just evolution-canary` with the HTTPS
origin, CA file, protected operator credential, tenant and release IDs. Supply
`--targets-file` containing the deployment's exact workload-to-runtime-ID map,
the pinned snapshot public key with `--snapshot-key-file` and `--snapshot-key-id`,
and one `--runtime-cache UUID=PATH` for each protected local runtime cache. Add
`--submit` to upload the generated signed report through the existing evidence
API. The command's `--help` lists every required argument.

The command verifies actual loaded policy digests, signed unexpired leases,
matching HTTPS receipts, exact target coverage and its own installed dependencies.
It records safe observations, hashes and measured checks under a new report ID.
Its source digest identifies the local observer, not an independently deployed
server's filesystem. These Phase 1 canary checks prove governance and application
receipts; later gates add actual evaluated model traffic. Server admission and
full promotion separately recheck current desired bindings, approval, target
health and lease freshness. `evidence submit --file REPORT` can also submit a
previously generated report while it is still fresh. Full release still waits
for its own new receipts.

## Legacy migration and rollback

Stop writes for the selected legacy migration profile and create a consistent
SQLite backup. The migration adapter only opens a read-only backup without an
active WAL. `just evolution-migrate dry-run --source PATH` verifies schema,
foreign keys, source digests and supported controls without modifying either
source or target. The safe report excludes old credential hashes and content.

Apply requires the explicit source, protected target DSN file and migration
author. Import is one transaction per tenant and idempotent for the same source
digest. Imported policies and translated mandatory floors are candidates;
legacy credentials are unusable and historical receipts never mark a v2 binding
applied. A distinct approver and new release are still required. Unsupported
controls abort the import.

Before new v2 writes, abandon the target and keep the unchanged proof profile.
After v2 writes, never copy v2 data backward into SQLite. Restore a verified v2
backup when that operational phase is available, or keep the old proof as a
separate non-authoritative profile. No automatic reset or source deletion is part
of migration.

## Evidence and retention

Reports contain safe IDs, hashes, durations, counts and reason codes. They must
not contain credentials, raw evaluated input/output, matched values or diagnostic
tracebacks that echo submitted data. Required skipped/missing/zero-assertion cases
fail the gate. Optional external-engine evidence cannot satisfy local cases.

Audit retention uses a separate injected retention repository with explicit
database permission and an operator-held signing key. Serving credentials have
no retention execution privilege. A purge must verify the signed checkpoint,
retain its sequence/hash boundary and continue the remaining chain correctly.
Stopping the task database preserves its data. Destructive reset requires the
documented task-owned backup procedure and is not an automatic error recovery.

## Validate implementation records

`just evolution-plan-check /path/to/implementation-plan/scripts/check_plan.py`
runs the installed checker with live session-history validation. The original
checker assumes a freshly generated plan and requires zero completed sessions.
The adapter instead checks the truthful count against complete sequential logs;
it preserves all other findings. `just verify` remains a separate required check.

## Exact destinations and team gateways

New policies compile with `compiler-v2.2`. In policy validate/submit bodies,
`destination_refs` carries exact `{id, version, digest}` references alongside
`provider_refs`; the compiler embeds `resolved_destinations` in the approved
bytes. A provider UUID alone cannot authorize an endpoint. Historical
`compiler-v2.1` digests and signed snapshot bytes remain verifiable; a historical
network policy without exact destination bytes cannot authorize new dispatch.
Creating another destination version leaves the earlier policy unchanged.

Each team mounts its own gateway settings, workload credential, gateway client
credential and `approved_routes_file`. The route manifest contains the exact
approved PolicyRef, ProviderProfile and Destination bytes, local CA file mapping,
and explicitly registered service DNS/IP pairs. It contains no model-provider
credential. Generate it from approved policy bytes through the trusted deployment
path, keep it read-only, and stage the new digest before releasing a policy.
Keep still-eligible old route entries during a rollout so pinned turns can finish;
runtime revocation and current mandatory restrictions still apply. An unlisted
policy digest or a changed alias, endpoint, provider or serialized request blocks
before socket dispatch. An endpoint change also requires the team's gateway
configuration update and a fresh policy approval and release.

The native connector supports non-streaming text messages with system, user and
assistant roles. It verifies both input and complete assistant output, validates
redactions, withholds unsupported output, and cancels in-flight work when the
caller disconnects. It never retries model inference. The final HTTPS transport
checks every DNS answer, connects only to a checked numeric IP with the original
TLS hostname, and refuses redirects, unapproved private services and metadata
addresses. Cluster network-policy evidence is a separate required gate.

When mandatory floors permit Observe, the first enforcing release requires the
Observe revision to be genuinely applied by every enrolled target with fresh
signed leases. Creating a pending Observe revision is insufficient. A mandatory
floor may require enforcement from the first release; it cannot be lowered to
satisfy onboarding.

## Run the team sample client

The sample reads a team-owned gateway credential; model-provider keys stay on the
gateway. For each team, pass that team's HTTPS origin, CA, credential file, model
alias and tenant ID to `examples/evolution/client.py`. Supply text on stdin or
through `--prompt-file`; use `--repeat 10` for the real-model traffic fixture.
Run with `PYTHONPATH=mvp` and the prepared Python interpreter. The full interface
is available through `--help`.

`--profile local_model` selects the real-model request timeout. The deterministic
mock uses `--profile deterministic_mock`; that label never proves real inference.
The cluster acceptance additionally verifies the prepared model artifact and
actual data path. The client checks both hook decision IDs, policy attribution,
binding revision, snapshot generation and required audit status. It prints only
safe metadata by default. `--show-response` explicitly displays inspected
assistant text. Do not compare stochastic model prose to a golden string.

Phase 2 completion remains governed by the closed registry and the actual
cluster/network/model cases in the plan's progress record.


## Capability admission and preparation evidence

The management capability catalog describes installed, pinned built-in support.
Its enabled status means those bytes support the declared revision, stage,
part and action; it does not assert a remote runtime is healthy. Release admission
and signed snapshot distribution check that catalog, so a policy requiring an
unsupported entity or a later-phase content evaluator is rejected before rollout.
Runtime apply and evaluation separately check live workers. A release remains
pending until every enrolled runtime reports the exact applied digest.

Deployment commands forward their explicit options: `just evolution-prepare
--state PATH`, `just evolution-up --state PATH`, `just evolution-status --state
PATH`, and `just evolution-down --state PATH`. `EVOLUTION_DEPLOYMENT_STATE` can
select the same persistent prepared directory across task worktrees. Ownership
and exact prepared bytes must match; a different checkout is not permission to
adopt an unrelated cluster. `just evolution-verify-prepared` verifies prepared
files and image references without registry access. `just evolution-probes`
executes the actual task cluster networking checks.

Only preparation may acquire artifacts. Phase 2 and later gate reports include
exact prepared tool, image, model and derived-file hashes, checked before and
after execution. Private keys remain outside that public artifact inventory.
Task-profile reset requires both `--confirm-reset` with the exact profile name
and `--backup-directory`; the deployment verifies ownership and backup evidence
before it removes the profile. Stopping the deployment preserves its data.

## Assemble the two-team local service

Use the same prepared state directory for this sequence:

```sh
just evolution-prepare
just evolution-verify-prepared
just evolution-up
just evolution-probes
just evolution-platform --profile deterministic_mock
just evolution-access
```

`up` boots the owned Minikube node from prepared artifacts and verifies its
network boundary and system image identities. `probes` establishes and tests the
owned namespaces, default-deny policies, service DNS and TLS prerequisites.
`platform` requires those prerequisites before creating PostgreSQL, management,
two runtime replicas and each team's gateway. Initial onboarding uses distinct
author, approver and operator identities and waits for all four signed runtime
receipts. The first application assembly must run from the worktree that owns
the acceptance signing key, because its public key becomes the fixture's
evidence trust root.

`access` prints safe loopback HTTPS addresses and the protected access-file path.
That mode-0600 file supplies each team's sample-client URL, model alias and
credential-file path. Keep the file private. Gateway aliases are `guarded-chat-a`
and `guarded-chat-b`; each gateway accepts its own credential.

`just evolution-platform --profile local_model` switches the two gateway routes
to the prepared Qwen model and waits for its exact manifest to load. It preserves
policy approvals and current binding revisions. Run the same command with
`deterministic_mock` to return to deterministic controls. Model warmup only proves
readiness; the Phase 2 case separately sends twenty inspected model requests and
verifies their decision and audit attribution.

Rerunning a completed assembly reconciles prepared application image references
and declared resource limits, then verifies Ready pods against their owning
Deployments and exact OCI identities. It preserves approvals, binding revisions,
credential files and seed journals. Workload updates retire validated owned
forwards before rollout and recreate them afterward. An unchanged replay refreshes
readiness without another Helm update or initial Observe release. Use `just evolution-down` to stop this profile and its owned forwards.
After a backed-up reset, assemble again from the same owning worktree; old
application state is archived while prepared certificate material is retained.


Incomplete application assembly retires only its validated, owned loopback
forwards before resuming workload updates. A Kubernetes forward selects one pod
and cannot follow a replacement. PostgreSQL host access uses an opaque loopback
relay in the database pod to isolate the CRI port-forward TLS-close reset; the
application still verifies PostgreSQL TLS end to end. In-cluster database traffic
uses the direct PostgreSQL service. Readiness checks do not open bare database
connections.

Bootstrap and credential delivery keep private recovery journals. A retry checks
the saved signing key, public key, credential identity, and committed database
receipt before reusing them. It never rotates these values to hide a mismatch.
If a one-time credential response was lost before durable storage, the hash-only
database cannot reconstruct it; revoke that credential and issue a new one with
a new idempotency key.

Renew deployment credentials before their current expiry. Capture a private
database and deployment-state backup first, then run:

```sh
just evolution-renew-credentials --state "$deployment_state" \
  --renewal-id "$renewal_id" --expires-at "$credential_expiry"
```

Use a new UUID for `renewal_id` and a fixed UTC timestamp, at most 24 hours in the
future, for `credential_expiry`. Preserve both values for an interrupted run.
The command issues credentials through the authenticated management API for the
same 17 principals and workload scopes. It reloads the two runtime replicas and
two team gateways, verifies their private files and fresh receipts, and revokes
the prior tenant credentials. Prior platform credentials expire naturally;
static gateway client credentials, TLS keys and model credentials stay intact.
Consumer restarts briefly interrupt service, so schedule this maintenance between
acceptance runs.

The private `assembly/credential-renewals/` journal records each generation and
the active overlay. Seed files, governance history and original token files remain
unchanged. Normal `platform` and `access` commands refuse an incomplete renewal;
resume using the same UUID and expiry. A completed renewal becomes the credential
source for later profile changes, which must preserve the active generation.

If a committed one-time secret was lost before it reached its private file,
ordinary resume stops with `credential_secret_lost`. After reviewing the retained
journal, add `--recover-lost` to that same command to permit one replacement per
affected slot. The command validates the committed receipt and retires the lost
tenant credential before replacement; a lost platform credential expires naturally.
A second lost response stops again. Do not delete journals, edit expiry records,
or reinitialize the deployment to bypass a failed renewal.

Prepared inventory verification reads and hashes every file on each invocation,
using three bounded workers with deterministic result order. It rejects symlinks,
nonregular files, and replacement or mutation during hashing. No verification
result is reused from a prior scan.

Re-preparation reuses an identical mode-0700 executable without rewriting its
running inode. Changed tool bytes are verified and installed by atomic rename.
The cold-start record is bound to the exact prepared manifest; changed application
images require fresh cold-start evidence while the earlier record remains historical.

The shared text profile keeps the 500 ms evaluation deadline. Management receives
500m CPU and Ollama receives 1000m; aggregate declared limits, including probes
and sidecars, remain within six CPUs and 8 GiB.
