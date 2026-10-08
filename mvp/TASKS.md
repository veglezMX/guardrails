# Task definitions — companion to section 4 of the delivery plan

> **Current release definition:** [RELEASE-DEFINITION.md](../solution-docs/60-delivery/RELEASE-DEFINITION.md) records the accepted local-proof boundary and acceptance matrix (AGR-022 through AGR-030, DEC-022 through DEC-024): internal teams, full API/CLI lifecycle, two real LiteLLM instances, offline mock inference, working local PII handling, and AWS deferred. This document predates that boundary; its implementation survey and estimates are not acceptance evidence.

Section 4 of `assets/AI-Guardrails-Local-First-MVP-Plan.docx` lists twenty-six
tasks with a size and a wave. It is deliberately terse: it exists so a
stakeholder can decide how many engineers to allocate, not so an engineer can
start work.

This document is the other half. For each task it answers three questions the
plan leaves open:

- **What does it actually mean** — one sentence, no euphemism.
- **Where does it stop** — the boundary, stated as an explicit *not in scope*,
  because most task overrun comes from absorbing the neighbouring task rather
  than from underestimating this one.
- **How do we know it is finished** — acceptance you can check, not a feeling.

Sizes come from a three-step ladder: **1, 3 or 5 days** for one engineer. They
are a sizing judgement, not a bottom-up estimate.

## Status is from a survey, not a tracker

Much of this already exists. Status below reflects a read of `mvp/` at the time
of writing — file presence, module docstrings, and the 67 tests under
`mvp/tests/`. It is indicative, and a task marked **Built** may still have gaps
that only its acceptance check will surface.

| | Meaning |
|---|---|
| **Built** | Implementation and tests present |
| **Partial** | Some of the scope exists; the gap is named in the entry |
| **Open** | Nothing found |

## The twenty-six at a glance

| Wave | Track | ID | Task | Days | Status |
|---|---|---|---|---|---|
| 1 | — | T1 | Foundations | 3 | Partial |
| 1 | — | T2 | Domain model and evaluation contract | 5 | Built |
| 1 | — | T3 | Typed models and conformance scaffold | 3 | Partial |
| 2 | A Policy | T4 | Policy schema | 3 | Built |
| 2 | A Policy | T5 | Immutable versioned store | 3 | Built |
| 2 | A Policy | T6 | Declared failure behaviour | 3 | Built |
| 2 | B Runtime | T7 | Policy resolution from a local cache | 3 | Built |
| 2 | B Runtime | T8 | Decision engine | 5 | Built |
| 2 | B Runtime | T9 | Audit record and archive | 3 | Built |
| 2 | B Runtime | T10 | Timing per detector and stage | 1 | Built |
| 2 | C Detectors | T11 | Rule-based detector | 3 | Built |
| 2 | C Detectors | T12 | Sensitive-data detector | 3 | Built |
| 2 | C Detectors | T13 | Structured-output validator | 1 | Built |
| 2 | C Detectors | T14 | A detector called over the network | 3 | **Open** |
| 2 | C Detectors | T15 | Managed-engine adapter, stubbed | 3 | Built |
| 2 | D Integration | T16 | Gateway hooks either side of the model | 5 | Built |
| 2 | D Integration | T17 | Enforcement and the blocked response | 3 | Built |
| 2 | D Integration | T18 | Correlation and timeout handling | 3 | Built |
| 2 | D Integration | T19 | Integration health reporting | 1 | **Open** |
| 2 | E Control plane | T20 | Tenant and workload registry | 3 | Partial |
| 2 | E Control plane | T21 | Binding and promotion | 5 | Partial |
| 2 | E Control plane | T22 | Rollback | 1 | Partial |
| 2 | E Control plane | T23 | Minimal management interface | 3 | Partial |
| 3 | — | T24 | Two-gateway harness | 3 | **Open** |
| 3 | — | T25 | The nine acceptance conditions | 5 | **Open** |
| 3 | — | T26 | Observe-mode metrics | 1 | **Open** |

**Where the remaining work actually is.** The runtime and detector side is
largely done. What is left clusters in three places: the control plane can
*read* bindings but cannot *author* them (T20–T23), no detector is invoked over
a network (T14), and wave 3 does not exist at all (T24–T26).

Counted against the ladder: **13 days are wholly untouched** (T14, T19, T24,
T25, T26) and **another 18 are partially done** (T1, T3, T20–T23). So somewhere
between roughly 15 and 31 days remain, depending on how much of each partial
task survives contact.

