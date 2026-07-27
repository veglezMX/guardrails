**AI Guardrails — Pitch Deck Q&A Preparation**

**Authority level:** L4
**Status:** Draft
**Date:** 2026-07-26
**Derived from:** AGREEMENTS.md

---

## About this document

This is the presenter's preparation companion for the internal pitch deck built from [`DECK-BRIEF.md`](DECK-BRIEF.md). For each slide it lists the questions this specific audience is most likely to ask, a response the presenter can use close to verbatim, the source that backs it, and — where the question invites an overclaim — the wording that must not be used.

It is an **L4** document. It must not contradict [`../00-source-of-truth/AGREEMENTS.md`](../00-source-of-truth/AGREEMENTS.md) or [`../00-source-of-truth/CONSTRAINTS.md`](../00-source-of-truth/CONSTRAINTS.md) (both **L1**), and every response below is written to stay inside the boundary that [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) enforces. Where this document and that register diverge, **the register governs and this document must be corrected**.

**Relationship to the sibling documents.** [`OBJECTION-HANDLING.md`](OBJECTION-HANDLING.md) answers *objections* — positions a listener argues for. This document answers *questions* — things a listener does not yet know, organized by the slide that prompts them. Where the two overlap, they must agree; where a question is really an objection, this document points at the objection document rather than restating it.

**Standing rule for every answer below.** Nothing is built. Every capability statement is design intent plus a delivery plan. If a question cannot be answered from the source material, the correct response is to say so and take it away — §6 lists the questions that are in that state today, so the presenter recognises them in the room instead of improvising.

---

## Table of contents

