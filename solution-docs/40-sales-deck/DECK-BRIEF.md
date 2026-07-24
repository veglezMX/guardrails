**AI Guardrails — Sales Deck Brief**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

## About this document

This is not the deck. It is the **instruction document** a deck-generation agent (human or automated) must follow to produce the AI Guardrails internal pitch deck. Every section below is a constraint or an input, not a suggestion — where this brief specifies content, tone, or a claim boundary, the generated deck must comply with it exactly.

This is an **L4** document: a derived audience-collateral artifact. It must not contradict [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both **L1**), and its claims sections are written to align with the restricted phrasing that [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) *(planned)* will enforce once authored. **Until `CLAIMS-REGISTER.md` exists, this brief's "Claims that may be used" and "Claims that must not be made" sections are the authoritative claims boundary for the deck.** Once `CLAIMS-REGISTER.md` is published, it governs, and any divergence found here must be corrected to match it — not the reverse. No figure, metric, SLA, date, or dollar amount appears below unless it is present in `seed.md` or `AGREEMENTS.md`; where the source material does not supply one, this brief says **TBD** or describes the point qualitatively, and the generated deck must do the same.

---

## Objective

Produce an internal pitch deck that takes a platform/enterprise audience from "I don't know why this exists" to "I will commit resourcing or a pilot workload to it," by explaining, in order:

