**AI Guardrails — Extensibility Architecture**

**Authority level:** L3
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md (AGR-021)

---

## Purpose and authority

This document defines **how the platform grows**: the four extension seams through which every guardrail addition flows, the constraints each seam inherits, and the governance path by which a vision-classified catalog entry becomes committed, delivered capability. It formalizes machinery that already exists distributively — the evaluator-adapter pattern ([AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)), policy portability ([AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability)), the one logical contract ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)), policy/connector separation ([AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)), and the extend-don't-fork rule ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)) — and is sanctioned by [AGR-021](../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model).

This is a derived (L3) analytical document. It introduces **no new commitments**: nothing here schedules, resources, or estimates anything, and where it appears to conflict with [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md), those registers win. Its companion is the guardrail-category catalog ([`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md), L2), which classifies *what* may grow; this document defines *how*.

---

## Table of contents

1. [Design stance](#1-design-stance)
2. [The four extension seams — overview](#2-the-four-extension-seams--overview)
3. [Seam 1 — New evaluation stage](#3-seam-1--new-evaluation-stage)
4. [Seam 2 — New evaluation engine](#4-seam-2--new-evaluation-engine)
5. [Seam 3 — New decision action](#5-seam-3--new-decision-action)
6. [Seam 4 — New connector](#6-seam-4--new-connector)
7. [Category attachment model](#7-category-attachment-model)
8. [Control-type metadata](#8-control-type-metadata)
9. [Guardrail definition model (vision)](#9-guardrail-definition-model-vision)
10. [Catalog governance and graduation](#10-catalog-governance-and-graduation)
11. [Policy-pack mechanics (vision)](#11-policy-pack-mechanics-vision)
12. [Constraints and agreements applied in this view](#12-constraints-and-agreements-applied-in-this-view)
13. [Related documents](#13-related-documents)

---

## 1. Design stance

Two rules govern every extension, no exceptions:

1. **Extend, never fork** ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)). The six-entity domain model — tenant, workload, policy, policy binding, evaluation, decision — is closed for forking and open for extension. A new capability adds values to existing vocabularies (a stage, an action) or new implementations behind existing contracts (an engine, a connector); it never introduces a parallel concept, a second policy model, or a mode-specific schema.
2. **One versioned contract** ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)). Every contract-visible change is versioned, additive, and evaluated for backward compatibility across Modes A, B, and C **simultaneously**, then conformance-tested identically for hosted and customer-managed runtimes. There is no such thing as an extension that ships in one mode's contract first.

The payoff of this stance, already visible in the trade-off analysis ([`TRADE-OFFS.md`](TRADE-OFFS.md), adapters as the cross-cutting pattern): breadth of guardrail coverage grows through **new implementations behind stable seams**, not through contract surface growth. That is what keeps eighteen categories of vision from becoming eighteen categories of coupling.

---

## 2. The four extension seams — overview

Every guardrail addition — whatever category it serves — decomposes into one or more of exactly four seam changes:

| Seam | Contract surface touched | Governing agreements | Current state |
|---|---|---|---|
| [1 — New evaluation stage](#3-seam-1--new-evaluation-stage) | Evaluation request (`stage` field); policy schema (`evaluation stages`) | AGR-006, AGR-017; CON-015, CON-016 | 2 active (`input`, `output`); 5 reserved |
| [2 — New evaluation engine](#4-seam-2--new-evaluation-engine) | None (adapter-internal) | AGR-004, AGR-005; CON-004 | 4 engines behind RT-4; plugin mechanism later (CAP-030) |
| [3 — New decision action](#5-seam-3--new-decision-action) | Decision structure (`action` enum); connector enforcement behavior | AGR-006, AGR-011, AGR-017 | 4 active; 4 reserved |
| [4 — New connector](#6-seam-4--new-connector) | None (client of the invocation contract) | AGR-003, AGR-007; CON-003, CON-006 | LiteLLM connector, generic SDK, Guarded Gateway |

A worked decomposition: "memory-write guardrails" (catalog [GR-011](../20-product/GUARDRAIL-CATALOG.md#gr-011--memory-and-state-safety)) = seam 1 (activate the reserved `memory_write` stage) + seam 2 (any memory-specific evaluator) + zero action changes + possibly seam 4 (an agent-framework connector that invokes the new stage). The catalog names the destination; the seams are the road.

---

## 3. Seam 1 — New evaluation stage

**What it is.** Adding a named point in the request lifecycle at which content can be evaluated — e.g., activating the reserved `retrieval` stage for RAG content (CAP-018) or `tool_call` for agent tool invocations (CAP-021).

**Contract surface.** The `stage` field of the evaluation request and the `evaluation stages` attribute of the policy entity (seed.md §9; [`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) §3.1). A stage addition is a contract change and is versioned per AGR-006.

