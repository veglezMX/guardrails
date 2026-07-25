# Close All Open Questions + Doc Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 20 open questions (OQ-001…019 + the mandatory-policy-authority question raised by the serving-model spec) by recording a real decision for each, accept ADR-0002 and run its graduation-exercise validation, absorb the two scope expansions the owner chose (multi-region availability at GA; multi-region active-active committed), then refactor every derived document so the whole set is internally consistent.

**Architecture:** This is a documentation/governance repository, not code. "Tasks" create or edit Markdown; "tests" are consistency checks run with `grep`/`rg` (cross-references resolve, no derived document contradicts an L1 document, every new agreement/decision has a traceability row, every closed OQ links to its decision). Source-of-truth precedence is unchanged: `AGREEMENTS.md` (L1) wins; everything else is derived and must not contradict it. All new decisions land with **Status: Proposed** and an **owner-to-ratify** note — this plan records decisions; it does not grant them L1 authority.

**Tech Stack:** Markdown, the existing `solution-docs/` document taxonomy, `git` for frequent commits, `grep`/`rg` for verification.

## Global Constraints

- **No invented facts.** Every decision is either (a) an owner-supplied answer captured verbatim from the interview, (b) a design resolution derived from an existing agreement, or (c) a governed deferral with a named trigger + owner. Where a numeric threshold or specific value is genuinely the owner's call, write `TBD — owner to set at ratification`, never a fabricated number.
- **Everything lands `Status: Proposed`** with `Owners:` and, where the source supplies no date, `Date: 2026-07-24`.
- **L1/seed changes are register-level (Path A).** Editing `AGREEMENTS.md`, or recording supersession of a `seed.md §6.3` exclusion, must be flagged in the entry as requiring owner ratification. Do not silently rewrite `seed.md` (it is the immutable origin document); record supersession in a decision and update the derived registers.
- **One logical evaluation contract (AGR-006)** and **extend-don't-fork the domain model (AGR-017)** are invariants every decision must respect.
- **Claims discipline (AGR-009/010/018)** holds: no external-facing wording claims delivered/committed coverage that these decisions do not support.
- **Commit after every task** with a conventional-commit message. Never bundle two tasks into one commit.
- **Date:** use `2026-07-24` for all new entries (do not read the system clock).

---

## Decision allocation (the map every task refers to)

| OQ / item | Owner answer or resolution | Artifact | New ID |
|---|---|---|---|
| OQ-001 enforcement posture | Cooperative front door; hard-enforcement routed to B/C | DECISIONS entry (from serving spec) | **DEC-010** |
| mandatory-policy authority (new) + OQ-012 inheritance | Security-role pins; app-teams strengthen-only; inheritance v0 = mandatory-pin layering | DECISIONS entry | **DEC-011** |
| OQ-014 observe→enforce criteria | Objective promotion gate (framework; thresholds owner-set) | DECISIONS entry | **DEC-012** |
| OQ-019 reason-code taxonomy + category audit dimension | Category-namespaced reason codes; `guardrail_category` first-class on decision | **ADR-0003** + DECISIONS pointer | **ADR-0003 / DEC-013** |
| OQ-017 token/cost metadata + OQ-018 stage vocabulary | Contract stays lean: no cost metadata now; seven stages frozen | DECISIONS entry | **DEC-014** |
| OQ-012 signing + OQ-013 evaluator-plugin governance | Signing deferred to Phase 4 (Mode C); no third-party in-process code until a Phase-5 security-gated plugin model | DECISIONS entry | **DEC-015** |
| OQ-004/005/006/007 Phase-5 agentic | Deferred to Phase 5; scoped when Phase 5 approved | DECISIONS entry | **DEC-016** |
| OQ-002/008/009/010/011/015 business posture | PrivateLink on-demand; EN-only; 2-account-until-triggered; air-gap trigger-gated; free-MVP-meter-Phase-3; SOC 2 on trigger | DECISIONS entry | **DEC-017** |
| OQ-003 regions | Multi-region availability at GA, tenant-pinned (⚠ AGR-013 revisit) | DECISIONS entry + AGR-013 amendment + new CAP | **DEC-018** |
| OQ-016 active-active | Committed later phase (⚠ supersedes seed §6.3 exclusion; alters DEC-008 basis) | DECISIONS entry + new CAP + registers | **DEC-019** |
| ADR-0002 catalog/extension | Accept + run first graduation exercise | ADR status flip + graduation record | — |