1. **The problem** — teams building on LLMs today each independently invent and maintain their own safety/policy safeguards, coupled to their own gateway and provider, with no shared policy definition, decision format, or audit trail (seed.md §1–§2).
2. **The solution** — AI Guardrails: a standalone policy, evaluation, and enforcement layer that lets a policy be defined once and applied across many workloads, gateways, and model providers, deployed in whichever of three modes fits a given workload's ownership and data-residency needs (seed.md §1–§3, §22–§23).
3. **The business value** — reduced engineering duplication, centralized governance evidence, and a safe, graduated adoption path that does not force any team to accept unproven blocking behavior on day one ([`../20-product/VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md)).
4. **The differentiation** — a genuinely portable policy contract, an explicit and separated ownership/exposure model, and an honest, stated scope boundary, instead of a single-vendor moderation feature masquerading as a governance platform ([`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md)).
5. **The next action** — a specific, low-risk, immediately fundable ask: approve **Phase 1 — Integration Proof** and/or commit a pilot workload, not a commitment to the full five-phase platform ([`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md), §9–§10).

This deck is **not** a substitute for [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) (the fuller written decision document) or [`../20-product/PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md) (the fuller written product narrative). It is the spoken, visual, time-boxed version of the same underlying agreements, built to be presented live and to drive a specific decision in the room.

---

## Primary audience

An **internal, platform/enterprise audience** evaluating whether to fund or adopt AI Guardrails — not external prospects, press, or procurement. Specifically (seed.md §7):

| Segment | Why they're in the room |
|---|---|
| **Platform engineering leadership** | Owns shared infrastructure decisions; likely operates or influences the internal LiteLLM instance(s) the deck references. |
| **Application/product engineering leads** | Prospective adopting teams — candidates to run the "second independent LiteLLM instance" the MVP demonstration requires ([AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration)). |
| **Security and compliance leadership** | Policy owners and risk sign-off; will scrutinize the content-exposure, retention, and "guardrails are not authorization" claims most closely. |
| **Executive sponsor(s)** (VP/CTO-level) | Present specifically for the funding/approval decision described in [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §10. |

Do not write this deck as if it will be shown to an external customer, an analyst, or a public audience. Every claim, comparison, and next-step assumes an internal reporting line and internal decision authority.

---

## Audience knowledge level

**Technically fluent, but not assumed familiar with this platform.**

* Assume general familiarity with LLM applications, gateways, and API-based architecture — do not explain what an LLM or a gateway is.
* Assume **uneven** familiarity with LiteLLM and Amazon Bedrock Guardrails specifically: some attendees (platform/application engineering) will know both well; others (security/compliance, executive sponsor) may know neither in depth. Introduce each on first use with one sentence of context, not a technical deep dive.
* Assume **zero** prior exposure to AI Guardrails' own vocabulary. Terms such as *policy binding*, *observe/enforce*, *evaluation contract*, *reason code*, and the three named deployment modes (Remote Evaluation / Guarded Gateway / Customer-Managed Runtime) must be introduced plainly before being used as shorthand later in the deck.
* Assume the security/compliance segment will actively probe the data-exposure and authorization boundaries — the deck must get these exactly right the first time they appear, not walk them back later.
* Do not assume the audience has read `seed.md`, `AGREEMENTS.md`, or any other solution-docs artifact. This deck must be self-contained for a first-time viewer while remaining consistent with those documents for anyone who checks afterward.

---

## Presentation context

* **Format:** Live, internal presentation, roughly **20–30 minutes** including a short live walkthrough, followed by open Q&A.
* **Venue (likely):** A platform investment review, an architecture/governance review board, or an internal leadership forum where funding or pilot commitments are made.
* **Presenter:** Product or technical leadership for AI Guardrails, speaking in the same voice as [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md).
* **Stage of the proposal:** Pre-approval. AI Guardrails has **not yet been built or piloted** at the time this deck is used — the MVP demonstration in [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration) is a target outcome of Phase 1, not a completed fact. The deck must never imply the platform is already running in production or already proven; it is asking for the resourcing to prove it.
* **Not this:** Not a customer-facing sales call, not a conference keynote, not a marketing launch, not a document intended for external distribution without a separate review against [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) *(planned)* once it exists.

---

## Desired audience action

**Primary ask:** Approve resourcing for **Phase 1 — Integration Proof** (per [AGR-022](../00-source-of-truth/AGREEMENTS.md#agr-022--five-phase-delivery)), whose objective is to prove that guardrail policy and evaluation can be separated from LiteLLM and reused across multiple gateways.

**Concrete, in-room commitment to seek alongside the approval:**

* Confirm at least one additional team, beyond the team proposing AI Guardrails, willing to operate an organizationally independent LiteLLM instance as part of the two-instance MVP demonstration ([AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration); `../00-source-of-truth/ASSUMPTIONS.md` ASM-002, ASM-003).
* Agree that the pilot workload will run in **Observe mode** first — never Block mode on day one ([AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)).

**Secondary ask (fallback if the primary ask cannot be closed in the room):** Schedule a follow-up technical deep-dive or architecture review with the requesting team(s), using [`../50-architecture/`](../50-architecture/) material *(planned)* as the working document.

**Explicitly not being asked for in this deck:** A commitment to Phases 2–5, a specific budget figure or headcount (these are **TBD**, per [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §7), or approval of any deployment mode beyond what Phase 1 exercises (Remote Evaluation via the LiteLLM connector).

---

## Core message

> **One guardrail policy. Any gateway you already run. The deployment mode your data needs — not the one we happen to support best.**

Everything else in the deck supports this single sentence. If a slide does not make this sentence more believable or more actionable, cut it.

---

## Supporting messages

| # | Message | Anchors |
|---|---|---|
| 1 | **The problem is real today, not hypothetical.** Teams building LLM applications are each independently deciding how to detect and act on unsafe prompts, unsafe responses, and policy violations, coupled to whichever gateway and provider they happen to use. There is no shared policy, decision format, or audit trail across teams. | seed.md §1–§2; [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §2 |
| 2 | **The fix is architectural, not a feature.** AI Guardrails is a standalone platform — not a feature bolted onto one team's LiteLLM instance — built around a policy/connector split so the same policy works no matter which gateway invokes it. | [AGR-001](../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm), [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) |
| 3 | **You choose the deployment mode; the platform doesn't choose it for you.** Three modes trade operational ownership against data exposure differently — Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime — and a team picks based on its own residency and ownership needs. | [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes), [AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns) |
| 4 | **Adoption is safe by design.** Every policy supports Observe mode before it can ever block anything. No team is asked to accept unproven blocking behavior in production on day one. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) |
| 5 | **We say plainly what this is not.** Not authorization. Not a bypass-proof boundary in cooperative mode. Not a private-data model in Remote Evaluation. Not a complete AI-governance or DLP platform. This honesty is a differentiator, not a disclaimer buried in an appendix. | [AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model), [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) |
| 6 | **The ask today is small and reversible.** We are requesting Phase 1 only — an integration proof — with Phases 2 through 5 as separately gated decisions made after each prior phase validates the core hypothesis. | [AGR-022](../00-source-of-truth/AGREEMENTS.md#agr-022--five-phase-delivery); [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §9–§10 |

---

## Required slides

The deck-generation agent must produce these slides, in this order, as the **core deck** (see [Maximum slide count](#maximum-slide-count)). Each row's "Content" column is a content requirement, not final copy — the generation agent may word it, but must not omit or contradict it.

| # | Slide | Purpose | Required content | Source anchor(s) |
|---|---|---|---|---|
| 1 | **Cover** | Set the core message before a word is spoken. | Title using the [Core message](#core-message); subtitle naming the ask ("Requesting approval: Phase 1 — Integration Proof"). | Core message above |
| 2 | **The problem** | Establish urgency without hyperbole. | Teams independently build safeguards, coupled to one gateway/provider; no shared policy, decision format, or audit trail across teams. | seed.md §1–§2; EXECUTIVE-BRIEF §2 |
| 3 | **Why this is costing us** | Translate the problem into organizational impact. | Table of impact dimensions: governance consistency, engineering duplication, adoption risk, compliance/residency gap, false assurance. State plainly that no dollar-figure cost estimate exists in the source material (**TBD**) — do not estimate one. | EXECUTIVE-BRIEF §3 |
| 4 | **Introducing AI Guardrails** | Name the solution once, cleanly. | The seed.md §23 product statement (quotable, near-verbatim), plus one line: "standalone platform, not a LiteLLM feature." | seed.md §23; [AGR-001](../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) |
| 5 | **How it works, at a glance** | Give the audience one mental model of the architecture. | Simplified version of the logical architecture (Control Plane → Runtime → Evaluation engines), drawn from seed.md §10. Keep to control plane / runtime / evaluation engines — do not reproduce full component inventory. | seed.md §10 |
| 6 | **Policy vs. connector** | Explain *why* one policy can span many gateways. | The policy/connector split, using the seed.md §5.3 example almost verbatim: "Policy: detect prompt injection and block it. Connector: invoke the Evaluation API during LiteLLM pre-call execution." | [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors); PRINCIPLES §5.3 |
| 7 | **Three ways to adopt** | Show the three deployment modes as a genuine choice, not a maturity ladder. | Side-by-side diagram: Mode A (Remote Evaluation), Mode B (Guarded Gateway), Mode C (Customer-Managed Runtime), one line each on who runs what. | [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes); PRODUCT-OVERVIEW §4 |
| 8 | **Ownership vs. exposure** | Pre-empt the security/compliance audience's first question. | Table separating "who operates this" from "where does content go," per mode. Must state plainly that Mode A is **not** a private-data or data-local model. | [AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), [AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model) |
| 9 | **Safe by design: observe before enforce** | Remove the adoption-risk objection before it's raised. | Disabled → Observe → Warn → Redact → Block progression; onboarding defaults to Observe, never Block. | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) |
| 10 | **What this is — and isn't** | Convert honest scoping into a credibility signal. | Explicit statements: not authorization; not a bypass-proof boundary in cooperative mode; not a complete AI-governance/DLP platform. | [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization), [AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model); PRODUCT-OVERVIEW §9 |
| 11 | **Why this is different** | Land the differentiation argument. | Summarize 2–3 of the six differentiators in [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) — recommend #1 (model/gateway independence), #2 (portability across modes), #5 (honest scoping). Do not reproduce all six; pick the ones most relevant to the specific audience segment in the room. | [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) |
| 12 | **The path we're proposing** | Show the full roadmap, but visually anchor "we are here." | Five-phase roadmap (Integration Proof → Internal Multi-Team → Guarded Gateway → Customer-Managed Runtime → Advanced Agent Protection), with Phase 1 visually highlighted as the current ask and Phases 3–5 visually marked as later, separately-gated decisions. | [AGR-022](../00-source-of-truth/AGREEMENTS.md#agr-022--five-phase-delivery); EXECUTIVE-BRIEF §6, §9 |
| 13 | **What success looks like** | Make the MVP concrete and falsifiable, not aspirational. | The MVP demonstration criteria from [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration): two independent LiteLLM instances, one policy, no shared model-provider credentials, one Observe / one Enforce, upgrade-and-rollback, disable-able raw-content persistence, defined failure behavior. Phrase as a target ("Phase 1 will be considered successful when…"), never as an achieved result. | [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration) |
| 14 | **Who owns what** | Answer "what does this cost my team in effort" before it's asked. | Ownership table: AI Guardrails team, Platform operations, Security and governance, Consuming teams — one row each, from seed.md §21. | seed.md §21; EXECUTIVE-BRIEF §7 |
| 15 | **The ask** | Close on the specific, concrete decision. | Restate the [Desired audience action](#desired-audience-action) verbatim in substance: approve Phase 1 resourcing, and confirm a second independent pilot team in the room if possible. State explicitly what is **not** being asked (Phases 2–5, specific budget, any mode beyond Remote Evaluation). | EXECUTIVE-BRIEF §9–§10 |

**Backup slides (not counted against the [maximum slide count](#maximum-slide-count); use only if raised in Q&A):**

| Backup slide | When to use it | Source anchor(s) |
|---|---|---|
| **Objection handling / FAQ** | If security/compliance or engineering leadership raises a question this deck doesn't resolve live. | [`../20-product/FAQ.md`](../20-product/FAQ.md) — pull the specific Q&A pair that matches the objection raised; do not read the whole FAQ into the deck. |
| **Domain model glossary** | If the audience gets lost in *tenant / workload / policy / binding / evaluation / decision* terminology. | seed.md §9; [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) |

---

## Claims that may be used

Every claim below traces to an accepted agreement or constraint. Use **"designed to / supports / is intended to"** language, matching the discipline in [`../20-product/VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md) — never proven-performance language, since Phase 1 has not yet executed at the time this deck is used.

| Claim (approved phrasing pattern) | Authority |
|---|---|
| "A guardrail policy is defined once and is designed to apply across many workloads, teams, and gateways." | [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability), [AGR-017](../00-source-of-truth/AGREEMENTS.md#agr-017--core-domain-model) |
| "AI Guardrails is being built as a standalone platform, not as a feature embedded in any single team's LiteLLM instance." | [AGR-001](../00-source-of-truth/AGREEMENTS.md#agr-001--standalone-platform-not-embedded-in-the-internal-litellm) |
| "The platform supports three deployment modes — Remote Evaluation, Guarded Gateway, Customer-Managed Runtime — chosen per workload based on operational-ownership and data-residency needs." | [AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes) |
| "LiteLLM is the first supported connector and Amazon Bedrock Guardrails is the first supported evaluation engine — neither defines the boundary of the product." | [AGR-003](../00-source-of-truth/AGREEMENTS.md#agr-003--litellm-as-first-connector-not-the-product-boundary), [AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract) |
| "Policies do not depend on LiteLLM's configuration structures or Bedrock Guardrails' response format — vendor-specific behavior sits behind adapters." | [AGR-005](../00-source-of-truth/AGREEMENTS.md#agr-005--policy-portability) |
| "The same logical evaluation contract — policy definitions, request/decision structures, reason codes, audit metadata — is designed to hold across all three deployment modes." | [AGR-006](../00-source-of-truth/AGREEMENTS.md#agr-006--one-logical-evaluation-contract-across-all-modes) |
| "A policy defines what must be evaluated and how violations are handled; a connector defines only how a gateway invokes and enforces it — this separation is mandatory." | [AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors) |
| "For every deployment mode, operational ownership and data exposure are documented as two separate, independently answered questions." | [AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns) |
| "Every policy is designed to support a non-blocking Observe mode, with a graduated path (Disabled → Observe → Warn → Redact → Block) before any policy can affect end users." | [AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression) |
| "Failure behavior — what happens on timeout, detector unavailability, or control-plane outage — is an explicit, documented part of every policy; there is no undocumented fallback." | [AGR-012](../00-source-of-truth/AGREEMENTS.md#agr-012--failure-behavior-is-part-of-policy) |
| "The hosted runtime is designed not to persist raw prompts and responses by default; any retention requires explicit, tenant-level configuration." | [AGR-014](../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default) |
| "The Customer-Managed Runtime is designed to execute policy without requiring a synchronous connection to the hosted control plane." | [AGR-015](../00-source-of-truth/AGREEMENTS.md#agr-015--customer-managed-runtime-executes-without-a-synchronous-control-plane-connection) |
| "In Customer-Managed Runtime, the customer chooses what telemetry (if any) reaches the hosted control plane; raw content is not designed to leave that runtime unless explicitly configured." | [AGR-016](../00-source-of-truth/AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime) |
| "The MVP is scoped to demonstrate that one reusable policy can be applied consistently across two independent LiteLLM deployments, with neither sharing model-provider credentials with the guardrail platform." (state as target outcome, not an achieved result) | [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration) |
| "Adopting a policy for a workload ('import') never requires transferring model-provider credentials or exposing the team's gateway administration endpoints." | [AGR-021](../00-source-of-truth/AGREEMENTS.md#agr-021--meaning-of-import) |
| "Delivery proceeds through five gated phases; only Phase 1 — Integration Proof — is being requested today." | [AGR-022](../00-source-of-truth/AGREEMENTS.md#agr-022--five-phase-delivery) |

---

## Claims that must not be made

These are hard prohibitions. If a slide, speaker note, or ad-lib answer during Q&A would assert any of the following, it must be rewritten before the deck is used. This list must be re-checked against [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) *(planned)* once that register is published, and this brief corrected if the register is stricter.

| Prohibited claim | Why it's prohibited |
|---|---|
| Describing **Remote Evaluation (Mode A)** as private, data-local, or "your data never leaves your environment." | Prompts, responses, and evaluated content are transmitted to the hosted Evaluation API in Mode A; this must be described only as "bring your own gateway while consuming a hosted guardrail runtime." ([AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model)) |
| Describing a guardrail decision (`allow`/`block`/etc.) as an **authorization** decision, or implying it removes the need for the consuming application's own authentication/authorization checks. | Content evaluation and business authorization are distinct; authentication, authorization, tenant isolation, resource ownership, transaction limits, and approval workflows remain the consuming application's responsibility. ([AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization)) |
| Claiming a **cooperative-mode connector** is bypass-proof, unremovable, or "a hard security boundary." | A cooperative connector can be disabled, removed, or misconfigured by the team that owns it; only platform-managed or network/IAM-enforced postures raise bypass resistance, and neither is an MVP requirement. ([AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)) |
| Describing **Amazon Bedrock Guardrails** as the platform's permanent, exclusive, or only detection engine. | Bedrock Guardrails is the first managed evaluation engine; the public contract is vendor-independent and additional engines are expected behind the same adapter pattern. ([AGR-004](../00-source-of-truth/AGREEMENTS.md#agr-004--bedrock-guardrails-as-first-engine-vendor-independent-contract)) |
| Stating that raw content is **never** retained anywhere, without the "by default" / "unless explicitly configured" qualifier. | No raw-content retention is the *default*, not an absolute guarantee across every possible tenant configuration. ([AGR-014](../00-source-of-truth/AGREEMENTS.md#agr-014--no-raw-promptresponse-retention-by-default), [AGR-016](../00-source-of-truth/AGREEMENTS.md#agr-016--customer-controlled-telemetry-for-the-customer-managed-runtime)) |
| Presenting the **MVP demonstration** (two independent LiteLLM instances, normalized decisions, etc.) as already achieved, in progress, or "proven." | As of this brief, Phase 1 has not been approved or executed; the demonstration is the *target outcome* the approval unlocks, per [AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration) — use future/conditional phrasing only. |
| Claiming AI Guardrails is, or will soon be, a **complete AI governance platform, a complete DLP platform, a proprietary detection/moderation model, a general MCP security platform, or organization-wide AWS enforcement.** | All explicitly out of scope for the MVP (seed.md §6.3). |
| Claiming **cross-cloud support, multi-region active-active availability, or full compliance certification** as current or near-term capabilities. | Explicitly out of scope for the MVP (seed.md §6.3, §17). |
| Stating **any specific latency figure, availability/SLA number, adoption percentage, cost, ROI, or dollar amount** not present in `seed.md` or `AGREEMENTS.md`. | No such figures exist in the authoritative source material; per source-of-truth policy, these must be marked **TBD** or described qualitatively, never invented or estimated for effect. See [`../00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md). |
| Naming or implying **any specific customer, partner, or logo.** | No customer names, case studies, or references exist in the source material; none may be fabricated or implied. |
| Presenting **Phase 3 (Guarded Gateway), Phase 4 (Customer-Managed Runtime), or Phase 5 (Advanced Agent Protection)** capabilities as available today, or as included in the resourcing being requested. | The ask in this deck is Phase 1 only; later phases are separately gated decisions. ([AGR-022](../00-source-of-truth/AGREEMENTS.md#agr-022--five-phase-delivery)) |
| Implying that **all three deployment modes are equally mature** or generally available at the same time. | Delivery is phased; Mode C in particular lands in Phase 4. Feature-parity gaps between modes must be tracked and stated, not assumed away. ([`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md), "Note on delivery phasing") |

---

## Tone

* **Confident, precise, non-hype.** This is an internal engineering and governance audience; superlatives ("revolutionary," "unbreakable," "bulletproof," "guaranteed," "seamless") read as evasive to this audience, not persuasive. State capabilities plainly and let the architecture argue for itself.
* **Consultative, not promotional.** Frame every slide as "here is the trade-off and why we made it," not "here is why you must say yes." The honest-scoping slide (required slide 10) is a feature of this tone, not a hedge to minimize.
* **Direct about trade-offs.** When a mode has a real limitation (e.g., Mode A transmits content to the hosted service; Mode C lands later in delivery), say so on the slide, not only in Q&A.
* **Technically credible, business-legible.** Use precise domain vocabulary (policy, binding, connector, decision, reason code) once introduced, but always pair architecture statements with the business consequence ("this means your team never re-integrates when it changes gateway").
* **Calm about scope.** Do not oversell the roadmap to compensate for a narrow Phase 1 ask. The narrowness of the ask is a strength — say so explicitly ("this is a small, reversible first step").

---

## Visual direction

* **Register:** Clean, restrained enterprise B2B/infrastructure aesthetic — closer to an architecture review deck than a product-launch deck. No stock "AI" imagery (glowing brains, robot hands, abstract neural-net textures).
* **Diagrams over prose.** Reuse the architecture and flow diagrams already established in sibling documents rather than inventing new visual language:
  * Three-mode comparison diagram — [`../20-product/PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md) §4.
  * Policy-portability / one-contract diagram — [`../20-product/VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md).
  * Observe → Enforce state diagram — [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) §5.6.
  * Logical architecture (Control Plane / Runtime / Evaluation engines) — seed.md §10.
* **One color per deployment mode, held constant across every slide that references modes.** A viewer should be able to tell Mode A from Mode B from Mode C by color alone by slide 10, without rereading labels.
* **Roadmap slide must be visually asymmetric, not a flat five-box timeline.** Phase 1 (the ask) should be visually emphasized — larger, foregrounded, or highlighted — while Phases 2–5 recede visually to signal "future, separately gated," consistent with the tone guidance against overselling scope.
* **Comparison tables, not bullet walls,** for anything that is inherently tabular (ownership-vs-exposure, mode comparison, ownership boundaries). Reserve free-text bullets for narrative slides (problem, ask).
* **Text density:** No slide should carry more than roughly 40 words of on-slide text; push elaboration into speaker notes (see [Output requirements](#output-requirements)).
* **Do not visually imply parity or completeness the platform doesn't yet have** — e.g., do not draw all three modes as equally "live" boxes if only Phase 1 (Mode A path) is committed; use visual weight to reflect delivery-phase reality, matching the "Note on delivery phasing" in [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md).

---

## Maximum slide count

**15 slides** for the core deck (the numbered slides in [Required slides](#required-slides)).

The two backup slides listed under [Required slides](#required-slides) do **not** count against this cap — they are held in reserve for Q&A and are not presented by default. If the core deck must be shortened further for a tighter time slot, slide 3 ("Why this is costing us") and slide 13 ("What success looks like") are the first candidates to compress into a single slide, since both are supporting evidence rather than the core argument; do not cut slide 10 ("What this is — and isn't") or slide 15 ("The ask") under any circumstance.

---

## Source documents

All paths below are given relative to the `solution-docs/` root; as actual markdown links from this file's location (`40-sales-deck/`), they resolve one directory up (`../`).

| Document | Role in this brief |
|---|---|
| [`../../seed.md`](../../seed.md) | Raw solution charter — full source-of-truth material; every claim ultimately traces here. |
| [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) | L1 authoritative agreement register (AGR-001…AGR-022) — the primary claims boundary for this deck. |
| [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) | L1 authoritative constraint register (CON-001…CON-016) — checkable boundaries the deck must not contradict. |
| [`../00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md) | Unvalidated beliefs (e.g., pilot-team availability, MVP funding) this deck's ask depends on. |
| [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) | Unresolved decisions that must not be presented as settled in the deck. |
| [`../10-charter/CHARTER.md`](../10-charter/CHARTER.md) | Full product charter narrative behind slides 4–7. |
| [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) | Source of the policy/connector split (slide 6) and the observe-before-enforce diagram (slide 9). |
| [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) | Supporting detail for slide 13 ("What success looks like"). |
| [`../20-product/PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md) | Primary source for slides 4, 7, and 10. |
| [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) | Primary source for slide 11. |
| [`../20-product/VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md) | Source for the "designed to / supports" claims-language discipline used throughout this brief. |
| [`../20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) | Supporting detail for slide 12 (five-phase roadmap). |
| [`../20-product/FAQ.md`](../20-product/FAQ.md) | Source for the objection-handling backup slide. |
| [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) | Primary source for slides 2, 3, 12, 14, 15, and for the [Desired audience action](#desired-audience-action). |

---

## Output requirements

The deck-generation agent must deliver output meeting all of the following:

1. **Structure.** A slide-by-slide outline (or slide file, if a design tool is used downstream) where every slide has: a title, an on-slide text block (≤ ~40 words per [Visual direction](#visual-direction)), a named visual/diagram, and a speaker-notes block.
2. **Traceability.** Every speaker-notes block must cite the `AGR-xxx` and/or `seed.md §` anchor backing the claims on that slide, so a reviewer can verify each slide against [Claims that may be used](#claims-that-may-be-used) without re-deriving it.
3. **No new claims.** Every on-slide or spoken claim must appear in, or be a direct paraphrase of, [Claims that may be used](#claims-that-may-be-used). Do not introduce a new capability claim, differentiator, or figure that is not already sourced from this brief's source documents.
4. **No fabricated figures.** Any metric, percentage, date, or dollar amount not present in `seed.md` or `AGREEMENTS.md` must render on-slide as **TBD** or be omitted and replaced with a qualitative statement — never estimated or invented for narrative effect.
5. **Mandatory scope-boundary slide.** Slide 10 ("What this is — and isn't") is required, not optional, and must not be cut, shortened to a footnote, or moved to backup — it is load-bearing for the security/compliance segment of the audience.
6. **Phase discipline.** The deck must visually and verbally distinguish what Phase 1 delivers from what Phases 2–5 will later deliver. No slide may present a Phase 3/4/5 capability (managed gateway, customer-managed runtime, agent/tool authorization, etc.) as available today or as part of the current ask.
7. **Claims-register check.** Before this deck is used in any setting beyond the immediate drafting team — and again before any reuse once [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) *(planned)* is published — every claim must be re-checked against that register (or, until it exists, against [Claims that may be used](#claims-that-may-be-used) / [Claims that must not be made](#claims-that-must-not-be-made) above).
8. **Self-contained for a first-time viewer.** Per [Audience knowledge level](#audience-knowledge-level), the deck must introduce every AI Guardrails-specific term on first use; it must not assume the audience has read any other solution-docs artifact.
9. **A closing slide that names the decision, not just "thank you."** Slide 15 must restate the specific ask from [Desired audience action](#desired-audience-action) as the last thing the audience sees.
10. **Format note.** Markdown slide outline is the primary deliverable of this brief's instructions. If a presentation-design tool or template is used downstream to build the visual deck, this brief remains the source of truth for *content and claims*; the tool governs layout and styling only, subject to [Visual direction](#visual-direction).

---

## Related documents

- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — L1 authoritative agreement register this brief's claims sections derive from.
- [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — L1 authoritative constraint register; no deck claim may contradict it.
- [`../00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md) — unvalidated beliefs the deck's ask depends on (pilot-team availability, MVP funding).
- [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) — decisions not yet made; must not be presented as settled.
- [`../10-charter/CHARTER.md`](../10-charter/CHARTER.md) — full charter narrative underlying the problem/solution slides.
- [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) — source of the policy/connector-separation and observe-before-enforce content.
- [`../20-product/PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md) — the broader product narrative this deck compresses for a live audience.
- [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) — full differentiation argument behind slide 11.
- [`../20-product/VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md) — audience-by-audience value detail and the claims-language discipline this brief follows.
- [`../20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) — phase-by-phase delivery detail behind slide 12.
- [`../20-product/FAQ.md`](../20-product/FAQ.md) — source material for the objection-handling backup slide.
- [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) — the fuller written decision document this deck is the live counterpart to.
- [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) *(planned)* — the authoritative external-claims register that will supersede this brief's claims sections once published.
- [`../70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) *(planned)* — risks (connector bypass, false confidence, hosted data exposure) that qualify several claims above.
