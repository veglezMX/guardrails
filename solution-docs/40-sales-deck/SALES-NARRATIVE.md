**AI Guardrails — Sales Narrative**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-24
**Derived from:** seed.md + AGREEMENTS.md

---

> This document is the connective narrative for the AI Guardrails sales deck: it explains, transition by transition, *why* each slide follows the one before it — the logical throughline from problem, to solution, to proof, to ask. It does not restate every fact already carried in [`PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md), [`VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md), [`DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md), or [`EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) — it points to them and explains the connective logic between slides. It is an **L4** document: a derived, audience-specific artifact that must not contradict [`AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both L1). Where a figure — cost, date, SLA, adoption number — is not present in the source material, this narrative says **TBD** rather than inventing one, exactly as the documents it draws from do.
>
> A companion `SLIDE-OUTLINE.md` (slide-by-slide titles, bullets, and speaker notes) is expected to exist alongside this file in `40-sales-deck/`. It has not yet been authored. The slide sequence below is the numbering this narrative assumes and that `SLIDE-OUTLINE.md` must match when it is written — every "Slide N" reference in this document refers to the row of the same number in the table in [Slide sequence](#slide-sequence).

---

## Table of contents

- [Slide sequence](#slide-sequence)
- [How to read this narrative](#how-to-read-this-narrative)
- [Act I — The problem (Slides 1–4)](#act-i--the-problem-slides-14)
- [Act II — The solution (Slides 5–10)](#act-ii--the-solution-slides-510)
- [Act III — The proof (Slides 11–14)](#act-iii--the-proof-slides-1114)
- [Act IV — The ask (Slides 15–17)](#act-iv--the-ask-slides-1517)
- [Trust-builders used throughout the deck](#trust-builders-used-throughout-the-deck)
- [What this narrative deliberately does not do](#what-this-narrative-deliberately-does-not-do)
- [Related documents](#related-documents)

---

## Slide sequence

| # | Slide title | One-line purpose |
|---|---|---|
| 1 | Title / opening | Frame the topic and the audience's stake in it. |
| 2 | The fragmentation problem | Show the current-state pain: guardrails built team by team, gateway by gateway. |
| 3 | Why it matters | Translate fragmentation into organizational consequence — no evidence of governance, duplicated effort. |
| 4 | The two adoption blockers | Name the two specific reasons teams resist a shared guardrail capability today. |
| 5 | Vision and product statement | State what AI Guardrails is, in one paragraph, as the direct answer to Slides 2–4. |
| 6 | How it works: policy vs. connector | Introduce the one structural idea everything else depends on. |
| 7 | Three deployment modes | Show that the platform meets teams where they are, not the reverse. |
| 8 | Ownership vs. exposure, mode by mode | Make the trust-building distinction concrete and mode-specific. |
| 9 | The trust and enforcement model | Show the platform is honest about how strong its guarantees actually are. |
| 10 | Observe before enforce | Show the platform will not ask a team to bet production traffic on an unproven policy. |
| 11 | What makes this different | Consolidate Slides 5–10 into named differentiators a buyer can compare against alternatives. |
| 12 | What this is not | Pre-empt the platform's own biggest risk: being mistaken for something it doesn't claim to be. |
| 13 | The MVP demonstration | Present the falsifiable proof that the reusable-policy hypothesis actually holds. |
| 14 | Roadmap: five phases | Show the proof in Slide 13 is the first step of a sequenced, de-risked delivery plan, not the whole plan. |
| 15 | How we'll know it's working | Show the platform will be held to measured outcomes, not declared victory. |
| 16 | Principal risks and how they're managed | Address the risks a sophisticated buyer will already be thinking about. |
| 17 | The ask | State exactly what is being requested, and exactly what is not. |

---

## How to read this narrative

Each section below covers one transition — "Slide *N* → Slide *N+1*" — and answers three questions: what the prior slide just established, what tension or question that leaves open, and why the next slide is the thing that resolves it. The four acts group slides by rhetorical function (problem, solution, proof, ask), matching the arc requested for this document, but the transition logic is written at the slide level because that is where an audience actually experiences the narrative.

---

## Act I — The problem (Slides 1–4)

### Slide 1 → Slide 2

The opening slide exists to earn the right to the next seventeen minutes: it names the audience's own situation back to them — multiple teams building on LLMs, agentic workflows entering production, and no shared answer yet to "are we governed consistently." It should not yet claim a solution. The moment it starts describing AI Guardrails itself, the deck has skipped its own proof structure and asked for trust it hasn't earned.

Slide 2 cashes that opening in. It shows the fragmentation pattern precisely as it exists today: teams with no guardrails, teams with bespoke and undocumented checks, teams duplicating the same detection problem with different quality bars — three failure patterns that can and do coexist inside the same organization (see [`PROBLEM-STATEMENT.md` §2.1–§2.2](../20-product/PROBLEM-STATEMENT.md#2-current-state-pain)). This slide's job is narrow and important: make the audience recognize their own environment, not AI Guardrails' pitch, in what's on screen.

### Slide 2 → Slide 3

Slide 2 shows *what* is broken. Left there, it reads as an engineering inconvenience — "some teams have better guardrails than others." Slide 3 exists to close that gap in perceived stakes: it reframes fragmentation as an organizational cost that compounds as adoption grows — no evidence of governance, inconsistent user and business exposure, duplicated engineering spend, and a gap between adoption and the ability to reason about aggregate risk that only widens over time (see [`PROBLEM-STATEMENT.md` §3](../20-product/PROBLEM-STATEMENT.md#3-why-this-matters)). This is also the point in the deck to state plainly, once, that the source material supplies no dollar figure, incident count, or adoption timeline for this cost — the qualitative case is what's being made, and it should not be dressed up as more than that.

### Slide 3 → Slide 4

Slide 3 has now made the case that fragmentation is expensive and that the organization has a rational reason to want centralization. That naturally invites a skeptical question from an experienced buyer: "if centralization is obviously better, why hasn't anyone already done it?" Slide 4 answers that question directly, because the honest answer is not "nobody thought of it" — it's that two specific, rational objections have blocked adoption every time it's been attempted:

1. Teams routinely conflate *who operates a component* with *where does my data go* — and a team can retain full operational control of its own gateway while still transmitting every prompt and response to a hosted service ([`PROBLEM-STATEMENT.md` §4](../20-product/PROBLEM-STATEMENT.md#4-adoption-blockers-data-exposure-and-operational-ownership)).
2. Guardrails have historically shipped as all-or-nothing: absent, or blocking — with no way to measure what a policy *would* have done before it can break real traffic ([`PROBLEM-STATEMENT.md` §5](../20-product/PROBLEM-STATEMENT.md#5-adoption-blocker-no-way-to-measure-impact-before-blocking-users)).

This slide is doing real work for the rest of the deck: it plants the two objections that Slides 8 and 10 will later resolve by name, so the audience recognizes the payoff when it arrives rather than encountering it cold.

---

## Act II — The solution (Slides 5–10)

### Slide 4 → Slide 5

Act I ends with two named, specific objections still open. Slide 5 is the hinge of the entire deck: it is the first point at which AI Guardrails itself appears, and it must do so as the direct, structural answer to everything just raised — not as a feature list. The product statement itself is written to do exactly this work: *"Teams may connect their existing LiteLLM instance to a hosted Evaluation API, consume a fully managed Guarded Gateway, or deploy the same guardrail runtime within their own AWS account. Policies remain portable across all three modes, while operational ownership and content exposure are explicitly defined for each deployment"* (seed.md §23, quoted in [`PRODUCT-OVERVIEW.md` §2](../20-product/PRODUCT-OVERVIEW.md#2-product-statement)). Notice that the product statement's own last clause — "operational ownership and content exposure are explicitly defined" — is a direct answer to objection #1 from Slide 4. That is not a coincidence to gloss over on stage; it is worth saying explicitly, because it is the moment the deck earns credibility rather than just asserting it.

### Slide 5 → Slide 6

Slide 5 states *what* AI Guardrails is. It has not yet explained the one design idea that makes "define a policy once, apply it everywhere" actually possible rather than aspirational. Slide 6 supplies that idea, and it must come before the deployment-modes discussion, not after — because the mode discussion only makes sense once the audience understands that a *policy* (what to check, how to act on violations) and a *connector* (how a specific gateway invokes and enforces that policy) are two different things by design, never conflated in any implementation (seed.md §5.3; [`AGREEMENTS.md` AGR-007](../00-source-of-truth/AGREEMENTS.md#agr-007--policies-are-separate-from-connectors)). Without this slide landing first, Slide 7's three modes look like three different products; with it, they read correctly as three ways of *deploying the same policy*.

### Slide 6 → Slide 7

Slide 6 established the separation of policy from connector. Slide 7 is where that separation pays off visibly: because a policy is portable, it can be bound to a workload under three different operating topologies — Remote Evaluation, Guarded Gateway, and Customer-Managed Runtime — without being rewritten ([`AGREEMENTS.md` AGR-002](../00-source-of-truth/AGREEMENTS.md#agr-002--three-deployment-modes)). This slide's rhetorical job is to demonstrate flexibility concretely: a team that already runs a gateway, a team that has none, and a team that must keep everything inside its own AWS account are all served by the same platform, choosing the mode that fits their situation rather than being forced onto the vendor's preferred topology.

### Slide 7 → Slide 8

Slide 7 shows that three modes exist. It would be a mistake to leave the mode comparison at "who operates what," because that is exactly the half of the picture that creates the false confidence named as objection #1 back in Slide 4. Slide 8 exists specifically to close that gap: for each mode, it states *independently* who operates the component and where the tenant's content actually goes ([`AGREEMENTS.md` AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns)). This is the slide that must say, without hedging, that Mode A (Remote Evaluation) is **not** a private-data or data-local model — a team that keeps running its own gateway is still transmitting every prompt and response to the hosted Evaluation API ([`AGREEMENTS.md` AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model)). Saying this plainly, in the sales deck itself rather than only in a security addendum, is a deliberate trust-builder: it tells a sophisticated buyer that this vendor will not let them discover a data-flow surprise during a compliance review after the deal closes. Teams with genuine data-residency or non-transmission requirements are pointed to Mode C on this same slide, not reassured that Mode A already covers them.

### Slide 8 → Slide 9

Slide 8 resolved objection #1 (ownership vs. exposure) for content flow. But "where my data goes" is not the only trust question a buyer has — the next is "how strong is the guarantee that the connector is even being used correctly." Slide 9 answers that by introducing the three enforcement postures explicitly, in ascending order of bypass resistance: cooperative integration (a team installs and maintains its own connector, appropriate for trusted teams where guardrails are an engineering standard), platform-managed enforcement (the platform team manages the gateway configuration), and network/IAM-enforced gateway (bypass structurally prevented via credential and egress controls) (seed.md §8; [`AGREEMENTS.md` AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model)). The critical sentence on this slide is a limitation, not a feature: a cooperative connector must never be represented as a hard security boundary — it can be disabled, removed, or misconfigured. Stating this as a limitation, out loud, in the sales narrative rather than leaving a buyer to discover it later, is the second deliberate trust-builder in this deck.

### Slide 9 → Slide 10

Slide 9 was honest about enforcement strength varying by posture. Slide 10 closes the loop on objection #2 from Slide 4 (no way to measure impact before blocking users) with the platform's actual answer: every policy supports a graduated, auditable progression — Disabled → Observe → Warn → Redact → Block — and a new binding defaults to Observe, never Block (seed.md §5.6; [`AGREEMENTS.md` AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)). This slide should be delivered as a direct callback to Slide 4: "you told us teams won't adopt a control that can silently break production — here is the structural reason that risk doesn't apply here." It is also the point to be candid about what observe mode does *not* do: it does not detect or prevent anything by itself; its value is the measured, auditable path it enables toward enforcement, not a substitute for enforcement.

---

## Act III — The proof (Slides 11–14)

### Slide 10 → Slide 11

Act II has now walked through six structural ideas: policy/connector separation, three modes, ownership-vs-exposure, three enforcement postures, and observe-before-enforce. Slide 11 exists purely to consolidate — a buyer evaluating alternatives needs a short, named list they can compare against a competitor's pitch, not a re-walk of the last six slides. It compresses Slides 6–10 into the differentiators already documented at [`DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md): a model- and gateway-independent contract, policy portability across three modes, ownership separated from exposure, observe-before-enforce as a structural default, and one evaluation contract whether hosted or customer-managed. This slide should read as a summary table, not new information — anything on it must already have been established earlier in the deck.