New capabilities: **CAP-035** (multi-region availability + per-tenant residency enforcement), **CAP-036** (multi-region active-active availability).

---

## Phase 0 — Baseline

### Task 0: Capture a green baseline of current cross-references

**Files:** none created; establishes the verification harness.

- [ ] **Step 1: Record the current OQ count and statuses**

Run: `rg -n "^## OQ-0" solution-docs/00-source-of-truth/OPEN-QUESTIONS.md | wc -l`
Expected: `19` (OQ-001…019).

- [ ] **Step 2: Snapshot broken-link baseline**

Run: `rg -o "\]\(\.\./[^)]+\)" solution-docs/00-source-of-truth/*.md | wc -l`
Expected: a number > 0; note it. (Used later to confirm we did not introduce dangling links.)

- [ ] **Step 3: Confirm working tree is clean or changes are known**

Run: `git status --short`
Expected: only the previously-created `FEATURE-BACKLOG.md`, `README.md`, and spec/plan files are dirty/untracked. Commit those first if desired.

- [ ] **Step 4: Commit any pending scaffolding**

```bash
git add solution-docs/60-delivery/FEATURE-BACKLOG.md solution-docs/README.md docs/superpowers/
git commit -m "docs: serving-model spec, feature backlog, close-OQ plan"
```

---

## Phase 1 — Record decisions (DECISIONS.md: DEC-010 … DEC-019)

Every task in this phase appends one entry to `solution-docs/00-source-of-truth/DECISIONS.md`, adds its row to the "Decision log summary" table at the top, and marks the corresponding OQ(s) resolved is deferred to Phase 4 (so all decisions exist before OQ entries point at them). Follow the existing entry shape exactly: **ID / Date / Status / Decision / Context / Related agreements / (Related ADRs)**.

### Task 1: DEC-010 — Serving model: Mode A front door as a developer service

**Files:**
- Modify: `solution-docs/00-source-of-truth/DECISIONS.md` (append entry + summary-table row)

**Interfaces:**
- Produces: anchor `#dec-010--serving-model-mode-a-front-door-as-a-developer-service` used by OQ-001 resolution and SERVING-MODEL.md.

- [ ] **Step 1: Append the entry** (Status: Proposed; Owners: Product lead, technical lead)

Decision: *AI Guardrails is served as a developer service whose front door is Mode A (Remote Evaluation): the consumer keeps their gateway, model provider, and data path, installs a cooperative connector or calls the hosted HTTP Evaluation API, and imports a policy; the platform hosts evaluation, the policy store, and the audited decision. Modes B and C are graduation paths that answer specific buyer objections, not competing front doors.*
Context: resolves OQ-001 for the front door; grounded in AGR-001/002/003/006/007, DEC-004, AGR-020, UC-001; enforcement is cooperative-only at the front door (never a hard boundary, AGR-018), hard-enforcement seekers routed to Mode B / network-IAM posture.
Related agreements: AGR-001, AGR-002, AGR-003, AGR-006, AGR-007, AGR-018, AGR-020. Closes: OQ-001.

- [ ] **Step 2: Add summary-table row** for DEC-010 (Accepted→**Proposed**).

- [ ] **Step 3: Verify anchor + table row**

Run: `rg -n "DEC-010" solution-docs/00-source-of-truth/DECISIONS.md`
Expected: at least 2 hits (summary row + heading).

- [ ] **Step 4: Commit**

```bash
git add solution-docs/00-source-of-truth/DECISIONS.md
git commit -m "docs(decisions): add DEC-010 serving-model front door (closes OQ-001)"
```

### Task 2: DEC-011 — Policy governance classes + mandatory-policy authority