**This changes the staffing answer, and the plan document does not know it
yet.** Section 4 assumes a greenfield start, where wave 2 splits five ways.
The remaining work does not: twelve of its days sit in track E alone, with three
in track C and one in track D. Adding engineers past two or three now buys very
little, because track E and wave 3 are both largely sequential and between them
hold most of what is left.

Before allocating five people, re-derive the table from what is open rather than
from the full 78. The honest headline has probably moved from *"five engineers,
33 days"* to something closer to *"two or three engineers, four to six weeks"* —
but that number should be recalculated, not guessed, and the plan document
updated to match.

---

# Wave 1 — foundations

Strictly sequential. Nothing in wave 2 can start until T3 lands, because every
later task is a client of the contract.

## T1 — Foundations · 3 days · Partial

**Definition.** The environment, the skeletons and the commands, so that any
engineer can clone the repository and get a running stack.

**Scope**
- Development shell with the toolchain pinned, so versions are not per-laptop.
- Service skeletons for the runtime and the control plane.
- Container topology: runtime, datastore, audit archive, two gateways.
- Task commands for bring-up, test, validate and demo.

**Not in scope**
- Any domain logic. A skeleton that evaluates anything belongs to T8.
- CI pipelines. Local first; automation once there is something to automate.

**Done when**
- One command brings the stack up from a clean clone.
- One command runs the test suite.

**Status.** Recipes exist (`just test`, `just policies`, `just serve`,
`just demo`, `just caps`) and the package runs. The container topology and the
second gateway are not there — that gap is really T24's input, so land it here
or accept T24 grows.

## T2 — Domain model and evaluation contract · 5 days · Built

**Definition.** The six things the system talks about, and the one contract
every caller uses regardless of which gateway they run.

**Scope**
- Entities: tenant, workload, policy, binding, evaluation, decision.
- Request shape: tenant, workload, stage, content, correlation identifier, and
  an optional policy hint that is never authoritative.
- Decision shape: action, reason codes, policy name and version, evaluator
  metadata, transformed content, correlation and audit identifiers, latency.
- Closed vocabularies for stage, content kind, action and enforcement state,
  rejected on input rather than coerced.

**Not in scope**
- Any evaluation. This task defines what is said, not what happens.
- Transport concerns beyond the shape — auth and size limits are T18 and T20.

**Done when**
- A decision can be constructed and serialised for every action.
- An unknown stage or content kind is rejected, not guessed at.

**Status.** `guardrails/contract.py`, with the ladder-to-action clamp and the
reason-code builder. `guardrails/policy.py` and `guardrails/binding.py` carry
the rest of the model.

## T3 — Typed models and conformance scaffold · 3 days · Partial

**Definition.** Types generated from the contract, and the shared test suite
that anything claiming to implement it must pass.

**Scope**
- Typed request and decision models available to runtime and connector alike.
- A conformance suite expressed against the contract, not against the
  implementation, so a second implementation could be checked with it.
- Fixtures covering every action and every closed-vocabulary rejection.

**Not in scope**
- Behavioural tests of the decision engine — those ship with T8.

**Done when**
- The suite fails when a field is dropped from the contract.

**Status.** Types are hand-written dataclasses rather than generated from a
specification, and the tests are organised by module rather than as a portable
conformance suite. Functionally close; structurally not yet the thing a second
implementation could be held to. The residual work is writing the specification
the types should have come from.

---

# Wave 2 — five independent tracks

Once T3 lands these share nothing. One engineer per track is the natural shape.

# Track A — Policy (9 days)

## T4 — Policy schema · 3 days · Built

**Definition.** The document that says what to detect, at which stages, how
confident a detection must be, and what a match produces.

**Scope**
- Name, version, stages, evaluator selections, thresholds, actions.
- Selections name a **detection family and capability**, never a vendor, a
  product or a network address. This is what makes a policy portable.
- A compatibility block declaring the contract version and the capabilities the
  policy requires, so a deployment that cannot serve them fails loudly.

**Not in scope**
- Storage and versioning — T5.
- Failure behaviour — T6, though it lives in the same document.

**Done when**
- A policy naming an unserved capability is rejected at validation, not at
  request time.

**Status.** `guardrails/policy.py`, exercised by `deployment/policies/`.

## T5 — Immutable versioned store · 3 days · Built

**Definition.** Storage where a published version can be read back but never
altered.

**Scope**
- Retrieval by name and version.
- Publishing a change creates a new version; no in-place edit exists as an
  operation.
- A cached copy is a faithful copy of exactly one version.

