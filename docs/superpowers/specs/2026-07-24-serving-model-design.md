# Serving Model Design — AI Guardrails

**Status:** Design (approved in brainstorming; not yet promoted into the registers)
**Date:** 2026-07-24
**Author:** Product/technical lead (contact@veglez.com)
**Scope of this spec:** How AI Guardrails is served to and consumed by its users — the front door, the managed/self split, the integration contract, the enforcement posture, and the policy-authoring/governance model. This is a decision record produced through the brainstorming process. It does **not** itself edit the source-of-truth registers; promoting its load-bearing decisions into `AGREEMENTS.md` / `DECISIONS.md` / `OPEN-QUESTIONS.md` and a product-facing `SERVING-MODEL.md` is a separate, governed follow-on step (see §7).

---

## 1. The question this resolves

The product thesis is: *"AI Guardrails is a policy and enforcement platform that attaches to any LLM gateway or agent runtime."* That is accurate for **gateways today** and **agent runtimes as directional vision** (agent-stage evaluation is Phase 5 / `Vision — uncommitted`; claiming it as delivered would breach AGR-010 and the claims register).

The unresolved part was never "what is it" — it was **how it is served**: should consumers implement the integration themselves, or should the provider manage the whole path? This spec settles that.

The short answer: **the three deployment modes already in the register (AGR-002) are the serving model.** The design work was choosing which mode is the *front door*, and defining the consumer contract and policy governance around it.

## 2. Design decisions

### D1 — Product identity

A **hosted policy + evaluation service** attached to a consumer's existing LLM gateway. The consumer keeps their gateway, model provider, and data path; the platform hosts evaluation, the policy store, and the audited decision. "Attach to any gateway" is the delivered direction; "any agent runtime" is stated only as vision.

### D2 — Front door = Mode A (Remote Evaluation), served as a developer service

Mode A is the default adoption path; Modes B and C are graduation paths, not alternatives competing for the front door. Rationale:

- **Proves the thesis fastest.** Policy reuse across *independent* gateways (the two-LiteLLM demonstration, UC-001) is only convincing when the gateways aren't provider-run. Mode B would hide it; Mode C is too heavy to stand up first.
- **Smallest provider scope.** Mode A runs only the Evaluation API + control plane. Mode B carries "the largest operational, security, and compliance scope for the provider" (DEPLOYMENT-ARCHITECTURE §3). Mode C requires packaging and supporting a runtime in the customer's account.
- **Matches the agreed phase order.** AGR-020 already sequences Phase 1 (integration proof = Mode A shape) → Phase 3 (Mode B) → Phase 4 (Mode C).

### D3 — The managed / self-serve split

Mode A is neither "consumer builds everything" nor "provider runs everything." It is a split, and naming the split is the expectation being set:

| Provider operates (managed) | Consumer does (self-serve) |
|---|---|
| Hosted Evaluation API (the contract) | Install connector / call SDK at pre-call + post-call |
| Policy store + versioning + rollback | Register their workload, import/select a policy |
| Workload & binding registry | Configure the failure behavior they chose |
| Normalized decision + audit metadata | Run observe → review false positives → promote to enforce |
| Control plane / dashboards | Keep their gateway, model credentials, request path |

**The provider manages the policy brain; the consumer owns the request-path plumbing.**

### D4 — Integration contract

- The **HTTP Evaluation API** is the real product surface — one logical contract across all three modes (AGR-006).
- An **official LiteLLM connector** and **thin Python/TS SDKs** ride over it as the paved path.
- **Hard rule:** an SDK never adds evaluation semantics the API lacks (preserves portability, AGR-005).
- The raw API stays public for unsupported stacks.
- **Adoption target:** guardrails on a workload in roughly ten lines, in observe mode, within an afternoon.

### D5 — First consumers

Internal teams (dogfooding) **and** external customers in parallel. Consequence, accepted deliberately: **external-grade rigor applies from day one** — tenant isolation, an SLA story, and a billing story are all pulled earlier than an internal-first path would require.

### D6 — Enforcement posture

- **MVP = cooperative connector only.** It can be disabled, removed, or misconfigured, and is **never** represented as a hard security boundary (AGR-018, CON-012).
- **Graduation ladder:** cooperative → platform-managed enforcement (Mode B) → network/IAM-enforced gateway (optional-advanced). The network/IAM posture is not an MVP requirement.
- This **resolves OQ-001 for the front door**: first external customers get cooperative posture with an explicit bypass caveat; a customer contractually requiring hard enforcement is routed to Mode B/C rather than over-building Mode A.

### D7 — Policy authoring & governance

The provider ships **curated policy packs** (starter policies mapped to the GR catalog); consumers **author their own** policies on top, in the portable format. Each curated policy carries a **governance class**:

| Class | Consumer may | Consumer may not |
|---|---|---|
| **Mandatory (pinned)** | strengthen — promote Observe→Block earlier, tighten thresholds, add stages | disable, unbind, select a version below the pinned floor, or weaken below the minimum enforcement |
| **Recommended / overridable** | adopt as-is, override thresholds, extend, or skip | — |
| **Own (consumer-authored)** | anything expressible in the portable format | contradict a mandatory policy layered above |

