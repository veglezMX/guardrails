**AI Guardrails — Audience Deck Variants**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

## About this document

[`DECK-BRIEF.md`](DECK-BRIEF.md) governs one deck: the internal pitch put in front of a platform investment review whose decision is whether to fund AI Guardrails. This document governs the other three. Each is built from the same source material for a different room making a different decision, and none of them is a reordering of the funding pitch.

This is an **L4** document with the same standing as `DECK-BRIEF.md`. It must not contradict [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both **L1**), and every claim in every variant stays inside the boundary [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) enforces. **Where this document and the claims register diverge, the register governs and this document must be corrected.**

The decks themselves are generated: `just deck <name>` builds one into `presentations/`, `just deck-all` builds and checks every one, and `just decks` lists them. The generator refuses to ship a variant whose slides carry internal document identifiers — see [§2](#2-rules-that-apply-to-every-variant).

---

## Table of contents

<!-- BEGIN GENERATED: toc -->
- [About this document](#about-this-document)
- [1. The four decks](#1-the-four-decks)
- [2. Rules that apply to every variant](#2-rules-that-apply-to-every-variant)
- [3. Architecture review board](#3-architecture-review-board)
- [4. Security and compliance review](#4-security-and-compliance-review)
- [5. Adopting team enablement](#5-adopting-team-enablement)
- [6. What none of these decks may do](#6-what-none-of-these-decks-may-do)
- [Related documents](#related-documents)
<!-- END GENERATED: toc -->

---

## 1. The four decks

| Deck | Room | The decision they make | Governed by |
|---|---|---|---|
| `internal` | Platform investment review | Approve the solution — full feature set, five phases | [`DECK-BRIEF.md`](DECK-BRIEF.md) |
| `architecture` | Architecture / governance review board | Are the contracts sound? Which open questions do we rule on today? | [§3](#3-architecture-review-board) |
| `security` | Security and compliance leadership | Which deployment modes may carry which data classes, under what conditions | [§4](#4-security-and-compliance-review) |
| `enablement` | The adopting pilot team | Do we commit as the second independent instance, and how do we integrate | [§5](#5-adopting-team-enablement) |

The three variants are **not** funding decks. None of them restates the ask in [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §10, and none of them asks for budget, headcount, or a phase commitment. Where a variant's audience raises funding, the correct move is to route the question back to the investment review rather than answer it in the room.

---

## 2. Rules that apply to every variant

1. **Nothing is built or piloted.** Every capability statement is design intent plus a delivery plan. Every deck carries that sentence as a standing footer on every slide, and no slide may use present-tense phrasing that implies a running system.
2. **No internal references on a slide.** Agreement, decision, assumption, open-question, requirement and ADR identifiers, document filenames, and section marks belong in the speaker notes, not on the projected surface. The build checker fails a variant that leaks one — this is enforced, not conventional.
3. **Speaker notes keep full traceability.** Every slide carries the four-part note [`VISUAL-DIRECTION.md`](VISUAL-DIRECTION.md) §8 requires — sourcing, the caveat that is deliberately not on the slide, the talk track, and the answer to the question the slide invites. The sourcing part names documents and identifiers exactly as the internal deck does.
4. **Shared facts are stated identically.** The three deployment modes, the exposure model, the enforcement states, the five phases, the validation criteria, the risk rows and the domain entities are the same words in every deck. A fact is never softened for a friendlier audience.
5. **The scope boundary is never dropped.** Guardrails are not authorization ([AGR-010](../00-source-of-truth/AGREEMENTS.md), [AGR-018](../00-source-of-truth/AGREEMENTS.md)); a cooperative connector is not a bypass-proof boundary; Remote Evaluation is not a data-local model ([AGR-009](../00-source-of-truth/AGREEMENTS.md)). Every variant states all three somewhere in its main sequence, never only in an appendix.
6. **Unknowns stay unknown.** No latency budget, availability objective, dollar figure, percentage, or calendar date appears in any deck. Where the source material has none, the deck says TBD or describes the point qualitatively.

---

## 3. Architecture review board

**Audience.** Staff and principal engineers, architects, and the governance body that signs off cross-cutting technical decisions. Technically deep; they will read the contracts rather than the narrative.

**The decision.** Sign off the contracts the platform is built on — the evaluation wire format, the policy schema and its mandatory failure behavior, the enforcement ladder and decision actions, the reason-code taxonomy, connector identity and distribution — and rule on the open questions that block Phase 1 design.

**Format.** 45–60 minutes, working session, expected to be interrupted.

**What the deck does, in order.** What is being reviewed and what is already decided · system context · the container architecture · the policy/connector split · the evaluation contract · the policy document and its failure behavior · the enforcement ladder and decision actions · reason codes · policy storage, distribution and caching · connector identity, liveness and distribution integrity · the three modes and the runtime deployment target · the three mode topologies · extensibility — adding a connector, adding a category · the non-functional posture, with latency and availability named as unspecified · technical risks and the trade-offs already accepted · the open questions this board is being asked to rule on · the ask.

**Appendix.** Domain model · integration manifest · bootstrap configuration · runtime capability manifest · audit coverage · onboarding sequence.

**Primary sources.** [`../50-architecture/`](../50-architecture/) in full, particularly [`CONTAINER-ARCHITECTURE.md`](../50-architecture/CONTAINER-ARCHITECTURE.md), [`SYSTEM-CONTEXT.md`](../50-architecture/SYSTEM-CONTEXT.md), [`NON-FUNCTIONAL-REQUIREMENTS.md`](../50-architecture/NON-FUNCTIONAL-REQUIREMENTS.md), [`TECHNICAL-RISKS.md`](../50-architecture/TECHNICAL-RISKS.md), [`TRADE-OFFS.md`](../50-architecture/TRADE-OFFS.md), [`EXTENSIBILITY-ARCHITECTURE.md`](../50-architecture/EXTENSIBILITY-ARCHITECTURE.md); the accepted ADRs in [`../50-architecture/adrs/`](../50-architecture/adrs/); and [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md).

**Where this deck has to be careful.** It presents design intent to people who can tell design intent from working software, so the pre-build framing is a credibility requirement rather than a disclaimer. An ADR in Proposed status must never be presented as settled. Where an ADR records a decision this board would have made differently, the deck surfaces it rather than routing around it.

---

## 4. Security and compliance review

**Audience.** Security and compliance leadership, privacy, and risk. Policy owners with sign-off authority who will scrutinise content exposure, retention, and the authorization boundary hardest.

**The decision.** Which deployment modes may carry which data classes, and what conditions attach — not whether the platform is a good idea.

**Format.** 30–45 minutes, adversarial by design, followed by written conditions.

**What the deck does, in order.** What is being approved · what AI Guardrails is, in one slide, with no pitch · ownership versus exposure · the three modes annotated by exposure · the three topologies with the content flow drawn · where content goes per mode across the request and response lifecycle · retention and audit records — what is stored and for how long · Remote Evaluation is not a data-local model, stated plainly · guardrails are not authorization · connector bypass as a residual risk, including what the mitigations do not cover · observe before enforce, and why enforcement is gated · failure behavior as a decision the policy owner makes · what this is and is not · the security rows of the risk register · who owns what · the ask.

**Appendix.** Domain model · reason codes · audit coverage · objection handling.

**Primary sources.** [`../50-architecture/SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md), [`../50-architecture/DATA-ARCHITECTURE.md`](../50-architecture/DATA-ARCHITECTURE.md), [`../50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md), [`../70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md), [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) exclusions, and agreements [AGR-008](../00-source-of-truth/AGREEMENTS.md), [AGR-009](../00-source-of-truth/AGREEMENTS.md), [AGR-012](../00-source-of-truth/AGREEMENTS.md), [AGR-014](../00-source-of-truth/AGREEMENTS.md), [AGR-016](../00-source-of-truth/AGREEMENTS.md), [AGR-018](../00-source-of-truth/AGREEMENTS.md).

**Where this deck has to be careful.** This is the audience most likely to be lost by an overclaim and most likely to be won by an accurate one. Exposure statements are quoted, not paraphrased. Connector bypass is presented as accepted residual risk, never as mitigated. No compliance certification, standard, or framework is named as achieved or targeted — none exists in the source material.

---

## 5. Adopting team enablement

**Audience.** The engineering team asked to run the second organizationally independent gateway for the Phase 1–2 validation demonstration, plus their engineering manager. They are not being sold to; they are being asked to do work.

**The decision.** Commit as the second independent instance, starting in observe mode.

**Format.** ~30 minutes, working session, heavy on what changes for them.

**What the deck does, in order.** What you are being asked to do · what AI Guardrails is, in one slide · what changes in your request path · you are on Remote Evaluation, and what that means for your data · what you install — the connector and its bootstrap configuration · the onboarding sequence · observe mode first, with nothing blocked · how to read a decision, including reason codes and audit records · what happens when evaluation is unavailable · what you own versus what the platform owns · what success means for this pilot · what we need from you, and when · the rough edges, stated honestly · the ask.

**Appendix.** Domain model · the three modes · topologies · the questions this team is most likely to ask.

**Primary sources.** [`../50-architecture/INTEGRATION-ARCHITECTURE.md`](../50-architecture/INTEGRATION-ARCHITECTURE.md), [`../50-architecture/OPERABILITY.md`](../50-architecture/OPERABILITY.md), [`../60-delivery/TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md), [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md), [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md), [`../20-product/FAQ.md`](../20-product/FAQ.md), and agreements [AGR-011](../00-source-of-truth/AGREEMENTS.md), [AGR-012](../00-source-of-truth/AGREEMENTS.md), [AGR-019](../00-source-of-truth/AGREEMENTS.md).

**Where this deck has to be careful.** The cost to the adopting team must be stated before the ask, not after it. "Import" must never be allowed to sound like the platform takes over their gateway ([AGR-019](../00-source-of-truth/AGREEMENTS.md)). The integration effort is described qualitatively — no measurement of application change volume exists, so none is quoted. Nothing on this deck may imply the team is adopting a proven system.

---

## 6. What none of these decks may do

* Be shown to an external customer, prospect, analyst, or partner. All four are internal. External material requires a separate review against [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) and is not covered by this document.
* Present a phase, mode, or capability beyond Phase 1 as available. Modes B and C, and everything in Phases 3–5, are sequenced future work.
* Introduce a fact that does not appear in the source material. If a room needs an answer the documents do not contain, the answer is that it is not decided and it goes on the open-questions register.
* Drop the appendix material that answers this audience's predictable objection in order to save time. Cutting the slide that pre-empts the objection is how the objection arrives unanswered.

---

## Related documents

* [`DECK-BRIEF.md`](DECK-BRIEF.md) — governs the internal investment-review deck, and the model this document follows.
* [`DECK-QA.md`](DECK-QA.md) — presenter question preparation for the internal deck; the variants draw their appendix Q&A from the same material.
* [`VISUAL-DIRECTION.md`](VISUAL-DIRECTION.md) — how every deck is built: tokens, slide density, speaker notes, native editable diagrams.
* [`AUDIENCE-AND-PERSONAS.md`](AUDIENCE-AND-PERSONAS.md) — the persona detail behind the four rooms above.
* [`OBJECTION-HANDLING.md`](OBJECTION-HANDLING.md) — objections rather than questions; the security and enablement decks draw on it directly.
* [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) — the governing claims boundary for every deck.