**Not in scope**
- Distribution from a control plane — the local cache is the store here.
- Signing. Out of scope for this release entirely.

**Done when**
- Two versions coexist and both are retrievable.
- No code path mutates a published version.

**Status.** `guardrails/store.py`.

## T6 — Declared failure behaviour · 3 days · Built

**Definition.** Every policy states what happens for each way evaluation can
fail, so no fallback is ever implicit.

**Scope** — six modes, each with a declared response:
- evaluation timeout
- detector unavailable
- policy unresolved
- control plane unreachable
- audit write failed
- content cannot be safely transformed

**Not in scope**
- Enforcing them inside the engine — that is T8 consuming this declaration.

**Done when**
- A policy missing any of the six is rejected at validation.

**Status.** `guardrails/failure.py`, with the block present in the shipped
policy document.

# Track B — Runtime (12 days)

## T7 — Policy resolution from a local cache · 3 days · Built

**Definition.** Resolving which policy version applies to a workload, from a
local cache, never by calling out.

**Scope**
- Resolve effective policy from workload identity and binding.
- Serve from cache with no blocking call to any central service, in every
  deployment mode rather than as a special case for one.
- Unresolvable policy raises the declared failure behaviour, not a default.

**Not in scope**
- Refreshing the cache from a control plane — that is a later phase.

**Done when**
- Evaluation succeeds with no control plane reachable at all.

**Status.** `guardrails/store.py`. This is the task that quietly de-risks the
customer-managed runtime phase.

## T8 — Decision engine · 5 days · Built

**Definition.** The single point every evaluation passes through, and the only
place a decision may be created.

**Scope**
- Run only the evaluators the policy configures for this stage.
- Normalise every evaluator's output into one shape.
- Findings at or above threshold contribute; the emitted action is the most
  severe contribution.
- Clamp the action to the binding's ladder ceiling, leaving reason codes and
  evaluator metadata untouched so promotion stays measurable.
- A redact that cannot be satisfied is an explicit transform failure, never a
  silent downgrade to allow or upgrade to block.

**Not in scope**
- Enforcing the decision — that is the connector's job, T17.
- Detection itself — the detectors are track C.

**Done when**
- No module other than this one can emit a decision.
- A disabled binding still produces an audit record.

**Status.** `guardrails/engine.py`, the largest module and the correct place for
the weight to sit.

## T9 — Audit record and archive · 3 days · Built

**Definition.** Every evaluation leaves a record — normal path, failure path,
and deliberately disabled bindings alike.

**Scope**
- Record carries tenant, workload, policy version, stage, action, reason codes,
  evaluator versions, correlation and audit identifiers, and whether content was
  retained.
- Raw prompts and responses are never written unless the policy opted in.
- A disabled binding still produces a record.

**Not in scope**
- Querying or reporting over the archive — T26 does the metrics.

**Done when**
- Turning retention off leaves no content anywhere in the archive.
- A record exists distinguishing "disabled on purpose" from "connector removed".

**Status.** `guardrails/audit.py`.

## T10 — Timing per detector and stage · 1 day · Built

**Definition.** Measure how long each detector takes, separately from the
evaluation as a whole.

**Scope**
- Latency on the decision.
- Per-detector, per-stage timing retained for aggregation.

**Not in scope**
- Setting a latency budget. That needs real infrastructure, and is a decision
  for the cloud stage.

**Done when**
- Per-detector timings can be aggregated to a distribution.

**Status.** Timing is threaded through the engine and carried on the decision.

# Track C — Detectors (13 days)

Every detector implements the same adapter interface and returns the same
finding shape. Nothing downstream can tell which engine produced a detection.

## T11 — Rule-based detector · 3 days · Built

**Definition.** Deterministic, first-party checks — patterns and phrase lists an
organisation authors itself.

**Scope**
- Pattern and phrase matching driven by policy parameters.
- Character spans returned where the match can be localised, so redaction is
  possible.

**Not in scope**
- Anything model-backed.

**Done when**
- A policy phrase list changes behaviour with no code change.

**Status.** `guardrails/evaluators/deterministic.py`.

## T12 — Sensitive-data detector · 3 days · Built

**Definition.** Baseline personal-data detection — email, phone, card, national
identifier.

**Scope**
- Detection with spans, so redaction can be satisfied.

**Not in scope**
- A full data-loss-prevention capability. Explicitly excluded from the product.

**Done when**
- A redact action on a detected entity produces transformed content, not a
  transform failure.

**Status.** `guardrails/evaluators/pii.py`.

