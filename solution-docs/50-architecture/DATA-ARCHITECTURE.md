**AI Guardrails — Data Architecture**

**Authority level:** L3
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

## Purpose and authority

This document defines the AI Guardrails **data architecture**: the six core domain entities, their attributes and relationships, the AWS storage services assigned to each, and the data-handling rules that govern what content the platform processes, persists, and transmits.

This is a **derived (L3)** artifact. It must not contradict [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both L1 — authoritative), and it must remain consistent with [`CHARTER.md`](../10-charter/CHARTER.md) and [`CAPABILITIES.md`](../20-product/CAPABILITIES.md) (L2). Where a conflict is found, the L1 documents win and this document must be corrected. This document specifically implements [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) **CON-007**, **CON-013**, and **CON-014**, as recorded in that register's [related documents](../00-source-of-truth/CONSTRAINTS.md#related-documents) section.

No metric, dollar figure, SLA, retention period, or performance number is asserted beyond what the L1 registers state. Where a figure would normally appear and is not present in the original charter, this document says **TBD**.

---

## Table of contents

- [1. Domain entity model](#1-domain-entity-model)
  - [1.1 Entity summary](#11-entity-summary)
  - [1.2 Tenant](#12-tenant)
  - [1.3 Workload](#13-workload)
  - [1.4 Policy](#14-policy)
  - [1.5 Policy binding](#15-policy-binding)
  - [1.6 Evaluation](#16-evaluation)
  - [1.7 Decision](#17-decision)
  - [1.8 Relationships](#18-relationships)
  - [1.9 Entity-relationship diagram](#19-entity-relationship-diagram)
- [2. Storage architecture](#2-storage-architecture)
  - [2.1 Storage mapping by entity](#21-storage-mapping-by-entity)
  - [2.2 Policy storage: DynamoDB or versioned S3](#22-policy-storage-dynamodb-or-versioned-s3)
  - [2.3 Audit event pipeline: SQS buffering to S3 archive](#23-audit-event-pipeline-sqs-buffering-to-s3-archive)
  - [2.4 Storage in the Customer-Managed Runtime](#24-storage-in-the-customer-managed-runtime)
  - [2.5 Encryption and transport](#25-encryption-and-transport)
  - [2.6 Per-tenant residency enforcement](#26-per-tenant-residency-enforcement)
- [3. Data-handling policy](#3-data-handling-policy)
  - [3.1 Default behavior (hosted runtime)](#31-default-behavior-hosted-runtime)
  - [3.2 Content categories that may be evaluated](#32-content-categories-that-may-be-evaluated)
  - [3.3 MVP content-scope limitation](#33-mvp-content-scope-limitation)
  - [3.4 Customer-managed telemetry](#34-customer-managed-telemetry)
- [4. Audit metadata](#4-audit-metadata)
- [5. Open items and TBDs](#5-open-items-and-tbds)
- [6. Traceability](#6-traceability)
- [7. Related documents](#7-related-documents)

---

## 1. Domain entity model

The platform's domain model consists of exactly six core entities — **Tenant**, **Workload**, **Policy**, **Policy binding**, **Evaluation**, and **Decision** — as agreed in [`AGREEMENTS.md` AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) and defined in [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) ("Core domain model"). All data storage, APIs, and audit records must map directly onto these six concepts; new capabilities must extend this model rather than introduce parallel vocabulary.

### 1.1 Entity summary

| Entity | Represents | Charter source |
|---|---|---|
| Tenant | An organization, business unit, or consuming team | §9 |
| Workload | An application, assistant, agent, gateway, or inference service using guardrails | §9 |
| Policy | The checks and actions applied to evaluated content, at a specific immutable version | §9 |
| Policy binding | The association between a workload and a policy version | §9 |
| Evaluation | A request to inspect content at a particular stage | §9 |
| Decision | The normalized result of an evaluation | §9 |

### 1.2 Tenant

Represents an organization, business unit, or consuming team.

| Attribute | Notes |
|---|---|
| Tenant identifier | Primary identifier; anchors per-tenant workload identity and tenant isolation (`CONSTRAINTS.md` CON-011) |
| Ownership information | Who owns/operates the tenant relationship |
| AWS account information | Which AWS account(s) the tenant's workloads run in |
| Data classification | Governs handling and retention expectations for the tenant's content |
| Allowed Regions | The AWS Region(s) within which the tenant's content may be processed and stored; enforces per-tenant data residency ([`CAPABILITIES.md` CAP-035](../20-product/CAPABILITIES.md), [`DECISIONS.md` DEC-018](../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga), Status: Proposed) — the platform is multi-region from GA; in the MVP, each tenant is pinned to a single processing region drawn from its Allowed Regions, not active-active across regions (see [2.6](#26-per-tenant-residency-enforcement)) |
| Retention configuration | The tenant-specific retention setting referenced throughout §13 (see [3. Data-handling policy](#3-data-handling-policy)) |
| Deployment mode | Which of the three modes (Remote Evaluation, Guarded Gateway, Customer-Managed Runtime) the tenant operates under |
| Authentication configuration | How the tenant's workloads authenticate to the platform |

### 1.3 Workload

Represents an application, assistant, agent, gateway, or inference service using guardrails.

| Attribute | Notes |
|---|---|
| Workload identifier | Primary identifier |
| Tenant identifier | Foreign key to the owning Tenant |
| Owner | Accountable owner for the workload |
| Environment | e.g., non-production or production, consistent with the minimal MVP account model (`CONSTRAINTS.md` CON-002) |
| Risk classification | Informs which policies and enforcement postures apply |
| Gateway type | e.g., LiteLLM, generic SDK, Guarded Gateway |
| Deployment mode | May differ per workload even within one tenant |
| Expected throughput | Capacity-planning input |
| Streaming requirements | Whether the workload requires streaming evaluation (future scope; see [`SCOPE.md`](../10-charter/SCOPE.md)) |
| Assigned policy | Denormalized pointer to the workload's currently effective policy — the operational read of the active [Policy binding](#15-policy-binding) |
| Enforcement mode | Denormalized pointer to the workload's currently effective mode (Disabled → Observe → Warn → Redact → Block, per [`AGREEMENTS.md` AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)) |

### 1.4 Policy

Defines the checks and actions applied to evaluated content.

| Attribute | Notes |
|---|---|
| Policy name | Groups an ordered family of immutable versions |
| Policy version | Immutable; supports controlled promotion, rollback, and evaluation-result comparison ([`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md)) |
| Evaluation stages | Which stages the policy applies to (`input`, `output`, and future stages — see [1.6 Evaluation](#16-evaluation)) |
| Evaluator configuration | Which evaluator adapters/engines are invoked and how |
| Enforcement actions | Which [Decision](#17-decision) actions the policy may produce |
| Thresholds | Detection thresholds per evaluator |
| Failure behavior | Mandatory per [`AGREEMENTS.md` AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) — no undocumented fallback is permitted |
| Audit behavior | What the policy requires to be captured in the audit trail (see [4. Audit metadata](#4-audit-metadata)) |
| Compatibility metadata | Supports runtime compatibility checks across hosted and customer-managed deployments |

A Policy is not attributed to a single Tenant in the original charter model: the product vision explicitly calls for defining a policy once and applying it to multiple workloads ([`CHARTER.md`](../10-charter/CHARTER.md)), so Policy is scoped independently of Tenant and joined to workloads only through the Policy binding.

### 1.5 Policy binding

Associates a workload with a policy version.

| Attribute | Notes |
|---|---|
| Workload identifier | Foreign key to Workload |
| Policy name + Policy version | Foreign key to the specific immutable Policy version |
| Observe or enforce mode | The binding's current position on the Disabled → Observe → Warn → Redact → Block progression |
| Allowed workload-specific overrides | Bounded per [`AGREEMENTS.md` AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) to avoid policy fragmentation ([`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md)) |
| Effective date | When the binding becomes/became active |
| Rollback version | The version to revert to if the bound version must be rolled back |
| Runtime compatibility requirements | Whether the runtime executing the workload's evaluations supports the bound policy version |

A workload may accumulate multiple Policy binding records over time (current and historical), which is what makes "immediate rollback" and "evaluation-result comparison between versions" ([`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md)) possible; the Workload's `Assigned policy` / `Enforcement mode` attributes are the operational read of whichever binding is currently effective.

### 1.6 Evaluation

Represents a request to inspect content at a particular stage.

Supported initial stages: `input`, `output`. Future stages: `retrieval`, `tool_call`, `tool_result`, `memory_write`, `final_response` ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)).

> **Note on completeness:** unlike the other five entities, [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) does not give Evaluation an explicit attribute bullet list — only its purpose and its stage enumeration. The minimal identifying attributes below (evaluation identifier, workload reference, resolved policy-version reference, correlation identifier) are inferred from the relationships the original charter describes elsewhere — the Decision's `Correlation identifier` attribute, and the audit metadata list in [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) — rather than asserted as a literal charter enumeration. No additional fields, formats, or cardinalities beyond what those two sources support are introduced here.

| Attribute (inferred, see note above) | Notes |
|---|---|
| Evaluation identifier | Needed to relate one Evaluation to exactly one Decision |
| Workload identifier | The workload on whose behalf the evaluation runs |
| Policy name + Policy version (resolved) | The specific policy version in effect for the workload at evaluation time |
| Stage | `input` or `output` (MVP); see [3.3 MVP content-scope limitation](#33-mvp-content-scope-limitation) |
| Correlation identifier | Propagated end-to-end per [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary) (LiteLLM connector responsibility: "Propagating correlation identifiers") |

### 1.7 Decision

Represents the normalized result of an evaluation.

Initial actions: `allow`, `block`, `redact`, `log_only`. Future actions: `rewrite`, `discard`, `abstain`, `require_approval` ([AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model)).

| Attribute | Notes |
|---|---|
| Action | One of the initial (or future) actions above |
| Allowed status | Whether the evaluated content is allowed to proceed |
| Reason codes | Normalized codes explaining the decision, independent of the underlying evaluator/vendor; emitted in category-namespaced form `GR-0NN:<evaluator-family>:<code>`, where `GR-0NN` is the guardrail-category ID from `GUARDRAIL-CATALOG.md` ([`DECISIONS.md` DEC-013](../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension); [`adrs/ADR-0003`](adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md)) |
| Guardrail category | First-class category dimension, populated from the category segment of the reason code(s) that drove the decision's `action` (DEC-013; ADR-0003) — an additive field on this existing entity (`AGREEMENTS.md` AGR-017 "extend, don't fork"), enabling per-category metrics and catalog→decision traceability directly from the decision record, with no external evaluator-code-to-category mapping table |
| Policy name and version | Restated on the Decision itself so the record is self-describing without a live binding lookup |
| Evaluator metadata | Which evaluator engines/adapters produced the result |
| Transformed content | Present only where a transformation (e.g., redaction) was applied |
| Correlation identifier | Ties the decision back to the originating request |
| Audit identifier | Ties the decision to its durable audit record (see [4. Audit metadata](#4-audit-metadata)) |
| Evaluation latency | Per-decision latency measurement, feeding the P50/P95/P99 metrics in [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) |

### 1.8 Relationships

* A **Tenant** owns many **Workloads** (`Workload.Tenant identifier`).
* A **Workload** has one currently effective **Policy binding**, plus zero or more historical bindings retained for rollback and version comparison.
* A **Policy** version may be bound, via **Policy binding**, to many **Workloads** — this many-to-many reuse (one policy, many workloads) is the core product hypothesis under validation in the Phase 1–2 validation demonstration ([`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md); [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md)).
* A **Workload** triggers many **Evaluations**; each **Evaluation** runs under exactly one resolved **Policy** version (the one currently bound to that workload).
* Each **Evaluation** produces exactly one **Decision** — the decision is defined in the original charter as "the normalized result of an evaluation," a one-to-one relationship.

### 1.9 Entity-relationship diagram

```mermaid
erDiagram
    TENANT ||--o{ WORKLOAD : "scopes"
    WORKLOAD ||--o{ POLICY_BINDING : "has current & historical"
    POLICY ||--o{ POLICY_BINDING : "bound via"
    WORKLOAD ||--o{ EVALUATION : "triggers"
    POLICY ||--o{ EVALUATION : "resolved version governs"
    EVALUATION ||--|| DECISION : "produces"

    TENANT {
        string tenant_id PK
        string ownership_information
        string aws_account_information
        string data_classification
        string allowed_regions
        string retention_configuration
        string deployment_mode
        string authentication_configuration
    }
    WORKLOAD {
        string workload_id PK
        string tenant_id FK
        string owner
        string environment
        string risk_classification
        string gateway_type
        string deployment_mode
        string expected_throughput
        string streaming_requirements
        string assigned_policy
        string enforcement_mode
    }
    POLICY {
        string policy_name PK
        string policy_version PK
        string evaluation_stages
        string evaluator_configuration
        string enforcement_actions
        string thresholds
        string failure_behavior
        string audit_behavior
        string compatibility_metadata
    }
    POLICY_BINDING {
        string binding_id PK
        string workload_id FK
        string policy_name FK
        string policy_version FK
        string mode
        string workload_overrides
        string effective_date
        string rollback_version
        string runtime_compatibility_requirements
    }
    EVALUATION {
        string evaluation_id PK
        string workload_id FK
        string policy_name FK
        string policy_version FK
        string stage
        string correlation_id
    }
    DECISION {
        string decision_id PK
        string evaluation_id FK
        string action
        string allowed_status
        string reason_codes
        string guardrail_category
        string policy_name
        string policy_version
        string evaluator_metadata
        string transformed_content
        string correlation_id
        string audit_id
        string evaluation_latency
    }
```

---

## 2. Storage architecture

[`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) ("AWS implementation direction") lists the suggested initial AWS service per capability. This section maps those service choices onto the domain entities in [Section 1](#1-domain-entity-model).

### 2.1 Storage mapping by entity

| Domain entity / data concern | Initial AWS service ([`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md)) | Notes |
|---|---|---|
| Tenant registry | DynamoDB | Grouped with the workload registry as control-plane registry data |
| Workload registry | DynamoDB | [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md): "Workload registry — DynamoDB" |
| Policy | DynamoDB or versioned S3 | [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md): "Policy storage — DynamoDB or versioned S3"; see [2.2](#22-policy-storage-dynamodb-or-versioned-s3) |
| Policy binding | DynamoDB | Requires low-latency point lookups on the evaluation path to resolve a workload's currently effective policy |
| Evaluation (in-flight) | Not persisted independently | Transient, request-scoped state inside the Evaluation runtime (ECS Fargate or EKS per [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md)); it is not a durable store — its durable trace is the Decision plus its audit record |
| Decision metadata (audit) | Amazon SQS (buffer) → Amazon S3 (archive) | [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md): "Audit event buffering — Amazon SQS"; "Audit archive — Amazon S3"; see [2.3](#23-audit-event-pipeline-sqs-buffering-to-s3-archive) |

Compute, networking, and cross-cutting services referenced by [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) (Evaluation runtime, API layer, Bedrock Guardrails, runtime credentials, Secrets Manager, CloudWatch, ECR) are architectural concerns outside the scope of a data-architecture document and are addressed in [`COMPONENT-ARCHITECTURE.md`](COMPONENT-ARCHITECTURE.md) and [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md).

### 2.2 Policy storage: DynamoDB or versioned S3

[`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) lists policy storage as "DynamoDB or versioned S3" without selecting between them; this document does not resolve that choice beyond restating the constraint on the outcome, consistent with the "write TBD rather than invent" rule for absent figures. Whichever store is selected must satisfy the immutable-version and rollout requirements in [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md):

* Immutable versions — a stored policy version must never be mutated in place.
* Observe mode and controlled promotion — the store must support a policy version existing before it is promoted to any workload.
* Workload-scoped rollout — the store must allow different workloads to be bound to different versions simultaneously (supported structurally by the separate Policy binding entity, regardless of which store holds Policy itself).
* Immediate rollback — a prior version must remain retrievable and immediately bindable.
* Compatibility validation — the store (or a layer above it) must expose compatibility metadata per version.

The choice between DynamoDB (single low-latency store, natural fit for point lookups by policy name/version) and versioned S3 (natural fit for immutable, content-addressed version objects with native versioning) is an implementation decision left open by the original charter; either can satisfy the requirements above.

### 2.3 Audit event pipeline: SQS buffering to S3 archive

```text
Decision engine (Guardrails Runtime)
        │
        │  emits one audit event per Evaluation + Decision pair
        ▼
Amazon SQS  ── audit event buffer ──┐
        │                            │  absorbs downstream archive
        │                            │  latency; durable, decouples
        │                            │  evaluation path from archival
        ▼                            │
Audit archive writer  ◄──────────────┘
        │
        ▼
Amazon S3 ── audit archive ── ([`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md))
        │
        ▼
KMS-encrypted at rest; tenant-scoped retention lifecycle applied
(see 3.1 Default behavior and CONSTRAINTS.md CON-009, CON-014)
```

Buffering audit events through SQS before they land in the S3 archive keeps the synchronous evaluation path (which is on the consuming application's request path, per [`RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) "Latency" risk) decoupled from archive-write latency and transient archive-side unavailability. Audit-delivery failure is itself a documented failure mode under [`AGREEMENTS.md` AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) ("Audit delivery fails" is one of the failure conditions a workload's policy must define behavior for), and audit delivery success rate is a named reliability metric ([`SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md)).

### 2.4 Storage in the Customer-Managed Runtime

In Mode C (Customer-Managed Runtime), storage responsibility splits between the customer's AWS account and the hosted control plane, per [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes) (Mode C), [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md):

| Data | Location | Owner |
|---|---|---|
| Policy cache | Customer AWS account, inside the customer-hosted Guardrails Runtime | Consuming team (runtime infrastructure) |
| Local audit integration / local audit storage | Customer AWS account | Consuming team ([AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), Mode C: "The consuming team owns... Local audit storage") |
| Policy source of truth (DynamoDB / versioned S3) | Hosted control plane | AI Guardrails provider; synchronized to the local cache asynchronously |
| Telemetry reaching the hosted control plane | Customer-selectable — health status, product version, policy version, aggregated evaluation counts, reason-code totals, latency metrics, sanitized decision metadata, or none at all | Consuming team selects; provider receives only what is selected |

The customer-managed runtime must execute policies from its local cache without requiring a synchronous connection to the hosted control plane ([`AGREEMENTS.md` AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)); policy synchronization is asynchronous and must not sit on the evaluation path. Raw content must never leave the customer-managed runtime unless explicitly configured (`CONSTRAINTS.md` CON-013).

### 2.5 Encryption and transport

All storage described in this section is subject to the transport and cryptographic constraints in [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md):

* **CON-008** — TLS for all runtime communication, including writes to DynamoDB, S3, and SQS.
* **CON-009** — KMS-backed encryption in transit and at rest, applying to policy storage, the workload/tenant registry, the audit buffer and archive, and any local policy cache in a customer-managed runtime.

Detailed key-management and IAM boundary design is out of scope for this document; see [`SECURITY-ARCHITECTURE.md`](SECURITY-ARCHITECTURE.md).

### 2.6 Per-tenant residency enforcement

Per [`CAPABILITIES.md` CAP-035](../20-product/CAPABILITIES.md) and [`DECISIONS.md` DEC-018](../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga) (Status: Proposed), the platform is multi-region from GA, and a tenant's content is processed and stored only within its pinned processing Region, drawn from its `Allowed Regions` (`Tenant.Allowed Regions`, see [1.2](#12-tenant)) — each tenant is pinned to a single processing region, not active-active across regions. Live cross-region failover/replication is a distinct capability, committed as a later phase but not yet delivered ([`DECISIONS.md` DEC-019](../00-source-of-truth/DECISIONS.md#dec-019--multi-region-active-active-availability-committed-as-a-later-phase) (Status: Proposed) / CAP-036) and is out of scope here.

Residency enforcement applies to every store in [2.1 Storage mapping by entity](#21-storage-mapping-by-entity) that can carry a tenant's content or content-derived data:

* **Evaluation runtime** (transient, in-region) — a Workload's Evaluations execute in the AWS Region matching its owning Tenant's pinned processing Region (drawn from its Allowed Regions); a tenant's evaluation requests do not route to another Region.
* **Decision metadata (audit) — SQS buffer and S3 archive** — the [audit event pipeline](#23-audit-event-pipeline-sqs-buffering-to-s3-archive) and its S3 archive are provisioned per Region, so a tenant's audit trail is buffered and archived only within its pinned processing Region.
* **Workload/tenant registry (DynamoDB) and Policy storage** — read on the evaluation path to resolve a workload's effective policy; a tenant's own request path must not depend on a cross-region call.
* **Customer-Managed Runtime (Mode C)** — residency is inherently satisfied for content that never leaves the customer's own AWS account ([2.4](#24-storage-in-the-customer-managed-runtime)); the Region constraint applies to the customer's own chosen Region, not to the hosted control plane.

This is a constraint on *where* the entities in [Section 1](#1-domain-entity-model) may be processed and stored, expressed through the additive `Tenant.Allowed Regions` attribute — it does not otherwise change the entities' shape. Regionality is expressed through regional resources, not additional AWS accounts; the two-account model (`AGREEMENTS.md` AGR-013) is retained (DEC-018). The specific enforcement mechanism (e.g., IAM condition keys, region-scoped routing, per-region deployment topology) is a deployment-architecture concern; see [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) and [5. Open items and TBDs](#5-open-items-and-tbds).

---

## 3. Data-handling policy

### 3.1 Default behavior (hosted runtime)

Per [original charter §13](../00-source-of-truth/PROVENANCE.md#3-section-by-section-map) ("Data handling requirements — Default behavior"), the hosted runtime (Remote Evaluation and Guarded Gateway modes) must:

1. **Process content only for evaluation.** Prompt and response content is read in order to run evaluators and produce a Decision; it is not used for any other purpose by default.
2. **Avoid persisting raw prompts and responses by default.** No raw-content retention occurs unless a tenant explicitly configures it (`CONSTRAINTS.md` CON-014).
3. **Persist decision metadata.** The Decision entity (see [1.7](#17-decision)) and its associated audit record (see [4. Audit metadata](#4-audit-metadata)) are persisted regardless of whether raw content is retained.
4. **Hash user and conversation identifiers where possible.** Identifiers that would otherwise directly identify an end user or conversation are hashed before being carried into audit or telemetry records.
5. **Remove secrets from telemetry.** Any secret material incidentally present in evaluated content must not appear in telemetry pipelines.
6. **Encrypt data in transit and at rest.** See [2.5 Encryption and transport](#25-encryption-and-transport).
7. **Support tenant-specific retention configuration.** Retention behavior is configured per Tenant (`Tenant.Retention configuration`, see [1.2](#12-tenant)), not fixed platform-wide.
8. **Clearly document which content is transmitted.** Per [`AGREEMENTS.md` AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), every deployment must state where prompts/responses are processed, whether retrieved documents or tool arguments are transmitted, whether content is persisted, which telemetry leaves the runtime, and which AWS accounts/Regions process the request — as a dedicated statement, independent of who operates the component.

Any retention of raw content requires explicit, tenant-level configuration — never a platform default (`AGREEMENTS.md` AGR-014). Data-architecture and security reviews must verify no incidental raw-content persistence occurs in logs, caches, or telemetry pipelines.

Remote Evaluation (Mode A) must never be described as private-data or data-local: prompts, responses, and any additional evaluated content are transmitted to the hosted Evaluation API regardless of who operates the gateway in front of it (`AGREEMENTS.md` AGR-009).

### 3.2 Content categories that may be evaluated

[original charter §13](../00-source-of-truth/PROVENANCE.md#3-section-by-section-map) lists the content categories that may be evaluated, depending on workload configuration:

| Content category | Description |
|---|---|
| User prompts | End-user input submitted to the model |
| System prompts | Instructions supplied to the model by the application |
| Conversation history | Prior turns in a multi-turn conversation |
| Retrieved documents | Content retrieved by a RAG or similar retrieval step |
| Model responses | Output produced by the model |
| Tool arguments | Arguments passed to a tool or function call |
| Tool responses | Results returned by a tool or function call |
| Structured-output payloads | Structured (e.g., JSON) output produced by the model |

### 3.3 MVP content-scope limitation

The MVP initially limits support to **user/model input and model output** — that is, user prompts as `input`-stage content and model responses as `output`-stage content ([original charter §13](../00-source-of-truth/PROVENANCE.md#3-section-by-section-map)). This directly matches the two initial [Evaluation](#16-evaluation) stages (`input`, `output`) defined in [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model). System prompts, conversation history, retrieved documents, tool arguments, tool responses, and structured-output payloads remain content categories the architecture must **permit**, but none are evaluated in the MVP; several correspond to future evaluation stages (`retrieval`, `tool_call`, `tool_result`) explicitly deferred to Phase 5 ([AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery), "Advanced agent protection") and future scope ([`SCOPE.md`](../10-charter/SCOPE.md)).

### 3.4 Customer-managed telemetry

For Customer-Managed Runtime deployments specifically, [original charter §13](../00-source-of-truth/PROVENANCE.md#3-section-by-section-map) ("Customer-managed telemetry") requires that the customer be able to choose what the hosted control plane receives, from a defined set down to nothing:

| Telemetry category | Notes |
|---|---|
| Health status | |
| Product version | |
| Policy version | |
| Aggregated evaluation counts | |
| Reason-code totals | |
| Latency metrics | |
| Sanitized decision metadata | |
| No telemetry | A fully supported configuration, not a degraded one (`AGREEMENTS.md` AGR-016) |

Raw content must never leave a customer-managed runtime unless explicitly configured ([original charter §13](../00-source-of-truth/PROVENANCE.md#3-section-by-section-map); `CONSTRAINTS.md` CON-013). Any telemetry payload design must be reviewed to confirm it cannot carry raw prompt or response content by construction (`AGREEMENTS.md` AGR-016).

---

## 4. Audit metadata

Per [`NON-FUNCTIONAL-REQUIREMENTS.md`](NON-FUNCTIONAL-REQUIREMENTS.md) ("Operational requirements — Auditing"), audit metadata must include the following fields for every evaluation:

| Field | Description |
|---|---|
| Tenant | The owning tenant, per [1.2 Tenant](#12-tenant) |
| Workload | The workload that triggered the evaluation, per [1.3 Workload](#13-workload) |
| Policy version | The specific immutable policy version applied, per [1.4 Policy](#14-policy) |
| Evaluation stage | `input`, `output`, or a future stage, per [1.6 Evaluation](#16-evaluation) |
| Decision | The normalized decision produced, per [1.7 Decision](#17-decision) |
| Reason codes | Normalized codes explaining the decision, in category-namespaced form `GR-0NN:<evaluator-family>:<code>` ([`DECISIONS.md` DEC-013](../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension); [`adrs/ADR-0003`](adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md)) |
| Guardrail category | The `GR-0NN` catalog category the decision's reason code(s) belong to, mirrored from `Decision.Guardrail category` ([1.7](#17-decision)); a first-class audit/metric dimension enabling per-category reporting (e.g., block/redact counts by category) directly from audit records, with no external code-to-category mapping table (DEC-013; ADR-0003) |
| Evaluator versions | Versions of the evaluator engines/adapters invoked |
| Timestamp | When the evaluation occurred |
| Correlation identifier | Ties the audit record back to the originating request across connector, runtime, and any downstream system |
| Whether raw content was retained | Records whether raw prompt/response content was persisted for this evaluation — defaults to "no" per [3.1](#31-default-behavior-hosted-runtime) |

These fields are the durable record written through the [audit event pipeline](#23-audit-event-pipeline-sqs-buffering-to-s3-archive) and are what security and compliance teams rely on as decision evidence and policy-version traceability ([`TARGET-USERS.md`](../20-product/TARGET-USERS.md), "Security and compliance teams").

---

## 5. Open items and TBDs

Per source-of-truth policy, the following figures are not present in the L1 registers and are recorded here as **TBD** rather than invented:

| Item | Status | Related constraint |
|---|---|---|
| Specific retention period(s) per tenant | TBD — tenant-specific retention is required, but no default duration is specified in the original charter | `Tenant.Retention configuration` ([1.2](#12-tenant)) |
| Specific hashing algorithm for user/conversation identifiers | TBD — "hash where possible" is required; the algorithm is not specified | [3.1](#31-default-behavior-hosted-runtime), item 4 |
| Specific input-size limits per evaluation stage | TBD — see `CONSTRAINTS.md` CON-015 | CON-015 |
| Specific timeout values per stage/detector | TBD — see `CONSTRAINTS.md` CON-016 | CON-016 |
| Choice between DynamoDB and versioned S3 for Policy storage | TBD — [`DEPLOYMENT-ARCHITECTURE.md`](DEPLOYMENT-ARCHITECTURE.md) lists both as acceptable without selecting one | [2.2](#22-policy-storage-dynamodb-or-versioned-s3) |
| Specific mechanism enforcing the per-tenant Region boundary (IAM condition keys, region-scoped routing, per-region deployment topology) | TBD — CAP-035/DEC-018 establish the requirement; the enforcement mechanism is a `DEPLOYMENT-ARCHITECTURE.md` concern not yet specified | [2.6](#26-per-tenant-residency-enforcement) |

---

## 6. Traceability

| This document's content | Charter source | AGREEMENTS.md / CONSTRAINTS.md |
|---|---|---|
| Domain entities and attributes | §9 | AGR-017 |
| Storage choices | §11 | — |
| Data-handling default behavior | §13 | AGR-014, CON-014 |
| Content-exposure documentation requirement | §5.4 | AGR-008, CON-007 |
| Remote Evaluation is not data-local | §3 (Mode A) | AGR-009 |
| Customer-managed telemetry and raw-content boundary | §13 | AGR-016, CON-013 |
| Customer-managed runtime independence from control plane | §3 (Mode C) | AGR-015 |
| Audit metadata fields | §15 | — |
| Content categories and phased scope limitation | §13 | — (scope per [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md)) |
| Per-tenant residency enforcement (`Tenant.Allowed Regions`, §2.6) | — (post-charter decision) | AGR-013 (amended); [`DECISIONS.md` DEC-018](../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga); [`CAPABILITIES.md` CAP-035](../20-product/CAPABILITIES.md) |
| `guardrail_category` field and category-namespaced reason codes | — (post-charter decision) | AGR-017; [`DECISIONS.md` DEC-013](../00-source-of-truth/DECISIONS.md#dec-013--reason-code-taxonomy-and-guardrail-category-audit-dimension); [`adrs/ADR-0003`](adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md) |

---

## 7. Related documents

- [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — authoritative agreement register, in particular AGR-008, AGR-009, AGR-014, AGR-015, AGR-016, AGR-017.
- [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — authoritative constraint register, in particular CON-007, CON-009, CON-013, CON-014.
- [`CHARTER.md`](../10-charter/CHARTER.md) — product charter narrative.
- [`CAPABILITIES.md`](../20-product/CAPABILITIES.md) — capability catalog referencing this same domain model; in particular CAP-035 (multi-region availability and per-tenant residency enforcement) and CAP-036 (multi-region active-active, later phase).
- [`DECISIONS.md`](../00-source-of-truth/DECISIONS.md) — decision log; in particular DEC-013 (reason-code taxonomy and `guardrail_category` field) and DEC-018 (multi-region availability and per-tenant residency enforcement).
- [`adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md`](adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md) — design detail and alternatives for the category-namespaced reason-code taxonomy and the `guardrail_category` field.
- `SECURITY-ARCHITECTURE.md` — IAM, KMS key management, and transport security detail (implements CON-008 through CON-012).
- `DEPLOYMENT-ARCHITECTURE.md` — account model and per-mode deployment topology (implements CON-001, CON-002, CON-005).
- `INTEGRATION-ARCHITECTURE.md` — connector and evaluation-contract detail (implements CON-003, CON-004, CON-006).
- `COMPONENT-ARCHITECTURE.md` — runtime component decomposition (Evaluation API, policy resolver, decision engine, evaluator adapters, audit producer).
