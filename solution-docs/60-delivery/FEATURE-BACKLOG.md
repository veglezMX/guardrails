**AI Guardrails — Feature Backlog (Evolution Ladders)**

**Authority level:** L3
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

## Purpose and authority

This document compiles, in one place, **how the AI Guardrails solution can evolve beyond its MVP surface** — organized as twelve independent evolution *axes*, each a ladder from the current MVP rung outward toward committed later-phase work, uncommitted vision, and explicitly out-of-boundary territory.

It is a **derived, analytical view (L3)**. It introduces **no new scope**. Every rung on every ladder maps to an existing identifier already recorded in the authoritative set — an agreement (`AGR-NNN`), a decision (`DEC-NNN`), an open question (`OQ-NNN`), a guardrail category (`GR-NNN`), or a capability (`CAP-NNN`). Where a conflict is ever found between this backlog and [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md), those L1 documents win and this backlog must be corrected.

> **Classification and backlog, not commitment.** Listing an item here does **not** schedule, resource, or estimate it. This file exists so future enhancement ideas have a stable home and a clear status, *instead of* accreting ad hoc as scope pressure — the "product over-expansion" failure mode the risk register warns against (seed.md §20). Only the **Now (MVP)** column describes shipping or Phase 1–2-committed surface. Everything to its right is future work held for consideration. Nothing in the `Later — committed` column alters the estimation basis fixed by [`DEC-008`](../00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases); nothing in `Vision — uncommitted` may be presented externally as a deliverable; nothing in `Out-of-boundary` is ever future work.

---

## How to read this file

* **Read a ladder left → right** to see how one capability evolves across iterations. The leftmost rung is today's MVP surface; each rung to the right is a later, less-committed iteration.
* **Status vocabulary** (identical to [`DEC-009`](../00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision), so the two documents never disagree):

