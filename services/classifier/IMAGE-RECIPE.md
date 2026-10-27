# Offline classifier image candidate

This recipe has not built an image or established model quality, runtime memory,
or cold-start readiness. The parent integration agent owns those operations.
The separate classifier v3 source and held-out corpus remain frozen.

The Dockerfile reuses the exact Python service base from
`deploy/evolution/images/service.Dockerfile`, digest
`cfe2e24a75302a15934d37c2d86412893c0aa934dc3a97cbb439d04c01890ca9`.
Its Python 3.14 interpreter only extracts the authenticated standalone archive;
the classifier and its subprocess exclusively use the pinned CPython 3.13.16
interpreter and 29 hashed CPU wheels. Installation uses no index, permits only
binary wheels, and checks every dependency hash. Every build RUN has network
disabled. The final stage excludes the wheelhouse and interpreter archive.

## Model and capacity decision

The exact nine public model files are baked into their own image layer. The
image root and model directory must be read-only at runtime. This refines the
earlier preparation proposal's read-only model mount into an immutable image
layer; the same nine-file byte closure remains mandatory. This avoids a second
737 MB weights copy in a PVC or init-container workspace and makes code plus
model one image digest. It increases image-transfer/storage cost; the parent
must measure the actual final image and cold import time.

There is one model child with two CPU threads and one interop thread. A proposed
starting limit is 2 CPU and 3 GiB for this pod, subject to real measurement, not a
capacity claim. The existing platform has 6 CPU and 8 GiB overall. Classifier,
OCR and Ollama heavy profiles must run sequentially while preserving the
platform's aggregate request/limit budget. The frozen benchmark records model
startup, CPU time, maximum child RSS, per-case latency and separate English and
Spanish results. It has not run. No accuracy claim or English enforcement
enablement follows from the image recipe or the passing fake/unit checks.

## Parent-authenticated build context

`build_context.py` reads a canonical JSON plan, authenticated by the explicit
`--sha256` argument. Canonical encoding is sorted keys, compact separators, UTF-8,
and one trailing newline. The exact closed plan fields are:

```text
schema: "classifier-image-context-plan-v1"
root: absolute canonical implementation worktree
model_directory: absolute canonical verified nine-file model directory
wheel_directory: absolute canonical verified 29-wheel directory
python_archive: absolute canonical downloaded standalone archive
output: new exclusive directory under an existing owned 0700 session directory
source_sha256: exact path-to-SHA256 map for SOURCE_KEYS in build_context.py
```

The nine source-map keys are the builder, the frozen preparation proposal,
Dockerfile, requirements lock, two package `__init__.py` files, classifier
contract, service app and worker. The proposal is additionally pinned inside the
builder. Parent binding must use reviewed source hashes, not bless arbitrary
current modifications. The builder must be executed from the same plan root.
The output must be outside all input directories. There is no ambient Docker
context, dependency resolution, download, model import, build, or deployment
mutation in the writer.

The 46 regular context entries are exactly seven source inputs, the interpreter
archive, 29 wheels and nine model files. Only those entries are copied; private
state, credentials, test prompts and unrelated repository files cannot enter the
archive through a directory copy. Long public wheel names use deterministic PAX
headers. File hashes are verified while streaming with a 1 MiB read bound, a
2 GiB total archive bound and a 600-second copy budget. Source identities and
artifact directory name sets are checked again before sealing.

The output directory is descriptor-held for the entire operation. Files are
exclusive and private; a replaced output path receives no success or failure
receipt. A partial failure is preserved. `seal.json` is written last and binds
the exact TAR, manifest and plan; `scope=context_only_not_built` is deliberate.
The parent must verify the sealed context again immediately before building.

Parent-only invocation after constructing and reviewing the concrete plan:

```text
<pinned Python> -B <plan.root>/services/classifier/build_context.py \
  --plan <private canonical plan path> --sha256 <reviewed raw plan SHA256>
```

Before the actual build, the parent must prove that the exact base image is
already loaded in the intended owned Docker daemon. `--pull=false` alone does
not establish offline base availability. The parent can then stream the sealed
TAR to its existing bounded Docker runner with these arguments:

```text
docker build --pull=false --network=none --provenance=false \
  --tag <parent-owned source-digest tag> -
```

The runner must pin the intended native Docker socket, use TAR bytes as stdin,
bound execution and retain output, verify actual OCI configuration/rootfs/image
identity, and create the required reviewed artifact/build/prepared records.
There is no implicit shared-lock update. The changed prepared digest requires a
new genuine cold proof. Base ABI/shared-library compatibility and the final
container's offline imports must be proved by that build/test sequence; they
have not been inferred from host installation success.

## Runtime integration prerequisites

The final container runs UID/GID 10001, one Uvicorn process, TLS port 8443 and
explicit HTTP/1.1. Access logging and proxy-header trust are disabled. HTTP
concurrency is bounded at 33; the app independently bounds its admission queue,
body size and complete 2.9-second detector work deadline. The parent runtime
reserves the remaining 100 ms of its 3-second server envelope for audit.

The parent deployment must supply:

- a read-only root filesystem, dropped capabilities and no privilege escalation;
- an exact ingress allowlist for runtime callers and readiness checks, with
  egress denied by the existing network policy mechanism;
- a UID10001-readable private token file at `/config/token`, mode 0600 or
  stricter, through an owned configuration copy mechanism; a default root-owned
  Kubernetes Secret0600 would be unreadable by this container;
- the matching readable certificate/key at `/config/tls.crt` and
  `/config/tls.key`, pinned by the operator's destination/CA configuration;
- an immutable configured endpoint per tenant slot, with the shared readiness
  monitor refreshed at most 500 ms apart and no request-created endpoint cache;
- measured requests/limits and explicit sequential heavy-profile ownership.

The app permits only the `en` operational profile. Offline flags prevent model
fetches but are not a substitute for network policy. Uvicorn must accept incoming
TLS traffic, so Docker's `--network=none` is a build control, not a usable service
deployment setting. The image itself makes no network-isolation claim.