**Files:** Modify `solution-docs/00-source-of-truth/DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Security & governance lead, product lead)

Decision: *Curated policy packs ship with three governance classes — Mandatory (pinned), Recommended/overridable, and consumer-authored (Own). The effective policy for a workload = the mandatory base (locked) layered with consumer overrides/additions (inheritance v0). Authority to mark a policy Mandatory rests with the security/governance role at org/tenant scope; application/workload owners may strengthen a mandatory policy (earlier enforcement, tighter thresholds) but may never disable, unbind, downgrade below the pinned version floor, or weaken it, and a tenant admin cannot override an org-mandatory policy. Cross-posture semantics: in cooperative posture "mandatory" means the platform records the binding as required and refuses to attest compliance when absent (a governance guarantee, not a physical one); platform-managed posture enforces at the provider path; network/IAM posture physically prevents bypass.*
Context: closes the mandatory-policy-authority question raised by the serving-model spec §3 and the inheritance half of OQ-012; grounded in AGR-005, AGR-011, AGR-017, AGR-018, AGR-019; authority split mirrors TARGET-USERS §4. Note: candidate for elevation to an Agreement (L1) at ratification — see serving spec §8.
Related agreements: AGR-005, AGR-011, AGR-017, AGR-018, AGR-019. Closes: mandatory-authority (new); partially OQ-012 (inheritance).

- [ ] **Step 2: Summary-table row.**
- [ ] **Step 3: Verify** `rg -n "DEC-011" .../DECISIONS.md` ≥ 2 hits.
- [ ] **Step 4: Commit** `docs(decisions): add DEC-011 policy governance + mandatory authority`

### Task 3: DEC-012 — Observe→enforce promotion criteria (closes OQ-014)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Security & governance lead, workload owners)

Decision: *Promotion of a workload binding from Observe to an enforcing state (Warn/Redact/Block) requires an objective, auditable go/no-go gate with these dimensions: (1) a minimum observation window — `TBD — owner to set at ratification` (e.g. calendar days AND/OR a minimum evaluation count); (2) a false-positive rate at or below a threshold — `TBD — owner to set`; (3) measured evaluation latency within the workload's configured budget; (4) explicit recorded sign-off by both the workload owner and the security/governance role. Promotion is an explicit, auditable action (AGR-011), never an implicit side effect.*
Context: closes OQ-014; needed before Phase 2 onboarding scales; framework is fixed, numeric thresholds are the owner's to set (no invented numbers).
Related agreements: AGR-011, AGR-012. Closes: OQ-014.

- [ ] **Step 2–4:** summary row; `rg` verify; commit `docs(decisions): add DEC-012 observe→enforce criteria (closes OQ-014)`.

### Task 4: DEC-013 — Reason-code taxonomy & category audit dimension (closes OQ-019, points to ADR-0003)

**Files:** Modify `DECISIONS.md` (the ADR itself is Task 11)

- [ ] **Step 1: Append entry** (Owners: Technical lead, security & governance lead)

Decision: *The canonical reason-code taxonomy is category-namespaced: every reason code is emitted as `GR-0NN:<evaluator-family>:<code>`, and the decision record gains `guardrail_category` as a first-class field. Guardrail category thereby becomes a first-class audit/metric dimension, making per-category reporting and catalog→decision traceability implementable without forking the decision structure (AGR-017) or the one logical contract (AGR-006). Design detail and alternatives are recorded in ADR-0003.*
Context: closes OQ-019 — the highest-leverage question; also supplies the catalog→decision traceability AGR-021 needs.
Related agreements: AGR-006, AGR-017, AGR-021. Related ADRs: ADR-0003 (Proposed). Closes: OQ-019.

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-013 reason-code taxonomy (closes OQ-019)`.

### Task 5: DEC-014 — Evaluation-contract stability (closes OQ-017, OQ-018)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Technical lead, architecture lead)

