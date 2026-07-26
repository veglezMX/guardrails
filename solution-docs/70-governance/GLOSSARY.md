**AI Guardrails — Glossary**

**Authority level:** L2
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

## Purpose

This glossary defines the domain and product terms used consistently across the AI Guardrails solution-docs set. Every term below is derived from `seed.md` and the accepted register in [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md); none of it may be used, in this document or any other, in a way that contradicts an entry in [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both **L1 — Authoritative**). Where this glossary and an L1 register appear to disagree, the L1 register wins and this document must be corrected.

This glossary exists so that product, architecture, sales, and executive materials use the same vocabulary for the same concepts — a direct requirement of [AGR-017 — Core domain model](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), which states that documentation across all layers must use the platform's domain-model terms consistently.

Figures, dates, and thresholds are not restated here beyond what `seed.md` specifies. Where a number would normally be expected (a latency figure, an SLA, a size limit) and the seed does not supply one, the corresponding definition says so or points to the constraint that marks it **TBD** (see [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md)).

---

## Quick index

[Action](#action-allow--block--redact--log_only) · [AI Guardrails](#ai-guardrails) · [Audit metadata](#audit-metadata) · [Bedrock Guardrails](#bedrock-guardrails) · [Category-namespaced reason code](#category-namespaced-reason-code) · [Connector](#connector) · [Control plane](#control-plane) · [Control type](#control-type) · [Correlation identifier](#correlation-identifier) · [Customer-Managed Runtime](#customer-managed-runtime) · [Data plane](#data-plane) · [Decision](#decision) · [Deterministic evaluator](#deterministic-evaluator) · [Enforce mode](#enforce-mode) · [Enforcement layer](#enforcement-layer) · [Evaluation](#evaluation) · [Evaluation contract](#evaluation-contract) · [Evaluator adapter](#evaluator-adapter) · [Extension seam](#extension-seam) · [Failure behavior](#failure-behavior) · [Governance class](#governance-class) · [Graduation](#graduation) · [Guarded Gateway](#guarded-gateway) · [Guardrail category](#guardrail-category) · [Guardrail item](#guardrail-item) · [Guardrail policy](#guardrail-policy) · [Import](#import) · [Integration manifest](#integration-manifest) · [LiteLLM](#litellm) · [Mandatory policy](#mandatory-policy) · [Observe mode](#observe-mode) · [PII evaluator](#pii-evaluator) · [Policy binding](#policy-binding) · [Policy inheritance (v0)](#policy-inheritance-v0) · [Policy pack](#policy-pack) · [Redaction](#redaction) · [Reason code](#reason-code) · [Remote Evaluation](#remote-evaluation) · [Tenant](#tenant) · [Vision — uncommitted](#vision--uncommitted) · [Workload](#workload)

---

## Core domain model at a glance

The six terms below in bold are the core domain entities named in [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model); every other term in this glossary describes something that acts on, configures, or reports against one of them.

```text
Tenant
  └─ owns ──► Workload
                └─ carries ──► Policy Binding ──► Guardrail Policy (versioned)
                                                       │
                                                       │ governs
                                                       ▼
                                                  Evaluation (input | output)
                                                       │
                                                       ▼
                                                    Decision
                                          (action, reason codes, correlation
                                           identifier, audit metadata)
```

---

## Terms

### Action (allow / block / redact / log_only)

The outcome a [Decision](#decision) assigns to evaluated content. `seed.md` §9 defines four initial actions and reserves four future actions:

| Action | Meaning | Status |
| --- | --- | --- |
| `allow` | Content proceeds without modification. | Initial (in scope) |
| `block` | Content or request is prevented from proceeding. | Initial (in scope) |
| `redact` | Content is transformed to remove or mask a detected violation. | Initial (in scope) |
| `log_only` | The decision is recorded without affecting the request. | Initial (in scope) |
| `rewrite` | Reserved — not defined in the Phase 1–2 core delivery. | Later phase |
| `discard` | Reserved — not defined in the Phase 1–2 core delivery. | Later phase |
| `abstain` | Reserved — not defined in the Phase 1–2 core delivery. | Later phase |
| `require_approval` | Reserved — not defined in the Phase 1–2 core delivery. | Later phase |

See also: [Redaction](#redaction), [Decision](#decision), [Enforce mode](#enforce-mode).

---

### AI Guardrails

The enterprise AI governance and runtime-enforcement platform described in this document set: a reusable policy, evaluation, and enforcement layer for applications that consume large language models or execute agentic workflows. It is built and delivered as a **standalone** platform — not as functionality embedded inside any single team's LiteLLM instance ([AGR-001](../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm)) — and supports three deployment modes: [Remote Evaluation](#remote-evaluation), [Guarded Gateway](#guarded-gateway), and [Customer-Managed Runtime](#customer-managed-runtime) ([AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes)).

See also: [`../10-charter/CHARTER.md`](../10-charter/CHARTER.md), [`../20-product/PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md).

---

### Audit metadata

The normalized record captured for every evaluation, including tenant, workload, policy version, evaluation stage, decision, reason codes, evaluator versions, timestamp, correlation identifier, and whether raw content was retained (`seed.md` §15). Audit metadata is part of the one logical [evaluation contract](#evaluation-contract) that must remain compatible across all three deployment modes ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)).

See also: [Correlation identifier](#correlation-identifier), [Decision](#decision), [Failure behavior](#failure-behavior).

---

### Bedrock Guardrails

Amazon Bedrock Guardrails, adopted as the platform's **first managed evaluation engine**. Its vendor-specific response format is normalized behind an [evaluator adapter](#evaluator-adapter) into the platform's own decision structure, so the public evaluation contract remains vendor-independent; Bedrock Guardrails must never be described as the product's permanent or exclusive detection engine ([AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)).

See also: [Deterministic evaluator](#deterministic-evaluator), [PII evaluator](#pii-evaluator), [Evaluator adapter](#evaluator-adapter).

---

### Category-namespaced reason code

A [reason code](#reason-code) emitted in the canonical form `GR-0NN:<evaluator-family>:<code>`, tying every decision back to a specific [guardrail category](#guardrail-category) and giving the decision record a first-class `guardrail_category` field for per-category audit and reporting ([DEC-013](../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension), Proposed; design detail in [ADR-0003](../50-architecture/adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md), Proposed).

See also: [Reason code](#reason-code), [Guardrail category](#guardrail-category), [Decision](#decision).

---

### Connector

The component that defines **how** a specific gateway or application invokes and enforces a [guardrail policy](#guardrail-policy) — for example, mapping requests into the evaluation contract, invoking input/output evaluation, and applying block or redact decisions. A connector must never embed policy logic, thresholds, or detection rules; this separation from policy is mandatory ([AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)). [LiteLLM](#litellm) is the first supported connector, but not the product boundary ([AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)).

**"Install connector" means three different things, depending on connector type.** The onboarding flow ([Import](#import)) uses one phrase for all three, and the workload's **connector type** attribute is what disambiguates:

| Connector type | What "install" means |
| --- | --- |
| Gateway plugin (e.g. LiteLLM) | Install a published plugin artifact into the gateway and configure it from the integration manifest |
| Generic SDK | Add the published SDK as a dependency and add evaluation calls at the application's own input and output points |
| Consumer-operated custom gateway | **Nothing is installed.** The consuming team implements the evaluation contract directly and demonstrates conformance ([AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)) |

In all three cases the connector's responsibilities, its non-responsibilities, and its cooperative status ([AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)) are identical — only the installation mechanics differ. Distribution and integrity of the published artifacts are specified in [ADR-0008](../50-architecture/adrs/ADR-0008-connector-distribution-and-integrity.md); how the platform learns which connector is actually running is specified in [ADR-0007](../50-architecture/adrs/ADR-0007-connector-identity-verification-and-liveness.md).

See also: [Guardrail policy](#guardrail-policy), [LiteLLM](#litellm), [Import](#import), [Integration manifest](#integration-manifest).

---

### Control plane

The centralized component that owns tenants, workloads, policies, bindings, and rollouts, and distributes policy configuration to the guardrails runtime (`seed.md` §10, Logical architecture). In [Customer-Managed Runtime](#customer-managed-runtime) deployments, the runtime must remain able to execute policies **without** a synchronous connection to the control plane ([AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)).

See also: [Data plane](#data-plane), [Policy binding](#policy-binding), [Customer-Managed Runtime](#customer-managed-runtime).

---

### Control type

Descriptive metadata classifying a guardrail's intent in the [guardrail-category catalog](#guardrail-category): **preventive**, **detective**, **corrective**, **directive**, **deterrent**, **recovery**, or **governance** ([`../20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md); [`../50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md) §8). Control types are classification only — they are not fields of the evaluation contract and impose no runtime behavior. One normative caution: a **directive** control (for example, a system-prompt instruction) is not an enforced control and must never be represented as a security boundary ([AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)).

See also: [Guardrail category](#guardrail-category), [Enforcement layer](#enforcement-layer).

---

### Correlation identifier

An identifier propagated across an evaluation request and its resulting [decision](#decision) so that a single logical operation — for example, input evaluation, model invocation, and output evaluation — can be traced end to end. It is a required field of both the Decision structure and [audit metadata](#audit-metadata) (`seed.md` §9, §15).

See also: [Decision](#decision), [Audit metadata](#audit-metadata).

---

### Customer-Managed Runtime

Deployment **Mode C**: the guardrail runtime is deployed inside the consuming team's own AWS account, including a local policy cache, a [Bedrock Guardrails](#bedrock-guardrails) adapter, custom evaluators, and local audit integration (`seed.md` §3, Mode C). It is intended for regulated workloads, data-residency requirements, high-volume workloads, air-gapped environments, and teams that prohibit prompt or response content from leaving their account. The runtime must be capable of executing policies without requiring a synchronous connection to the hosted [control plane](#control-plane) ([AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)), and telemetry sent to the hosted control plane is customer-controlled down to a fully supported "no telemetry" configuration ([AGR-016](../00-source-of-truth/AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime)).

See also: [Remote Evaluation](#remote-evaluation), [Guarded Gateway](#guarded-gateway), [Control plane](#control-plane).

---

### Data plane

The runtime path that actually processes and evaluates tenant content — the Guardrails Runtime, its Evaluation API, policy resolver, decision engine, evaluator adapters, and audit producer (`seed.md` §10) — as distinct from the [control plane](#control-plane), which manages tenants, workloads, policies, bindings, and rollouts rather than evaluated content itself.

See also: [Control plane](#control-plane), [Evaluation](#evaluation), [Evaluator adapter](#evaluator-adapter).

---

### Decision

The normalized result of an [evaluation](#evaluation), containing an [action](#action-allow--block--redact--log_only), an allowed status, [reason codes](#reason-code), policy name and version, evaluator metadata, transformed content where applicable, a [correlation identifier](#correlation-identifier), an audit identifier, and evaluation latency (`seed.md` §9). Decision structures are part of the one logical [evaluation contract](#evaluation-contract) that must remain compatible across Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)).

See also: [Action](#action-allow--block--redact--log_only), [Reason code](#reason-code), [Correlation identifier](#correlation-identifier).

---

### Deterministic evaluator

An evaluation engine that applies fixed, organization-defined rules — rather than a probabilistic or model-based detector — to evaluated content. A basic deterministic evaluator is named explicitly as one of the Phase 1–2 core components, alongside the [Bedrock Guardrails](#bedrock-guardrails) adapter and [PII evaluators](#pii-evaluator) (`seed.md` §6.1, §16).

See also: [Bedrock Guardrails](#bedrock-guardrails), [PII evaluator](#pii-evaluator), [Evaluator adapter](#evaluator-adapter).

---

### Enforce mode

The [policy binding](#policy-binding) state in which a policy's block, redact, or other enforcement actions actively apply to a workload's traffic, rather than merely being recorded. Enforce mode is the terminal stage of the observe-before-enforce progression and must be reached only after a team has measured false positives, latency, and business impact in [observe mode](#observe-mode) ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)).

```text
Disabled → Observe → Warn → Redact → Block
```

See also: [Observe mode](#observe-mode), [Policy binding](#policy-binding), [Failure behavior](#failure-behavior).

---

### Enforcement layer

The classification, per [guardrail category](#guardrail-category), of **where** a control is realized ([`../20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md)): `evaluator` (content evaluation at a stage, behind the [evaluator adapter](#evaluator-adapter) pattern), `connector-gateway` (enforced by the gateway/connector layer — rate limits, routing, model selection), `control-plane` (registries, policy store, rollout, audit pipeline), or `out-of-boundary` (not realized by this product; owned by a named external party per seed.md §6.3 or [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization)).

See also: [Guardrail category](#guardrail-category), [Control type](#control-type).

---

### Evaluation

A request to inspect content at a particular stage of an interaction (`seed.md` §9).

| Stage | Status |
| --- | --- |
| `input` | Initial (in scope) |
| `output` | Initial (in scope) |
| `retrieval` | Later phase |
| `tool_call` | Later phase |
| `tool_result` | Later phase |
| `memory_write` | Later phase |
| `final_response` | Later phase |

Phases 1–2 limit evaluated content to user/model input and model output; retrieved documents, tool arguments, tool responses, and structured-output payloads are later-phase evaluation categories, part of the defined full feature set (DEC-008) but delivered only in their phase, depending on workload configuration (`seed.md` §13).

See also: [Evaluation contract](#evaluation-contract), [Decision](#decision).

---

### Evaluation contract

The single logical contract — covering policy definitions, request structures, decision structures, reason codes, enforcement actions, audit metadata, and policy-version semantics — that [Remote Evaluation](#remote-evaluation), [Guarded Gateway](#guarded-gateway), and [Customer-Managed Runtime](#customer-managed-runtime) must all implement identically, regardless of where the runtime is deployed ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)). Mode-specific optimizations, such as caching or telemetry, must never alter this externally observable contract.

See also: [Decision](#decision), [Evaluation](#evaluation), [Reason code](#reason-code).

---

### Evaluator adapter

The component that normalizes a specific detection engine's request/response format — for example, [Bedrock Guardrails'](#bedrock-guardrails) — into the platform's vendor-independent decision structure. Evaluator adapters are the mechanism by which additional evaluation engines can be added without changing the public evaluation contract or requiring policies to be rewritten ([AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability)).

See also: [Bedrock Guardrails](#bedrock-guardrails), [Deterministic evaluator](#deterministic-evaluator), [PII evaluator](#pii-evaluator).

---

### Extension seam

One of the four defined channels through which all guardrail-coverage growth flows ([`../50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md); [AGR-021](../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model)): a new **evaluation stage**, a new **evaluation engine** (adapter-internal, no contract change), a new **decision action**, or a new **connector** (a client of the invocation contract). Each seam has a defined process, inherited constraints, and a governance gate; every extension extends — never forks — the core domain model ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)).

See also: [Evaluator adapter](#evaluator-adapter), [Evaluation contract](#evaluation-contract), [Graduation](#graduation).

---

### Failure behavior

The explicit, per-workload definition of what happens when an evaluation cannot complete normally — for example, an Evaluation API timeout, detector unavailability, an unresolved policy, control-plane unavailability, audit-delivery failure, or an unsafe response transformation (`seed.md` §5.7). Failure behavior is a mandatory part of the policy itself, and there must be no undocumented fallback behavior ([AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy)).

See also: [Guardrail policy](#guardrail-policy), [Control plane](#control-plane).

---

### Governance class

One of three classifications a [guardrail policy](#guardrail-policy) carries — **Mandatory (pinned)**, **Recommended (overridable)**, or **Own (consumer-authored)** — that determines who may set, strengthen, or override it. The effective policy for a workload is the mandatory base (locked) layered with consumer overrides and additions, per [policy inheritance (v0)](#policy-inheritance-v0) ([DEC-011](../00-source-of-truth/DECISIONS.md#dec-011--policy-governance-classes-and-mandatory-policy-authority), Proposed).

See also: [Mandatory policy](#mandatory-policy), [Policy inheritance (v0)](#policy-inheritance-v0), [Guardrail policy](#guardrail-policy).

---

### Graduation

The governed pipeline by which a [Vision — uncommitted](#vision--uncommitted) catalog entry becomes committed scope: gating open question raised/resolved → decision recorded as a new AGR/DEC (the explicit scope-change decision required by [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) §8) → CAP/FR entry created → phased delivery per [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) ([`../50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md) §10). Out-of-boundary entries have no graduation path — re-scoping one requires amending the underlying exclusion itself.

See also: [Guardrail category](#guardrail-category), [Vision — uncommitted](#vision--uncommitted), [Extension seam](#extension-seam).

---

### Guarded Gateway

Deployment **Mode B**: the AI Guardrails provider operates both the LLM gateway and the guardrail runtime, offering input guardrails, model routing, model invocation, output guardrails, and audit/usage reporting as a single managed path (`seed.md` §3, Mode B). It is intended for teams that do not operate their own LiteLLM instance, want a managed model-access endpoint, and accept that the provider participates in the complete inference path.

See also: [Remote Evaluation](#remote-evaluation), [Customer-Managed Runtime](#customer-managed-runtime), [LiteLLM](#litellm).

---

### Guardrail category

One of the eighteen change-controlled entries (`GR-001`–`GR-018`) of the guardrail-category catalog ([`../20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md)), sanctioned by [AGR-021](../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model). A category classifies guardrail controls by subject (for example, Prompt and Instruction Security, Sensitive Data and Privacy) and carries an [enforcement layer](#enforcement-layer), [control types](#control-type), stages, evaluator family, and a phase status. A category is a **classification dimension over the existing domain model, not a seventh domain entity** ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)); the catalog classifies — it never commits scope.

See also: [Guardrail item](#guardrail-item), [Graduation](#graduation), [Enforcement layer](#enforcement-layer).

---

### Guardrail item

An individual guardrail listed inside a [guardrail category's](#guardrail-category) item table. Items are **illustrative and not change-controlled** — they may be added, reworded, split, or merged as ordinary derived-document edits, provided no edit alters a status, contradicts an L1 register, or phrases an out-of-boundary item as future work. An item receives a stable sub-identifier only if and when it [graduates](#graduation) into committed scope.

See also: [Guardrail category](#guardrail-category), [Vision — uncommitted](#vision--uncommitted).

---

### Guardrail policy

The versioned definition of what must be evaluated and how violations are handled: evaluation stages, evaluator configuration, enforcement actions, thresholds, [failure behavior](#failure-behavior), audit behavior, and compatibility metadata (`seed.md` §9). A policy is mandatorily separate from any [connector](#connector) that invokes it — a policy defines *what* to check and how to react; a connector defines *how* a specific gateway invokes and enforces it ([AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)).

See also: [Connector](#connector), [Policy binding](#policy-binding), [Failure behavior](#failure-behavior).

---

### Import

The act of a consuming team adopting a versioned [guardrail policy](#guardrail-policy), a workload identity, an integration manifest, a [connector](#connector) configuration, and an [enforce mode](#enforce-mode) or [observe mode](#observe-mode) setting (`seed.md` §4). Import never means importing, cloning, registering, or remotely controlling another team's LiteLLM instance, and the process must never require copying another team's LiteLLM database, transferring model-provider credentials, exposing gateway administration endpoints, replacing model-routing configuration, or granting the guardrail provider administrative access to the team's gateway ([AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import)).

The canonical onboarding flow that defines "import" in practice:

```text
Register workload → Select policy → Generate integration manifest
  → Install connector → Run in observe mode → Review evaluation results
  → Enable enforcement
```

See also: [Policy binding](#policy-binding), [Connector](#connector), [Observe mode](#observe-mode), [Integration manifest](#integration-manifest).

---

### Integration manifest

The generated, shareable configuration artifact a consuming team receives at the "generate integration manifest" step of [Import](#import) and installs alongside its [connector](#connector). It carries workload identity, the bound policy name and version, enforcement mode, deployment mode and Evaluation API endpoint, connector type and supported version range, per-stage timeouts, client-side [failure behavior](#failure-behavior), and required runtime capabilities.

A manifest is a **file**, not a screen: version-controllable, reviewable in a pull request, and shareable between teams. It is **derived from a [policy binding](#policy-binding)** and is never authoritative at evaluation time — the runtime resolves the effective policy and mode server-side regardless of what a manifest asserts. It is **not a credential**: the workload identity it carries is an identifier, authentication remains IAM-based, and possession grants no access.

A manifest must never contain model-provider credentials, gateway administration endpoints, model-routing configuration, any reference requiring access to a consuming team's LiteLLM database, or any platform secret ([AGR-019](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import); [ADR-0006](../50-architecture/adrs/ADR-0006-integration-manifest-format-and-contents.md)).

See also: [Import](#import), [Policy binding](#policy-binding), [Connector](#connector), [Failure behavior](#failure-behavior).

---

### LiteLLM

The **first supported gateway connector target** for AI Guardrails, and optionally the substrate for the [Guarded Gateway](#guarded-gateway) mode (`seed.md` header, §12). LiteLLM does not define the boundary of the product — the evaluation contract and policy model are defined independently of its internal request/response structures, so additional connectors can be added without changing policy semantics ([AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)).

See also: [Connector](#connector), [Guarded Gateway](#guarded-gateway), [Remote Evaluation](#remote-evaluation).

---

### Mandatory policy

A policy at the **Mandatory (pinned)** [governance class](#governance-class). The security/governance role — never an application team or a tenant admin — holds the authority to mark a policy Mandatory at org/tenant scope; application teams may only strengthen it (earlier enforcement, tighter thresholds), never disable, unbind, downgrade below the pinned version floor, or otherwise weaken it, and a tenant admin cannot override an org-mandatory policy ([DEC-011](../00-source-of-truth/DECISIONS.md#dec-011--policy-governance-classes-and-mandatory-policy-authority), Proposed).

See also: [Governance class](#governance-class), [Policy inheritance (v0)](#policy-inheritance-v0), [Guardrail policy](#guardrail-policy).

---

### Observe mode

The non-blocking [policy binding](#policy-binding) state in which every policy must be capable of running: decisions are recorded and measured without affecting production traffic. Observe mode is the required starting stage of the `Disabled → Observe → Warn → Redact → Block` progression, and onboarding workflows must default new bindings to Observe rather than Block ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)).

See also: [Enforce mode](#enforce-mode), [Policy binding](#policy-binding), [Import](#import).

---

### PII evaluator

An evaluation engine focused on detecting personally identifiable information in evaluated content, supporting the "basic sensitive-data handling" scope item in the Phase 1–2 core (`seed.md` §6.1). PII evaluators sit behind the same [evaluator-adapter](#evaluator-adapter) pattern as [Bedrock Guardrails](#bedrock-guardrails) and [deterministic evaluators](#deterministic-evaluator) (`seed.md` §10, Logical architecture).

See also: [Deterministic evaluator](#deterministic-evaluator), [Evaluator adapter](#evaluator-adapter), [Redaction](#redaction).

---

### Policy binding

The association between a [workload](#workload) and a specific [guardrail policy](#guardrail-policy) version. A binding may also define observe or enforce mode, allowed workload-specific overrides, effective date, rollback version, and runtime compatibility requirements (`seed.md` §9). Policy binding is one of the six core domain entities that all architecture and implementation must be expressed in terms of ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)).

See also: [Workload](#workload), [Guardrail policy](#guardrail-policy), [Enforce mode](#enforce-mode), [Observe mode](#observe-mode).

---

### Policy inheritance (v0)

The MVP-simple composition model for a workload's effective policy: mandatory base (locked) ⊕ consumer overrides and additions. This layering is the v0 form of inheritance, distinct from the structured inheritance/composition capability tracked separately as later-phase CAP-029 ([DEC-011](../00-source-of-truth/DECISIONS.md#dec-011--policy-governance-classes-and-mandatory-policy-authority), Proposed).

See also: [Governance class](#governance-class), [Mandatory policy](#mandatory-policy), [Guardrail policy](#guardrail-policy).

---

### Policy pack

**Vision — uncommitted.** A named, versioned policy template that pre-selects evaluators, thresholds, actions, and failure behavior for a workload archetype (for example, an enterprise baseline or a RAG-secure profile). If pursued, instantiating a pack yields an ordinary versioned [guardrail policy](#guardrail-policy) — a copy at a version, **not** policy inheritance (structured inheritance/composition is the separate later-phase capability CAP-029). Sketched in [`../20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) (Policy packs) and [`../50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md) §11.

See also: [Guardrail policy](#guardrail-policy), [Guardrail category](#guardrail-category).

---

### Redaction

One of the four initial [Decision](#decision) actions (`redact`), in which evaluated content is transformed — rather than fully allowed or fully blocked — to remove or mask a detected violation before it proceeds. Redaction sits between Warn and Block in the recommended observe-to-enforce progression (`seed.md` §5.6, §9).

See also: [Action](#action-allow--block--redact--log_only), [Enforce mode](#enforce-mode).

---

### Reason code

A normalized code attached to a [Decision](#decision) that explains why a particular action was taken. Reason codes are part of the one logical [evaluation contract](#evaluation-contract) and must remain consistent across Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime ([AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)).

See also: [Decision](#decision), [Audit metadata](#audit-metadata).

---

### Remote Evaluation

Deployment **Mode A**: a consuming team operates its own LiteLLM (or equivalent) gateway, and that gateway invokes the hosted AI Guardrails Evaluation API for input evaluation before model invocation and output evaluation after it (`seed.md` §3, Mode A). Mode A must be described only as **"bring your own gateway while consuming a hosted guardrail runtime"** — it must never be described as a private-data or data-local deployment model, because prompts, responses, and any additional evaluated content are transmitted to the hosted Evaluation API ([AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model)).

See also: [Guarded Gateway](#guarded-gateway), [Customer-Managed Runtime](#customer-managed-runtime), [LiteLLM](#litellm).

---

### Tenant

The entity representing an organization, business unit, or consuming team. A tenant carries a tenant identifier, ownership information, AWS account information, data classification, allowed Regions, retention configuration, deployment mode, and authentication configuration (`seed.md` §9). Tenant is one of the six core domain entities defined in [AGR-017 — Core domain model](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model).

See also: [Workload](#workload), [Policy binding](#policy-binding).

---

### Vision — uncommitted

The catalog phase status marking an entry as **direction only**: outside the defined-and-estimated full feature set fixed by [DEC-008](../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases), not scheduled, not resourced, and not estimated ([DEC-009](../00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision)). A vision-classified entry may enter committed scope only through [graduation](#graduation). The other three catalog statuses are `Core (Phases 1–2)`, `Later phase — committed`, and `Out-of-boundary` ([`../20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md), "How to read this catalog").

See also: [Graduation](#graduation), [Guardrail category](#guardrail-category).

---

### Workload

The entity representing an application, assistant, agent, gateway, or inference service that uses guardrails. A workload carries a workload identifier, tenant identifier, owner, environment, risk classification, gateway type, deployment mode, expected throughput, streaming requirements, assigned policy, and enforcement mode (`seed.md` §9). Workload is one of the six core domain entities defined in [AGR-017 — Core domain model](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model).

See also: [Tenant](#tenant), [Policy binding](#policy-binding), [Enforce mode](#enforce-mode).

---

## Related documents

- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — the authoritative agreement register this glossary must never contradict.
- [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — non-negotiable boundaries that give several of these terms (failure behavior, raw-content retention, telemetry) their precise edges.
- [`../10-charter/CHARTER.md`](../10-charter/CHARTER.md) — the full charter narrative these terms are drawn from.
- [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) — the product principles (policy portability, one contract, policies-vs-connectors, observe-before-enforce, failure-as-policy) that several terms operationalize.
- [`../20-product/CAPABILITIES.md`](../20-product/CAPABILITIES.md) — capability descriptions that use these terms in product-facing language.
- [`../20-product/USE-CASES.md`](../20-product/USE-CASES.md) — worked scenarios that apply the onboarding/import flow and the observe-to-enforce progression.
- [`../50-architecture/COMPONENT-ARCHITECTURE.md`](../50-architecture/COMPONENT-ARCHITECTURE.md) — where the control-plane and data-plane components named here are designed in detail.
- [`../50-architecture/DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md) — where audit metadata, retention, and content-exposure terms are given concrete data-flow treatment.
- [`../50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) — where the three deployment-mode terms are expanded into full topology detail.
- [`../50-architecture/INTEGRATION-ARCHITECTURE.md`](../50-architecture/INTEGRATION-ARCHITECTURE.md) — where the connector, evaluator adapter, and evaluation contract terms are expanded into integration detail.
