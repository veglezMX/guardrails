**AI Guardrails — Changelog**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** AGREEMENTS.md

---

> This is the chronological ledger of meaningful changes to the AI Guardrails **solution definition** — the substance of what the product is, how it is scoped, and how it is governed — as recorded across the `solution-docs/` set. It follows [Keep a Changelog](https://keepachangelog.com/)-style conventions: entries are grouped under a version/date heading with `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, and `Security` subsections, newest first, and the log is additive-only — prior entries are never rewritten, only appended to.
>
> This ledger is distinct from [`00-source-of-truth/DECISIONS.md`](00-source-of-truth/DECISIONS.md), which records product, organizational, and delivery decisions (`DEC-NNN`). A single accepted decision or agreement can, and typically will, produce several entries here as each affected document is authored or corrected. It is also distinct from [`00-source-of-truth/AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md), which is the authoritative (L1) register of *what was agreed*; this file records *when the documentation came to reflect it*. The governing rules for how this ledger is used — including the Path A / Path B change-control distinction — are defined in [`70-governance/DOCUMENT-GOVERNANCE.md §9`](70-governance/DOCUMENT-GOVERNANCE.md#9-change-history--changelogmd).
>
> As with every document at Authority level L4, this changelog summarizes and dates changes already settled at L1–L3; it introduces no new agreement, scope, or claim of its own.

---

## Table of contents

<!-- BEGIN GENERATED: toc -->
- [[2026-10-10] — Local evolution implementation plan](#2026-10-10--local-evolution-implementation-plan)
- [[2026-10-08] — Local technical proof release defined](#2026-10-08--local-technical-proof-release-defined)
- [[Unreleased]](#unreleased)
- [[2026-07-26d] — Traceability generated; hand-maintained indexes retired](#2026-07-26d--traceability-generated-hand-maintained-indexes-retired)
- [[2026-07-26c] — Documentation set made self-contained; original charter absorbed](#2026-07-26c--documentation-set-made-self-contained-original-charter-absorbed)
- [[2026-07-26b] — Connector lifecycle and the pre-implementation decision backlog](#2026-07-26b--connector-lifecycle-and-the-pre-implementation-decision-backlog)
- [[2026-07-26] — Integration manifest defined](#2026-07-26--integration-manifest-defined)
- [[2026-07-25] — Evaluation contract wire format and enforcement-ladder reconciliation](#2026-07-25--evaluation-contract-wire-format-and-enforcement-ladder-reconciliation)
- [[2026-07-24] — Guardrail-category catalog and extensibility architecture](#2026-07-24--guardrail-category-catalog-and-extensibility-architecture)
- [[2026-07-24] — Initial documentation set](#2026-07-24--initial-documentation-set)
- [Related documents](#related-documents)
<!-- END GENERATED: toc -->
---

## [2026-10-10] — Local evolution implementation plan

**Change type:** Path A — explicit follow-on interview decisions recorded separately from historical proof acceptance.
**Trigger:** User selected all nine evolution workstreams and confirmed shared local processing, four-category restrictions and policy approval workflow.

### Added

- AGR-031 through AGR-036 and DEC-025 preserve the accepted interview decisions.
- `plans/guardrails-evolution/`: six local phases, 28 components, domain/wire contracts, detector/residency/tenancy rules, UI and deployment specifications, executable acceptance gates and explicitly marked implementation assumptions.

### Changed

- Release definition, charter, scope and product-roadmap entry points distinguish the new local sequence from the completed proof and the broader hosted programme. No predecessor specification/progress or production-readiness claim changed.

---

## [2026-10-08] — Local technical proof release defined

**Change type:** Path A — release-boundary agreements recorded; derived scope views annotated.
**Trigger:** DEC-022 and the user's release-definition interview answers.

### Added

- AGR-022 through AGR-024: local technical proof with AWS deferred, internal application teams before later service adoption, and API/CLI release-day experience.
- DEC-022 and `60-delivery/RELEASE-DEFINITION.md`: a separate local acceptance boundary, completed through the three interview rounds.
- OQ-023 and OQ-024: local gateway/model proof setup and working guardrail coverage, subsequently resolved by DEC-023.
- AGR-025 through AGR-027: two real LiteLLM instances, an offline-capable default proof with a mock model, and locally working sensitive-data detection/redaction selected under the user's delegated choice.

- AGR-028 through AGR-030 and DEC-024: full API/CLI lifecycle, one configurable published policy version per workload, and automated demonstration as sufficient local acceptance evidence. OQ-021 resolved for this release.
- A derived local acceptance matrix and workflow in `RELEASE-DEFINITION.md`; implementation acceptance remains unverified.

- AGR-028 through AGR-030 and DEC-024: full API/CLI lifecycle, one configurable published policy version per workload, and automated demonstration as sufficient local acceptance evidence. OQ-021 resolved for this release.
- A derived local acceptance matrix and workflow in `RELEASE-DEFINITION.md`; implementation acceptance remains unverified.

- AGR-028 through AGR-030 and DEC-024: full API/CLI lifecycle, one configurable published policy version per workload, and automated demonstration as sufficient local acceptance evidence. OQ-021 resolved for this release.
- A derived local acceptance matrix and workflow in `RELEASE-DEFINITION.md`; implementation acceptance remains unverified.

### Changed

- Charter, scope, constraints and delivery entry points identify the current local milestone separately from existing hosted Phase 1–2 and GA obligations.
- The MVP README and task survey point to the current interview definition. Existing estimates and the circulated delivery proposal require rebaselining after the interview closes.

---

## [Unreleased]

**Change type:** Path A (amendment of the L1 register, propagated to every derived layer)
**Trigger:** [DEC-008](00-source-of-truth/DECISIONS.md#dec-008--supersede-the-mvp-bounded-scope-define-and-estimate-the-full-feature-set-across-five-phases) — the MVP-bounded scope was too narrow to support a valid delivery estimation; the solution is now defined and estimated over the full feature set, delivered across five phases.
**Owner:** Product lead, Technical lead (joint)

### Added
- [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) **DEC-008** — supersedes DEC-005; records the removal of the MVP scope boundary and MVP success demonstration from the register, the register renumbering, and the full-feature-set estimation basis.

### Changed
- [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) renumbered: "Meaning of import" is now **AGR-019**, "Five-phase delivery" is now **AGR-020**; register is AGR-001–AGR-020 (20 agreements).
- Scope reframed across the derived set (charter, product, executive, sales-deck, architecture, delivery, governance, agent-input layers): the core capability set (former "MVP scope") is now **Phases 1–2 core scope**, and the former "future scope" capabilities are **later-phase scope — committed and estimated (Phases 3–5)**. Phase gating per AGR-020 is unchanged; the [`SCOPE.md`](10-charter/SCOPE.md) product-category exclusions remain excluded; the claims discipline (AGR-009, AGR-010, AGR-018, TBD figures) is unchanged.
- Executive and sales-deck ask reframed: approval of the full solution (five-phase program) on a full-feature estimation basis, replacing the Phase-1-only ask; budget/headcount figures remain TBD until supplied.
- The two-independent-LiteLLM-instances demonstration reframed from register-level "MVP success demonstration" to the **Phase 1–2 validation demonstration** owned by [`60-delivery/VALIDATION-PLAN.md`](60-delivery/VALIDATION-PLAN.md).
- [`40-sales-deck/AI-Guardrails-Project-Proposal.pptx`](40-sales-deck/) regenerated to match the reframed proposal.

### Deprecated
- _Nothing._

### Removed
- Former **AGR-019 — MVP scope boundaries** and former **AGR-020 — MVP success demonstration** removed from [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) per DEC-008.

### Fixed
- _Nothing._

### Security
- _Nothing._

---

## [2026-07-26d] — Traceability generated; hand-maintained indexes retired

**Change type:** Path A (amendment of the L1 register, propagated to every derived layer)
**Trigger:** [DEC-021](00-source-of-truth/DECISIONS.md#dec-021--retire-the-traceability-matrix-in-favour-of-generated-traceability) — four hand-maintained indexes had drifted simultaneously; a reverse index derivable from forward citations should not be maintained by hand.
**Owner:** Product lead, Technical lead (joint)

### Added
- `flake.nix`, `.envrc`, `justfile`, and `tools/docs/` — a nix devShell plus `just check`, `just gen`, `just toc`, `just trace`, and `just verify`.
- `tools/docmeta.json` — editorial prose with no derivable source: condensed questions, short forms, categories, targets, external mappings, per-document descriptions, and each index's curated ordering.
- [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) **DEC-021** — retires the traceability matrix in favour of generated traceability.
- [`DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) §8.1 — machine-owned regions are delimited by BEGIN/END GENERATED comment markers and never hand-edited.
- `GR-000` — a reserved platform namespace for reason codes that are not guardrail findings ([`GUARDRAIL-CATALOG.md`](20-product/GUARDRAIL-CATALOG.md) § Reason codes). Deliberately not a nineteenth category.
- New index sections: root-level document tables in [`README.md`](README.md) and [`DOCUMENT-MAP.md`](DOCUMENT-MAP.md); [`REFERENCES.md`](70-governance/REFERENCES.md) §§7.3–7.5 for `80-agent-inputs/`, `90-archive/`, and the repository root, none of which previously had one.

### Changed
- DEC-010 through DEC-020 ratified from `Proposed` to **`Accepted`**, resolving 21 open questions that cited them; dependent wording updated in `SERVING-MODEL.md`, `DEPLOYMENT-ARCHITECTURE.md`, `CAPABILITIES.md`, and `GUARDRAIL-CATALOG.md`.
- The defined-and-estimated feature set re-baselined from `CAP-001`–`CAP-034` to **`CAP-001`–`CAP-041`**; core is now `CAP-001`–`CAP-017` plus `CAP-038`–`CAP-041`. Corrected in `DECISIONS.md`, `GUARDRAIL-CATALOG.md`, `ADR-0002`, and `CLAIMS-REGISTER.md`.
- 87 regions across 48 documents became generated: every table of contents, the register summary and count tables, the guardrail category index, the glossary quick index, the ADR index, and the document indexes in `README.md`, `DOCUMENT-MAP.md`, and `REFERENCES.md`.
- [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) — the matrix removed from three **Affected documents** lists.
- [`DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) §8 rewritten around `just check` and `just trace`; change-control steps and both process diagrams updated.
- [`OUTPUT-CONTRACTS.md`](80-agent-inputs/OUTPUT-CONTRACTS.md) — generation agents now cite `just trace` as their traceability guardrail.

### Deprecated
- _Nothing._

### Removed
- `00-source-of-truth/TRACEABILITY-MATRIX.md` — archived as [`90-archive/00-source-of-truth--TRACEABILITY-MATRIX--archived-2026-07-26.md`](90-archive/00-source-of-truth--TRACEABILITY-MATRIX--archived-2026-07-26.md). First entry in the archive.

### Fixed
- 9 broken heading anchors (`SLIDE-OUTLINE.md`, `OBJECTION-HANDLING.md`, `ARCHITECTURE-REVIEW-AGENT.md`, `SALES-DECK-AGENT.md`) — headings had been reworded without updating their cross-references.
- 1 dead link to the removed `folder-spectation.md` in [`90-archive/README.md`](90-archive/README.md).
- Stale identifier ranges: `AGR-001`–`AGR-020` → `AGR-021`, `FR-001`–`FR-094` → `FR-127`, and a reference to an agreement number the register has never held.
- Missing index entries: 24 documents absent from `REFERENCES.md`, 16 from `DOCUMENT-MAP.md`, 13 from `README.md`.

### Security
- _Nothing._

---

## [2026-07-26c] — Documentation set made self-contained; original charter absorbed

**Change type:** Path A (structural change to the documentation set's provenance model; no product scope, agreement, claim, or figure changed)
**Trigger:** The set depended on a raw solution charter (`seed.md`) held outside `solution-docs/`. Every document's `Derived from:` header and roughly two thousand inline citations pointed at that external file, so the set could not be read, reviewed, or relocated on its own.
**Owner:** Product lead, Technical lead (joint)

### Added
- [`00-source-of-truth/PROVENANCE.md`](00-source-of-truth/PROVENANCE.md) — historical record of where the original solution charter's content now lives: a section-by-section map from each charter section to its owning document, plus the deliberate divergences where an accepted agreement or decision supersedes the original wording. Explicitly outside the L1–L5 hierarchy and explicitly not a source: nothing may be derived from it and it may not be cited as evidence for a claim.
- Registered in [`README.md`](README.md) §5, [`DOCUMENT-MAP.md`](DOCUMENT-MAP.md), [`70-governance/DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) §4, and [`70-governance/REFERENCES.md`](70-governance/REFERENCES.md).

### Changed
- **Every inline charter citation re-pointed at the document that owns the content.** `seed.md §9` became [AGR-017](00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model), `seed.md §6.3` became [`SCOPE.md`](10-charter/SCOPE.md), `seed.md §20` became [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md), and so on across all 33 charter sections and subsections. Where the citing document *is* the owning document, the citation became an `original charter §N` link into `PROVENANCE.md`, which states origin rather than authority.
- **`Derived from:` headers** across 89 documents no longer name the charter file.
- [`00-source-of-truth/AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) **AGR-017** — the domain model's attributes were defined by reference to "the solution charter"; they are now defined by reference to [`70-governance/GLOSSARY.md`](70-governance/GLOSSARY.md) and the `FR-` entries in [`50-architecture/REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md), which already carried them in full. `REQUIREMENTS.md` added to the entry's affected-documents list.
- [`70-governance/DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) §1 — "Relationship to `seed.md`" replaced by "Relationship to the original solution charter", describing the provenance record rather than a live external file.
- [`README.md`](README.md) §2 — the ground-truth section now states that the set is self-contained.
- **Agent instruction sets (`80-agent-inputs/`)** — the charter is no longer a permitted or readable source. [`CHARTER-AGENT.md`](80-agent-inputs/CHARTER-AGENT.md) loses its raw-charter fallback: a charter section with no backing `AGR-`/`CON-` entry is now escalated rather than written from raw material. [`EXECUTIVE-BRIEF-AGENT.md`](80-agent-inputs/EXECUTIVE-BRIEF-AGENT.md), [`SALES-DECK-AGENT.md`](80-agent-inputs/SALES-DECK-AGENT.md), [`ARCHITECTURE-REVIEW-AGENT.md`](80-agent-inputs/ARCHITECTURE-REVIEW-AGENT.md), and [`OUTPUT-CONTRACTS.md`](80-agent-inputs/OUTPUT-CONTRACTS.md) updated to match.
- **"No invented figures" wording** throughout the set now anchors on the L1 registers rather than on the charter file. The discipline itself is unchanged.

### Deprecated
- _Nothing._

### Removed
- **`seed.md`** — the raw solution charter at the repository root. A section-by-section coverage audit confirmed every claim, list, table, diagram, and requirement it carried is present in `solution-docs/`; all nine of its diagrams are reproduced in the architecture set. No product content was lost, and no agreement, constraint, capability, requirement, claim, or figure changed as a result of the removal.

### Fixed
- _Nothing._

### Security
- _Nothing._

---

## [2026-07-26b] — Connector lifecycle and the pre-implementation decision backlog

**Change type:** Path B (nine new ADRs at `Proposed`, one new open question, glossary additions; no L1 register amended)
**Trigger:** Continuation of the pre-implementation review that produced ADR-0005 and ADR-0006. Two sweeps: the **connector lifecycle**, where three accepted requirements (FR-006, FR-016, FR-075) rest on a fact the platform cannot currently obtain; and the **decision backlog** — the structural choices that a pre-implementation design pass identified as load-bearing and unrecorded, each of which would otherwise be settled arbitrarily by whoever wrote code first.
**Owner:** Technical lead, architecture lead (joint; security and governance lead co-owner on ADR-0008, ADR-0009, ADR-0014; platform operations lead co-owner on ADR-0007, ADR-0015)

### Added

**Connector lifecycle**
- [`adrs/ADR-0007-connector-identity-verification-and-liveness.md`](50-architecture/adrs/ADR-0007-connector-identity-verification-and-liveness.md) — **Proposed.** Compatibility validation is two-phase: *declared* at bind time, *observed* at request time, and neither alone satisfies FR-016. Connector identity travels as an optional request field (minor under ADR-0005 §2), is self-reported and therefore explicitly not a security control. A workload stays in `pending verification` until an evaluation has been observed at every stage its policy declares — making a half-wired connector visible. Bound workloads that stop evaluating are reported stale, and an explicit decommissioning action exists because without it staleness becomes alert noise and the only detection capability cooperative posture offers is destroyed.
- [`adrs/ADR-0008-connector-distribution-and-integrity.md`](50-architecture/adrs/ADR-0008-connector-distribution-and-integrity.md) — **Proposed.** Named per-ecosystem publication channels, connector versioning independent of the contract/policy/product versions but declaring which contract majors it speaks, integrity verifiable at installation, documented privilege, and an artifact carrying no policy and no credential.

**Decision backlog**
- [`adrs/ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md`](50-architecture/adrs/ADR-0009-policy-document-schema-and-mandatory-failure-behavior.md) — **Proposed.** Every failure-behavior key required, **no defaults**; capability-based evaluator selection; immutable versions; retention off unless opted in.
- [`adrs/ADR-0010-policy-storage-split.md`](50-architecture/adrs/ADR-0010-policy-storage-split.md) — **Proposed.** Ratifies `TRADE-OFFS.md` §6's standing recommendation: key-value for hot-path bindings, versioned object storage for immutable documents, append-only bindings, one artifact for both storage and Mode C distribution.
- [`adrs/ADR-0011-policy-distribution-and-caching.md`](50-architecture/adrs/ADR-0011-policy-distribution-and-caching.md) — **Proposed.** Runtimes pull; resolution reads local state only in every mode; refresh failure never fails an evaluation; staleness measured and bounded; "immediate rollback" means immediate *at the control plane*, converging within the refresh interval.
- [`adrs/ADR-0012-client-bootstrap-configuration.md`](50-architecture/adrs/ADR-0012-client-bootstrap-configuration.md) — **Proposed.** A dedicated configuration retrieval path seeded by the integration manifest; a never-configured client **refuses to evaluate** rather than defaulting to allow; losing contact never un-configures a client; the channel carries no policy.
- [`adrs/ADR-0013-runtime-capability-manifest.md`](50-architecture/adrs/ADR-0013-runtime-capability-manifest.md) — **Proposed.** Runtimes publish capabilities (never implementations), fail to start on unsatisfiable configuration, and unsatisfiable bindings are refused at bind time.
- [`adrs/ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md`](50-architecture/adrs/ADR-0014-audit-coverage-rejections-and-client-synthesized-decisions.md) — **Proposed.** Rejected requests and client-synthesized decisions both produce audit records; the client store is bounded, content-free, and visibly lossy; client-origin records are never presented as equivalent to platform observations.
- [`adrs/ADR-0015-runtime-deployment-target.md`](50-architecture/adrs/ADR-0015-runtime-deployment-target.md) — **Proposed.** Containers on ECS Fargate behind an ALB, chosen because Phase 4 ships this runtime into customer accounts as a container and a second packaging path would falsify the [`SUCCESS-CRITERIA.md`](10-charter/SUCCESS-CRITERIA.md) criterion.

**Registers**
- [`00-source-of-truth/OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md) **OQ-022 — Connector version support window and deprecation policy**, under a new section **K. Client lifecycle**. `PROOF-POINTS.md` already commits externally that a connector must be "maintained by the consuming team" — an obligation with no terms, and no answer for a workload that never migrates when ADR-0005 §2's migration window closes.
- [`70-governance/GLOSSARY.md`](70-governance/GLOSSARY.md) **Integration manifest** entry, and a table in the **Connector** entry recording that "install connector" means three different things by connector type — a plugin install, an SDK dependency plus call sites, or (for a custom gateway) nothing installed at all.

**Registration of the nine ADRs into the requirement and capability registers**
- [`50-architecture/REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md) **FR-101–FR-127** (27 requirements) and four new sections: **§13 Policy distribution and caching**, **§14 Runtime capability manifest and bind-time compatibility**, **§15 Connector identity, installation verification, and liveness**, **§16 Client distribution, configuration, and conformance**. Former §13–§15 renumbered to §17–§19.
- [`20-product/CAPABILITIES.md`](20-product/CAPABILITIES.md) **CAP-039** (connector installation verification and liveness), **CAP-040** (published, verifiable client artifacts), **CAP-041** (client conformance suite).
- **FR-127** — the generic SDK must offer both synchronous and asynchronous surfaces over one shared transport, configuration source, and failure-behavior path, so adopting guardrails never forces a consuming application onto an asynchronous runtime.
- **FR-126 / CAP-041 close a gap found during the final extraction sweep.** [`INTEGRATION-ARCHITECTURE.md` §5](50-architecture/INTEGRATION-ARCHITECTURE.md) states that a conformance suite *"can"* validate any new connector — a property of the design, never a committed artifact. With a consumer-operated custom gateway in Phase 1–2 scope ([AGR-003](00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary)) and the glossary now stating that such a team "demonstrates conformance," the suite must be a published deliverable or that obligation has nothing behind it.

### Changed
- [`adrs/README.md`](50-architecture/adrs/README.md) — ADR-0007 through ADR-0015 added to the Current ADRs index with summaries; backlog candidates updated: **client-side failure behaviour and configuration distribution** closed by ADR-0012, **event-driven policy distribution** closed by ADR-0011, **separate control plane and data plane** partially addressed by ADR-0010/ADR-0011, **support managed and standalone deployment** compute target settled by ADR-0015.

### Deprecated
- _Nothing._

### Removed
- _Nothing._

### Fixed
- **FR-006, FR-016, and FR-075 were unimplementable on the connector side.** All three require validating compatibility against "the resolving runtime/**connector** version," and no document stated how the platform learns which connector version a workload runs. The integration manifest carries the *expected* range, so the check compared an expectation against itself. ADR-0007 §1–§2 closes this.
- **`CONTAINER-ARCHITECTURE.md` §7.1's explicit TBD** on policy-distribution transport, on which AGR-015, "immediate rollback," the policy-cache-hit metric, and TECHNICAL-RISKS R7 all depend. ADR-0011 closes it.
- **`TRADE-OFFS.md` §6's "recommended, not yet an AGR"** policy-storage note, which asked to be recorded as an agreement and its own ADR. ADR-0010 does so.
- **The binding's "Runtime compatibility requirements" attribute** (`DATA-ARCHITECTURE.md` §1) was defined and unused. ADR-0013 activates it.
- **The bootstrap gap ADR-0005 named in its own scope boundaries** — a client is required to apply configuration that resolves server-side, at the moment the server is unreachable. ADR-0012 closes it.
- **"ECS Fargate or EKS" and "API Gateway or ALB"** were repeated across `CONTAINER-ARCHITECTURE.md` and `INVESTMENT-SUMMARY.md` without either being chosen. ADR-0015 resolves both.

### Security
- ADR-0008 addresses a supply-chain surface no document covered: the connector executes **inside the consuming team's gateway process, on the request path, with access to every prompt and response** that workload handles, while the Mode C runtime container — which runs in an account its operator controls — already had a fully specified supply chain. Integrity verification is required to be possible, documented, and part of the installation instructions.
- ADR-0007 §2 and ADR-0014 §4 both state explicitly that self-reported client data is operationally valuable and **not evidentiary**, preserving CON-012 and AGR-018 against the most likely misuse — aggregating client self-reports into figures that read as platform observations.

---

## [2026-07-26] — Integration manifest defined

**Change type:** Path B (new ADR at `Proposed`, plus a new capability, requirements, and an open question; no L1 register amended)
**Trigger:** A review of the onboarding flow found that "integration manifest" — named in [AGR-019](00-source-of-truth/AGREEMENTS.md#agr-019--meaning-of-import) and repeated across the charter, product, executive, sales-deck, architecture, delivery, and governance layers — had no definition anywhere. The same defect ADR-0005 found in the evaluation contract, one step earlier in the flow: a load-bearing named artifact that every document references and none specifies.
**Owner:** Product lead, technical lead (joint)

### Added
- [`adrs/ADR-0006-integration-manifest-format-and-contents.md`](50-architecture/adrs/ADR-0006-integration-manifest-format-and-contents.md) — **Proposed.** The manifest is a generated, schema-validated, secret-free YAML **file** a consuming team commits, reviews, and shares — not a screen. It is derived from a policy binding and never authoritative at evaluation time; one shape serves all three deployment modes; and its **prohibited-contents list turns AGR-019's trust commitments into schema constraints checkable in CI** rather than review discipline.
- [`20-product/CAPABILITIES.md`](20-product/CAPABILITIES.md) **CAP-038 — Integration manifest generation.** Core (Phases 1–2), dependent on the workload registry and bindings. Generation is API-first, so a later policy-catalog surface is one consumer of it rather than the only way to obtain a manifest.
- [`50-architecture/REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md) **FR-095–FR-100** — manifest as a shareable file; required contents; prohibited contents; the manifest is not a credential; the manifest is not authoritative at evaluation time; one shape across all three modes.
- [`00-source-of-truth/OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md) **OQ-021 — Policy-selection cardinality in the onboarding flow.** Curated policy packs organised by guardrail category invite selecting several, but a binding associates exactly one workload with exactly one immutable policy version. Whether "select policy" means one version or a composed set must be settled before any selection surface is designed; sequenced with the [OQ-012](00-source-of-truth/OPEN-QUESTIONS.md#oq-012--policy-signing-and-policy-inheritance-design) inheritance work.

### Changed
- [`50-architecture/REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md) §3 now records that the binding is what a manifest is generated from, and implements CAP-038 alongside CAP-003; the functional-area map gains the manifest-generation step between binding and connectors; the traceability summary and requirement counts updated (100 requirements, 82 MVP).
- [`20-product/CAPABILITIES.md`](20-product/CAPABILITIES.md) capability count summary: 38 cataloged capabilities, 18 core.
- [`adrs/README.md`](50-architecture/adrs/README.md) — ADR-0006 added to the Current ADRs index with a summary.

### Deprecated
- _Nothing._

### Removed
- _Nothing._

### Fixed
- **[EO-B1](30-executive/EXPECTED-OUTCOMES.md) was unverifiable.** It commits to the onboarding flow "implemented end to end," with "generate integration manifest" as an explicit step, while no artifact existed to point at. ADR-0006 gives that step something demonstrable.
- **AGR-019's prohibitions were unenforceable.** The agreement forbids import requiring model-provider credentials, gateway administration access, model-routing configuration, or LiteLLM database access — all constraints on what the manifest carries, and uncheckable against an undefined object. FR-097 and ADR-0006 §4 make them schema constraints.

### Security
- The manifest is explicitly **not a credential** (FR-098, ADR-0006 §5): the workload identity it carries is an identifier, authentication remains IAM-based per [CON-010](00-source-of-truth/CONSTRAINTS.md#con-010--iam-based-authentication-and-least-privilege-roles), and possession grants no access. This is what permits a manifest to be committed and shared; any future proposal to embed a credential is a proposal to make it unshareable and must be recorded as such.

---

## [2026-07-25] — Evaluation contract wire format and enforcement-ladder reconciliation

**Change type:** Path B (new ADRs at `Proposed`, plus derived-document corrections; no L1 register amended)
**Trigger:** A pre-implementation consistency review of the architecture set found one contradiction and one structural gap that would each have been resolved arbitrarily by whoever wrote code first: the AGR-011 five-state enforcement ladder shares two identifiers (`Redact`, `Block`) with the §3.4 decision-action set while `Warn` has no action at all; and [AGR-006](00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes)'s requirement that contract changes be "versioned and evaluated for backward compatibility before release" had no versionable artifact to operate on.
**Owner:** Technical lead, architecture lead (joint; security and governance lead co-owner on ADR-0004)

### Added
- [`adrs/ADR-0004-enforcement-ladder-states-and-decision-actions.md`](50-architecture/adrs/ADR-0004-enforcement-ladder-states-and-decision-actions.md) — **Proposed.** The ladder state is a *ceiling* on the emitted action, held on the policy binding as `enforcement_state` and reported on the decision. `Warn` is distinguished from `Observe` by that field rather than by a fifth action; `disabled` skips evaluators but still produces a decision and an audit record; reason codes are invariant across all five states. AGR-011 is expressed, not relaxed — all five states survive.
- [`adrs/ADR-0005-evaluation-contract-wire-format.md`](50-architecture/adrs/ADR-0005-evaluation-contract-wire-format.md) — **Proposed.** JSON over HTTPS with SigV4; URI major versioning; an OpenAPI 3.1 document generated from the runtime's own models and gated by a CI golden diff; RFC 9457 error bodies; and the rule that any completed evaluation returns `200` whatever its action, so a policy block never travels the client's error path.
- [`50-architecture/INTEGRATION-ARCHITECTURE.md`](50-architecture/INTEGRATION-ARCHITECTURE.md) **§3.8 — Enforcement-ladder states and the action set**, applying ADR-0004 at the contract level.
- [`adrs/README.md`](50-architecture/adrs/README.md) backlog candidate **Client-side failure behaviour and configuration distribution** — §4.4 requires a connector to apply its workload's configured timeout and failure behaviour, but that configuration resolves server-side and the timeout case is precisely when the server is unreachable. Named as an explicit open gap in ADR-0005's scope boundaries.

### Changed
- [`50-architecture/INTEGRATION-ARCHITECTURE.md`](50-architecture/INTEGRATION-ARCHITECTURE.md) §3.2 and §3.3 now cite ADR-0005 as the normative serialization of the field sets they describe, and §3.3 records that `guardrail_category` (DEC-013 / ADR-0003) and `enforcement_state` (ADR-0004) are carried from the first release rather than added later. §3.4 gains a pointer to §3.8 distinguishing actions from ladder states.
- [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](50-architecture/EXTENSIBILITY-ARCHITECTURE.md) §5 (seam 3 — new decision action): the `warn` exclusion is generalized to `warn` **and** `disabled`, cites ADR-0004 for the underlying rule that an identifier may name a binding state or a decision action but never both, and records that a new action must declare its position in the `allow < log_only < redact < block` severity order because ladder states clamp against it.
- [`adrs/README.md`](50-architecture/adrs/README.md) — ADR-0004 and ADR-0005 added to the Current ADRs index; the **API + SDK integration model** backlog candidate marked partially fulfilled by ADR-0005, with its client-side half explicitly still open.

### Deprecated
- _Nothing._

### Removed
- _Nothing._

### Fixed
- The `Warn` ladder state was previously unimplementable: no document said what action a `Warn` binding produced, and `EXTENSIBILITY-ARCHITECTURE.md` §5 forbids a `warn` action. FR-051 marks all five states MVP scope. ADR-0004 §3 closes this.
- The `Disabled` ladder state had no defined evaluation or audit semantics, leaving no evidence distinguishing a deliberately disabled workload from a removed connector — a gap directly relevant to the connector-bypass risk (`TECHNICAL-RISKS.md` R1) and to AGR-018's requirement that enforcement posture be recorded and visible. ADR-0004 §4 closes this.
- An internal inconsistency in ADR-0005's own first draft was corrected before publication: it had classified a new response-enum value as a *minor* change on the strength of a fail-closed client rule, which contradicts `EXTENSIBILITY-ARCHITECTURE.md` §5's position that "unknown-action tolerance is **not** acceptable for enforcement clients." Bind-time compatibility refusal is now primary and the fail-closed rule is a backstop; a new action is a **major** contract change following the full seam-3 process.

### Security
- _Nothing._ Both ADRs are contract-shape decisions; the transport, identity, and encryption controls of `SECURITY-ARCHITECTURE.md` are cited, not changed.

---

## [2026-07-24] — Guardrail-category catalog and extensibility architecture

**Change type:** Path A ([AGR-021](00-source-of-truth/AGREEMENTS.md#agr-021--guardrail-category-taxonomy-and-extension-model) appended to the L1 register) propagated through the derived set
**Trigger:** [DEC-009](00-source-of-truth/DECISIONS.md#dec-009--adopt-the-guardrail-category-catalog-as-directional-vision) — adopt an eighteen-category guardrail taxonomy as directional vision and formalize the architecture's growth model, so incremental guardrail additions are governed instead of ad hoc.
**Owner:** Product lead, Technical lead, Security and governance lead (joint)

### Added
- [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) **AGR-021** — Guardrail-category taxonomy and extension model (register is now AGR-001–AGR-021).
- [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) **DEC-009** — adopt the catalog as directional vision; Vision-status entries stay outside the DEC-008 estimation basis.
- [`OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md) **OQ-017** (token/cost metadata in the evaluation contract), **OQ-018** (stage-vocabulary expansion beyond the reserved seven), **OQ-019** (reason-code taxonomy and category as an audit/metric dimension), under a new theme "I. Guardrail-catalog growth and contract vocabulary".
- [`20-product/GUARDRAIL-CATALOG.md`](20-product/GUARDRAIL-CATALOG.md) — **new L2 register**: eighteen change-controlled categories `GR-001`–`GR-018` with enforcement layer, control types, stages, evaluator family, four-value phase status (`Core` / `Later phase — committed` / `Vision — uncommitted` / `Out-of-boundary`), illustrative item tables, an out-of-boundary and exclusion map with named external owners, and a vision-status policy-pack sketch.
- [`50-architecture/EXTENSIBILITY-ARCHITECTURE.md`](50-architecture/EXTENSIBILITY-ARCHITECTURE.md) — **new L3 document**: the four extension seams (new stage / new engine / new action / new connector) with per-seam process, inherited constraints, conformance rules, and governance gates; the category attachment model; and the catalog graduation pipeline (§10).
- [`50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md`](50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md) — **Proposed** ADR recording the design and alternatives.
- [`50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md`](50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md) **NFR-MAINT-003** — bounded evaluator and category sprawl.
- [`50-architecture/TECHNICAL-RISKS.md`](50-architecture/TECHNICAL-RISKS.md) **§4.9** — category-taxonomy drift and evaluator sprawl (risk #9).
- [`50-architecture/TRADE-OFFS.md`](50-architecture/TRADE-OFFS.md) **§10** — guardrail-coverage breadth vs. contract surface and evaluation latency (decision #7); subsequent sections renumbered.
- [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md) **RSK-010** — guardrail catalog misread as commitment / taxonomy drift.
- [`70-governance/CLAIMS-REGISTER.md`](70-governance/CLAIMS-REGISTER.md) §7 — new **Not approved** row: "covers all eighteen guardrail categories / complete AI-risk coverage", with approved substitute wording.
- [`70-governance/GLOSSARY.md`](70-governance/GLOSSARY.md) — new terms: guardrail category, guardrail item, control type, enforcement layer, extension seam, graduation, policy pack, Vision — uncommitted.

### Changed
- [`TRACEABILITY-MATRIX.md`](90-archive/00-source-of-truth--TRACEABILITY-MATRIX--archived-2026-07-26.md) — AGR-021 row, ADR-0002 backlog row, coverage-check row for the new Not-approved claim; register-range text updated to AGR-001–AGR-021 (also in [`DECISIONS.md`](00-source-of-truth/DECISIONS.md) and [`OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md)).
- [`20-product/CAPABILITIES.md`](20-product/CAPABILITIES.md) — new "Guardrail-category cross-reference" section (GR→CAP mapping; "the catalog classifies, CAPABILITIES commits").
- [`20-product/PRODUCT-ROADMAP.md`](20-product/PRODUCT-ROADMAP.md) — new "Guardrail-category coverage by phase" view; Vision/Out-of-boundary categories appear in no phase row.
- [`50-architecture/CONTAINER-ARCHITECTURE.md`](50-architecture/CONTAINER-ARCHITECTURE.md) §5, [`REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md) §13–§14 (new `Guardrail category` column on FR-078–FR-094), [`SECURITY-ARCHITECTURE.md`](50-architecture/SECURITY-ARCHITECTURE.md) §10–§11 (GR-009/GR-010 boundary reconciliation), [`DEPLOYMENT-ARCHITECTURE.md`](50-architecture/DEPLOYMENT-ARCHITECTURE.md) §5 (catalog owns the category×mode statement), [`OPERABILITY.md`](50-architecture/OPERABILITY.md) §3/§6.1 (category as planned metric dimension, gated on OQ-019), [`ARCHITECTURE-REPORT.md`](50-architecture/ARCHITECTURE-REPORT.md) §3.3/§7.2, and [`TECHNICAL-RISKS.md`](50-architecture/TECHNICAL-RISKS.md) §4.5/§4.8 — cross-references to the catalog and extension model added.
- [`70-governance/DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) §4 — documentation-set map rows for both new documents; [`70-governance/REFERENCES.md`](70-governance/REFERENCES.md) — internal rows for both new documents, §11 retitled to include the NIST AI RMF orientation reference; [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md) RSK-006 — catalog noted as the over-expansion mitigation channel; [`50-architecture/adrs/README.md`](50-architecture/adrs/README.md) — ADR-0002 indexed as Proposed.
- [`README.md`](README.md) §5 and [`DOCUMENT-MAP.md`](DOCUMENT-MAP.md) — both new documents and ADR-0002 registered in the navigation set.

### Deprecated
- _Nothing._

### Removed
- _Nothing._

### Fixed
- _Nothing._

### Security
- _Nothing._

---

## [2026-07-24] — Initial documentation set

**Change type:** Path A (baseline authorship — establishes the documentation set itself, not a correction against a prior version)
**Trigger:** Initial distillation of the original solution charter (see [`PROVENANCE.md`](00-source-of-truth/PROVENANCE.md)) into the accepted [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) register, followed by full derivation of the layered documentation set.
**Owner:** Product lead, Technical lead, Security and governance lead (joint)

### Added

Generated the complete initial `solution-docs/` documentation set from the original solution charter (see [`PROVENANCE.md`](00-source-of-truth/PROVENANCE.md)), distilled first into the authoritative agreements register and then derived outward into every downstream layer. This is the founding entry of the changelog: it establishes the baseline against which all future changes are tracked.

- **Source-of-truth agreements (`00-source-of-truth/`)** — [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) (AGR-001 through AGR-021, the authoritative L1 register), [`CONSTRAINTS.md`](00-source-of-truth/CONSTRAINTS.md), [`DECISIONS.md`](00-source-of-truth/DECISIONS.md), [`ASSUMPTIONS.md`](00-source-of-truth/ASSUMPTIONS.md), and [`OPEN-QUESTIONS.md`](00-source-of-truth/OPEN-QUESTIONS.md).
- **Charter (`10-charter/`)** — [`CHARTER.md`](10-charter/CHARTER.md), [`PRINCIPLES.md`](10-charter/PRINCIPLES.md), [`SCOPE.md`](10-charter/SCOPE.md), and [`SUCCESS-CRITERIA.md`](10-charter/SUCCESS-CRITERIA.md).
- **Product materials (`20-product/`)** — [`CAPABILITIES.md`](20-product/CAPABILITIES.md), [`PRODUCT-OVERVIEW.md`](20-product/PRODUCT-OVERVIEW.md), [`USE-CASES.md`](20-product/USE-CASES.md), [`TARGET-USERS.md`](20-product/TARGET-USERS.md), [`VALUE-PROPOSITION.md`](20-product/VALUE-PROPOSITION.md), [`DIFFERENTIATORS.md`](20-product/DIFFERENTIATORS.md), [`PROBLEM-STATEMENT.md`](20-product/PROBLEM-STATEMENT.md), [`PRODUCT-ROADMAP.md`](20-product/PRODUCT-ROADMAP.md), and [`FAQ.md`](20-product/FAQ.md).
- **Executive materials (`30-executive/`)** — [`EXECUTIVE-BRIEF.md`](30-executive/EXECUTIVE-BRIEF.md), [`EXPECTED-OUTCOMES.md`](30-executive/EXPECTED-OUTCOMES.md), [`DECISION-REQUEST.md`](30-executive/DECISION-REQUEST.md), [`BUSINESS-CASE.md`](30-executive/BUSINESS-CASE.md), [`INVESTMENT-SUMMARY.md`](30-executive/INVESTMENT-SUMMARY.md), and [`RISKS-AND-DEPENDENCIES.md`](30-executive/RISKS-AND-DEPENDENCIES.md).
- **Sales-deck inputs (`40-sales-deck/`)** — [`DECK-BRIEF.md`](40-sales-deck/DECK-BRIEF.md), [`SLIDE-OUTLINE.md`](40-sales-deck/SLIDE-OUTLINE.md), [`SALES-NARRATIVE.md`](40-sales-deck/SALES-NARRATIVE.md), [`OBJECTION-HANDLING.md`](40-sales-deck/OBJECTION-HANDLING.md), [`PROOF-POINTS.md`](40-sales-deck/PROOF-POINTS.md), [`CUSTOMER-PAIN-POINTS.md`](40-sales-deck/CUSTOMER-PAIN-POINTS.md), [`AUDIENCE-AND-PERSONAS.md`](40-sales-deck/AUDIENCE-AND-PERSONAS.md), [`BENEFITS-AND-OUTCOMES.md`](40-sales-deck/BENEFITS-AND-OUTCOMES.md), [`CALL-TO-ACTION.md`](40-sales-deck/CALL-TO-ACTION.md), and [`VISUAL-DIRECTION.md`](40-sales-deck/VISUAL-DIRECTION.md).
- **Architecture report and supporting docs (`50-architecture/`)** — [`ARCHITECTURE-REPORT.md`](50-architecture/ARCHITECTURE-REPORT.md), [`SYSTEM-CONTEXT.md`](50-architecture/SYSTEM-CONTEXT.md), [`COMPONENT-ARCHITECTURE.md`](50-architecture/COMPONENT-ARCHITECTURE.md), [`CONTAINER-ARCHITECTURE.md`](50-architecture/CONTAINER-ARCHITECTURE.md), [`DATA-ARCHITECTURE.md`](50-architecture/DATA-ARCHITECTURE.md), [`DEPLOYMENT-ARCHITECTURE.md`](50-architecture/DEPLOYMENT-ARCHITECTURE.md), [`INTEGRATION-ARCHITECTURE.md`](50-architecture/INTEGRATION-ARCHITECTURE.md), [`SECURITY-ARCHITECTURE.md`](50-architecture/SECURITY-ARCHITECTURE.md), [`NON-FUNCTIONAL-REQUIREMENTS.md`](50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md), [`REQUIREMENTS.md`](50-architecture/REQUIREMENTS.md), [`OPERABILITY.md`](50-architecture/OPERABILITY.md), [`FEASIBILITY-ASSESSMENT.md`](50-architecture/FEASIBILITY-ASSESSMENT.md), [`TECHNICAL-RISKS.md`](50-architecture/TECHNICAL-RISKS.md), [`TRADE-OFFS.md`](50-architecture/TRADE-OFFS.md), and the ADR log ([`adrs/README.md`](50-architecture/adrs/README.md), [`adrs/ADR-0001-example-decision.md`](50-architecture/adrs/ADR-0001-example-decision.md)).
- **Delivery plan (`60-delivery/`)** — [`IMPLEMENTATION-STRATEGY.md`](60-delivery/IMPLEMENTATION-STRATEGY.md), [`MILESTONES.md`](60-delivery/MILESTONES.md), [`RELEASE-CRITERIA.md`](60-delivery/RELEASE-CRITERIA.md), [`VALIDATION-PLAN.md`](60-delivery/VALIDATION-PLAN.md), [`DEPENDENCIES.md`](60-delivery/DEPENDENCIES.md), and [`TEAM-AND-OWNERSHIP.md`](60-delivery/TEAM-AND-OWNERSHIP.md).
- **Governance (`70-governance/`)** — [`DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md), [`CLAIMS-REGISTER.md`](70-governance/CLAIMS-REGISTER.md), [`RISK-REGISTER.md`](70-governance/RISK-REGISTER.md), [`GLOSSARY.md`](70-governance/GLOSSARY.md), [`REFERENCES.md`](70-governance/REFERENCES.md), and this changelog.
- **Agent-input contracts (`80-agent-inputs/`)** — [`README.md`](80-agent-inputs/README.md), [`OUTPUT-CONTRACTS.md`](80-agent-inputs/OUTPUT-CONTRACTS.md), [`CHARTER-AGENT.md`](80-agent-inputs/CHARTER-AGENT.md), [`ARCHITECTURE-REVIEW-AGENT.md`](80-agent-inputs/ARCHITECTURE-REVIEW-AGENT.md), [`EXECUTIVE-BRIEF-AGENT.md`](80-agent-inputs/EXECUTIVE-BRIEF-AGENT.md), and [`SALES-DECK-AGENT.md`](80-agent-inputs/SALES-DECK-AGENT.md).
- **Archive scaffold (`90-archive/`)** — [`README.md`](90-archive/README.md), reserved for future superseded and archived document versions per the lifecycle defined in [`DOCUMENT-GOVERNANCE.md §10`](70-governance/DOCUMENT-GOVERNANCE.md#10-document-lifecycle-and-archival).

**Also established:** the **L1–L5 authority model** governing the entire documentation set — Authoritative, Defining, Analytical, Communicative, and Generated — together with the non-contradiction rule, per-area ownership, and the change-control process (Path A for amending an L1 register, Path B for editing a derived document), all defined in [`DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md). Every document added in this entry declares its authority level in its own header per that model, and every document is traceable back to one or more entries in [`AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md).

| Directory | Authority level(s) added | Scope |
| --- | --- | --- |
| [`00-source-of-truth/`](00-source-of-truth/) | L1 | Agreements, constraints, decisions, assumptions, open questions |
| [`10-charter/`](10-charter/) | L2 | Charter, principles, scope, success criteria |
| [`20-product/`](20-product/) | L2 (`CAPABILITIES.md`) / L4 (remainder) | Product-facing overview, use cases, roadmap, FAQ |
| [`30-executive/`](30-executive/) | L3 (business case, investment, risks/dependencies) / L4 (brief, outcomes, decision request) | Executive narrative and analysis |
| [`40-sales-deck/`](40-sales-deck/) | L4 | Sales-enablement inputs |
| [`50-architecture/`](50-architecture/) | L2 (NFRs) / L3 (remainder) | Architecture report and supporting analysis |
| [`60-delivery/`](60-delivery/) | L3 | Delivery and implementation planning |
| [`70-governance/`](70-governance/) | L2 (governance, claims, glossary) / L3 (risk register) / cross-cutting (this changelog) | Documentation-set governance |
| [`80-agent-inputs/`](80-agent-inputs/) | L5 | Machine-consumable agent digests |
| [`90-archive/`](90-archive/) | — (scaffold only) | Reserved for archived document versions |

### Changed
- Not applicable — this is the founding entry of the documentation set.

### Deprecated
- Not applicable.

### Removed
- Not applicable.

### Fixed
- Not applicable.

### Security
- Not applicable — see [`50-architecture/SECURITY-ARCHITECTURE.md`](50-architecture/SECURITY-ARCHITECTURE.md) for the security requirements established in this initial set.

---

## Related documents

| Document | Relationship |
| --- | --- |
| [`00-source-of-truth/AGREEMENTS.md`](00-source-of-truth/AGREEMENTS.md) | Authoritative (L1) register this changelog traces every substantive addition back to. |
| [`00-source-of-truth/DECISIONS.md`](00-source-of-truth/DECISIONS.md) | Chronological log of product/delivery decisions (`DEC-NNN`) — distinct from this file, which logs documentation-set edits. |
| [`70-governance/DOCUMENT-GOVERNANCE.md`](70-governance/DOCUMENT-GOVERNANCE.md) | Defines the L1–L5 authority model, the Path A/Path B change-control process, and the required fields for entries in this changelog (§9). |
| [`70-governance/CLAIMS-REGISTER.md`](70-governance/CLAIMS-REGISTER.md) | Approved/rejected external-facing claim patterns enforced across every document added in this entry. |
| [`70-governance/RISK-REGISTER.md`](70-governance/RISK-REGISTER.md) | Actively tracked product risk register introduced in this initial set. |
| [`10-charter/CHARTER.md`](10-charter/CHARTER.md) | The L2 charter this entire documentation set is derived from. |