Decision: *The logical evaluation contract stays lean for the committed feature set: (a) it is NOT extended with token/cost metadata — resource/cost controls (catalog GR-012) remain gateway-native and control-plane-side; this reopens only when a GR-012 catalog entry graduates. (b) The evaluation-stage vocabulary stays the seven reserved stages; no new stage is added until a graduated category requires one, and then only via the evaluation-stage extension seam under AGR-006 backward-compatibility.*
Context: closes OQ-017 and OQ-018; keeps the contract stable across all three modes.
Related agreements: AGR-006, AGR-017. Related ADRs: ADR-0002. Closes: OQ-017, OQ-018.

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-014 evaluation-contract stability (closes OQ-017, OQ-018)`.

### Task 6: DEC-015 — Evaluator-plugin & policy-signing governance (closes OQ-013, remainder of OQ-012)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Technical lead, security & governance lead)

Decision: *(a) No third-party or customer-supplied evaluator code runs in-process in the MVP or the committed phases; "new engine" means a provider-authored adapter behind the standard evaluator contract. Customer-specific evaluator plugins are a Phase-5 vision, admissible only behind a mandatory security + compatibility review gate (isolation/sandboxing, signed artifacts, contract-conformance tests). (b) Policy signing is deferred to Phase 4, gated by the first Mode C (customer-managed runtime) deployment that distributes policy artifacts outside the hosted control plane.*
Context: closes OQ-013 and the signing half of OQ-012; preserves the adapter discipline (evaluator-sprawl risk) and AGR-005 portability.
Related agreements: AGR-004, AGR-005, AGR-006, AGR-015. Closes: OQ-013; OQ-012 (signing).

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-015 evaluator-plugin & signing governance (closes OQ-013, OQ-012)`.

### Task 7: DEC-016 — Phase-5 agentic scope timing (closes OQ-004, OQ-005, OQ-006, OQ-007)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Technical lead, product lead)

Decision: *Streaming output evaluation (OQ-004), retrieval-content evaluation and indirect-prompt-injection detection (OQ-005), tool-call/tool-result validation and MCP authorization scope (OQ-006), and human-approval workflows (OQ-007) are deferred to Phase 5 and scoped only when Phase 5 is separately approved (AGR-020, DEC-006/007). Until then, the corresponding workload attributes exist but carry a documented "not yet covered" limitation, and no collateral may imply coverage.*
Context: closes OQ-004/005/006/007 as governed deferrals; Phase 5 remains a separate expansion, not MVP scope.
Related agreements: AGR-010, AGR-020. Closes: OQ-004, OQ-005, OQ-006, OQ-007.

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-016 Phase-5 agentic timing (closes OQ-004/005/006/007)`.

### Task 8: DEC-017 — Deployment & commercial posture (closes OQ-002, OQ-008, OQ-009, OQ-010, OQ-011, OQ-015)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Product lead, platform-ops lead, security & governance lead)

Decision, as a labelled list (owner answers captured verbatim):
- **OQ-002 PrivateLink:** public TLS+IAM endpoint at GA; PrivateLink added on-demand, trigger = first signed customer with a network-egress restriction.
- **OQ-008 Languages:** English only at launch; multilingual deferred with a documented non-English limitation; trigger = first committed non-English workload.
- **OQ-009 Accounts:** remain two-account (non-prod + prod); multi-region availability is served with regional resources inside the prod account, not new accounts; move to 3+ on first external customer, compliance requirement, or independent security ownership.
- **OQ-010 Air-gap:** air-gapped installation stays committed-but-undated (CAP-033, Phase-4-adjacent); dedicated-tenant runtimes have no committed phase; trigger = signed regulated/air-gapped customer.
- **OQ-011 Commercial:** no metering/billing at MVP (internal + design partners); usage metering (CAP-034) built in Phase 3 once usage patterns are observed; marketplace stays vision.
- **OQ-015 Compliance:** no certification claimed at MVP; SOC 2 Type II pursued on the first external customer that contractually requires it; "full compliance certification" stays out-of-boundary.
Context: owner-supplied business decisions captured in the 2026-07-24 interview; each is a governed deferral with an explicit trigger.
Related agreements: AGR-013, AGR-018, AGR-020; seed.md §6.2/§6.3. Closes: OQ-002, OQ-008, OQ-009, OQ-010, OQ-011, OQ-015.

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-017 deployment & commercial posture (closes OQ-002/008/009/010/011/015)`.

### Task 9: DEC-018 — Multi-region availability at GA (closes OQ-003; ⚠ amends AGR-013)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Architecture lead, product lead, platform-ops lead; **register-level — owner ratification required**)