- **Effective policy = mandatory base (locked) ⊕ consumer overrides/additions.** This layering is the MVP-simple first form of the policy *inheritance* anticipated by OQ-012.
- **Authority to mark a policy mandatory rests with the security/governance role** (TARGET-USERS §4), never the application team. Application teams may strengthen, never weaken. This separation is the product's core value to the security buyer.
- **Honesty caveat (keeps the design AGR-018-clean):** in cooperative Mode A, "mandatory" is a *governance* guarantee, not a *physical* one. The platform records the binding as required and refuses to attest compliance when it is absent; it does not physically prevent an application from running without it. Bypass-proof mandatory enforcement requires Mode B or network/IAM posture — so mandatory-policy *strength* climbs the same posture ladder as D6.

### D8 — Graduation paths

Modes B and C answer specific buyer objections rather than competing for the front door:

| Objection | Path |
|---|---|
| "We have no gateway." | Mode B — Guarded Gateway (Phase 3) |
| "Content can't leave our account." | Mode C — Customer-Managed Runtime (Phase 4) |
| "Mandatory controls must be bypass-proof." | Mode B / network-IAM posture (not a Mode-A promise) |

## 3. What this design forces onto the critical path

All already recorded; now load-bearing because of D5 (external from day one):

- **OQ-001** (enforcement posture) — resolved for the front door by D6.
- **OQ-011** (metering/billing) — pulled forward; external buyers need a commercial answer early.
- **OQ-012** (policy inheritance/signing) — partially pulled forward; mandatory pinning (D7) is proto-inheritance.
- **Tenant isolation hardening** — external-grade from day one.
- **NEW open question (to be filed):** *Mandatory-policy authority & cross-posture semantics* — who may pin a policy mandatory; whether a tenant admin can override an org-mandatory policy; how "cannot weaken" is attested per posture (cooperative vs managed vs network-enforced).

## 4. Non-goals / explicitly out of scope

- **Policy marketplace** — envisioned, not now (`Vision — uncommitted`, backlog axis 7 / OQ-011).
- **Agent-runtime attachment** as a delivered claim — Phase 5 / vision only.
- **Mode B or C as the front door** — they are graduation paths.
- **Bypass-proof "mandatory" in cooperative Mode A** — a governance guarantee only until the posture graduates.
- **Turnkey drop-in (change base_url)** — that is Mode B, not the Mode A front door.

## 5. Traceability

| Decision | Anchored in / relates to |
|---|---|
| D1 identity | AGR-001, AGR-003, AGR-007; AGR-010 (agent-runtime claim discipline) |
| D2 front door | AGR-002, DEC-004, AGR-020; UC-001 |
| D3 managed/self split | AGR-007, AGR-017; DEPLOYMENT-ARCHITECTURE §2 |
| D4 contract | AGR-006, AGR-005; CAP (generic HTTP Evaluation API) |
| D5 first consumers | AGR-020 phasing; risk register (product over-expansion) |
| D6 posture | AGR-018, CON-012; OQ-001 |
| D7 authoring/governance | AGR-005, AGR-011, AGR-017, AGR-018; AGR-019 ("import"); OQ-012; TARGET-USERS §4 |
| D8 graduation paths | AGR-002, AGR-009, AGR-015; FEATURE-BACKLOG axes 1–2 |

Evolution of every axis beyond the MVP surface is compiled in [`../../../solution-docs/60-delivery/FEATURE-BACKLOG.md`](../../../solution-docs/60-delivery/FEATURE-BACKLOG.md).

## 6. Consumer-facing one-liner

> "AI Guardrails is a hosted policy + evaluation service you attach to your existing LLM gateway with a connector or a few lines of SDK. You keep your gateway, your model, and your data path; we evaluate, decide, and audit. Your security team pins the mandatory policies; your app teams can strengthen but never weaken them."

## 7. Follow-on governed promotion (next step, not done here)

Promoting this design into the authoritative set is a deliberate, separate step through the scope-change path (SCOPE §8 / DOCUMENT-GOVERNANCE):

1. **New DECISIONS entries** — (a) front door = Mode A served as a developer service; (b) curated packs + mandatory/overridable/own governance classes.
2. **New OPEN-QUESTIONS entry** — mandatory-policy authority & cross-posture semantics (§3).
3. **Possible new AGREEMENT** — if "security-owned mandatory policies, app-teams-may-only-strengthen" is to carry L1 authority rather than living as a decision.
4. **New derived doc** — `20-product/SERVING-MODEL.md` (product-facing), plus edits to `PRODUCT-OVERVIEW.md` §8 and the traceability matrix.

## 8. Open questions for the reviewer

- Should the security-owned-mandatory principle (D7) be an **Agreement (L1)** or a **Decision (L2)**?
- Is "internal + external in parallel" (D5) truly the intended first-consumer shape, given it forces external-grade rigor immediately — or is this a solo/vendor context where "internal" just means dogfooding?
- Any objection to cooperative-only posture (D6) as the honest MVP ceiling?
