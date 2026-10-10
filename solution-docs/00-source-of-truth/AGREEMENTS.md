**AI Guardrails — Agreements**

**Authority level:** L1 (Authoritative)
**Status:** Draft
**Date:** 2026-07-24

These agreements are the authoritative source of truth for the AI Guardrails solution. Every other document in this set — the charter, product materials, executive brief, sales deck, architecture report, and delivery plan — is a derived view produced for a specific audience or purpose. Derived documents must not contradict any agreement recorded here. When a conflict is discovered between a derived document and this register, this register wins, and the derived document must be corrected.

---

## Table of contents

<!-- BEGIN GENERATED: toc -->
- [AGR-001 — Standalone platform, not embedded in the internal LiteLLM](#agr-001--standalone-platform-not-embedded-in-the-internal-litellm)
- [AGR-002 — Three deployment modes](#agr-002--three-deployment-modes)
- [AGR-003 — LiteLLM as first connector, not the product boundary](#agr-003--litellm-as-first-connector-not-the-product-boundary)
- [AGR-004 — Bedrock Guardrails as first engine, vendor-independent contract](#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)
- [AGR-005 — Policy portability](#agr-005--policy-portability)
- [AGR-006 — One logical evaluation contract across all modes](#agr-006--one-logical-evaluation-contract-across-all-modes)
- [AGR-007 — Policies are separate from connectors](#agr-007--policies-are-separate-from-connectors)
- [AGR-008 — Explicit content-exposure model; ownership vs. exposure are separate concerns](#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns)
- [AGR-009 — Remote Evaluation is not a private-data or data-local model](#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model)
- [AGR-010 — Guardrails are not authorization](#agr-010--guardrails-are-not-authorization)
- [AGR-011 — Observe-before-enforce progression](#agr-011--observe-before-enforce-progression)
- [AGR-012 — Failure behavior is part of policy](#agr-012--failure-behavior-is-part-of-policy)
- [AGR-013 — AWS as primary cloud; minimal MVP account model](#agr-013--aws-as-primary-cloud-minimal-mvp-account-model)
- [AGR-014 — No raw prompt/response retention by default](#agr-014--no-raw-promptresponse-retention-by-default)
- [AGR-015 — Customer-managed runtime executes without a synchronous control-plane connection](#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)
- [AGR-016 — Customer-controlled telemetry for the customer-managed runtime](#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime)
- [AGR-017 — Core domain model](#agr-017--core-domain-model)
- [AGR-018 — Trust and enforcement model](#agr-018--trust-and-enforcement-model)
- [AGR-019 — Meaning of "import"](#agr-019--meaning-of-import)
- [AGR-020 — Five-phase delivery](#agr-020--five-phase-delivery)
- [AGR-021 — Guardrail-category taxonomy and extension model](#agr-021--guardrail-category-taxonomy-and-extension-model)
- [AGR-022 — Current release is a local technical proof; AWS integration is deferred](#agr-022--current-release-is-a-local-technical-proof-aws-integration-is-deferred)
- [AGR-023 — Internal application teams first; service adoption by other teams later](#agr-023--internal-application-teams-first-service-adoption-by-other-teams-later)
- [AGR-024 — API and CLI are the release-day experience](#agr-024--api-and-cli-are-the-release-day-experience)
- [AGR-025 — Two real local LiteLLM instances form the proof topology](#agr-025--two-real-local-litellm-instances-form-the-proof-topology)
- [AGR-026 — Offline-capable proof with a mock model by default](#agr-026--offline-capable-proof-with-a-mock-model-by-default)
- [AGR-027 — Sensitive-data detection and redaction is the required local guardrail scenario](#agr-027--sensitive-data-detection-and-redaction-is-the-required-local-guardrail-scenario)
- [AGR-028 — The local API and CLI cover the full policy lifecycle](#agr-028--the-local-api-and-cli-cover-the-full-policy-lifecycle)
- [AGR-029 — One configurable published policy version per local workload](#agr-029--one-configurable-published-policy-version-per-local-workload)
- [AGR-030 — Repeatable automated demonstration is the local acceptance evidence](#agr-030--repeatable-automated-demonstration-is-the-local-acceptance-evidence)
- [AGR-031 — Select all nine evolution workstreams](#agr-031--select-all-nine-evolution-workstreams)
- [AGR-032 — Keep two team-owned gateways as shared-service consumers](#agr-032--keep-two-team-owned-gateways-as-shared-service-consumers)
- [AGR-033 — Keep evolution delivery local and assess Minikube](#agr-033--keep-evolution-delivery-local-and-assess-minikube)
- [AGR-034 — Prioritize immutable policy approval and audited activation](#agr-034--prioritize-immutable-policy-approval-and-audited-activation)
- [AGR-035 — Accept evolution through automated evidence](#agr-035--accept-evolution-through-automated-evidence)
- [AGR-036 — Allow shared local processing and constrain four allow-deny categories](#agr-036--allow-shared-local-processing-and-constrain-four-allow-deny-categories)
<!-- END GENERATED: toc -->
---

## AGR-001 — Standalone platform, not embedded in the internal LiteLLM

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, technical lead
**Agreement:** AI Guardrails will be built and delivered as a standalone policy and enforcement platform, not as functionality embedded inside the existing internal LiteLLM instance.
**Rationale:** Embedding guardrails inside a single internal LiteLLM deployment would couple policy enforcement to one team's gateway lifecycle and prevent reuse across teams with different gateways, providers, or deployment models.
**Implications:**
- The guardrails runtime must be deployable, versioned, and operated independently of any single LiteLLM instance.
- LiteLLM becomes one of potentially several connectors rather than the platform's foundation.
- The product's release cadence, ownership, and roadmap are decoupled from the internal LiteLLM team's roadmap.
**Affected documents:**
- `10-charter/CHARTER.md`
- `20-product/PRODUCT-OVERVIEW.md`
- `50-architecture/SYSTEM-CONTEXT.md`
- `50-architecture/adrs/ADR-0001-example-decision.md`

---

## AGR-002 — Three deployment modes

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, technical lead, architecture lead
**Agreement:** The product will support exactly three primary deployment modes at general availability: Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime.
**Rationale:** Different consuming teams have different operational-ownership, data-residency, and gateway-ownership requirements that a single deployment topology cannot satisfy.
**Implications:**
- Every architectural component must be evaluated against all three modes before being accepted.
- Documentation, onboarding, and support processes must account for three distinct operating models rather than one.
- Feature-parity gaps between modes must be explicitly tracked and justified.
**Affected documents:**
- `10-charter/CHARTER.md`
- `20-product/CAPABILITIES.md`
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md`
- `50-architecture/ARCHITECTURE-REPORT.md`

---

## AGR-003 — LiteLLM as first connector, not the product boundary

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, integration lead
**Agreement:** LiteLLM will be implemented as the first supported gateway connector and, optionally, as the substrate for the Guarded Gateway mode, but it will not define the boundary of the AI Guardrails product.
**Rationale:** Treating LiteLLM as the boundary would prevent onboarding teams that use other gateways or invoke the Evaluation API directly, contradicting the goal of a model- and framework-independent platform.
**Implications:**
- The evaluation contract and policy model must be defined independently of LiteLLM's internal request/response structures.
- Additional connectors (generic SDK, other gateways) must be addable without changing policy semantics.
- The LiteLLM connector owns request mapping and enforcement invocation only, not policy definition or detection logic.
**Affected documents:**
- `50-architecture/INTEGRATION-ARCHITECTURE.md`
- `20-product/CAPABILITIES.md`
- `60-delivery/IMPLEMENTATION-STRATEGY.md`

---

## AGR-004 — Bedrock Guardrails as first engine, vendor-independent contract

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, architecture lead
**Agreement:** Amazon Bedrock Guardrails will be adopted as the first managed evaluation engine, while the public evaluation contract exposed by the platform remains vendor-independent.
**Rationale:** Bedrock Guardrails accelerates delivery of a working managed evaluator, but permanently coupling the product's contract to a single vendor's response format would undermine policy portability and long-term flexibility.
**Implications:**
- Bedrock-specific response formats must be normalized into the platform's own decision structure before reaching policies or connectors.
- Additional evaluation engines (deterministic rules, PII evaluators, future third-party engines) must be addable behind the same adapter pattern.
- Marketing and technical documentation must not describe Bedrock Guardrails as the product's permanent or exclusive detection engine.
**Affected documents:**
- `50-architecture/INTEGRATION-ARCHITECTURE.md`
- `50-architecture/COMPONENT-ARCHITECTURE.md`
- `70-governance/CLAIMS-REGISTER.md`

---

## AGR-005 — Policy portability

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, security and governance lead
**Agreement:** Guardrail policies must not depend directly on LiteLLM configuration structures, Bedrock Guardrails response formats, a specific model provider, a specific programming language, or a specific application framework.
**Rationale:** Policy portability is the mechanism that allows a single organizational policy to be reused across heterogeneous workloads, gateways, and deployment modes.
**Implications:**
- Vendor- and framework-specific logic must be isolated behind adapters and connectors.
- Policy authors define detection and enforcement intent, not integration mechanics.
- Any proposed policy feature that can only be expressed in vendor-specific terms must be rejected or redesigned.
**Affected documents:**
- `10-charter/PRINCIPLES.md`
- `50-architecture/COMPONENT-ARCHITECTURE.md`
- `70-governance/CLAIMS-REGISTER.md`

---

## AGR-006 — One logical evaluation contract across all modes

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, architecture lead
**Agreement:** Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime deployments must all implement the same logical evaluation contract, including policy definitions, request structures, decision structures, reason codes, enforcement actions, audit metadata, and policy-version semantics.
**Rationale:** A single reusable policy can only be applied consistently across deployment modes if the contract governing evaluation requests and decisions does not vary by where the runtime is deployed.
**Implications:**
- Contract changes must be versioned and evaluated for backward compatibility across all three modes simultaneously.
- Mode-specific optimizations (for example, caching or telemetry) must not alter the externally observable contract.
- Conformance tests must validate the contract identically for hosted and customer-managed runtimes.
**Affected documents:**
- `50-architecture/INTEGRATION-ARCHITECTURE.md`
- `50-architecture/ARCHITECTURE-REPORT.md`

---

## AGR-007 — Policies are separate from connectors

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, policy owner
**Agreement:** A policy defines what must be evaluated and how violations are handled; a connector defines how a specific gateway or application invokes and enforces that policy. This separation is mandatory and must be preserved in every implementation.
**Rationale:** Conflating policy intent with integration mechanics would recreate the coupling the platform is designed to remove and would block reuse of a policy across multiple connectors.
**Implications:**
- Connectors must not embed policy logic, thresholds, or detection rules.
- Policies must be expressible and testable without reference to any specific connector.
- New connectors can be added by implementing the invocation contract only, without modifying existing policies.
**Affected documents:**
- `10-charter/PRINCIPLES.md`
- `50-architecture/COMPONENT-ARCHITECTURE.md`
- `50-architecture/INTEGRATION-ARCHITECTURE.md`

---

## AGR-008 — Explicit content-exposure model; ownership vs. exposure are separate concerns

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Security and governance lead, architecture lead
**Agreement:** Every deployment must explicitly document where prompts and responses are processed, whether retrieved documents or tool arguments are transmitted, whether content is persisted, which telemetry leaves the runtime, and which AWS accounts and Regions process the request; operational ownership and data exposure must be communicated as separate concerns.
**Rationale:** Stakeholders frequently conflate "who operates this component" with "where does my data go," and the two questions require independently correct answers for security and compliance sign-off.
**Implications:**
- Each deployment mode's documentation must include a dedicated content-exposure statement, not an inferred one.
- Sales and executive materials must not imply reduced data exposure from reduced operational ownership, or vice versa.
- Security review of a new mode or connector must verify both dimensions independently.
**Affected documents:**
- `50-architecture/SECURITY-ARCHITECTURE.md`
- `50-architecture/DATA-ARCHITECTURE.md`
- `70-governance/CLAIMS-REGISTER.md`

---

## AGR-009 — Remote Evaluation is not a private-data or data-local model

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, security and governance lead
**Agreement:** Mode A (Remote Evaluation) must be described only as "bring your own gateway while consuming a hosted guardrail runtime"; it must never be described as a private-data or data-local deployment model, since prompts, responses, and any additional evaluated content are transmitted to the hosted Evaluation API.
**Rationale:** Misrepresenting Remote Evaluation as data-local would create false compliance assurances for regulated or data-residency-sensitive workloads that actually require Customer-Managed Runtime.
**Implications:**
- All product, sales, and executive collateral describing Mode A must state that tenant content reaches the hosted service.
- Customers with data-residency requirements must be routed toward Customer-Managed Runtime, not Remote Evaluation.
- The claims register must flag and reject any wording implying Mode A keeps content local.
**Affected documents:**
- `20-product/PRODUCT-OVERVIEW.md`
- `40-sales-deck/DECK-BRIEF.md`
- `70-governance/CLAIMS-REGISTER.md`
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md`

---

## AGR-010 — Guardrails are not authorization

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Security and governance lead, product lead
**Agreement:** Content evaluation performed by the platform is distinct from business authorization; a guardrail decision that content or a tool call appears safe does not determine whether the current user is permitted to execute that action against a given resource.
**Rationale:** Conflating safety evaluation with authorization would create a false sense of security and leave consuming applications unaware that they remain responsible for authentication, authorization, tenant isolation, and approval workflows.
**Implications:**
- The consuming application retains full responsibility for authentication, business authorization, tenant isolation, resource ownership, transaction limits, and approval workflows.
- Tool and agent authorization is excluded from the guardrail MVP and treated as a distinct, later product capability.
- Documentation and sales materials must not describe guardrail decisions as authorization decisions.
**Affected documents:**
- `10-charter/SCOPE.md`
- `20-product/CAPABILITIES.md`
- `70-governance/CLAIMS-REGISTER.md`
- `70-governance/RISK-REGISTER.md`

---

## AGR-011 — Observe-before-enforce progression

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, security and governance lead
**Agreement:** Every policy must support a non-blocking evaluation mode and a defined progression from Disabled to Observe to Warn to Redact to Block, allowing a team to measure false positives, latency, and business impact before enabling blocking behavior.
**Rationale:** Enforcing an unproven policy immediately risks breaking production traffic and eroding trust in the platform; a graduated rollout path lets teams validate detection quality before it can affect users.
**Implications:**
- The policy schema and runtime must support all five states per policy, per workload.
- Onboarding workflows must default new bindings to Observe rather than Block.
- Promotion between states must be an explicit, auditable action, not an implicit side effect of another change.
**Affected documents:**
- `10-charter/PRINCIPLES.md`
- `20-product/CAPABILITIES.md`
- `60-delivery/RELEASE-CRITERIA.md`
- `50-architecture/OPERABILITY.md`

---

## AGR-012 — Failure behavior is part of policy

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, security and governance lead
**Agreement:** Runtime failure behavior must be explicit and defined per workload, covering Evaluation API timeout, detector unavailability, unresolved policy, control-plane unavailability, audit-delivery failure, and inability to safely transform a response; there must be no undocumented fallback behavior.
**Rationale:** Silent or implicit failure handling can either fail open in a way that defeats the guardrail's purpose or fail closed in a way that breaks production traffic without anyone having agreed to that trade-off.
**Implications:**
- The policy schema must include a mandatory failure-behavior section covering each defined failure mode.
- Runtime implementations must reject or flag policies that omit failure behavior rather than defaulting silently.
- Audit records must capture which failure-behavior path was taken when evaluation could not complete normally.
**Affected documents:**
- `50-architecture/COMPONENT-ARCHITECTURE.md`
- `50-architecture/OPERABILITY.md`
- `70-governance/RISK-REGISTER.md`

---

## AGR-013 — AWS as primary cloud; minimal MVP account model

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, platform operations lead
**Agreement:** Amazon Web Services is the primary and initial cloud platform, and the MVP will use a minimal account model of one non-production account and one production account, with additional account separation introduced only when justified by external customers, compliance requirements, independent security ownership, immutable audit requirements, production scale, or dedicated tenant deployments.
**Rationale:** A minimal account topology reduces MVP delivery complexity while still preserving explicit IAM, storage, and environment boundaries; premature account proliferation would slow delivery without validated justification.
**Implications:**
- MVP infrastructure-as-code must not assume a three-or-more-account structure.
- Any request for additional account separation must be justified against the listed triggers before being implemented.
- Cross-cloud deployment is excluded from the MVP.
- Multi-region availability is offered from GA with tenant-pinned single-region processing (DEC-018); this does not by itself trigger additional AWS accounts — the two-account model is retained until an OQ-009 trigger fires. *(Proposed amendment — register-level; owner ratification required.)*
**Affected documents:**
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md`
- `60-delivery/IMPLEMENTATION-STRATEGY.md`
- `60-delivery/RELEASE-CRITERIA.md`

---

## AGR-014 — No raw prompt/response retention by default

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Security and governance lead, technical lead
**Agreement:** The hosted runtime will process content only for evaluation and will not persist raw prompts and responses by default; it will persist decision metadata, hash user and conversation identifiers where possible, remove secrets from telemetry, and support tenant-specific retention configuration.
**Rationale:** Minimizing default retention of sensitive tenant content reduces breach impact, simplifies compliance posture, and aligns with the principle that operational ownership and data exposure are separate, deliberately managed concerns.
**Implications:**
- Any retention of raw content requires explicit tenant-level configuration, not a platform default.
- Audit metadata must record whether raw content was retained for a given evaluation.
- Data-architecture and security reviews must verify no incidental raw-content persistence occurs in logs, caches, or telemetry pipelines.
**Affected documents:**
- `50-architecture/DATA-ARCHITECTURE.md`
- `50-architecture/SECURITY-ARCHITECTURE.md`
- `70-governance/CLAIMS-REGISTER.md`

---

## AGR-015 — Customer-managed runtime executes without a synchronous control-plane connection

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, architecture lead
**Agreement:** The customer-managed runtime must be capable of executing policies without requiring a synchronous connection to the hosted control plane.
**Rationale:** Regulated, air-gapped, or high-availability workloads that adopt Customer-Managed Runtime cannot accept the hosted control plane as a runtime dependency for every evaluation; policy execution must survive control-plane unavailability.
**Implications:**
- The runtime must maintain a local policy cache sufficient to keep evaluating during control-plane outages.
- Policy synchronization must be asynchronous and must not block the evaluation path.
- Degraded-mode behavior when the local cache is stale must be defined as part of failure behavior (AGR-012).
**Affected documents:**
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md`
- `50-architecture/COMPONENT-ARCHITECTURE.md`
- `60-delivery/IMPLEMENTATION-STRATEGY.md`

---

## AGR-016 — Customer-controlled telemetry for the customer-managed runtime

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Security and governance lead, product lead
**Agreement:** Customer-managed runtimes must allow the customer to choose what the hosted control plane receives, ranging from health status, product version, policy version, aggregated evaluation counts, reason-code totals, latency metrics, and sanitized decision metadata down to no telemetry at all; raw content must never leave a customer-managed runtime unless explicitly configured.
**Rationale:** Customers adopting Customer-Managed Runtime specifically to keep content within their own account require assurance that telemetry does not become an unacknowledged data-exfiltration path.
**Implications:**
- Telemetry configuration must be a first-class, documented setting of the customer-managed runtime, not an implicit default.
- The platform must ship a "no telemetry" operating configuration that remains fully supported.
- Any telemetry payload design must be reviewed to confirm it cannot carry raw prompt or response content by construction.
**Affected documents:**
- `50-architecture/DATA-ARCHITECTURE.md`
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md`
- `70-governance/CLAIMS-REGISTER.md`

---

## AGR-017 — Core domain model

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Technical lead, product lead
**Agreement:** The platform's domain model consists of six core entities — tenant, workload, policy, policy binding, evaluation, and decision — with the per-entity attributes enumerated in [`70-governance/GLOSSARY.md`](../70-governance/GLOSSARY.md) and made testable as requirements in [`50-architecture/REQUIREMENTS.md`](../50-architecture/REQUIREMENTS.md) (`FR-001`, `FR-007`, `FR-008`, `FR-012`–`FR-016`), and all architecture and implementation must be expressed in terms of this model.
**Rationale:** A shared, stable domain model is what allows policies, connectors, and deployment modes to interoperate; without it, each component would invent its own vocabulary and the one-contract-across-modes agreement (AGR-006) could not hold.
**Implications:**
- Data storage, APIs, and audit records must map directly onto tenant, workload, policy, binding, evaluation, and decision concepts.
- New capabilities (for example, future evaluation stages or decision actions) must extend this model rather than introduce parallel concepts.
- Documentation across all layers must use these terms consistently.
**Affected documents:**
- `50-architecture/DATA-ARCHITECTURE.md`
- `50-architecture/COMPONENT-ARCHITECTURE.md`
- `50-architecture/REQUIREMENTS.md`
- `70-governance/GLOSSARY.md`

---

## AGR-018 — Trust and enforcement model

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Security and governance lead, technical lead
**Agreement:** The product must explicitly distinguish three enforcement postures — cooperative integration, platform-managed enforcement, and network/IAM-enforced gateway — and must not represent a cooperative connector as a hard security boundary.
**Rationale:** Different teams and compliance requirements call for different bypass-resistance levels; presenting a voluntary connector as an enforced control would misstate the platform's actual security guarantees.
**Implications:**
- Each workload's enforcement posture must be recorded and visible, not assumed.
- Sales and executive materials must not claim bypass-proof enforcement for cooperative-mode deployments.
- Network/IAM-enforced gateway controls (no direct provider credentials, restricted egress) are treated as an advanced, optional posture, not an MVP requirement.
**Affected documents:**
- `50-architecture/SECURITY-ARCHITECTURE.md`
- `70-governance/RISK-REGISTER.md`
- `70-governance/CLAIMS-REGISTER.md`
- `10-charter/CHARTER.md`

---


## AGR-019 — Meaning of "import"

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, technical lead
**Agreement:** "Import" means a consuming team adopting a versioned guardrail policy, a workload identity, an integration manifest, a connector configuration, and an enforcement mode; it does not mean importing, cloning, registering, or remotely controlling another team's LiteLLM instance, and the import process must not require copying another team's LiteLLM database, transferring model-provider credentials, exposing gateway administration endpoints, replacing model-routing configuration, or granting the guardrail provider administrative access to the team's gateway.
**Rationale:** Without an explicit definition, "import" could be misread as centralized control over another team's gateway, which would contradict the cooperative-integration trust model and create unwanted operational and security coupling.
**Implications:**
- Onboarding UX and documentation must use "import" only in the adopt-a-policy sense defined here.
- The connector installation process must be designed so it never needs another team's gateway credentials or administrative access.
- The onboarding flow (register workload, select policy, generate integration manifest, install connector, observe, review, enforce) is the canonical reference implementation of "import."
**Affected documents:**
- `10-charter/CHARTER.md`
- `20-product/USE-CASES.md`
- `70-governance/GLOSSARY.md`
- `20-product/FAQ.md`

---

## AGR-020 — Five-phase delivery

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, technical lead, delivery lead
**Agreement:** Delivery will proceed through five phases — Phase 1 Integration proof, Phase 2 Internal multi-team platform, Phase 3 Guarded gateway, Phase 4 Customer-managed runtime, and Phase 5 Advanced agent protection — with Phase 5 treated as a separate product expansion rather than an MVP prerequisite.
**Rationale:** Sequencing delivery from a narrow integration proof toward progressively larger operating models lets the platform validate policy reuse and connector independence before taking on the greater operational and security scope of gateway hosting and customer-managed deployment.
**Implications:**
- Each phase's deliverables must be completed and validated before the next phase's scope is committed.
- Phase 5 capabilities must not be scheduled or resourced as part of MVP delivery.
- Milestones and release criteria must be organized around these five phases rather than an undifferentiated backlog.
**Affected documents:**
- `60-delivery/IMPLEMENTATION-STRATEGY.md`
- `60-delivery/MILESTONES.md`
- `20-product/PRODUCT-ROADMAP.md`
- `30-executive/EXPECTED-OUTCOMES.md`

---

## AGR-021 — Guardrail-category taxonomy and extension model

**Status:** Accepted
**Date:** 2026-07-24
**Owners:** Product lead, technical lead, security and governance lead
**Agreement:** The product adopts an eighteen-category guardrail taxonomy (GR-001 through GR-018, cataloged in `20-product/GUARDRAIL-CATALOG.md`) as its sanctioned classification of guardrail controls and its directional growth vision, together with a defined extension model (documented in `50-architecture/EXTENSIBILITY-ARCHITECTURE.md`) consisting of four extension seams — new evaluation stages, new evaluation engines, new decision actions, and new connectors. Catalog entries are classification, not commitment: an entry is not scheduled, resourced, or estimated unless it is separately committed through the existing scope registers, and every extension must extend — never fork — the core domain model (AGR-017) and the one logical evaluation contract (AGR-006).
**Rationale:** Without a named taxonomy, guardrail growth would occur ad hoc — new checks, stages, and evaluators added without a shared classification, eroding policy portability and inviting silent scope expansion. A change-controlled category taxonomy paired with explicit extension seams lets the product grow incrementally while keeping the contract stable and the scope registers authoritative.
**Implications:**
- Category identifiers (GR-001 through GR-018) are change-controlled: adding, removing, renaming, or reclassifying a category is a register-level change, not an editorial one.
- Item-level guardrails listed inside a category are illustrative and may be edited without register change, provided they do not contradict this register or `CONSTRAINTS.md`.
- Catalog entries whose subject matter falls under a standing exclusion ([`SCOPE.md`](../10-charter/SCOPE.md)) or under AGR-010 must be classified out-of-boundary with a named external owner and must never be presented as future deliverables.
- This taxonomy does not alter the defined-and-estimated full feature set recorded by DEC-008; graduation of a vision-classified entry into committed scope requires the scope-change process (`10-charter/SCOPE.md` §8) and a recorded decision.
**Affected documents:**
- `20-product/GUARDRAIL-CATALOG.md`
- `50-architecture/EXTENSIBILITY-ARCHITECTURE.md`
- `50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md`
- `70-governance/GLOSSARY.md`
- `70-governance/CLAIMS-REGISTER.md`

---

## AGR-022 — Current release is a local technical proof; AWS integration is deferred

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead
**Agreement:** The release currently being defined is a local technical proof. AWS integration is deferred to a subsequent delivery milestone and is not a condition for accepting this local release.
**User statement:** "delivery outcome is local technical prood. Integration with AWS is deferred"
**Rationale:** The release interview explicitly selects a local outcome so the current release and later cloud delivery have separate acceptance boundaries.
**Alternatives rejected for this release:** Hosted pilot, general availability, and completion of the full five-phase programme as the current release outcome.
**Implications:**
- Live Bedrock integration, AWS deployment, and AWS-specific security evidence belong to later delivery; local substitutes do not prove those controls or real Bedrock compatibility.
- AGR-002, AGR-004, AGR-013 and AGR-020 continue to govern the eventual product. Accepting the local proof does not complete the existing hosted Phase 1–2 gates, the Customer-Managed Runtime mode, or GA.
- Local operation does not by itself decide offline operation, model-provider access, gateway topology, detector coverage, or which dependencies may be simulated. These questions were subsequently resolved for the local proof by AGR-025 through AGR-027 and DEC-023.
- Contract independence, workload identity boundaries, declared failure behavior and retention protections still apply to the local implementation.
**Affected documents:**
- `00-source-of-truth/CONSTRAINTS.md`
- `10-charter/CHARTER.md`
- `10-charter/SCOPE.md`
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/MILESTONES.md`
- `60-delivery/RELEASE-CRITERIA.md`

---

## AGR-023 — Internal application teams first; service adoption by other teams later

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead
**Agreement:** The local technical proof serves internal application teams only. Exposing the solution as a service to other teams is a subsequent milestone.
**User statement:** "Use by internal application teams only for now, then this should be exposed as service to other teams"
**Rationale:** Initial consumers and later service adoption must be distinguished when defining onboarding, operational responsibility and acceptance evidence.
**Alternatives rejected for this release:** A release offered to external customers or a general service launch as its acceptance boundary.
**Implications:**
- Current-release workflows and evidence are designed for internal application teams.
- The later service direction preserves the standalone-platform commitment in AGR-001 and the developer-service direction in DEC-010.
- "Other teams" does not establish a commitment to external customers, commercial availability, a launch date, or a service-level agreement.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/MILESTONES.md`
- `20-product/TARGET-USERS.md`

---

## AGR-024 — API and CLI are the release-day experience

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead
**Agreement:** The local technical proof is delivered through an API and a command-line interface. A browser interface or self-service portal is not required for this release.
**User statement:** "on release day experience should be API and CLI"
**Rationale:** The release interview selects the interaction surfaces explicitly, allowing the local workflow to be defined without an assumed web application.
**Alternatives rejected for this release:** A basic browser interface or a self-service portal as a release-day requirement.
**Implications:**
- Local setup, onboarding and policy-operation documentation must describe the API and CLI workflows once their operation set is closed.
- This choice does not itself settle endpoint schemas, command names, API/CLI parity, or the complete operation set.
- Later service or interface decisions do not expand this local release implicitly.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/RELEASE-CRITERIA.md`
- `20-product/SERVING-MODEL.md`

---

## AGR-025 — Two real local LiteLLM instances form the proof topology

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead
**Agreement:** The local proof's acceptance tests must exercise two independently configured local LiteLLM instances.
**User statement:** "Two independently configured local LiteLLM instances (Recommended)"
**Rationale:** This proves connector operation and policy reuse through two real gateway configurations.
**Alternatives rejected:** One gateway or a simulated gateway as the complete acceptance topology.
**Implications:** The mock model selected by AGR-026 does not replace either gateway. Whether two actual application teams must independently execute the proof is a separate acceptance-evidence decision.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/VALIDATION-PLAN.md`

---

## AGR-026 — Offline-capable proof with a mock model by default

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead
**Agreement:** After its dependencies are installed, the local proof must run without internet access, using a mock model by default.
**User statement:** "Offline-capable, using a mock model by default (Recommended)"
**Rationale:** The default acceptance run must be repeatable without live inference services or provider credentials.
**Alternatives rejected:** Required live non-AWS model-provider calls or a required real local model as the default acceptance dependency.
**Implications:** Local communication among gateways, runtime and mock model is permitted. Dependency acquisition may require internet access. The mock demonstrates request/response integration and enforcement; it does not establish model quality or live model-provider compatibility. Detector dependencies needed at runtime must also be installed before the offline run.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/DEPENDENCIES.md`
- `60-delivery/VALIDATION-PLAN.md`

---

## AGR-027 — Sensitive-data detection and redaction is the required local guardrail scenario

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead, security and governance lead
**Agreement:** Sensitive-data detection and redaction is the required working guardrail scenario for the local proof, evaluated on input prompts and model responses. Detector behavior must run locally; a Bedrock stub does not satisfy this requirement.
**User statement:** "Use the most common one"
**Decision basis:** The user delegated the initial guardrail selection. The assistant selected sensitive-data/PII detection and redaction as the concrete baseline and communicated it during the interview. This is a release-scenario selection, not a measured popularity ranking or a claim of complete PII coverage.
**Rationale:** A sensitive-data example gives the local proof an observable detection and transformation result on both sides of the model call. Email detection and masking is an illustrative case. Sensitive-information filtering is an established guardrail category; see [the official Bedrock documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html). This reference does not add an AWS runtime dependency.
**Alternatives rejected for the proof scenario:** A managed-engine stub standing in for working detection, or an implicit promise to demonstrate every managed-engine category.
**Implications:** The broader guardrail catalog and future programme commitments remain intact. Existing rule and JSON validation implementations need not be removed, but their presence does not expand the accepted local scenario. Exact supported entities and limitations must be documented alongside the acceptance examples; the scenario does not imply arbitrary sensitive-data detection.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/VALIDATION-PLAN.md`

---

## AGR-028 — The local API and CLI cover the full policy lifecycle

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead
**Agreement:** The local API and CLI support workload registration, policy publication and binding, result inspection, enforcement promotion, and rollback. Teams must be able to complete this lifecycle without editing internal deployment state by hand.
**User statement:** "Full lifecycle: register workloads, publish and bind policies, inspect results, promote enforcement, and roll back (Recommended)"
**Rationale:** Internal application teams need a complete operational workflow to evaluate the service concept, including changes after initial onboarding.
**Alternatives rejected:** Evaluation-only interfaces with manually edited workload and policy deployment state.
**Implications:** Both interfaces cover the lifecycle, use the same validation and governance rules, and report the effective policy version and enforcement state. Policy authors may submit policy documents; that is distinct from editing the service's internal bindings or registry files. Exact commands and endpoint schemas remain implementation design.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/RELEASE-CRITERIA.md`
- `20-product/SERVING-MODEL.md`

---

## AGR-029 — One configurable published policy version per local workload

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead, security and governance lead
**Agreement:** Each local-proof workload binds to one configurable, immutable published policy version containing its checks. The release does not offer user selection of multiple policy packs for composition or request-time policy-set evaluation.
**User statement:** "Bind one configurable, versioned policy containing its checks (Recommended)"
**Rationale:** A single addressable policy version makes reuse, audit attribution, promotion and rollback explicit.
**Alternatives rejected for this release:** Selecting several policy packs and composing them into an effective policy.
**Implications:** Configuration changes create a new policy version. A policy may contain multiple checks within its declared capabilities. DEC-011's mandatory-policy minimums continue to apply: choosing one effective published policy version does not authorize weakening required controls. Future multi-policy composition requires a separate scope decision.
**Resolves:** OQ-021 for the current local release; no permanent exclusion of a future composition feature is inferred.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `50-architecture/DATA-ARCHITECTURE.md`
- `20-product/SERVING-MODEL.md`

---

## AGR-030 — Repeatable automated demonstration is the local acceptance evidence

**Status:** Accepted
**Date:** 2026-10-08
**Owners:** Product lead, technical lead
**Agreement:** A repeatable automated demonstration using the two real local gateways is sufficient acceptance evidence for the local proof. Internal application teams must be able to run it; acceptance does not require two named teams to independently adopt or sign off the release.
**User statement:** "A repeatable automated demonstration with two gateways that internal teams can run (Recommended)"
**Rationale:** The local milestone proves the complete technical workflow. Organizational adoption and later hosted readiness have separate evidence requirements.
**Alternatives rejected for this release:** Requiring two actual application teams to execute and approve the proof before it can be accepted.
**Implications:** The harness runs the accepted API/CLI lifecycle and reports each acceptance condition independently, failing when any required condition fails. Synthetic fixtures verify known outcomes, including clean content, detection, redaction, block behavior and configured failure paths. Results do not establish production SLAs, population-level detection accuracy or real model-provider compatibility.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- `60-delivery/VALIDATION-PLAN.md`
- `10-charter/SUCCESS-CRITERIA.md`

---

## AGR-031 — Select all nine evolution workstreams

**Status:** Accepted
**Date:** 2026-10-09
**Owners:** Product lead, technical lead
**Agreement:** The next local evolution plan includes real application integration; stronger guardrail coverage; policy governance including composition; a shared internal service; operational readiness; customer-managed runtime; administration UI; streaming, tool, retrieval and multimodal evaluation boundaries; and a provider-operated gateway option.
**User statement:** "1. All options in the table"
**Rationale:** Preserve the explicit follow-on planning interview as scope authority for implementation.
**Implications:** This is a new local follow-on scope, not an expansion of what the completed proof claimed. It authorizes a locally gated implementation sequence for the selected workstreams while preserving separate hosted programme acceptance requirements. Composition is now selected for this follow-on; AGR-029 remains the historical proof boundary. Gateway/application responsibilities and the charter exclusions remain intact.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- [Guardrails evolution architecture](../../plans/guardrails-evolution/architecture.md)

---

## AGR-032 — Keep two team-owned gateways as shared-service consumers

**Status:** Accepted
**Date:** 2026-10-09
**Owners:** Product lead, technical lead
**Agreement:** Two teams each retain their own independently operated LiteLLM instance and consume guardrails as a service.
**User statement:** "Two teams has their own instance of litellm, they should comsume guardrails as service"
**Rationale:** Preserve the explicit follow-on planning interview as scope authority for implementation.
**Implications:** Do not import, replace or require administration of either team gateway. The selected provider-operated gateway is an additional option for other consumers, not a mandatory migration for these teams.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- [Guardrails evolution architecture](../../plans/guardrails-evolution/architecture.md)

---

## AGR-033 — Keep evolution delivery local and assess Minikube

**Status:** Accepted
**Date:** 2026-10-09
**Owners:** Product lead, technical lead
**Agreement:** The next phases target a local environment; use Minikube if suitable for that environment.
**User statement:** "right local, if minikube suitable, then do that"
**Rationale:** Preserve the explicit follow-on planning interview as scope authority for implementation.
**Implications:** Local deployment and automated evidence remain independent of AWS hosting. The implementation plan records a positive preliminary Minikube assessment, explicit resource/network requirements and a real deployment gate. This does not certify production capacity or physical data residency.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- [Guardrails evolution architecture](../../plans/guardrails-evolution/architecture.md)

---

## AGR-034 — Prioritize immutable policy approval and audited activation

**Status:** Accepted
**Date:** 2026-10-09
**Owners:** Product lead, technical lead
**Agreement:** Policy versioning and approval are the first priorities. An author creates an immutable candidate; a different authorized principal approves its exact content; an authorized operator activates it. Changes require a new version and approval; activation and rollback are audited.
**User statement:** "policy versioning and approval; follow-up workflow: correct"
**Rationale:** Preserve the explicit follow-on planning interview as scope authority for implementation.
**Implications:** Approval covers resolved executable policy dependencies, not a mutable name. Separation of author and approver is required. Operator is a separate permission; the interview did not require a third distinct person. Rollback cannot bypass current mandatory controls or restore revoked approval.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- [Guardrails evolution architecture](../../plans/guardrails-evolution/architecture.md)

---

## AGR-035 — Accept evolution through automated evidence

**Status:** Accepted
**Date:** 2026-10-09
**Owners:** Product lead, technical lead
**Agreement:** Automated acceptance is sufficient for this local evolution plan.
**User statement:** "Automated acceptance is sufficient"
**Rationale:** Preserve the explicit follow-on planning interview as scope authority for implementation.
**Implications:** Exercise the real service and independent gateways with reproducible positive, negative and failure cases. Named application-team sign-off is not a gate. This evidence threshold does not remove the product policy-approval workflow or satisfy untested hosted/production claims.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- [Guardrails evolution architecture](../../plans/guardrails-evolution/architecture.md)

---

## AGR-036 — Allow shared local processing and constrain four allow-deny categories

**Status:** Accepted
**Date:** 2026-10-09
**Owners:** Product lead, technical lead
**Agreement:** Evaluated content may reach a shared local guardrails service. Data-residency restrictions and explicit allowlists and denylists cover network destinations, model/evaluator providers, tools/actions and content terms/topics.
**User statement:** "data-resident restriction, explitic allowedlist & denylist; follow-up: yes, shared local guardrail service; all four"
**Rationale:** Preserve the explicit follow-on planning interview as scope authority for implementation.
**Implications:** The shared service may process raw content locally; this is not per-team data-local evaluation. No external processing is implied. The plan must define matching, precedence, inheritance, enforcement and negative evidence for all four categories. Tool eligibility does not grant business authorization.
**Affected documents:**
- `60-delivery/RELEASE-DEFINITION.md`
- [Guardrails evolution architecture](../../plans/guardrails-evolution/architecture.md)