| Status | Meaning | May it be scheduled / sold? |
|---|---|---|
| **Now (MVP)** | Shipping, or inside the Phase 1–2 committed core. | Yes — it is the current product. |
| **Later — committed** | Belongs to a later delivery phase already agreed in [`AGR-020`](../00-source-of-truth/AGREEMENTS.md#agr-020--five-phase-delivery) / [`DEC-006`](../00-source-of-truth/DECISIONS.md#dec-006--sequence-delivery-into-five-phases). Sequenced, not dated. | Yes, as a sequenced later phase — never with a committed date. |
| **Vision — uncommitted** | Directional only. Parked in the catalog / open-questions register; not in the DEC-008 estimation. | No — internal direction only, never an external deliverable. |
| **Out-of-boundary** | Excluded by seed.md §6.3 or [`AGR-010`](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization). Someone else's job. | Never — must carry a named external owner, never "future work." |

* **Graduation.** An item moves left (toward commitment) **only** through the recorded scope-change path in [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md` §10](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md#10-catalog-governance-and-graduation) and [`10-charter/SCOPE.md` §8](../10-charter/SCOPE.md) — never a silent promotion in this file. Editing a rung here does not commit it.
* **The front door.** The MVP column across all axes describes one coherent product: **Mode A (Remote Evaluation) served as a developer service** — the consumer keeps their gateway, model, and data path, installs a cooperative connector (or calls the hosted HTTP Evaluation API), and imports a policy; the platform hosts evaluation, the policy store, and the audited decision. Every axis below evolves outward from that baseline.

---

## Master matrix

| # | Axis | Now (MVP) | Later — committed | Vision — uncommitted | Out-of-boundary |
|---|---|---|---|---|---|
| 1 | Enforcement posture | Cooperative connector | Platform-managed enforcement (Mode B); network/IAM-enforced gateway (optional-advanced) | — | Org-wide AWS enforcement |
| 2 | Serving / deployment mode | Mode A — Remote Evaluation | Mode B Guarded Gateway (Ph 3); Mode C Customer-Managed Runtime (Ph 4); air-gapped installation, committed-but-undated, trigger-gated on first signed regulated/air-gapped customer (CAP-033, DEC-017) | Dedicated-tenant runtimes / "if ever" timing (OQ-010) | Cross-cloud deployment |
| 3 | Evaluation stages | Input + output only | Retrieval, tool-call, tool-result, memory-write (Ph 5) | agent-plan, tool-selection, memory-read (OQ-018) | — |
| 4 | Evaluation engines | Bedrock Guardrails | Deterministic rules, PII evaluators | Third-party / customer evaluator plugins (OQ-013) | Proprietary detection models; model training |
| 5 | Decision actions | observe / warn / redact / block | require_approval → human-approval workflow (Ph 5, OQ-007) | — | Business authorization (AGR-010) |
| 6 | Connectors | LiteLLM + generic HTTP / SDK | Other gateways | Agent runtimes / MCP integration (OQ-006) | General MCP security platform |
| 7 | Policy authoring & distribution | Curated packs + author-your-own | Policy signing + inheritance (OQ-012) | Policy marketplace (OQ-011) | — |
| 8 | Guardrail categories | Core GR set (Ph 1–2) | GR-005 / 006 / 009 / 011 / 013 (later phases); GR-017 per-category decision metrics, graduated from Vision (proposed, DEC-020) | Vision-status GR entries | GR-010 identity/auth; DLP-complete (GR-003); org-wide enforcement (GR-008); business auth (GR-018) |
| 9 | Streaming | Non-streaming | Streaming output evaluation (Ph 5, OQ-004) | — | — |
| 10 | Regions & residency | Multi-region availability, tenant-pinned; 2-account model until an OQ-009 trigger fires (CAP-035, DEC-018; DEC-017) | Per-tenant residency enforcement (CAP-035, DEC-018); multi-region active-active availability (CAP-036, DEC-019); 3+ account model, trigger-gated (OQ-009, DEC-017) | — | — |
| 11 | Commercial | Internal / no metering, free through MVP (DEC-017) | Interim cost + rate tracking (Ph 3); commercial metering, trigger-gated on Phase 3 usage patterns (CAP-034, DEC-017) | Policy marketplace (OQ-011) | — |
| 12 | Compliance | None | SOC 2 Type II, trigger-gated on first contractually-requiring external customer (OQ-015, DEC-017) | — | Full compliance certification |

The per-axis ladders below expand each row, name the trigger that graduates each rung, and carry the authoritative cross-references.

---

## 1. Enforcement posture

*How resistant is the control to bypass?* Orthogonal to deployment mode (a workload in any mode can sit at any posture). Governed by [`AGR-018`](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model), [`CON-012`](../00-source-of-truth/CONSTRAINTS.md).

1. **Now (MVP) — Cooperative connector.** The consumer installs the connector; it can be disabled, removed, or misconfigured. Never represented as a hard security boundary (AGR-018). Answers OQ-001 for the front door: first consumers get cooperative posture with an honest bypass caveat.
2. **Later — committed — Platform-managed enforcement.** The provider operates the enforcement point (Mode B): bypass requires bypassing the provider-run path, not just a client-side connector.
3. **Later — committed — Network / IAM-enforced gateway.** Strongest posture: no direct provider credentials for the application, restricted egress so the gateway is unavoidable. Explicitly *optional-advanced*, **not** an MVP requirement.
4. **Out-of-boundary — Organization-wide AWS enforcement** (seed.md §6.3) — the platform never becomes an org-wide AWS control plane; that is the customer's cloud-governance function.

**Graduates when:** an external customer contractually requires bypass-resistance beyond cooperative — routed to Mode B/C rather than over-building Mode A (OQ-001).

## 2. Serving / deployment mode

*Who operates which part of the path?* Governed by [`AGR-002`](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [`DEC-004`](../00-source-of-truth/DECISIONS.md#dec-004--support-three-deployment-modes-as-the-primary-deployment-framework). Detail in [`50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md).

1. **Now (MVP) — Mode A, Remote Evaluation.** Consumer runs their own gateway, calls the hosted Evaluation API before/after inference. The front door. Content **does** reach the hosted service (AGR-009) — never sold as data-local.
2. **Later — committed — Mode B, Guarded Gateway (Phase 3).** Provider runs an OpenAI-compatible gateway + guardrails + routing. For teams with no gateway. Largest provider operational/security scope.
3. **Later — committed — Mode C, Customer-Managed Runtime (Phase 4).** Provider ships a runtime the customer runs inside their own AWS account; raw content stays local; executes without a synchronous control-plane connection ([`AGR-015`](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection)). Capability [`CAP-017`](../20-product/CAPABILITIES.md).
4. **Later — committed — Air-gapped installation** ([`CAP-033`](../20-product/CAPABILITIES.md)) — a runtime + policy-distribution model that functions with no control-plane connectivity at all; a Phase 4-adjacent hardening of Mode C, committed and estimated within CAP-018–034.
5. **Vision — uncommitted — Dedicated-tenant runtimes / "if ever" timing** (OQ-010) — whether isolated per-tenant runtimes are offered, and when, has no committed phase.
6. **Out-of-boundary — Cross-cloud deployment** (seed.md §6.3) — AWS is the primary and only supported cloud for the estimated feature set.

**Graduates when:** a buyer objection can't be met by Mode A — "we have no gateway" → Mode B; "content can't leave our account" → Mode C.

## 3. Evaluation stages

*Which points in the request lifecycle are evaluated?* Reserved-but-unused stages already exist in the domain model. Governed by [`AGR-017`](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model); expansion tracked by OQ-018.

1. **Now (MVP) — Input + output only.** No retrieval, tool, or memory stages (matches the former MVP boundary, now Phase 1–2 core).
2. **Later — committed — Retrieval-content, tool-call, tool-result, memory-write (Phase 5).** The "advanced agent protection" stage set from [`DEC-006`](../00-source-of-truth/DECISIONS.md#dec-006--sequence-delivery-into-five-phases). Phase 5 is a separate expansion, not an MVP prerequisite. Related: OQ-005, OQ-006.
3. **Vision — uncommitted — Further stages** (`retrieval_query`, `agent_plan`, `memory_read`, `tool_selection`) beyond the reserved seven (OQ-018).

**Graduates when:** OQ-018 resolves the stage vocabulary and a category needing the stage is graduated; every stage change is a versioned, backward-compatible contract change across all three modes (AGR-006).

## 4. Evaluation engines

*What actually performs detection, behind a vendor-independent contract?* Governed by [`AGR-004`](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract), [`AGR-005`](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability). This is the evaluator-adapter seam.

1. **Now (MVP) — Amazon Bedrock Guardrails**, normalized into the platform's own decision structure before reaching policies/connectors.
2. **Later — committed — Deterministic rules + PII evaluators**, added behind the same adapter pattern without contract change.
3. **Vision — uncommitted — Third-party / customer-supplied evaluator plugins** (OQ-013) — needs a security + compatibility review model (trust boundary) before design.
4. **Out-of-boundary — Proprietary detection models & model training** (seed.md §6.3) — the platform integrates engines; it does not build or train detection models.

**Graduates when:** a new engine is needed and (for plugins) OQ-013 defines the trust boundary. Constraint: every engine sits behind the standard adapter contract (evaluator sprawl risk, `NFR-MAINT-003`).

## 5. Decision actions

*What can a policy do about a violation?* Governed by [`AGR-011`](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression). Note: `Warn` is a binding-mode state on the observe→enforce ladder, not a decision action.

1. **Now (MVP) — observe / warn / redact / block**, with the mandatory Disabled → Observe → Warn → Redact → Block progression per binding.
2. **Later — committed — require_approval → human-approval workflow (Phase 5, OQ-007).** The `require_approval` action already exists as a reserved future action with no owning workflow/UI yet.
3. **Out-of-boundary — Business authorization** ([`AGR-010`](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization)) — a guardrail decision never determines whether a user is *permitted* to act; that stays with the consuming application.

**Graduates when:** OQ-007 assigns the approval UX owner and Phase 5 scope is approved.

## 6. Connectors

*How does a given gateway or runtime invoke and enforce a policy?* Additive clients of one invocation contract. Governed by [`AGR-003`](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [`AGR-007`](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors).

1. **Now (MVP) — LiteLLM connector + generic HTTP / SDK path.** The HTTP Evaluation API is the real contract; the LiteLLM plugin and thin Python/TS SDKs ride over it without adding semantics the API lacks (AGR-005).
2. **Later — committed — Additional gateway connectors**, added by implementing the invocation contract only — no change to existing policies.
3. **Vision — uncommitted — Agent-runtime / MCP integration** (OQ-006) — connecting to agent frameworks and MCP; paired with the Phase 5 agent stages (axis 3).
4. **Out-of-boundary — General MCP security platform** (seed.md §6.3) — the product connects to MCP; it does not become a standalone MCP security product.

**Graduates when:** a new gateway/runtime is targeted; the connector owns request mapping + enforcement invocation only, never policy or detection logic.

## 7. Policy authoring & distribution

*Where do the policies consumers import come from?*

1. **Now (MVP) — Curated policy packs + author-your-own.** The platform ships starter policies (PII, prompt-injection, toxicity, …) mapped to the GR catalog — this is what makes the "import a policy" onramp ([`AGR-019`](../00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import)) and the ~10-line quickstart real. Consumers author or override their own versioned policies on top, in the portable format.
2. **Later — committed — Policy signing + inheritance (OQ-012).** Signing verifies policy artifacts distributed to Mode C runtimes; inheritance governs workload-specific overrides so they don't accumulate ungoverned.
3. **Vision — uncommitted — Policy marketplace (OQ-011).** Third-party-authored, shared or sold policies. Parked; commercial question.

**Graduates when:** OQ-012 (signing/inheritance) resolves at the Phase 2→3 transition; marketplace only after OQ-011 commercial decision.

## 8. Guardrail categories

*Which classes of control the taxonomy covers.* Governed by [`AGR-021`](../00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) / [`DEC-009`](../00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision). Authoritative per-category status lives in [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) (`GR-001`–`GR-018`); this axis is a pointer, not a second source of truth.

1. **Now (MVP) — Core GR set (Phases 1–2)** — the categories reachable by input/output evaluation with the MVP engine.
2. **Later — committed — GR-005, GR-006, GR-009, GR-011, GR-013** — the later-phase categories phase-sequenced by ADR-0002 (agentic/advanced categories land with Phase 5 stages). Also **Later phase — committed (proposed) — GR-017's "Per-category decision metrics" item** (block/redact rates by guardrail category; new CAP-037, pending owner ratification and formal `CAPABILITIES.md` registration) — [`DEC-020`](../00-source-of-truth/DECISIONS.md#dec-020--graduate-gr-017-per-category-decision-metrics-from-vision-to-committed-scope) graduates this single sub-item of the otherwise-Core GR-017 (Monitoring, Evaluation and Incident Response) category out of Vision.
3. **Vision — uncommitted — Vision-status GR entries** — categories (and category sub-items, e.g. the remaining GR-017 monitoring items) classified but not committed; enter scope only via the graduation exercise (EXTENSIBILITY §10).
4. **Out-of-boundary — GR-010 (identity/authorization); DLP-complete items in GR-003; org-wide enforcement in GR-008; business authorization in GR-018** — each carries a named external owner in the catalog and is never a future deliverable (AGR-010, seed.md §6.3).

**Graduates when:** a Vision entry is taken through the recorded scope-change path — the first such exercise, GR-017's per-category decision metrics item, is now recorded as DEC-020, satisfying ADR-0002's own validation criterion.

## 9. Streaming

*Can output be evaluated as it streams?*

1. **Now (MVP) — Non-streaming.** The `streaming` workload attribute exists, but streaming output evaluation is not delivered — workloads must not assume streaming is covered (OQ-004).
2. **Later — committed — Streaming output evaluation (Phase 5, OQ-004).** Defines what applies to streaming workloads until it ships.

**Graduates when:** OQ-004 sets the timeline and interim behavior at Phase 5 scoping.

## 10. Regions & residency

*Where does processing physically happen, and can residency be enforced?* Governed by [`AGR-013`](../00-source-of-truth/AGREEMENTS.md#agr-013--aws-as-primary-cloud-minimal-mvp-account-model) (amended by DEC-018).

1. **Now (MVP) — Multi-region availability, tenant-pinned; minimal 2-account model.** Each tenant's evaluation traffic is pinned to a single processing region ([`CAP-035`](../20-product/CAPABILITIES.md), [`DEC-018`](../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga)) — this supersedes the former single-region MVP reading. Regionality is expressed through regional resources inside the existing production account, not additional accounts; the minimal 2-account model (one non-production, one production) is retained until an OQ-009 trigger fires ([`DEC-017`](../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture)).
2. **Later — committed — Per-tenant residency enforcement** ([`CAP-035`](../20-product/CAPABILITIES.md), [`DEC-018`](../00-source-of-truth/DECISIONS.md#dec-018--multi-region-availability-at-ga)). The "Allowed Regions" domain attribute needs this enforcement mechanism before residency is claimed — formerly OQ-003's unresolved backstop, now closed by DEC-018.
3. **Later — committed — Multi-region active-active availability** ([`CAP-036`](../20-product/CAPABILITIES.md), [`DEC-019`](../00-source-of-truth/DECISIONS.md#dec-019--multi-region-active-active-availability-committed-as-a-later-phase)) — live cross-region failover/replication, beyond the single-region-per-tenant model of CAP-035. Formerly out-of-boundary (OQ-016, seed.md §6.3); DEC-019 explicitly supersedes that exclusion and commits it as a later phase, expanding the DEC-008 estimation basis.
4. **Later — committed — 3+-account separation, trigger-gated (OQ-009).** Added only on the first external customer, a compliance requirement, or independent security ownership ([`DEC-017`](../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture)).

**Graduates when:** OQ-003 is closed by DEC-018 (residency enforcement is now committed core, not open); OQ-016 is closed by DEC-019 (active-active is now a committed later phase, not out-of-boundary); OQ-009's trigger (DEC-017) is reassessed at each phase gate.

## 11. Commercial

*How (if at all) is consumption metered and billed?*

1. **Now (MVP) — Internal / no metering, free through MVP.** First consumers include internal dogfooding and design partners; no billing surface required to prove policy reuse ([`DEC-017`](../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture)).
2. **Later — committed — Interim cost + rate tracking (Phase 3); commercial metering ([`CAP-034`](../20-product/CAPABILITIES.md), [`DEC-017`](../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture)).** Phase 3 (Guarded Gateway) needs cost/rate controls even without full billing; usage-based metering suitable for commercial billing is a committed, estimated later-phase capability, built once Phase 3 usage patterns are observed.
3. **Vision — uncommitted — Policy marketplace (OQ-011).** Whether commercial metering is turned into a billed offering, and whether a third-party policy marketplace is pursued, is the open commercial question. Serving external customers from day one (per the serving-model design) makes the billing decision the first likely to be pulled forward.

**Graduates when:** OQ-011 commercial decision, revisited after Phase 3 usage is observed.

## 12. Compliance

*What formal certifications back the platform?* Governed by OQ-015, resolved by [`DEC-017`](../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture).

1. **Now (MVP) — None.** Claims discipline (AGR-009, AGR-010, AGR-018) holds regardless; no certification is claimed at MVP (DEC-017).
2. **Later — committed — SOC 2 Type II, trigger-gated** ([`DEC-017`](../00-source-of-truth/DECISIONS.md#dec-017--deployment-and-commercial-posture)) — pursued on the first external customer that contractually requires it; no other certification is committed.
3. **Out-of-boundary — Full compliance certification** as a product promise (seed.md §6.3); stays excluded per DEC-017.

**Graduates when:** the first external customer contractually requires SOC 2 Type II (DEC-017); no other certification has a defined trigger yet.

---

## Traceability note

This file is a **compilation**, not an origin of scope. It is authoritative for nothing: the sources of truth remain [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md), [`DECISIONS.md`](../00-source-of-truth/DECISIONS.md), [`OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md), [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md), and [`20-product/CAPABILITIES.md`](../20-product/CAPABILITIES.md). When any of those changes, update the affected rung here; when they conflict, they win. A rung is added, moved, or removed here **only** to reflect a change already made in one of those registers — never to introduce one.

## Related documents

- [`00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) — the OQ-NNN items that gate most later/vision rungs.
- [`20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) — the phase-sequenced, product-facing view of committed work.
- [`20-product/GUARDRAIL-CATALOG.md`](../20-product/GUARDRAIL-CATALOG.md) — authoritative per-category status (axis 8).
- [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md) — the four extension seams and the [§10 graduation path](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md#10-catalog-governance-and-graduation) every rung must travel to graduate.
- [`50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) — modes for axes 1–2.
- [`60-delivery/MILESTONES.md`](MILESTONES.md), [`60-delivery/IMPLEMENTATION-STRATEGY.md`](IMPLEMENTATION-STRATEGY.md) — phase-level delivery detail.