### Slide 11 → Slide 12

Slide 11 has just made the platform sound comprehensive. That is precisely the moment overclaiming risk is highest, and it is exactly the risk the source material names explicitly as the platform's single largest exposure: teams mistaking a passing guardrail evaluation for complete AI security (seed.md §20, "False confidence"). Slide 12 exists to cut that risk off before a prospect's own security team does it for them in the follow-up meeting. It states, without hedging, what AI Guardrails deliberately does not do: a guardrail decision is not a business-authorization decision — the consuming application remains responsible for authentication, authorization, tenant isolation, resource ownership, transaction limits, and approval workflows ([`AGREEMENTS.md` AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization)); a cooperative connector is not bypass-proof (restated from Slide 9); and the MVP is not a complete DLP platform, a proprietary detection model, or a general AI governance suite. This is the third and most important trust-builder in the deck: a vendor who volunteers the boundaries of its own product, unprompted, is more credible on everything it *does* claim — including the proof that follows immediately in Slide 13.

### Slide 12 → Slide 13

Slide 12 has just told the audience what AI Guardrails does not prove. Slide 13 tells them, concretely, what it *does* prove, and does so with a falsifiable demonstration rather than a claim: two organizationally independent LiteLLM instances, sharing no model-provider credentials with the guardrail platform, both bound to the same versioned policy, receiving consistent normalized decisions — one running in observe mode, the other in enforce mode — with policy upgrade and rollback demonstrated, raw-content persistence shown to be disable-able, and evaluation failures shown to follow a configured failure policy rather than an undocumented one ([`AGREEMENTS.md` AGR-020](../00-source-of-truth/AGREEMENTS.md#agr-020--mvp-success-demonstration); full criteria table at [`SUCCESS-CRITERIA.md` §1.2](../10-charter/SUCCESS-CRITERIA.md#12-criteria-table)). The ordering here is deliberate: this slide only works *after* Slide 12, because a scope-honest platform's proof reads as evidence, while the same proof presented right after a differentiator slide with no boundary statement in between would read as marketing.

### Slide 13 → Slide 14

Slide 13 proves the reusable-policy hypothesis holds architecturally, under Mode A, at MVP scale. It would be a mistake to let that stand as "the whole plan is done" — the MVP demonstration deliberately covers only Phase 1 and the early part of Phase 2 of a five-phase delivery ([`PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md); [`AGREEMENTS.md` AGR-022](../00-source-of-truth/AGREEMENTS.md#agr-022--five-phase-delivery)). Slide 14 exists to show that the proof just delivered is the deliberately sequenced first step, not the finish line: Phase 1 (Integration proof) and Phase 2 (Internal multi-team platform) complete the MVP; Phase 3 (Guarded Gateway) and Phase 4 (Customer-Managed Runtime) extend the same proven contract into the two modes not yet exercised; and Phase 5 (Advanced agent protection) is explicitly named as a separate future product expansion, not an MVP prerequisite. This sequencing is itself a differentiator worth stating on stage: the platform is guarding against its own named risk of over-expansion (seed.md §20, "Product over-expansion") by construction, not by promise.

---

## Act IV — The ask (Slides 15–17)

### Slide 14 → Slide 15

Slide 14 showed a phased roadmap. A roadmap without a way to measure whether each phase actually delivered value is just a schedule. Slide 15 exists to show the platform will be held accountable to outcomes across four categories — adoption, reliability, effectiveness, and product portability (seed.md §19; [`SUCCESS-CRITERIA.md` §2](../10-charter/SUCCESS-CRITERIA.md#2-ongoing-success-metrics)) — and, in keeping with the honesty this whole narrative has practiced since Slide 8, this slide should say plainly that specific numeric targets for these metrics are not yet set in the source material and are recorded as **TBD** pending an accountable owner, rather than presenting invented target numbers that would not survive diligence.

### Slide 15 → Slide 16

Slide 15 committed the platform to being measured. A sophisticated buyer's next question is "measured against what could go wrong" — and answering that proactively, rather than waiting for it in Q&A, keeps control of the narrative. Slide 16 names the principal risks already identified in the charter — connector bypass, false confidence, latency, hosted data exposure, policy fragmentation, and product over-expansion (seed.md §20) — paired with the mitigation already designed against each, most of which this deck has already shown in earlier slides (observe-before-enforce for false confidence and bypass risk from Slides 9–10; explicit exposure documentation from Slide 8; phased delivery for over-expansion from Slide 14). This slide's function is consolidation and credibility, not new information — every risk on it should feel like a callback, because the audience already met the mitigation before meeting the risk by name.

### Slide 16 → Slide 17

Slide 16 has demonstrated the platform knows its own risks and has already designed against most of them. That is precisely the state of mind in which to make the ask, and the ask must be exactly as scoped as everything that came before it: approval to build AI Guardrails as a **standalone platform**, and authorization to resource **Phase 1 — Integration Proof** specifically, with Phases 2 through 5 explicitly remaining separate, later, gated decisions ([`EXECUTIVE-BRIEF.md` §10](../30-executive/EXECUTIVE-BRIEF.md#10-decision-required)). The closing slide should also restate, as clearly as Slide 12 stated the platform's technical boundaries, what this ask does *not* commit leadership to: no specific budget, headcount, or delivery date (all **TBD** pending delivery and product leads); no commitment to Guarded Gateway, Customer-Managed Runtime, or Advanced Agent Protection resourcing; and no compliance-certification or multi-region availability commitment. A deck that has been this disciplined about scope for sixteen slides loses nothing, and gains real credibility, by being equally disciplined about the size of the ask on the seventeenth.

---

## Trust-builders used throughout the deck

Two specific honesty moves recur by design across this narrative, and both should be called out to whoever delivers the deck, because they are easy to soften under sales pressure and their value depends on *not* being softened:

| Trust-builder | Where it appears | Why it must not be softened |
|---|---|---|
| **Observe-before-enforce** | Slides 4 (named as a blocker), 10 (resolved), 16 (recalled as a risk mitigation) | It directly answers the objection a buyer is already forming during Slides 2–3 — that adopting this will risk breaking their production traffic. Diluting it into "we have monitoring" loses the specific, auditable graduated-state guarantee that makes it credible ([`AGREEMENTS.md` AGR-011](../00-source-of-truth/AGREEMENTS.md#agr-011--observe-before-enforce-progression)). |
| **Ownership vs. exposure are separate concerns** | Slides 4 (named as a blocker), 8 (resolved mode by mode), 12 (recalled as a scope boundary) | This is the distinction that prevents a security review from discovering, after the deal is signed, that "we kept our own gateway" did not mean "our data never left our environment." Stating plainly that Mode A is not data-local — even though it is the easiest mode to sell as such — is what makes every other claim in the deck more believable, not less ([`AGREEMENTS.md` AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), [AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model)). |

A third, subtler trust-builder worth preserving is the deck's own restraint on figures: at no point does this narrative supply a latency number, an adoption percentage, a cost figure, or an SLA that the source material does not already contain. Any presenter tempted to fill an audience's silence with an invented number on Slides 3, 10, or 15 should be redirected to say **TBD**, precisely because the deck's credibility on Slides 8 and 12 depends on the audience trusting that this vendor does not say things it cannot back up.

---

## What this narrative deliberately does not do

* It does not assign calendar dates, budget figures, or headcount to any phase or slide — none exist in the source material, and none are invented here (consistent with [`PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) and [`EXECUTIVE-BRIEF.md` §7](../30-executive/EXECUTIVE-BRIEF.md#7-investment-and-organizational-impact)).
* It does not claim the MVP demonstration (Slide 13) proves anything beyond what [`SUCCESS-CRITERIA.md` §1](../10-charter/SUCCESS-CRITERIA.md#1-mvp-demonstration-criteria) states — it is evidence that the reusable-policy hypothesis holds architecturally under Mode A at MVP scale, not evidence of adoption at scale, reliability under production load, or effectiveness against real-world attack traffic.
* It does not, at any point, let a deployment-mode slide (7–8) imply that reduced operational ownership means reduced data exposure, or the reverse — per [`AGREEMENTS.md` AGR-008](../00-source-of-truth/AGREEMENTS.md#agr-008--explicit-content-exposure-model-ownership-vs-exposure-are-separate-concerns), those two claims must never be allowed to borrow credibility from each other.
* It does not describe a cooperative connector (Slide 9) as bypass-proof, nor a guardrail `allow` decision (Slide 12) as an authorization decision, under any phrasing — these are the two claims [`AGREEMENTS.md` AGR-018](../00-source-of-truth/AGREEMENTS.md#agr-018--trust-and-enforcement-model) and [AGR-010](../00-source-of-truth/AGREEMENTS.md#agr-010--guardrails-are-not-authorization) most directly prohibit.

---

## Related documents

- [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) — the L1 agreement register this entire narrative must remain consistent with, particularly AGR-008, AGR-009, AGR-010, AGR-011, AGR-018, AGR-019, AGR-020, and AGR-022.
- [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) — checkable boundaries that bound what any slide may claim.
- [`../20-product/PROBLEM-STATEMENT.md`](../20-product/PROBLEM-STATEMENT.md) — full detail behind Act I (Slides 1–4).
- [`../20-product/VALUE-PROPOSITION.md`](../20-product/VALUE-PROPOSITION.md) — full audience-by-audience and mode-by-mode detail behind Act II (Slides 5–10).
- [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) — the six differentiators consolidated on Slide 11.
- [`../20-product/PRODUCT-OVERVIEW.md`](../20-product/PRODUCT-OVERVIEW.md) — the product statement quoted on Slide 5.
- [`../20-product/PRODUCT-ROADMAP.md`](../20-product/PRODUCT-ROADMAP.md) — full phase detail behind Slide 14.
- [`../10-charter/PRINCIPLES.md`](../10-charter/PRINCIPLES.md) — the seven product principles underlying Slides 6, 8, 9, 10, and 12.
- [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) — the MVP demonstration criteria (Slide 13) and ongoing metrics (Slide 15) in full.
- [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) — the executive-facing counterpart to the ask in Slide 17, including the exact decision-required language.
- `SLIDE-OUTLINE.md` — the companion slide-by-slide outline this narrative is keyed to (not yet written; numbering must match the [Slide sequence](#slide-sequence) table above).
- `DECK-BRIEF.md` — deck-level positioning brief referenced by [`AGREEMENTS.md` AGR-009](../00-source-of-truth/AGREEMENTS.md#agr-009--remote-evaluation-is-not-a-private-data-or-data-local-model) as an affected document (not yet written).