## T13 — Structured-output validator · 1 day · Built

**Definition.** Validate that a model response conforms to a declared schema.

**Scope**
- Schema supplied through policy parameters.
- Validation failure maps to whichever action the policy configured.

**Not in scope**
- Repairing malformed output.

**Done when**
- A malformed response produces the configured action, not an exception.

**Status.** `guardrails/evaluators/schema_validator.py`.

## T14 — A detector called over the network · 3 days · **Open**

**Definition.** One detector invoked as a remote service rather than in-process,
so the remote path is exercised for real before the managed engine arrives.

**Scope**
- Adapter making a real network call to a detector running as a separate
  process or container.
- Real timeout enforcement, and a real unavailable path when the process is
  stopped.
- Same finding shape as every in-process detector.

**Not in scope**
- The managed engine's own mapping — T15 and the cloud stage.

**Done when**
- Stopping the detector's container produces the declared detector-unavailable
  behaviour, observed rather than simulated.

**Status.** Nothing found. Every current evaluator is in-process, and the
managed-engine adapter uses a stubbed client with no socket involved. **This is
the highest-value open task in the track**: without it, the timeout and
unavailability paths are only tested against fakes, and those paths are exactly
what the cloud stage will stress first.

## T15 — Managed-engine adapter, stubbed · 3 days · Built

**Definition.** The adapter for Amazon Bedrock Guardrails, with a fake client
standing in for the service.

**Scope**
- Translation table from the vendor's native vocabulary to the platform's
  capability names. No native code may escape this table.
- Fake client emitting vendor-shaped responses, with a hook to drive the outage
  path.
- Detection parity with the in-process sensitive-data detector, so swapping one
  for the other changes only evaluator metadata.

**Not in scope**
- A real client. Swapping it in changes this file and nothing else — that is the
  whole claim, and it is validated in the cloud stage.

**Done when**
- The same policy resolved against this adapter and against the in-process
  detector produces identical decisions apart from evaluator metadata.

**Status.** `guardrails/evaluators/bedrock.py`.

# Track D — Integration (12 days)

## T16 — Gateway hooks either side of the model · 5 days · Built

**Definition.** The plug-in that calls evaluation before the model and again
after it.

**Scope**
- Input evaluation before inference, output evaluation after.
- Gateway-agnostic core, with a thin binding for the specific gateway on top.

**Not in scope**
- Policy, thresholds or detection of any kind. If a threshold appears here, the
  boundary has been crossed.

**Done when**
- The gateway-specific binding is thin enough that a second gateway is a new
  binding, not a new implementation.

**Status.** `connector/core.py` with `connector/litellm_hooks.py` over it —
the split is already correct.

## T17 — Enforcement and the blocked response · 3 days · Built

**Definition.** Applying the decision to the real model call, and deciding what
a caller actually receives when blocked.

**Scope**
- Apply allow, block, redact and record-only to the request and the response.
- A defined, recorded safe fallback for a post-call block — the model has
  already run and been paid for, and the caller still needs an answer shape.

**Not in scope**
- Deciding *whether* to block. That already happened.

**Done when**
- A post-call block returns the agreed shape rather than an error, and the
  choice is written down.

**Status.** `connector/core.py`. Confirm the blocked-response shape is recorded
somewhere a stakeholder can see it — it is one of the decisions the plan asks
to close at this gate.

## T18 — Correlation and timeout handling · 3 days · Built

**Definition.** One identifier across both checks and into the audit record, and
declared behaviour when evaluation does not answer in time.

**Scope**
- Correlation identifier generated if absent, propagated if present.
- Timeout handled per the policy's declared behaviour, not by a hardcoded
  default.

**Not in scope**
- Distributed tracing.

**Done when**
- Both evaluations and the audit record for one request share an identifier.

**Status.** `guardrails/intake.py` for propagation, `connector/core.py` for the
timeout path.

## T19 — Integration health reporting · 1 day · **Open**

**Definition.** The connector reports that it is installed and calling — a
signal of adoption, never proof of enforcement.

**Scope**
- Periodic or per-request health signal carrying connector version and workload
  identity.
- Recorded so a workload bound to a policy but sending nothing is visible.

**Not in scope**
- Treating this as an enforcement guarantee. A cooperative connector can be
  removed, and the document must never claim otherwise.

**Done when**
- A workload with a binding and no traffic is distinguishable from one with no
  binding.

**Status.** Nothing found. Small, and it is what makes the audit record's
"disabled on purpose versus quietly removed" distinction actually observable.