<!-- BEGIN GENERATED: toc -->
- [About this document](#about-this-document)
- [1. How to use this document](#1-how-to-use-this-document)
- [2. Cross-cutting questions](#2-cross-cutting-questions)
  - [2.1 "We run workloads on Azure. Does this support other clouds?"](#21-we-run-workloads-on-azure-does-this-support-other-clouds)
  - [2.2 "What does this add to every request in latency?"](#22-what-does-this-add-to-every-request-in-latency)
  - [2.3 "What will this cost, and how long will it take?"](#23-what-will-this-cost-and-how-long-will-it-take)
  - [2.4 "Why not just use Amazon Bedrock Guardrails directly?"](#24-why-not-just-use-amazon-bedrock-guardrails-directly)
  - [2.5 "How good is the detection actually going to be?"](#25-how-good-is-the-detection-actually-going-to-be)
  - [2.6 "What about streaming responses?"](#26-what-about-streaming-responses)
  - [2.7 "What availability and which Regions?"](#27-what-availability-and-which-regions)
  - [2.8 "What happens to the guardrails teams have already built?"](#28-what-happens-to-the-guardrails-teams-have-already-built)
  - [2.9 "Nobody here is volunteering a second team. Now what?"](#29-nobody-here-is-volunteering-a-second-team-now-what)
- [3. Questions per slide — core deck](#3-questions-per-slide--core-deck)
  - [Slide 1 — Cover](#slide-1--cover)
  - [Slide 2 — The problem](#slide-2--the-problem)
  - [Slide 3 — Why this is costing us](#slide-3--why-this-is-costing-us)
  - [Slide 4 — Introducing AI Guardrails](#slide-4--introducing-ai-guardrails)
  - [Slide 5 — How it works, at a glance](#slide-5--how-it-works-at-a-glance)
  - [Slide 6 — Policy vs. connector](#slide-6--policy-vs-connector)
  - [Slide 7 — Three ways to adopt](#slide-7--three-ways-to-adopt)
  - [Slide 8 — Ownership vs. exposure](#slide-8--ownership-vs-exposure)
  - [Slide 9 — Safe by design: observe before enforce](#slide-9--safe-by-design-observe-before-enforce)
  - [Slide 10 — What this is — and isn't](#slide-10--what-this-is--and-isnt)
  - [Slide 11 — Why this is different](#slide-11--why-this-is-different)
  - [Slide 12 — The path we're proposing](#slide-12--the-path-were-proposing)
  - [Slide 13 — What success looks like](#slide-13--what-success-looks-like)
  - [Slide 14 — Principal risks, and what we do about them](#slide-14--principal-risks-and-what-we-do-about-them)
  - [Slide 15 — Who owns what](#slide-15--who-owns-what)
  - [Slide 16 — The ask](#slide-16--the-ask)
- [4. Questions per slide — appendix](#4-questions-per-slide--appendix)
  - [Slides 18–20 — Mode A / B / C topologies](#slides-1820--mode-a--b--c-topologies)
  - [Slide 21 — What "import" means](#slide-21--what-import-means)
  - [Slide 22 — Trust and enforcement postures](#slide-22--trust-and-enforcement-postures)
  - [Slide 23 — Phase 1–2 validation criteria in full](#slide-23--phase-12-validation-criteria-in-full)
  - [Slide 25 — Core domain model](#slide-25--core-domain-model)
- [5. Answers that must never be given](#5-answers-that-must-never-be-given)
- [6. Questions with no answer today](#6-questions-with-no-answer-today)
- [Related documents](#related-documents)
<!-- END GENERATED: toc -->
---

## 1. How to use this document

* **Before the meeting**, read §2. Those questions are not tied to a slide and can arrive at any moment; a presenter who has to search for them mid-answer loses the room.
* **During the meeting**, the per-slide sections (§3–§4) are ordered to match the deck, so the relevant block is always the one matching the slide on screen.
* **When an answer is not there**, check §6 before improvising. A question in §6 has no answer in the source material, and inventing one violates the claims register.
* **Answers are written to be spoken.** They are deliberately shorter than the underlying documents and use the "designed to / supports / is intended to" discipline required for pre-delivery material.

---

## 2. Cross-cutting questions

These arrive at unpredictable moments. Each is the kind of question that, answered badly once, colours everything after it.

### 2.1 "We run workloads on Azure. Does this support other clouds?"

**Answer.** No — not today, and not as a near-term capability. Amazon Web Services is the primary and initial cloud platform, and cross-cloud deployment is an explicit exclusion from the feature set, not an unfinished corner of it ([AGR-013](../00-source-of-truth/AGREEMENTS.md); [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md), out-of-scope items 11 and 12).

**The distinction worth drawing.** Two different things are often collapsed into this question:

* **The policy contract is not AWS-shaped.** Policies do not depend on a specific cloud, model provider, gateway product, language, or framework ([AGR-005](../00-source-of-truth/AGREEMENTS.md)). An Azure-hosted application calling a gateway that has a connector is not excluded by the policy model.
* **The runtime and the first evaluation engine are AWS-bound.** The hosted control plane, runtime, and audit pipeline are designed and delivered on AWS ([CON-001](../00-source-of-truth/CONSTRAINTS.md)), and the first managed evaluation engine is Amazon Bedrock Guardrails ([AGR-004](../00-source-of-truth/AGREEMENTS.md)). Serving a non-AWS cloud would require both a non-AWS hosting story and at least one non-Bedrock evaluation engine behind the existing adapter pattern.

So the honest framing is: *the contract was designed so this would not require a rewrite; the deployment work has not been scoped, estimated, or funded, and is currently excluded.*

**What an Azure-hosted team can do today.** An application running anywhere can call a gateway that carries a connector, and that gateway can call the hosted Evaluation API — subject to the Mode A content-exposure statement, which applies unchanged ([AGR-009](../00-source-of-truth/AGREEMENTS.md)). What is not available is running the guardrail runtime itself inside a non-AWS environment; the Customer-Managed Runtime is specified for the customer's own **AWS** account ([AGR-002](../00-source-of-truth/AGREEMENTS.md), Mode C).

**Do not say.** "Deploys anywhere" (explicitly restricted wording in [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md)); "multi-cloud"; "cross-cloud is on the roadmap" — it is an exclusion, and moving it into scope requires the scope-change process and a recorded decision, not a verbal commitment in a review.

**If pressed for a path.** The correct answer is that cross-cloud support would be a scope change under [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) §8, requiring a recorded decision — and that the person asking should register the demand so it can be weighed, not that the team will "look into it."

### 2.2 "What does this add to every request in latency?"

**Answer.** Remote input and output evaluation adds synchronous network and processing latency to every evaluated call — that is inherent to Mode A and Mode B, not an implementation defect. No numeric latency budget has been set, and there is no figure in the source material to quote ([ASM-011](../00-source-of-truth/ASSUMPTIONS.md)).

What is designed rather than hoped: per-evaluator latency is measured; only the evaluators a policy actually needs are applied; stage-specific timeouts are part of a policy's failure behaviour rather than a runtime default ([AGR-012](../00-source-of-truth/AGREEMENTS.md)); and Customer-Managed Runtime exists partly for latency-sensitive workloads, since evaluation happens locally. The plan is to measure P50/P95/P99 against pilot workloads during Observe mode, before any workload is promoted to Enforce.

**Do not say.** Any millisecond figure, any "negligible" or "minimal overhead" characterisation. Neither exists in the source material.

### 2.3 "What will this cost, and how long will it take?"

**Answer.** Specific budget, headcount, and timeline are not present in the source material and must be supplied by the delivery and product leads before a funding decision is finalised ([ASM-014](../00-source-of-truth/ASSUMPTIONS.md), and skills/provisioning assumptions [ASM-006](../00-source-of-truth/ASSUMPTIONS.md), [ASM-007](../00-source-of-truth/ASSUMPTIONS.md)). What can be said qualitatively is that the initial phases run on a minimal AWS footprint — one non-production and one production account, on managed AWS services rather than novel infrastructure ([AGR-013](../00-source-of-truth/AGREEMENTS.md)).

**Frame the ask correctly.** We are asking to approve the scope and the estimation basis — the full feature set across five phases ([DEC-008](../00-source-of-truth/DECISIONS.md)) — with execution sequenced behind per-phase validation gates ([AGR-020](../00-source-of-truth/AGREEMENTS.md)). Funding releases can follow those gates. Approving scope is not the same as releasing all funding at once.

**Do not say.** Any dollar amount, headcount, or delivery date, including a range offered "just to give a sense."

### 2.4 "Why not just use Amazon Bedrock Guardrails directly?"

**Answer.** For a single team on a single gateway with a single provider, Bedrock Guardrails directly is a reasonable choice — and this platform uses it as its first managed evaluation engine, so the detection capability is not the difference ([AGR-004](../00-source-of-truth/AGREEMENTS.md)).

The difference appears at organisational scale, in four places: a policy expressed once and bound to many workloads across teams and gateways rather than re-expressed per team ([AGR-005](../00-source-of-truth/AGREEMENTS.md)); a normalized decision structure, reason-code vocabulary, and audit trail that is the same across teams rather than per-vendor ([AGR-006](../00-source-of-truth/AGREEMENTS.md)); a graduated observe-before-enforce rollout with auditable promotion, which is a platform behaviour rather than a detector feature ([AGR-011](../00-source-of-truth/AGREEMENTS.md)); and the freedom to add or replace evaluation engines behind the adapter without changing policy semantics.

Put plainly: Bedrock Guardrails is a detector. What is missing today is the policy, binding, decision, and evidence layer around it.

**Do not say.** Anything that positions Bedrock Guardrails as inadequate, or that describes it as the platform's permanent or exclusive engine — both are prohibited directions.

### 2.5 "How good is the detection actually going to be?"

**Answer.** Detection quality in the initial phases rests on Amazon Bedrock Guardrails plus basic deterministic organisational rules. Whether that is adequate is a **named, currently unvalidated assumption** ([ASM-017](../00-source-of-truth/ASSUMPTIONS.md)) — not a measured result. The plan is to evaluate detection quality against representative, risk-specific datasets during Phase 1–2 Observe mode, before any workload is promoted to Enforce. A proprietary prompt-injection or moderation model is explicitly excluded from the feature set.

**Do not say.** "Blocks all prompt injection," "stops every jailbreak," any percentage, or any absolute detection claim.

### 2.6 "What about streaming responses?"

**Answer.** Streaming output evaluation is an open question with no committed timeline — when it ships, and what applies to streaming workloads until then, are both undecided ([OQ-004](../00-source-of-truth/OPEN-QUESTIONS.md)). A team whose workload is predominantly streaming should be treated as a candidate for a later phase, not as a Phase 1 pilot.

### 2.7 "What availability and which Regions?"

**Answer.** Multi-region availability is offered from GA with tenant-pinned single-region processing ([DEC-018](../00-source-of-truth/DECISIONS.md), [CAP-035](../20-product/CAPABILITIES.md)). Multi-region **active-active** availability is committed as a later-phase capability ([DEC-019](../00-source-of-truth/DECISIONS.md), [CAP-036](../20-product/CAPABILITIES.md)) — not delivered, and no longer excluded. Which Regions are supported at GA, and how per-tenant residency commitments are enforced, remain open ([OQ-003](../00-source-of-truth/OPEN-QUESTIONS.md)). No availability objective has been set numerically ([ASM-012](../00-source-of-truth/ASSUMPTIONS.md)).

**Do not say.** Any SLA or availability percentage; that active-active is available; or, in the other direction, that it is excluded or never planned. Both errors are specifically called out.

### 2.8 "What happens to the guardrails teams have already built?"

**Answer.** The source material does not define a migration path for existing bespoke safeguards, and this deck does not claim one. What the platform offers is adoption alongside: a team registers a workload, binds a policy, runs it in Observe, and compares the platform's decisions against whatever it does today before changing anything it owns ([AGR-011](../00-source-of-truth/AGREEMENTS.md), [AGR-019](../00-source-of-truth/AGREEMENTS.md)). Whether existing checks are then retired is that team's call, not a precondition of onboarding.

**Flag it honestly.** If asked for a migration plan, say one has not been written. It is a real gap, recorded in §6.

### 2.9 "Nobody here is volunteering a second team. Now what?"

**Answer.** Then the primary ask still stands, and the second-team commitment becomes a follow-up action with a named owner rather than a room decision. It matters because the Phase 1–2 validation demonstration requires two genuinely independent LiteLLM deployments — one instance exercised twice does not satisfy it ([`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) §1.3) — and the availability of two such teams is an unvalidated assumption today ([ASM-002](../00-source-of-truth/ASSUMPTIONS.md), [ASM-003](../00-source-of-truth/ASSUMPTIONS.md)).

**Do not** trade scope for a volunteer, or redefine the demonstration in the room to fit whoever is available.

---

## 3. Questions per slide — core deck

### Slide 1 — Cover

| Question | Response | Anchor |
|---|---|---|
| Is any of this running today? | No. Delivery has not been approved or started. This is design intent plus a delivery plan, and the approval being requested is what starts execution. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §9 |
| Who has already seen this? | Answer factually from the meeting's own history; the source material makes no claim about prior review. | — |
| Why is the styling unbranded? | No brand kit exists in the source material yet. The slide structure is built so a brand swap is mechanical rather than a redesign. | [`VISUAL-DIRECTION.md`](VISUAL-DIRECTION.md) §1 |

### Slide 2 — The problem

| Question | Response | Anchor |
|---|---|---|
| How many teams actually have this problem? | Not measured. There is no survey, incident count, or duplication estimate in the source material, and I am not going to invent one. The argument is structural: nothing in how we work today would produce a shared policy even if every team wanted one. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §2 |
| Has an incident already happened because of this? | Not documented. If one has, it belongs in the risk register and this deck should be updated to cite it. | [`../70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md) |
| Isn't this a problem teams should solve themselves? | They can — and each one currently does, differently. The cost is that no policy, decision format, or audit trail is shared, so governance cannot answer "is this policy actually applied" for the organisation. | [`../10-charter/CHARTER.md`](../10-charter/CHARTER.md) |

### Slide 3 — Why this is costing us

| Question | Response | Anchor |
|---|---|---|
| Why is there no cost number? | Because none exists in the authoritative source material, and per our own source-of-truth policy an invented figure is worse than an absent one. The estimation over the full feature set is owed by delivery and product leads before final sign-off. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §3; [ASM-014](../00-source-of-truth/ASSUMPTIONS.md) |
| Can you at least estimate the duplicated engineering effort? | Not credibly, and a number produced under pressure in this room would end up quoted back at us. What can be committed is producing it as part of the estimation. | [`../00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md) |
| Which of these five dimensions matters most? | Governance consistency and compliance/residency are the two that no individual team can fix for itself — the others are painful but locally solvable. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §3 |

### Slide 4 — Introducing AI Guardrails

| Question | Response | Anchor |
|---|---|---|
| Why standalone rather than a feature in our LiteLLM? | Embedding it couples policy enforcement to one team's gateway lifecycle and blocks reuse by teams on different gateways or providers. Standalone also decouples release cadence, ownership, and roadmap from that team. | [AGR-001](../00-source-of-truth/AGREEMENTS.md) |
| "AWS-based" — does that constrain who can adopt it? | It constrains where the runtime is hosted, not which applications can call it. See §2.1 for the full answer; the short version is that cross-cloud deployment is an explicit exclusion. | [AGR-013](../00-source-of-truth/AGREEMENTS.md); [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) |
| Does this replace our LLM gateway? | No. A general-purpose gateway replacement is an explicit exclusion. Mode B offers a managed gateway for teams that do not have one — that is an option, not a replacement programme. | [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md); [AGR-002](../00-source-of-truth/AGREEMENTS.md) |

### Slide 5 — How it works, at a glance

| Question | Response | Anchor |
|---|---|---|
| What happens when the control plane is down? | Policy distribution is asynchronous, so it is not on the evaluation path. For the hosted runtime, control-plane unavailability is one of the failure modes a policy must define behaviour for. For Customer-Managed Runtime, evaluation continues against the local policy cache with no synchronous dependency. | [AGR-012](../00-source-of-truth/AGREEMENTS.md), [AGR-015](../00-source-of-truth/AGREEMENTS.md) |
| Is this a single point of failure for every LLM call in the org? | For workloads that adopt it and run in an enforcing state, evaluation is in the request path — which is exactly why failure behaviour is a mandatory part of every policy rather than a runtime default, and why nothing starts in a blocking state. | [AGR-011](../00-source-of-truth/AGREEMENTS.md), [AGR-012](../00-source-of-truth/AGREEMENTS.md) |
| Where does the audit trail land, and who can read it? | Audit events flow from the runtime through a queue to an audit archive. Retention rules and audit requirements are owned by security and governance, not by the product team. | [`../50-architecture/CONTAINER-ARCHITECTURE.md`](../50-architecture/CONTAINER-ARCHITECTURE.md); [`../60-delivery/TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md) |
| Can we add our own detectors? | Deterministic rules and custom evaluators are part of the design, and the extension model defines evaluation engines as one of four extension seams. Governance of customer-specific evaluator plugins is still open. | [AGR-021](../00-source-of-truth/AGREEMENTS.md); [OQ-013](../00-source-of-truth/OPEN-QUESTIONS.md) |

### Slide 6 — Policy vs. connector

| Question | Response | Anchor |
|---|---|---|
| So it works with any gateway? | It works with gateways that have a connector. LiteLLM is the first. Adding another means implementing the invocation contract — real work — but it never changes policy semantics, and existing policies are not modified. | [AGR-003](../00-source-of-truth/AGREEMENTS.md), [AGR-007](../00-source-of-truth/AGREEMENTS.md) |
| Who writes connectors — us or the consuming team? | Connectors are product deliverables owned by the AI Guardrails team. Consuming teams install and keep them healthy in cooperative mode; they do not author policy logic inside them. | [`../60-delivery/TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md); [AGR-007](../00-source-of-truth/AGREEMENTS.md) |
| What if a team needs a policy exception? | Workload-specific overrides exist but are deliberately limited by category, and each exception requires a named owner. Whether that limit blocks adoption is a tracked assumption. | [ASM-018](../00-source-of-truth/ASSUMPTIONS.md) |
| How long does a connector stay supported after a new version ships? | The connector version support window and deprecation policy are not yet decided. | [OQ-022](../00-source-of-truth/OPEN-QUESTIONS.md) |

### Slide 7 — Three ways to adopt

| Question | Response | Anchor |
|---|---|---|
| Which mode would we actually use? | Almost certainly Mode A for a Phase 1–2 pilot — it is the mode delivered in the first phases, and it fits teams that already operate a gateway. | [AGR-020](../00-source-of-truth/AGREEMENTS.md) |
| Can we start in one mode and move to another? | The same policy, contract, and decision semantics hold across all three modes by design, so changing mode is not a policy rewrite. It is still a deployment change, and Modes B and C are not available until Phases 3 and 4. | [AGR-006](../00-source-of-truth/AGREEMENTS.md), [AGR-020](../00-source-of-truth/AGREEMENTS.md) |
| Are the three modes equally capable? | Not at the same time. Delivery is phased, and feature-parity gaps between modes are tracked and stated rather than assumed away. Mode B is the only mode where gateway-layer controls can be enforced natively; Mode C runs evaluators locally. | [AGR-002](../00-source-of-truth/AGREEMENTS.md); [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) |
| Does Mode C mean an air-gapped install? | Air-gapped installation is committed but undated, and gated on the first signed regulated or air-gapped customer. Mode C itself does not require air-gapping. | [CAP-033](../20-product/CAPABILITIES.md), [DEC-017](../00-source-of-truth/DECISIONS.md), [OQ-010](../00-source-of-truth/OPEN-QUESTIONS.md) |

### Slide 8 — Ownership vs. exposure

This is the slide most likely to generate a hostile follow-up. Answer precisely and do not soften.

| Question | Response | Anchor |
|---|---|---|
| Can we use Mode A for regulated workloads? | No. Mode A transmits prompts, responses, and evaluated content to the hosted Evaluation API on every evaluated call. Workloads with residency or "content must not leave our account" requirements belong in Customer-Managed Runtime, which lands in Phase 4. | [AGR-009](../00-source-of-truth/AGREEMENTS.md), [AGR-002](../00-source-of-truth/AGREEMENTS.md) |
| Is retrieved content or are tool arguments transmitted? | Retrieved content only where a workload is configured to submit it; the initial scope limits evaluated content to model input and output. Tool-argument evaluation is not in the initial scope. | [`../50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) §2.3; [OQ-006](../00-source-of-truth/OPEN-QUESTIONS.md) |
| Is anything retained? | Not raw prompts and responses, by default — decision metadata is retained, identifiers are hashed where possible, and secrets are removed from telemetry. Any raw-content retention requires explicit tenant-level configuration. It is a default, not an absolute guarantee. | [AGR-014](../00-source-of-truth/AGREEMENTS.md), [CON-014](../00-source-of-truth/CONSTRAINTS.md) |
| Is private connectivity available? | Whether PrivateLink or private connectivity is required for Mode A or Mode B, and by when, is an open question. | [OQ-002](../00-source-of-truth/OPEN-QUESTIONS.md) |
| Will documented content-exposure be enough for our compliance reviewers? | That is an explicit assumption, not a certainty — if reviewers require Mode C instead, that is a finding we want early, and it changes which teams can pilot. | [ASM-016](../00-source-of-truth/ASSUMPTIONS.md) |

### Slide 9 — Safe by design: observe before enforce

| Question | Response | Anchor |
|---|---|---|
| Who decides when a policy is promoted to blocking? | Security and governance own policy approval; the consuming team reviews Observe-mode findings and accepts enforcement. Promotion is an explicit, auditable action, never a side effect of another change. | [AGR-011](../00-source-of-truth/AGREEMENTS.md); [`../60-delivery/TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md) |
| What are the criteria to promote? | Go/no-go criteria for moving a workload from observe to enforce are an open question, with a related decision on promotion criteria recorded. Expect the pilot to help define them. | [OQ-014](../00-source-of-truth/OPEN-QUESTIONS.md), [DEC-012](../00-source-of-truth/DECISIONS.md) |
| Can a team be forced into Block mode? | Mandatory-policy authority — who may pin a policy as mandatory — is an open question. Nothing in the current design defaults a new binding to anything but Observe. | [OQ-020](../00-source-of-truth/OPEN-QUESTIONS.md), [AGR-011](../00-source-of-truth/AGREEMENTS.md) |
| How long does Observe last? | Not defined in the source material; it depends on the workload's traffic volume and the promotion criteria above. Do not quote a duration. | [OQ-014](../00-source-of-truth/OPEN-QUESTIONS.md) |

### Slide 10 — What this is — and isn't

| Question | Response | Anchor |
|---|---|---|
| If a connector can just be removed, what is enforcement worth? | Under cooperative integration, the connector is an engineering standard rather than a hard control, and that residual risk is accepted rather than hidden. Two stronger postures exist — platform-managed and network/IAM-enforced — for workloads where bypass prevention is a formal requirement. Neither is required for the first cooperative delivery. | [AGR-018](../00-source-of-truth/AGREEMENTS.md); [`../50-architecture/SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md) §3 |
| If it is not authorization, what stops an agent doing something destructive? | Nothing here does. Authentication, business authorization, tenant isolation, resource ownership, transaction limits, and approval workflows remain the consuming application's responsibility. Tool and agent authorization is a distinct, later-phase capability and is not available today. | [AGR-010](../00-source-of-truth/AGREEMENTS.md) |
| The slide says "not cross-cloud" — what exactly is excluded? | Running the platform on, or deploying its runtime into, a non-AWS cloud. What is *not* excluded is an application hosted anywhere calling a gateway that carries a connector. See §2.1 for the full split between the cloud-independent policy contract and the AWS-bound runtime. | [AGR-013](../00-source-of-truth/AGREEMENTS.md); [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) |
| Doesn't listing all this weaken the pitch? | It is what makes the rest of the claims checkable. The largest named risk in the charter is false confidence — teams reading a passing evaluation as complete AI security. | [`../70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md); [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) §5 |

### Slide 11 — Why this is different

| Question | Response | Anchor |
|---|---|---|
| Different from what — which products did you compare? | No competitive analysis exists in the source material and I will not improvise one. The differentiation argued here is against how we build guardrails today: per team, per gateway, per provider. | [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) |
| Is portability real, or aspirational? | It is a design commitment with a falsifiable test attached: the Phase 1–2 demonstration requires one policy version applied across two independent deployments producing normalized decisions. If that fails, the claim fails. | [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) MVP-01, MVP-03 |
| You listed three of six differentiators. What are the others? | Ownership separated from exposure, observe-before-enforce as a structural default, and one contract hosted or customer-managed — each already has its own slide, which is why they are not repeated here. | [`../20-product/DIFFERENTIATORS.md`](../20-product/DIFFERENTIATORS.md) |

### Slide 12 — The path we're proposing

| Question | Response | Anchor |
|---|---|---|
| Why approve five phases instead of just Phase 1? | Because a valid delivery estimation requires the full feature set to be defined and estimated. What is sequenced is execution: each phase validates before the next proceeds, and funding releases can follow those gates. | [DEC-008](../00-source-of-truth/DECISIONS.md), [AGR-020](../00-source-of-truth/AGREEMENTS.md) |
| How long is each phase? | No durations or calendar dates exist in the source material. Producing them is part of the estimation owed before final sign-off. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §7 |
| Can we pull Phase 5 forward? | Phase 5 is sequenced last deliberately, and pulling later-phase items forward requires a documented scope-change decision. The pressure to do this is itself a tracked risk. | [DEC-016](../00-source-of-truth/DECISIONS.md), [ASM-015](../00-source-of-truth/ASSUMPTIONS.md) |
| What if Phase 1 fails? | Then Phases 1–2 are not validated, Phase 3 scope is not committed, and the decision to continue returns to this group. That is what the gate is for. | [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) §1.3 |

### Slide 13 — What success looks like

| Question | Response | Anchor |
|---|---|---|
| What if only seven of the nine criteria pass? | Then the demonstration is not met. All nine must be demonstrated together, using two genuinely independent deployments — a subset passing, or the same deployment exercised twice under different labels, does not satisfy it. | [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) §1.3 |
| Who signs off that a criterion is met? | Verification method, environment, and sign-off owner per criterion are TBD and must be defined in the validation plan and gated in the release criteria. | [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md), [`../60-delivery/RELEASE-CRITERIA.md`](../60-delivery/RELEASE-CRITERIA.md) |
| Where are the other four criteria? | On a backup slide, in full. MVP-06 through MVP-09 cover upgrade-and-rollback, disable-able raw-content persistence, configured failure behaviour, and packaging feasibility. All nine gate the phase; only five are argued on this slide. | [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) §1.2 |
| Does MVP-09 mean Mode C arrives in Phase 2? | No. It validates that the runtime can be packaged for customer-managed deployment without changing the connector contract. Full customer-managed deployment is Phase 4. | [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) MVP-09 |
| What are the ongoing metrics after the demonstration? | Adoption, reliability, effectiveness, and product portability are tracked as ongoing success metrics, distinct from the one-time demonstration. Targets are not set numerically. | [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) §2 |

### Slide 14 — Principal risks, and what we do about them

| Question | Response | Anchor |
|---|---|---|
| Which of these worries you most? | Connector bypass and latency. Bypass because Phases 1–2 are deliberately cooperative, so the residual risk is accepted rather than engineered away. Latency because it is inherent to remote evaluation and has not been measured against real pilot traffic yet. | [AGR-018](../00-source-of-truth/AGREEMENTS.md), [ASM-011](../00-source-of-truth/ASSUMPTIONS.md) |
| Is connector bypass actually mitigated? | Not under cooperative integration, and the deck does not claim it is. Configuration validation and connector-health telemetry reduce the chance of accidental bypass; deliberate bypass is prevented only by the platform-managed or network/IAM-enforced postures, neither of which is required for the first delivery. | [`../50-architecture/SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md) §3; [ASM-005](../00-source-of-truth/ASSUMPTIONS.md) |
| What is the latency budget? | None is set. Measuring P50/P95/P99 against pilot workloads during Observe, before any promotion to Enforce, is the plan — the number comes out of the pilot rather than going into it. | [ASM-011](../00-source-of-truth/ASSUMPTIONS.md) |
| Is this the complete risk list? | It is the six principal risks from the executive brief. The fuller register, with status and owners, is maintained separately, and unresolved decisions that could materially change scope or cost are tracked as open questions. | [`../70-governance/RISK-REGISTER.md`](../70-governance/RISK-REGISTER.md); [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) |
| What is the risk of doing nothing? | The five impact dimensions on slide 3 continue and compound as more teams adopt LLMs — but that is an argument, not a measurement, and no quantification exists in the source material. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §3 |

### Slide 15 — Who owns what

| Question | Response | Anchor |
|---|---|---|
| What does a pilot team actually have to do? | Register the workload, select a policy, generate an integration manifest, install the connector, run in Observe, review findings, then decide about enforcement. No credentials are transferred and no gateway administration access is granted. | [AGR-019](../00-source-of-truth/AGREEMENTS.md) |
| Does ownership change by deployment mode? | Yes. In Customer-Managed Runtime the consuming team also owns runtime infrastructure, availability, networking, local audit storage, and upgrades unless managed support is purchased. | [`../60-delivery/TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md) §4 |
| Who is on the delivery team, and do they have the AWS skills? | Team composition is not fixed in the source material, and the availability of the required AWS skills is a tracked assumption with a skills-gap assessment as its mitigation. | [ASM-007](../00-source-of-truth/ASSUMPTIONS.md) |
| Who runs the hosted infrastructure in production? | Platform operations — deployment, monitoring, capacity, networking, and production incident response. | [`../60-delivery/TEAM-AND-OWNERSHIP.md`](../60-delivery/TEAM-AND-OWNERSHIP.md) §2.2 |

### Slide 16 — The ask

| Question | Response | Anchor |
|---|---|---|
| Can we approve Phase 1 only? | Execution starts at Phase 1 regardless. What needs approving is the scope and estimation basis for the full solution; splitting that leaves the estimation invalid. Funding can still be released per gate. | [DEC-008](../00-source-of-truth/DECISIONS.md) |
| What exactly are we not approving? | No budget figure, headcount, or date; no compliance certification; no commercial or metering model; and nothing in the standing scope exclusions. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §10; [`../10-charter/SCOPE.md`](../10-charter/SCOPE.md) |
| What do you need from us if we say yes? | Two things beyond the approval: a second organizationally independent team for the validation demonstration, and agreement that the pilot runs in Observe first. | [ASM-003](../00-source-of-truth/ASSUMPTIONS.md), [AGR-011](../00-source-of-truth/AGREEMENTS.md) |
| Can you come back when you have the numbers? | Yes, and that is the honest fallback — a technical deep-dive or architecture review using the architecture set as the working document, with the estimation supplied. | [`../30-executive/EXECUTIVE-BRIEF.md`](../30-executive/EXECUTIVE-BRIEF.md) §9 |

---

## 4. Questions per slide — appendix

The appendix is only shown in response to a question. These are the follow-ups each backup slide tends to generate once it is on screen.

### Slides 18–20 — Mode A / B / C topologies

| Question | Response | Anchor |
|---|---|---|
| In Mode A, does our model traffic touch your infrastructure? | No. The model invocation stays in your environment; only the input and output evaluation calls reach the hosted Evaluation API. That is a statement about the *call path*, not about content exposure — the evaluated content does reach us. | [`../50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) §2 |
| In Mode B, who holds the model-provider credentials? | We do, or we hold delegated AWS access. That is a material change in provider scope and is a large part of why Mode B is sequenced at Phase 3. | [`../50-architecture/DEPLOYMENT-ARCHITECTURE.md`](../50-architecture/DEPLOYMENT-ARCHITECTURE.md) §3.2 |
| In Mode C, what if the policy cache goes stale? | Degraded-mode behaviour for a stale cache is part of the workload's configured failure behaviour — there is no undocumented default. What staleness window regulated adopters will tolerate is a tracked assumption. | [AGR-012](../00-source-of-truth/AGREEMENTS.md), [ASM-013](../00-source-of-truth/ASSUMPTIONS.md) |
| In Mode C, can we send you nothing at all? | Yes. Telemetry is customer-selected, down to none, and a "no telemetry" configuration remains fully supported. Raw content must not leave the runtime unless explicitly configured. | [AGR-016](../00-source-of-truth/AGREEMENTS.md), [CON-013](../00-source-of-truth/CONSTRAINTS.md) |

### Slide 21 — What "import" means

| Question | Response | Anchor |
|---|---|---|
| How much application change is involved? | Designed to be minimal — primarily connector installation and configuration rather than application rewrites. There is no measurement of actual change volume, so no quantity should be given. | [ASM-016](../00-source-of-truth/ASSUMPTIONS.md); [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) §2 |
| Can a workload adopt more than one policy? | Policy-selection cardinality in the onboarding flow is an open question. | [OQ-021](../00-source-of-truth/OPEN-QUESTIONS.md) |
| Do you get access to our gateway? | No. Import never requires copying a LiteLLM database, transferring model-provider credentials, exposing gateway administration endpoints, replacing model routing, or granting administrative access. | [AGR-019](../00-source-of-truth/AGREEMENTS.md) |

### Slide 22 — Trust and enforcement postures

| Question | Response | Anchor |
|---|---|---|
| Why not start with the strongest posture? | Network/IAM enforcement is explicitly not required for the first cooperative delivery, and pulling it forward adds platform-layer work that does not help prove policy portability — which is what Phase 1 exists to prove. | [AGR-018](../00-source-of-truth/AGREEMENTS.md) |
| Which posture will external customers need? | Undecided and tracked as an open question. | [OQ-001](../00-source-of-truth/OPEN-QUESTIONS.md) |
| How would we know a team had bypassed the connector? | Configuration validation and connector-health telemetry are the associated controls, and the workload's posture is recorded and visible. Under cooperative integration the residual risk is accepted, not eliminated. | [`../50-architecture/SECURITY-ARCHITECTURE.md`](../50-architecture/SECURITY-ARCHITECTURE.md) §3; [ASM-005](../00-source-of-truth/ASSUMPTIONS.md) |

### Slide 23 — Phase 1–2 validation criteria in full

| Question | Response | Anchor |
|---|---|---|
| Why were four criteria kept off the main slide? | Density. Slide 13 carries the five criteria that establish policy portability, which is the hypothesis Phases 1–2 exist to test; the other four are equally binding but are verification detail rather than argument. All nine still gate the phase. | [`VISUAL-DIRECTION.md`](VISUAL-DIRECTION.md) §7; [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) §1.3 |
| Who signs each criterion off? | Not yet assigned. Verification method, environment, and sign-off owner per criterion are TBD and owed in the validation plan before Phase 1 exit. | [`../60-delivery/VALIDATION-PLAN.md`](../60-delivery/VALIDATION-PLAN.md) |
| How would MVP-07 actually be verified? | By showing that evaluated prompts and responses appear in no runtime store, cache, or log after evaluation completes, with retention configured off, while decision metadata still persists. The exact method is part of the TBD above. | [AGR-014](../00-source-of-truth/AGREEMENTS.md), [CON-014](../00-source-of-truth/CONSTRAINTS.md) |

### Slide 25 — Core domain model

| Question | Response | Anchor |
|---|---|---|
| Why is a binding a separate entity from a policy? | Because it is what lets two independent workloads run the same policy version in different enforcement modes — precisely what the Phase 1–2 demonstration must show. | [AGR-017](../00-source-of-truth/AGREEMENTS.md); [`../10-charter/SUCCESS-CRITERIA.md`](../10-charter/SUCCESS-CRITERIA.md) MVP-04, MVP-05 |
| Are policies signed? | Policy signing and policy inheritance design are an open question; today, transport security rather than a signed artifact is the safeguard for policy reaching a customer-managed runtime. | [OQ-012](../00-source-of-truth/OPEN-QUESTIONS.md), [DEC-015](../00-source-of-truth/DECISIONS.md) |
| Does a decision carry cost or token metadata? | Whether token and cost metadata belong in the evaluation contract is an open question. | [OQ-017](../00-source-of-truth/OPEN-QUESTIONS.md) |

---

## 5. Answers that must never be given

Restated from [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) because Q&A is where these slip out. If any of the following is about to leave the presenter's mouth, stop and reframe.

| Never say | Say instead |
|---|---|
| Mode A is private, data-local, or "your data never leaves your environment" | "Bring your own gateway while consuming a hosted guardrail runtime — evaluated content reaches the hosted service." |
| A guardrail decision authorizes an action | "Content evaluation and business authorization are separate; authorization stays with your application." |
| A cooperative connector is bypass-proof or a hard security boundary | "Cooperative integration is an engineering standard; stronger postures exist where bypass prevention is a formal requirement." |
| Bedrock Guardrails is the permanent or only detection engine | "The first managed evaluation engine, behind a vendor-independent contract." |
| Raw content is never retained | "Not persisted by default; retention requires explicit tenant-level configuration." |
| The validation demonstration is proven, achieved, or in progress | "Phases 1–2 will be considered validated when all nine criteria are demonstrated together." |
| Any latency, availability, SLA, adoption, cost, ROI, or dollar figure | "Not present in the source material — it is TBD and owed before final sign-off." |
| "Deploys anywhere" / multi-cloud / cross-cloud support | "AWS is the primary and initial cloud; cross-cloud deployment is an explicit exclusion." |
| Multi-region active-active is available — or that it is excluded | "Committed as a later-phase capability; not delivered, and no longer an exclusion." |
| Any named customer, partner, or reference | Nothing — no customer names, case studies, or references exist to cite. |

---

## 6. Questions with no answer today

These have no answer in the source material. The correct response is to name the gap, say who owns it, and take it away. Attempting an answer in the room is how a claims-register violation enters the record.

| Question | State | Owner to route it to |
|---|---|---|
| What will it cost, in budget or headcount? | TBD — estimation owed before final sign-off ([ASM-014](../00-source-of-truth/ASSUMPTIONS.md)) | Delivery and product leads |
| How long will each phase take? | TBD — no durations or dates in the source material | Delivery lead |
| What latency does evaluation add? | No numeric budget set ([ASM-011](../00-source-of-truth/ASSUMPTIONS.md)) | Technical lead |
| What availability objective will the hosted runtime meet? | Not numerically defined ([ASM-012](../00-source-of-truth/ASSUMPTIONS.md)) | Architecture lead |
| Which Regions at GA, and how is residency enforced? | Open ([OQ-003](../00-source-of-truth/OPEN-QUESTIONS.md)) | Architecture lead |
| Is PrivateLink required? | Open ([OQ-002](../00-source-of-truth/OPEN-QUESTIONS.md)) | Architecture lead |
| When does streaming output evaluation ship? | Open ([OQ-004](../00-source-of-truth/OPEN-QUESTIONS.md)) | Product and technical leads |
| When do retrieval-content evaluation and indirect prompt-injection detection ship? | Open ([OQ-005](../00-source-of-truth/OPEN-QUESTIONS.md)) | Product and technical leads |
| What is the scope and timing of tool-call and MCP work? | Open ([OQ-006](../00-source-of-truth/OPEN-QUESTIONS.md)) | Product lead |
| What are the observe-to-enforce go/no-go criteria? | Open ([OQ-014](../00-source-of-truth/OPEN-QUESTIONS.md)) | Security and governance lead |
| Who may pin a policy as mandatory? | Open ([OQ-020](../00-source-of-truth/OPEN-QUESTIONS.md)) | Security and governance lead |
| Which enforcement posture will external customers require? | Open ([OQ-001](../00-source-of-truth/OPEN-QUESTIONS.md)) | Security and governance lead |
| What is the compliance-certification target? | Open ([OQ-015](../00-source-of-truth/OPEN-QUESTIONS.md)) | Security and governance lead |
| Is there a commercial or metering model? | Open ([OQ-011](../00-source-of-truth/OPEN-QUESTIONS.md)) | Product lead |
| How do teams migrate guardrails they already run? | Not addressed anywhere in the source material | Product lead |
| Is cross-cloud support ever going to happen? | Excluded; changing that requires the scope-change process | Product lead |

---

## Related documents

* [`DECK-BRIEF.md`](DECK-BRIEF.md) — what the deck says, in what order, and the claims boundary it must respect.
* [`VISUAL-DIRECTION.md`](VISUAL-DIRECTION.md) — how the deck is built, including the requirement that every slide carry speaker notes.
* [`OBJECTION-HANDLING.md`](OBJECTION-HANDLING.md) — objections rather than questions; must agree with this document.
* [`../70-governance/CLAIMS-REGISTER.md`](../70-governance/CLAIMS-REGISTER.md) — the governing claims boundary for every answer above.
* [`../00-source-of-truth/OPEN-QUESTIONS.md`](../00-source-of-truth/OPEN-QUESTIONS.md) and [`../00-source-of-truth/ASSUMPTIONS.md`](../00-source-of-truth/ASSUMPTIONS.md) — the registers behind §6.