Decision: *The platform offers multi-region availability from GA, with each tenant pinned to a single processing region (not active-active — see DEC-019). This supersedes the single-region reading of the minimal-MVP posture and pulls per-tenant data-residency enforcement into the committed core (new CAP-035). The two-account model is retained (DEC-017/OQ-009); regionality is expressed through regional resources, not additional accounts, until an OQ-009 trigger fires. AGR-013 is amended to record this (Task 12).*
Context: owner chose multi-region-from-GA over the minimal single-region default; this is a deliberate scope expansion with an AGR-013 implication and a residency-enforcement dependency (formerly OQ-003's unresolved backstop).
Related agreements: AGR-006, AGR-008, AGR-013. Closes: OQ-003.

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-018 multi-region availability at GA (closes OQ-003)`.

### Task 10: DEC-019 — Multi-region active-active committed (closes OQ-016; ⚠ supersedes seed §6.3)

**Files:** Modify `DECISIONS.md`

- [ ] **Step 1: Append entry** (Owners: Architecture lead, platform-ops lead, product lead; **register-level — owner ratification required**)

Decision: *Multi-region active-active availability (live cross-region failover / replication) is brought into committed scope as a later-phase capability (new CAP-036). This explicitly supersedes its listing as a `seed.md §6.3` exclusion and, per DEC-008, expands the defined-and-estimated feature set; the estimation basis and the exclusion lists in DEC-008, SCOPE.md, and CLAIMS-REGISTER.md are updated accordingly (Tasks 13–14, Phase 7). This is distinct from DEC-018: DEC-018 is region availability with tenant pinning; DEC-019 is live cross-region high availability.*
Context: owner chose to commit active-active as a later phase, accepting the estimation change; recorded as a deliberate expansion, the inverse of the risk-register "product over-expansion" caution, legitimate because explicitly chosen.
Related agreements: AGR-002, AGR-006; supersedes seed.md §6.3 (active-active exclusion). Related decisions: DEC-008 (estimation basis). Closes: OQ-016.

- [ ] **Step 2–4:** summary row; verify; commit `docs(decisions): add DEC-019 active-active committed (closes OQ-016)`.

---

## Phase 2 — ADRs

### Task 11: ADR-0003 — Reason-code taxonomy & guardrail category as an audit/metric dimension

**Files:**
- Create: `solution-docs/50-architecture/adrs/ADR-0003-reason-code-taxonomy-and-category-audit-dimension.md`
- Modify: `solution-docs/50-architecture/adrs/README.md` (register the ADR)

**Interfaces:** Produces the anchor DEC-013 and TRACEABILITY-MATRIX reference.

- [ ] **Step 1: Author the ADR** following the ADR-0002 template (Status/Context/Decision/Alternatives Considered/Consequences/Validation Required/Related Agreements/Related Documents). Status: **Proposed**.
  - Decision: category-namespaced reason code `GR-0NN:<evaluator-family>:<code>`; `guardrail_category` first-class on the decision record; per-category metrics + catalog→decision traceability.
  - Alternatives: (1) evaluator-local codes only (status quo — no cross-evaluator aggregation); (2) a flat global code registry (loses category mapping, invites collisions); (3) chosen — category-namespaced.
  - Consequences: enables per-category dashboards and AGR-021 traceability; cost = every evaluator adapter must map its native codes into the namespace.
  - Validation: a decision record for a real evaluation carries a resolvable `GR-0NN` and `guardrail_category`, and a per-category count can be produced.

- [ ] **Step 2: Register in adrs/README.md** ADR index/table.

- [ ] **Step 3: Verify** `rg -n "ADR-0003" solution-docs/50-architecture/adrs/README.md` ≥ 1 and file exists.

- [ ] **Step 4: Commit** `docs(adr): add ADR-0003 reason-code taxonomy (Proposed)`

### Task 12: Accept ADR-0002 + amend AGR-013

**Files:**
- Modify: `solution-docs/50-architecture/adrs/ADR-0002-guardrail-category-catalog-and-extension-model.md` (Status Proposed→**Accepted**, after graduation exercise Task 15 — see ordering note)
- Modify: `solution-docs/00-source-of-truth/AGREEMENTS.md` (AGR-013: add an implication + note recording the multi-region-availability decision DEC-018, retaining the two-account model)

> **Ordering note:** ADR-0002's own Validation Required §2 demands the first graduation exercise be executed before it is truly validated. Do the status flip in this task only *after* Phase 6 (Task 15) completes. If executing strictly top-to-bottom, split: leave ADR-0002 Proposed here, flip in Task 15's final step.

- [ ] **Step 1: Amend AGR-013** — append an implication: *"Multi-region availability is offered from GA with tenant-pinned single-region processing (DEC-018); this does not by itself trigger additional AWS accounts — the two-account model is retained until an OQ-009 trigger fires."* Mark the amendment `Proposed — owner ratification required`.

- [ ] **Step 2: Verify** `rg -n "DEC-018" solution-docs/00-source-of-truth/AGREEMENTS.md` ≥ 1.

- [ ] **Step 3: Commit** `docs(agreements): amend AGR-013 for multi-region availability (DEC-018)`

---

## Phase 3 — New capabilities

### Task 13: Add CAP-035 and CAP-036 to CAPABILITIES.md

**Files:** Modify `solution-docs/20-product/CAPABILITIES.md`

- [ ] **Step 1: Add CAP-035** — *Multi-region availability & per-tenant residency enforcement.* Status: `Core (Phases 1–2)` for availability; residency enforcement `Later phase — committed`. Traces: AGR-008, AGR-013 (amended), DEC-018. (Closes the OQ-003 "residency has no enforcement mechanism" gap.)

- [ ] **Step 2: Add CAP-036** — *Multi-region active-active availability.* Status: `Later phase — committed`. Traces: DEC-019 (supersedes seed §6.3 exclusion).

- [ ] **Step 3: Update the capability-count rollup** (the "Later-phase capabilities … `CAP-018`–`CAP-034` = 17" line becomes `CAP-018`–`CAP-036`; recompute counts).

- [ ] **Step 4: Verify** `rg -n "CAP-035|CAP-036" solution-docs/20-product/CAPABILITIES.md` = 2+ and the count line is arithmetically correct.

- [ ] **Step 5: Commit** `docs(capabilities): add CAP-035 multi-region availability, CAP-036 active-active`

---

## Phase 4 — Resolve the OQ entries

### Task 14: Mark all OQs resolved and update the summary table

**Files:** Modify `solution-docs/00-source-of-truth/OPEN-QUESTIONS.md`

- [ ] **Step 1:** For each of OQ-001…019, change the entry to `Status: Resolved` (or `Deferred — decided`, matching the register's vocabulary) and add a `Resolution:` line linking the closing DEC/ADR from the allocation map. Update the summary table's "Impact if unresolved" column to a "Resolved by" column value or append a Resolution column.

- [ ] **Step 2:** Add the mandatory-policy-authority item as a *resolved* entry (closed by DEC-011) so the register shows it was raised and closed, not left dangling.

- [ ] **Step 3: Verify every OQ links a decision**

Run: `for n in 001 002 003 004 005 006 007 008 009 010 011 012 013 014 015 016 017 018 019; do rg -q "OQ-$n" solution-docs/00-source-of-truth/OPEN-QUESTIONS.md && rg -q "DEC-0(1[0-9])|ADR-000[23]" <(rg -A6 "## OQ-$n" solution-docs/00-source-of-truth/OPEN-QUESTIONS.md) || echo "OQ-$n MISSING resolution"; done`
Expected: no `MISSING` lines.

- [ ] **Step 4: Commit** `docs(open-questions): resolve OQ-001…019 with decision links`

---

## Phase 5 — Serving-model promotion

### Task 15: Create SERVING-MODEL.md and wire PRODUCT-OVERVIEW

**Files:**
- Create: `solution-docs/20-product/SERVING-MODEL.md` (L4 derived, product-facing)
- Modify: `solution-docs/20-product/PRODUCT-OVERVIEW.md` (§8 "From onboarding to enforcement" — link the serving model + governance classes)
- Modify: `solution-docs/README.md` (index the new doc)

- [ ] **Step 1:** Author `SERVING-MODEL.md` from the design spec `docs/superpowers/specs/2026-07-24-serving-model-design.md` — identity, front door, managed/self split, integration contract, governance classes, graduation paths — as a derived view citing DEC-010/DEC-011. Header per house style (`Authority level: L4`, `Derived from`).
- [ ] **Step 2:** Add the PRODUCT-OVERVIEW cross-link + README row.
- [ ] **Step 3: Verify** links resolve: `rg -n "SERVING-MODEL" solution-docs/README.md solution-docs/20-product/PRODUCT-OVERVIEW.md`.
- [ ] **Step 4: Commit** `docs(product): add SERVING-MODEL.md, wire PRODUCT-OVERVIEW (DEC-010/011)`

---

## Phase 6 — First graduation exercise (ADR-0002 validation §2)

### Task 16: Take one Vision-status catalog entry through the §10 graduation pipeline

**Files:**
- Modify: `solution-docs/20-product/GUARDRAIL-CATALOG.md` (the chosen entry's status)
- Create/Modify: the OQ→AGR/DEC→CAP/FR trail per `EXTENSIBILITY-ARCHITECTURE.md §10`
- Modify: `ADR-0002` (record the exercise as satisfying Validation §2, then flip Status→Accepted)

- [ ] **Step 1:** Pick one `Vision — uncommitted` GR entry whose graduation is already implied by these decisions (candidate: a GR tied to OQ-019 metrics or to a now-committed axis). Document the pick and rationale.
- [ ] **Step 2:** Run it end-to-end through §10: source OQ → recorded DEC → CAP/FR classification, producing a recorded scope-change decision (a new DEC-020 if needed), *not* a silent promotion.
- [ ] **Step 3:** Update ADR-0002 Validation Required §2 to reference the exercise; flip ADR-0002 Status Proposed→**Accepted** (register-level; owner ratification note).
- [ ] **Step 4: Verify** `rg -n "Accepted" solution-docs/50-architecture/adrs/ADR-0002*.md` shows the status; graduation trail links resolve.
- [ ] **Step 5: Commit** `docs(governance): first graduation exercise; accept ADR-0002`

---

## Phase 7 — Doc refactor / consistency sweep

Each task below reconciles one derived document with the decisions above. These are independent of each other and safe to parallelize (subagent-per-doc), but all depend on Phases 1–6 being committed.

### Task 17: TRACEABILITY-MATRIX.md
**Files:** Modify `solution-docs/00-source-of-truth/TRACEABILITY-MATRIX.md`
- [ ] Flip the AGR-021 row `ADR` cell from `TBD (Proposed)` to `ADR-0002 (Accepted)`. Add rows for the AGR-013 amendment. Add matrix coverage for DEC-010…020 where the matrix tracks decisions. Verify: `rg -n "ADR-0002" TRACEABILITY-MATRIX.md` shows Accepted, no remaining `Proposed` for AGR-021. Commit `docs(traceability): close AGR-021 gap, add new decisions`.

### Task 18: FEATURE-BACKLOG.md
**Files:** Modify `solution-docs/60-delivery/FEATURE-BACKLOG.md`
- [ ] Move active-active from **axis 10 Out-of-boundary → Later — committed** (DEC-019, CAP-036). Update axis 10 regions rung (multi-region availability now Now/committed, DEC-018, CAP-035). Update axis 11 commercial (metering Phase 3 confirmed), axis 12 compliance (SOC 2 trigger), axis 2 (air-gap trigger). Every moved rung cites its DEC. Verify no rung still lists a now-committed item as Vision/Out-of-boundary that a DEC contradicts. Commit `docs(backlog): reconcile axes with DEC-017/018/019`.

### Task 19: PRODUCT-ROADMAP.md
**Files:** Modify `solution-docs/20-product/PRODUCT-ROADMAP.md`
- [ ] Add active-active (CAP-036) and residency enforcement (CAP-035) to the appropriate later phase; note multi-region availability at GA. Verify phase table references the new CAPs. Commit `docs(roadmap): place CAP-035/036 in phases`.

### Task 20: DEPLOYMENT-ARCHITECTURE.md
**Files:** Modify `solution-docs/50-architecture/DEPLOYMENT-ARCHITECTURE.md`
- [ ] Add a multi-region availability subsection (tenant-pinned, DEC-018) and an active-active later-phase note (DEC-019); reconcile the cross-cutting principles table. Verify. Commit `docs(architecture): multi-region availability + active-active notes`.

### Task 21: DATA-ARCHITECTURE.md
**Files:** Modify `solution-docs/50-architecture/DATA-ARCHITECTURE.md`
- [ ] Add per-tenant residency enforcement (CAP-035, DEC-018) and the `guardrail_category` decision-record field + category-namespaced reason codes (DEC-013/ADR-0003). Verify. Commit `docs(architecture): residency enforcement + reason-code field`.

### Task 22: CLAIMS-REGISTER.md
**Files:** Modify `solution-docs/70-governance/CLAIMS-REGISTER.md`
- [ ] Remove "multi-region active-active" from the excluded/never-claim list (now committed later-phase, DEC-019) and replace with the correct "committed, not yet delivered" wording. Add claim rows for multi-region availability (DEC-018) and category coverage/metrics (DEC-013). Verify no row still asserts active-active is excluded. Commit `docs(governance): reconcile claims register with DEC-018/019/013`.

### Task 23: SCOPE.md + DEC-008 estimation note
**Files:** Modify `solution-docs/10-charter/SCOPE.md`, `solution-docs/00-source-of-truth/DECISIONS.md` (DEC-008 note)
- [ ] In SCOPE.md, move active-active from exclusions to committed later-phase scope (DEC-019) and add multi-region availability (DEC-018). Append a note to DEC-008 recording that DEC-019 expands the estimated feature set (CAP-035/036 added). Verify: `rg -n "active-active" SCOPE.md` no longer under an "excluded" heading. Commit `docs(scope): absorb multi-region expansions into scope`.

### Task 24: TARGET-USERS.md + GLOSSARY
**Files:** Modify `solution-docs/20-product/TARGET-USERS.md`, `solution-docs/70-governance/GLOSSARY.md`
- [ ] TARGET-USERS §4: record the security/governance role's authority to pin mandatory policies (DEC-011). GLOSSARY: add "governance class", "mandatory policy", "policy inheritance (v0)", "category-namespaced reason code". Verify terms present. Commit `docs(product): mandatory-policy authority + glossary terms`.

### Task 25: GUARDRAIL-CATALOG.md reason-code note
**Files:** Modify `solution-docs/20-product/GUARDRAIL-CATALOG.md`
- [ ] Add a short note that each category's reason codes are namespaced `GR-0NN:…` (DEC-013/ADR-0003) and that category is an audit/metric dimension. Verify. Commit `docs(catalog): category-namespaced reason codes note`.

---

## Phase 8 — Final consistency verification

### Task 26: Whole-set consistency gate

**Files:** none (verification only) + a short `docs/superpowers/plans/CLOSEOUT.md` summary.

- [ ] **Step 1: No dangling links** — `rg -o "\]\(([^)]+)\)" -r '$1' solution-docs/**/*.md` piped through an existence check; expected: every relative path resolves.
- [ ] **Step 2: No open OQs remain** — `rg -n "Status:\s*Open" solution-docs/00-source-of-truth/OPEN-QUESTIONS.md`; expected: no matches.
- [ ] **Step 3: No derived doc contradicts a now-committed decision** — `rg -n "active-active" solution-docs | rg -i "exclud|out-of-boundary|never"`; expected: only historical/seed references clearly marked as superseded.
- [ ] **Step 4: Every new DEC/ADR has a traceability row** — cross-check DEC-010…020, ADR-0002 (Accepted), ADR-0003 against TRACEABILITY-MATRIX.md.
- [ ] **Step 5: Every OQ has a resolution link** — re-run Task 14 Step 3 loop; expected: no `MISSING`.
- [ ] **Step 6: Write CLOSEOUT.md** — a one-page index: each OQ → closing decision → affected docs. Commit `docs: OQ close-out summary + final consistency gate`.

---

## Self-review checklist (run before handing off)

- **Coverage:** every OQ-001…019 + mandatory-authority maps to a task and a decision (see allocation table). ✔ by construction.
- **Scope-expansion honesty:** DEC-018 (AGR-013) and DEC-019 (seed §6.3) are flagged register-level, owner-ratify, and cascade into Tasks 12/13/22/23. ✔
- **No invented facts:** numeric thresholds in DEC-012 are `TBD — owner to set`; all business answers are owner-supplied verbatim. ✔
- **ID consistency:** DEC-010…020, ADR-0002/0003, CAP-035/036 used identically across tasks. ✔
- **Ordering:** ADR-0002 acceptance (Task 12/16) follows the graduation exercise. ✔