# Track E — Control plane (12 days)

The data model exists; the operations that produce it do not. Everything in
`deployment/` is currently hand-authored JSON.

## T20 — Tenant and workload registry · 3 days · Partial

**Definition.** Registering a tenant and a workload, and issuing the identity a
workload authenticates with.

**Scope**
- Register a tenant; register a workload under it.
- Issue and verify workload identity.
- Record risk classification, environment and owner.

**Not in scope**
- End-user authentication. That stays with the consuming application.

**Done when**
- A workload can be registered and immediately authenticate.

**Status.** `deployment/identities.json` is read and verified at intake, but
there is no registration operation — identities are hand-written.

## T21 — Binding and promotion · 5 days · Partial

**Definition.** Associating a workload with one immutable policy version, and
moving it along the enforcement ladder.

**Scope**
- Bind one workload to exactly one policy version.
- Record enforcement state and the version to roll back to.
- Promotion as an explicit, recorded act. No binding starts out blocking.

**Not in scope**
- Composing several policies into one binding. Not settled, and must not be
  built.

**Done when**
- Promotion appears in the audit trail as its own event, with who and when.

**Status.** `guardrails/binding.py` reads bindings and the shipped documents
carry `rollback_version`. There is no promotion operation — changing state means
editing a file.

## T22 — Rollback · 1 day · Partial

**Definition.** Returning a workload to its previous policy version
immediately.

**Scope**
- Repoint the binding at the recorded prior version.
- No rebuild, no regeneration, no re-authoring. The prior version already exists
  unchanged.

**Not in scope**
- Undoing decisions already made under the newer version.

**Done when**
- Rollback is a single operation with no build step, verified by timing it.

**Status.** The field exists and is null in both shipped bindings. The operation
does not. Cheap once T21 lands — it is the same write.

## T23 — Minimal management interface · 3 days · Partial

**Definition.** Enough surface to drive registration, binding, promotion and
rollback without editing files by hand.

**Scope**
- Command line or minimal HTTP covering the four operations above.
- Validation of a policy document before it is published.

**Not in scope**
- An administration portal. Explicitly not required for this release.

**Done when**
- The whole onboarding path runs without a text editor.

**Status.** `guardrails/cli.py` covers serve, validate and capabilities — the
read side. The write side is the gap.

---

# Wave 3 — proof

Sequential, and the only wave whose output a stakeholder will actually look at.

## T24 — Two-gateway harness · 3 days · **Open**

**Definition.** Two gateways, genuinely independent of one another, running the
same policy.

**Scope**
- Distinct configurations, distinct model backends, distinct workload
  identities, distinct credentials.
- Neither shares model credentials with the guardrail service.
- Both reachable in one command.

**Not in scope**
- Two *teams*. Recruiting real adopting teams is a separate, still-open
  commitment and this harness does not substitute for it.

**Done when**
- The same policy version produces the same decision for the same content
  through both, differing only in workload identity.

**Status.** `demo.py` walks one workload through input evaluation, the model and
output evaluation. The second gateway does not exist. This is the task the
central claim of the whole release rests on.

## T25 — The nine acceptance conditions · 5 days · **Open**

**Definition.** The nine conditions automated as one repeatable suite, because
the release is accepted only when all hold at once.

**Scope** — one automated check per condition:
1. Two gateways run the same policy
2. Neither shares model credentials with the service
3. Both get the same decision for the same content
4. One workload in observe
5. Another in enforce
6. A policy upgraded and rolled back
7. Raw content retention switchable off
8. A failed evaluation behaving as declared
9. The runtime packageable to run inside a team's own network unchanged

**Not in scope**
- Unit tests. Those already exist and are not this.

**Done when**
- One command reports all nine, and any single failure fails the run.

**Status.** 67 tests exist under `mvp/tests/`, organised by module. There is no
suite expressed in terms of the nine conditions — and that framing is what makes
it evidence for a gate rather than for a developer.

## T26 — Observe-mode metrics · 1 day · **Open**

**Definition.** The numbers that let someone decide whether a workload is ready
to move from observe to enforce.

**Scope**
- False-positive rate, latency distribution, spread of decisions by action and
  by category.
- Derived from the audit archive, not from a separate pipeline.

**Not in scope**
- Setting the promotion threshold. That is a decision, not a report.

**Done when**
- The three numbers can be produced for one workload over a time window.

**Status.** Nothing found. Cheap, and it is the input to every promotion
decision — without it, "observe long enough and review the metrics" has no
metrics.