**Reserved vs. proposed.** Exactly seven stages are **reserved** by the domain model: `input`, `output` (active) and `retrieval`, `tool_call`, `tool_result`, `memory_write`, `final_response` (future scope). Anything else — the catalog's candidates `retrieval_query`, `agent_plan`, `memory_read`, `tool_selection` — is **proposed, not reserved**: it may be named by vision-classified catalog items but must not appear in policy schemas, contract definitions, or connector code until [OQ-018](../00-source-of-truth/OPEN-QUESTIONS.md#oq-018--evaluation-stage-vocabulary-expansion-beyond-the-reserved-seven) is resolved and the reserved vocabulary formally extended.

**Process (activating a reserved stage).**
1. Scope decision recorded (the stage's gating OQ resolved → AGR/DEC per [`10-charter/SCOPE.md`](../10-charter/SCOPE.md) §8; for the five reserved future stages the CAP entries already exist: CAP-018/021/023).
2. Define the stage's content payload in terms of the existing Evaluation entity — no parallel request shape (AGR-017).
3. Set the stage's input-size limit and timeout budget (values TBD per CON-015/CON-016 — the requirement that they exist is not TBD).
4. Extend the stage table in [`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) §3.1 and the policy schema; version the contract (AGR-006).
5. Extend conformance fixtures so all three modes validate the new stage identically.
6. Update: `GLOSSARY.md` (stage entry), the catalog's affected `Stages used` fields, `REQUIREMENTS.md` (the corresponding FR moves from future-scope), audit/metric stage enumerations ([`OPERABILITY.md`](OPERABILITY.md) §5).

**Constraints inherited.** CON-015 (input-size limit per stage), CON-016 (timeout per stage — explicitly written to cover "any future stage"), CON-005 (identical across modes). NFR-OPS-004 requires the documented degraded mode to be re-verified at every new evaluation stage.

**Conformance.** Additive only: existing connectors that never send the new stage remain valid; a runtime that receives an unknown stage rejects it explicitly at RT-1 (never silently ignores it).

**Governance gate.** OQ resolved → AGR/DEC recorded → CAP/FR exists. No stage activates from this document.

---

## 4. Seam 2 — New evaluation engine

**What it is.** Adding a detection/validation engine behind the evaluator-adapter layer (RT-4) — e.g., a groundedness evaluator (FR-084), an indirect-injection detector (FR-079), or a customer-specific plugin (FR-090/CAP-030).

**Contract surface.** **None.** This is the cheapest seam by design: an engine is adapter-internal. The adapter translates the Runtime's internal call into the engine's native invocation and normalizes the engine's output into the standard per-evaluator finding (category, verdict/confidence, reason codes, evaluator version, latency). No engine-specific field, code, or response shape may pass the adapter boundary (CON-004; [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md) §5 — "addable behind this same adapter pattern without changing policy semantics").

**Process.**
1. Classify the engine against the catalog: which category (or categories) does it serve, at which stages?
2. Implement the adapter emitting the standard finding shape; map the engine's native verdicts onto stable, vendor-neutral reason codes ([`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) §3.5; taxonomy pending [OQ-019](../00-source-of-truth/OPEN-QUESTIONS.md#oq-019--reason-code-taxonomy-and-guardrail-category-as-an-auditmetric-dimension)).
3. Expose the engine through policy `evaluator configuration` only — policies select it by neutral name, never by vendor identifier (AGR-005).
4. Define the engine's failure signature: detector-unavailable and detector-timeout events route to RT-6 and are governed by the workload's declared failure behavior (AGR-012).
5. Run the standard conformance fixtures — the same suite that validates the Bedrock adapter validates any new engine ([`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) §7).
6. Update: `CONTAINER-ARCHITECTURE.md` §5 engine table, the catalog's `Evaluator family` fields, `GLOSSARY.md` if new vocabulary.

**Constraints inherited.** CON-003/CON-004 (no coupling to vendor structures), CON-005/CON-006 (mode-identical, policy/connector separation), CON-016 (per-engine stage timeout). Third-party or customer-authored engines additionally require the security review model of [OQ-013](../00-source-of-truth/OPEN-QUESTIONS.md#oq-013--customer-specific-evaluator-plugin-governance) to be resolved first — plugin code sees evaluated content, so its trust boundary is a precondition, not an afterthought.

**Governance gate.** First-party engines within an existing category and stage: engineering-level decision plus the catalog/architecture updates above. Engines implying a new category, a new stage, or third-party code: the corresponding register path (Path A / OQ-013).

**Sprawl control.** Engines multiply latency and operational surface. NFR-MAINT-003 ([`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md)) bounds this: every engine behind the standard adapter, every engine mapped to a catalog category, no policy able to invoke an engine the platform does not list. Only policy-configured evaluators execute per stage — breadth of available engines must not degrade workloads that use few.

---

## 5. Seam 3 — New decision action

**What it is.** Adding a value to the normalized decision's `action` vocabulary — what a policy may instruct a connector to do.

**Contract surface.** The `Decision.action` enum and, critically, **connector enforcement behavior**: an action only exists once every connector can enforce it (or explicitly declares it unsupported and the policy compatibility check refuses the binding).

**Reserved vs. proposed vs. misclassified.** Active: `allow`, `block`, `redact`, `log_only`. Reserved (future scope, seed.md §9): `rewrite`, `discard`, `abstain`, `require_approval`. The catalog's draft heritage suggests more; this document classifies them normatively:

| Candidate | Classification | Reason |
|---|---|---|
| `warn` | **Not an action — a binding mode.** | `Warn` is a state on the AGR-011 enforcement ladder (Disabled → Observe → Warn → Redact → Block), recorded on the policy binding (CP-3), not a per-decision instruction. Introducing a `warn` action would create two meanings for one word across the same contract. |
| `terminate`, `revoke` | **Control-plane operations, not decision actions.** | Stopping a workflow, suspending a workload, revoking a credential are administrative operations against control-plane entities, with their own authorization and audit path — not values a per-content evaluation returns. |
| `regenerate`, `quarantine`, `safe_fallback` | **Proposed, not reserved.** | Plausible future actions; each needs its own OQ→AGR path. Note `safe_fallback` already exists as *failure behavior* (AGR-012) — reserving it as an action would need to reconcile the two meanings first. |

**Process (activating a reserved action).**
1. Scope decision recorded (e.g., `require_approval` activates with CAP-027/FR-087 in Phase 5).
2. Define enforcement semantics per connector type — what the LiteLLM connector, the SDK caller, and the Guarded Gateway each do on receiving it, including its failure behavior interaction (AGR-012).
3. Version the decision structure (AGR-006); unknown-action tolerance is **not** acceptable for enforcement clients — compatibility metadata (policy entity) plus CP-4's compatibility validation must prevent a policy using the new action from binding to a workload whose connector cannot enforce it.
4. Extend conformance fixtures across all three modes; extend the decision-distribution metric enumeration ([`OPERABILITY.md`](OPERABILITY.md) §3).
5. Update: `INTEGRATION-ARCHITECTURE.md` §3.4 action table, `GLOSSARY.md` action entry, catalog items that referenced the action as vision.

**Constraints inherited.** CON-005 (identical across modes); AGR-011 (the action must compose sensibly with observe mode — in Observe, every action's effect degrades to recorded-not-applied); AGR-012 (its failure paths must be declarable).

**Governance gate.** Always register-level: an action changes what every connector must implement, so it is never an engineering-local decision.

---

## 6. Seam 4 — New connector

**What it is.** Adding a client of the Evaluation API — another gateway plugin (Kong, Envoy), an agent-framework hook, app middleware, a sidecar — beyond the three defined clients (LiteLLM connector, generic SDK, Guarded Gateway).

**Contract surface.** **None.** A connector implements the invocation contract; it cannot see, let alone change, policy semantics (AGR-007: "new connectors can be added by implementing the invocation contract only").

**Process.** Implement the connector responsibilities of [`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) §4.2, none of §4.3:
1. Map the host framework's request/response into stage-appropriate evaluation requests.
2. Invoke the stages the workload's policy configures (input before inference, output after; later stages as they activate).
3. Enforce every active action (`block`, `redact`, safe fallbacks) — an enforcement client that cannot enforce an action must be refused at binding time, not fail silently at runtime.
4. Propagate one correlation identifier across all stages of a logical request.
5. Apply the workload's configured timeout and failure behavior — no undocumented fallback (AGR-012).
6. Emit connector-health telemetry (reachability, timeout rate, error rate).
7. Pass the connector conformance suite — the same fixtures used for the LiteLLM connector ([`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) §7).

**Constraints inherited.** CON-003 (the public contract never bends toward the new framework's structures), CON-006 (no policy logic, thresholds, or detection in the connector). AGR-018 applies undiminished: a cooperatively installed connector — any connector — is not a hard security boundary and must not be represented as one.

**Governance gate.** Engineering-level: no policy, decision, or reason-code change is required, which is precisely the point (AGR-003 — LiteLLM is the first connector, not the boundary). A connector that *would* require a contract change is a signal that seams 1 or 3 are actually being exercised — route it there.

---

## 7. Category attachment model

The guardrail category (`GR-001`–`GR-018`) is a **classification dimension, not a domain entity**. Under AGR-017 the domain model stays six entities; a category attaches to the existing model at three points without adding a seventh:

1. **Policy → categories (indirect).** A policy's `evaluator configuration` selects evaluators; each evaluator is mapped (by the catalog's `Evaluator family` fields) to the categories it serves. A policy therefore *covers* a set of categories derivably — no `category` field is added to the policy schema.
2. **Finding/decision → category (via reason codes).** The natural join between a runtime decision and the catalog is the reason code: if reason codes are namespaced by category (the open design of [OQ-019](../00-source-of-truth/OPEN-QUESTIONS.md#oq-019--reason-code-taxonomy-and-guardrail-category-as-an-auditmetric-dimension)), every decision becomes category-attributable with no new decision field, and per-category metrics ([`OPERABILITY.md`](OPERABILITY.md)) fall out of existing audit metadata. Until OQ-019 resolves, category attribution is derivable only indirectly and no per-category metric is committed.
3. **Catalog → committed scope (via CAP/FR IDs).** Every non-vision catalog item cites the CAP or FR entry that commits it; the traceability chain is GR → CAP/FR → AGR, never GR directly to an implementation.

What this deliberately prevents: category-specific request shapes, category-conditional contract behavior, or a "category engine" — a category has no runtime existence beyond the evaluators that serve it and the reason codes that attribute to it.

---

## 8. Control-type metadata

The catalog annotates entries with control types — preventive, detective, corrective, directive, deterrent, recovery, governance. In this architecture they are **descriptive metadata only**:

- They classify *intent* for readers, risk mapping, and pack composition. They are not fields of the evaluation contract, not policy schema values, and impose no runtime behavior.
- One normative consequence, inherited from [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model): anything classified **directive** (system-prompt instructions, conventions, guidance to the model) is by definition not an enforced control and must never be represented as a security boundary. Where the catalog marks an item directive, the honest reading is "this shapes behavior; something else must enforce it."

Should control type ever need to become machine-readable (e.g., in the §9 definition model), that is a policy-schema design decision routed through the normal contract-change process — not implied by this section.

---

## 9. Guardrail definition model (vision)

**Status: Vision — uncommitted.** As the catalog matures, each *implemented* guardrail should carry a machine-readable definition. The shape below is an **illustrative target**, subordinate to the versioned policy schema and evaluation contract (AGR-006) — no field named here is committed, and where this sketch and the actual policy schema diverge, the schema wins:

```yaml
id: GR-001-TBD              # stable item ID, assigned only at graduation
name: direct-prompt-injection
category: GR-001            # catalog category (classification, §7)

controlTypes: [preventive, detective]   # descriptive metadata (§8)

stages: [input]             # reserved stages only (§3)

supportedModes: [remote-evaluation, guarded-gateway, customer-managed-runtime]
                            # must be all three unless a recorded exception exists (AGR-006)

evaluator:
  family: bedrock-guardrails    # vendor-neutral selector (AGR-005)
  capability: prompt-attack

defaultAction: block        # active or reserved actions only (§5)
enforcementModes: [observe, warn, block]   # AGR-011 ladder states the item supports

threshold: TBD              # per-policy configuration, not per-definition constants

failureBehavior:            # references the six AGR-012 failure modes
  evaluatorUnavailable: per-policy
  timeout: per-policy

evidence:
  retainContent: false      # AGR-014 default; tenant-configurable
  retainReasonCodes: true

ownership:
  policyOwner: TBD
  technicalOwner: TBD

testing:
  requiredSuites: [conformance, adversarial-regression]
```

Two rules the sketch already encodes: `supportedModes` defaults to all three (one contract — a mode-limited guardrail is an exception demanding a recorded reason), and `failureBehavior` delegates to policy (AGR-012 makes failure behavior a policy obligation, not a per-guardrail constant).

---

## 10. Catalog governance and graduation

The catalog stays honest only if movement through it is governed. Two change classes, then the pipeline:

**Path A — register-level (change-controlled):**
- Adding, removing, renaming, or reclassifying a **category** (amend AGR-021).
- Changing an entry's **status** between the four values — in any direction. Promotion (Vision → committed) and demotion both.
- Activating a **stage** or **action**; expanding the reserved vocabularies (OQ-018 path).
- Anything touching an out-of-boundary classification (these encode seed.md §6.3 and AGR-010 — moving one is a scope decision of the first order).

**Path B — derived-document edits (not change-controlled):**
- Adding, rewording, splitting, or merging **item rows** within their existing status.
- Updating cross-references, evaluator-family fields, external-mapping cells.
- Constraint: no Path B edit may alter any status, contradict an L1 register, or phrase an out-of-boundary item as future work.

**Graduation pipeline** (the only path from vision to delivery — each step recorded, none skippable):

```text
Vision — uncommitted (catalog entry)
   │  1. Raise the gating OQ in OPEN-QUESTIONS.md (or cite the existing one —
   │     several graduations are pre-gated: OQ-017 for GR-012, OQ-018 for new
   │     stages, OQ-013 for plugin engines, OQ-012 for signing/inheritance)
   ▼
Open question resolved
   │  2. Record the decision: new AGR (and/or DEC) via change control —
   │     this IS the explicit scope-change decision SCOPE.md §8 requires
   ▼
Committed scope
   │  3. Add/activate the CAP and FR entries; add the TRACEABILITY-MATRIX row;
   │     update the catalog entry's status to "Later phase — committed"
   ▼
Phased delivery
      4. Sequence per AGR-020; implement via the seams (§3–§6);
         ship behind observe-before-enforce (AGR-011)
```

The inverse also holds: nothing may skip the pipeline by editing the catalog. A catalog entry whose status says `Vision — uncommitted` and whose text quietly reads like a commitment is a defect — the claims-register review and RSK-010 ([`70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md)) exist to catch it.

**Out-of-boundary is not a pipeline stage.** `Out-of-boundary` entries have no graduation path. Re-scoping one requires amending the underlying exclusion (seed.md §6.3 reaffirmed by DEC-008, or AGR-010) — a fundamentally different, heavier decision than graduating a vision item, and one this document takes no position on.

---

## 11. Policy-pack mechanics (vision)

**Status: Vision — uncommitted.** If pursued, packs compose from existing machinery only:

- **A pack is a policy template, not a policy type.** Instantiating a pack yields an ordinary versioned policy (CAP-001 semantics: immutable versions, bindings, observe-before-enforce). The domain model gains nothing (AGR-017).
- **Packs are not inheritance.** A pack instantiation is a copy-at-a-version; changes to the pack do not propagate to instantiated policies. Live propagation is exactly the structured inheritance/composition of CAP-029 (Later phase, gated on [OQ-012](../00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design)) — packs must not pre-implement it informally, or the policy-fragmentation risk ([`TECHNICAL-RISKS.md`](TECHNICAL-RISKS.md) §4.5) returns through the side door.
- **Packs select only what exists.** A pack may reference only capabilities delivered at its release; the catalog's pack table names target categories, not active features.
- **Packs stay portable.** Pack definitions are policy-schema artifacts — vendor-neutral (AGR-005), connector-free (AGR-007).

---

## 12. Constraints and agreements applied in this view

| Rule applied here | Source |
|---|---|
| Extend the six-entity domain model; never fork or parallel it | [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) |
| Contract changes versioned, backward compatible, all three modes simultaneously, conformance-tested identically | [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes), CON-005 |
| Engines only behind adapters; no vendor shape past the adapter boundary | [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), CON-004 |
| Policies vendor-, framework-, connector-neutral | [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), CON-003 |
| Connectors implement invocation only; no policy logic | [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors), CON-006 |
| Every stage (present and future) has an input-size limit and timeout | CON-015, CON-016 |
| Every new action composes with the observe ladder; `warn` is a binding mode, not an action | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) |
| Failure behavior is declared policy, including for new engines and actions | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) |
| No connector — new or old — is a hard security boundary | [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) |
| Category taxonomy is classification; graduation is the recorded scope-change path | [AGR-021](../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model), [`SCOPE.md`](../10-charter/SCOPE.md) §8 |
| Guardrails are not authorization; out-of-boundary entries never graduate sideways | [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization), seed.md §6.3 |

---

## 13. Related documents

- [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) — the L2 catalog this document gives mechanics to.
- [`adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md`](adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md) — design record (Proposed).
- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) / [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — the L1 registers every seam process cites.
- [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) — OQ-012/013/017/018/019 gate specific graduations.
- [`INTEGRATION-ARCHITECTURE.md`](INTEGRATION-ARCHITECTURE.md) — the contract (§3), connector responsibilities (§4.2), and conformance property (§7) the seams extend.
- [`CONTAINER-ARCHITECTURE.md`](CONTAINER-ARCHITECTURE.md) §5 — the evaluator-engine table seam 2 grows.
- [`COMPONENT-ARCHITECTURE.md`](COMPONENT-ARCHITECTURE.md) — RT-1…RT-7/CP-1…CP-4 components the seams touch.
- [`REQUIREMENTS.md`](REQUIREMENTS.md) §13 — the future-scope FR slots (FR-078–FR-094) graduations activate.
- [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) — NFR-MAINT-001/002/003 bounding this growth.
- [`TECHNICAL-RISKS.md`](TECHNICAL-RISKS.md) §4.5/§4.8/§4.9 — the risks this model is designed to contain.
- [`OPERABILITY.md`](OPERABILITY.md) — metric and audit enumerations extended by seams 1 and 3.
- [`../70-governance/DOCUMENT-GOVERNANCE.md`](../70-governance/DOCUMENT-GOVERNANCE.md) §6 — Path A / Path B change control referenced throughout §10.
