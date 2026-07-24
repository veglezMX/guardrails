# Recommended Documentation Set

Treat the **agreements** as the authoritative source. Executive briefs, sales materials, architecture reports, and the charter should be derived views of those agreements rather than independent documents that can contradict them.

```text
solution-docs/
├── README.md
├── DOCUMENT-MAP.md
├── CHANGELOG.md
│
├── 00-source-of-truth/
│   ├── AGREEMENTS.md
│   ├── DECISIONS.md
│   ├── ASSUMPTIONS.md
│   ├── CONSTRAINTS.md
│   ├── OPEN-QUESTIONS.md
│   └── TRACEABILITY-MATRIX.md
│
├── 10-charter/
│   ├── CHARTER.md
│   ├── SCOPE.md
│   ├── PRINCIPLES.md
│   └── SUCCESS-CRITERIA.md
│
├── 20-product/
│   ├── PRODUCT-OVERVIEW.md
│   ├── PROBLEM-STATEMENT.md
│   ├── VALUE-PROPOSITION.md
│   ├── TARGET-USERS.md
│   ├── USE-CASES.md
│   ├── CAPABILITIES.md
│   ├── DIFFERENTIATORS.md
│   ├── PRODUCT-ROADMAP.md
│   └── FAQ.md
│
├── 30-executive/
│   ├── EXECUTIVE-BRIEF.md
│   ├── BUSINESS-CASE.md
│   ├── EXPECTED-OUTCOMES.md
│   ├── INVESTMENT-SUMMARY.md
│   ├── RISKS-AND-DEPENDENCIES.md
│   └── DECISION-REQUEST.md
│
├── 40-sales-deck/
│   ├── DECK-BRIEF.md
│   ├── SLIDE-OUTLINE.md
│   ├── SALES-NARRATIVE.md
│   ├── AUDIENCE-AND-PERSONAS.md
│   ├── CUSTOMER-PAIN-POINTS.md
│   ├── BENEFITS-AND-OUTCOMES.md
│   ├── PROOF-POINTS.md
│   ├── OBJECTION-HANDLING.md
│   ├── CALL-TO-ACTION.md
│   └── VISUAL-DIRECTION.md
│
├── 50-architecture/
│   ├── ARCHITECTURE-REPORT.md
│   ├── REQUIREMENTS.md
│   ├── NON-FUNCTIONAL-REQUIREMENTS.md
│   ├── SYSTEM-CONTEXT.md
│   ├── CONTAINER-ARCHITECTURE.md
│   ├── COMPONENT-ARCHITECTURE.md
│   ├── DATA-ARCHITECTURE.md
│   ├── INTEGRATION-ARCHITECTURE.md
│   ├── SECURITY-ARCHITECTURE.md
│   ├── DEPLOYMENT-ARCHITECTURE.md
│   ├── OPERABILITY.md
│   ├── FEASIBILITY-ASSESSMENT.md
│   ├── TECHNICAL-RISKS.md
│   ├── TRADE-OFFS.md
│   └── adrs/
│       ├── README.md
│       └── ADR-0001-example-decision.md
│
├── 60-delivery/
│   ├── IMPLEMENTATION-STRATEGY.md
│   ├── MILESTONES.md
│   ├── DEPENDENCIES.md
│   ├── TEAM-AND-OWNERSHIP.md
│   ├── VALIDATION-PLAN.md
│   └── RELEASE-CRITERIA.md
│
├── 70-governance/
│   ├── DOCUMENT-GOVERNANCE.md
│   ├── CLAIMS-REGISTER.md
│   ├── RISK-REGISTER.md
│   ├── GLOSSARY.md
│   └── REFERENCES.md
│
├── 80-agent-inputs/
│   ├── README.md
│   ├── EXECUTIVE-BRIEF-AGENT.md
│   ├── SALES-DECK-AGENT.md
│   ├── ARCHITECTURE-REVIEW-AGENT.md
│   ├── CHARTER-AGENT.md
│   └── OUTPUT-CONTRACTS.md
│
└── 90-archive/
    └── README.md
```

## 1. Root files

### `README.md`

The entry point for humans and agents. It should explain:

* What the proposed solution is.
* Where the source-of-truth agreements live.
* Which documents are authoritative.
* Which documents are generated or derived.
* How to navigate the repository.
* Who owns each documentation area.

### `DOCUMENT-MAP.md`

Maps audiences to documents.

| Reader               | Start here                               | Supporting documents                     |
| -------------------- | ---------------------------------------- | ---------------------------------------- |
| Executive sponsor    | `30-executive/EXECUTIVE-BRIEF.md`        | Business case, outcomes, risks           |
| Product or sales     | `20-product/PRODUCT-OVERVIEW.md`         | Value proposition, personas, use cases   |
| Presentation agent   | `40-sales-deck/DECK-BRIEF.md`            | Slide outline, narrative, proof points   |
| Technical lead       | `50-architecture/ARCHITECTURE-REPORT.md` | NFRs, security, deployment, feasibility  |
| Delivery lead        | `60-delivery/IMPLEMENTATION-STRATEGY.md` | Milestones, dependencies, validation     |
| Contributor or agent | `10-charter/CHARTER.md`                  | Agreements, principles, output contracts |

### `CHANGELOG.md`

Records meaningful changes to the solution definition, not every wording edit.

---

# 2. Source-of-truth agreements

This is the most important directory.

## `AGREEMENTS.md`

A consolidated list of decisions that stakeholders have accepted.

Each agreement should contain:

```markdown
## AGR-001 — Deployment ownership

**Status:** Accepted  
**Date:** 2026-07-24  
**Owners:** Product lead, technical lead  
**Agreement:** The solution will support both managed and customer-operated deployment modes.

**Rationale:** Different teams have different data residency and operational ownership requirements.

**Implications:**
- The core engine must be deployment-agnostic.
- Configuration distribution must be separated from runtime traffic routing.

**Affected documents:**
- `10-charter/CHARTER.md`
- `20-product/CAPABILITIES.md`
- `50-architecture/DEPLOYMENT-ARCHITECTURE.md`
```

## `DECISIONS.md`

A lightweight decision log for product, organizational, and delivery decisions. Deep architectural decisions should become ADRs.

## `ASSUMPTIONS.md`

Anything believed to be true but not yet validated:

* Expected user volume.
* Existing infrastructure.
* Available skills.
* Regulatory context.
* Integration availability.
* Budget assumptions.

## `CONSTRAINTS.md`

Non-negotiable boundaries:

* Mandatory cloud provider.
* Approved languages.
* Data residency.
* Security policies.
* Deadline.
* Existing platform dependencies.
* Procurement restrictions.

## `OPEN-QUESTIONS.md`

Questions that can materially change scope, feasibility, cost, or architecture.

Each question should have:

* Owner.
* Target decision date.
* Impact if unresolved.
* Current working assumption.

## `TRACEABILITY-MATRIX.md`

Connects agreements to requirements, capabilities, architecture decisions, risks, and presentation claims.

| Agreement | Product capability        | Architecture section       | ADR      | Executive claim          |
| --------- | ------------------------- | -------------------------- | -------- | ------------------------ |
| AGR-001   | Multiple deployment modes | Deployment architecture    | ADR-0003 | Flexible operating model |
| AGR-002   | Central policy governance | Configuration architecture | ADR-0005 | Consistent controls      |

This prevents generated documents from drifting away from the original agreements.

---

# 3. `CHARTER.md`

The charter should establish the durable identity and boundaries of the initiative.

## Recommended `CHARTER.md` sections

```markdown
# Solution Charter

## 1. Purpose

## 2. Problem being addressed

## 3. Vision

## 4. Target users and stakeholders

## 5. Intended outcomes

## 6. In-scope capabilities

## 7. Out-of-scope capabilities

## 8. Solution boundaries

## 9. Guiding principles

## 10. Operating model

## 11. Ownership and responsibilities

## 12. Success criteria

## 13. Constraints

## 14. Major risks

## 15. Decision rights

## 16. Change-control process

## 17. Related documents
```

The charter should not contain detailed component diagrams or implementation instructions. Its purpose is to prevent scope and intent from changing silently.

Supporting files such as `SCOPE.md`, `PRINCIPLES.md`, and `SUCCESS-CRITERIA.md` are useful when those sections become substantial. For a smaller solution, they can remain sections inside `CHARTER.md`.

---

# 4. Executive reader package

Executives usually need four things:

1. What problem exists.
2. Why it matters.
3. What the solution changes.
4. What decision or investment is required.

## `EXECUTIVE-BRIEF.md`

Keep this to approximately two to four pages.

Recommended structure:

```markdown
# Executive Brief

## Executive summary

## Current problem

## Business impact

## Proposed solution

## Expected outcomes

## Strategic alignment

## Investment and organizational impact

## Principal risks

## Recommended next step

## Decision required
```

## `BUSINESS-CASE.md`

Contains the deeper justification:

* Current-state costs.
* Operational inefficiencies.
* Risk exposure.
* Opportunity cost.
* Expected benefits.
* Investment range.
* Adoption assumptions.
* Cost-avoidance or revenue-enablement logic.

## `EXPECTED-OUTCOMES.md`

Separate outputs from outcomes.

| Type            | Example                                             |
| --------------- | --------------------------------------------------- |
| Output          | Central policy service deployed                     |
| Outcome         | Reduced policy inconsistency across teams           |
| Metric          | 90% of participating services use approved controls |
| Business effect | Lower operational and compliance exposure           |

## `DECISION-REQUEST.md`

State exactly what executives are being asked to approve:

* Discovery funding.
* MVP implementation.
* Team allocation.
* Vendor evaluation.
* Platform adoption.
* Production rollout.

Without this document, executive material can become informative but non-actionable.

---

# 5. Product and sales-deck package

An agent creating a PowerPoint should not be expected to infer the product story directly from architecture files.

Give it a curated presentation package.

## `DECK-BRIEF.md`

This is the primary instruction document for the deck-generation agent.

```markdown
# Sales Deck Brief

## Objective

Create a product-oriented presentation that explains the problem, solution,
business value, differentiation, and recommended next action.

## Primary audience

## Audience knowledge level

## Presentation context

## Desired audience action

## Core message

## Supporting messages

## Required slides

## Claims that may be used

## Claims that must not be made

## Tone

## Visual direction

## Maximum slide count

## Source documents

## Output requirements
```

## `SLIDE-OUTLINE.md`

Define the expected story arc:

```markdown
1. Title and product promise
2. The current problem
3. Why existing approaches are insufficient
4. The proposed solution
5. How the product works
6. Core capabilities
7. User or customer journey
8. Business outcomes
9. Differentiation
10. Adoption model
11. Roadmap
12. Call to action
```

## `SALES-NARRATIVE.md`

Provides the logical transition between slides. It should explain not only what each slide contains, but why it follows the previous one.

## `PROOF-POINTS.md`

Store evidence separately from marketing language.

Each proof point should have:

```markdown
## PP-001 — Reduced integration effort

**Claim:** Teams can adopt shared policies without replacing their existing model gateway.

**Evidence status:** Architectural hypothesis

**Supporting sources:**
- AGR-004
- ADR-0007
- Initial integration prototype

**Restrictions:** Do not describe this as proven production performance.
```

## `CLAIMS-REGISTER.md`

A centralized register prevents sales materials from inventing unsupported claims.

| Claim                              | Status           | Evidence            | Approved wording                        | Restricted wording      |
| ---------------------------------- | ---------------- | ------------------- | --------------------------------------- | ----------------------- |
| Reduces onboarding effort          | Hypothesis       | Design analysis     | “Designed to reduce…”                   | “Reduces effort by 70%” |
| Supports multiple deployment modes | Confirmed design | Architecture report | “Supports managed and standalone modes” | —                       |
| Enterprise-ready                   | Not approved     | None                | —                                       | Do not use              |

## `VISUAL-DIRECTION.md`

Give the agent concrete presentation guidance:

* Brand and design tokens.
* Diagram style.
* Preferred chart types.
* Image policy.
* Icon style.
* Color restrictions.
* Density per slide.
* Whether speaker notes are required.
* Whether diagrams must remain editable.

---

# 6. Architecture validation package

The technical lead needs more than an architecture diagram. The report should make the feasibility argument explicit.

## `ARCHITECTURE-REPORT.md`

This is the consolidated technical report.

```markdown
# Architecture Report

## 1. Executive technical summary

## 2. Architecture objectives

## 3. Scope and system boundaries

## 4. Functional requirements

## 5. Non-functional requirements

## 6. Current-state context

## 7. Proposed architecture

## 8. Major components and responsibilities

## 9. Data architecture

## 10. Integration model

## 11. Identity and access management

## 12. Security and threat considerations

## 13. Deployment and infrastructure

## 14. Availability and resilience

## 15. Scalability and performance

## 16. Observability and operations

## 17. Configuration and secrets management

## 18. Multi-tenancy or isolation model

## 19. Delivery and migration approach

## 20. Technical feasibility assessment

## 21. Trade-offs

## 22. Risks and mitigations

## 23. Open technical questions

## 24. Proofs of concept required

## 25. Recommendation
```

## `NON-FUNCTIONAL-REQUIREMENTS.md`

Make NFRs measurable.

Bad:

```markdown
The system must be highly available.
```

Better:

```markdown
## NFR-AVAIL-001

The managed control-plane API must provide a monthly availability target of
99.9%, excluding approved maintenance windows.

**Measurement:** External synthetic monitoring  
**Validation stage:** Production readiness  
**Source:** AGR-009
```

Recommended NFR categories:

* Availability.
* Reliability.
* Recovery.
* Performance.
* Scalability.
* Security.
* Privacy.
* Auditability.
* Maintainability.
* Portability.
* Operability.
* Cost.
* Compliance.
* Accessibility, when relevant.

## `FEASIBILITY-ASSESSMENT.md`

This deserves its own file because it answers the technical lead’s central question.

```markdown
# Feasibility Assessment

## Overall assessment

Feasible / Feasible with conditions / Requires validation / Not recommended

## Confirmed capabilities

## Unvalidated assumptions

## Required proofs of concept

## Technology maturity

## Integration complexity

## Operational complexity

## Security feasibility

## Skills and staffing requirements

## Cost drivers

## Schedule risks

## Blocking decisions

## Recommendation
```

## ADR directory

Use ADRs for decisions such as:

```text
ADR-0001-use-api-and-sdk-integration-model.md
ADR-0002-separate-control-plane-and-data-plane.md
ADR-0003-support-managed-and-standalone-deployment.md
ADR-0004-use-event-driven-policy-distribution.md
ADR-0005-select-tenant-isolation-model.md
ADR-0006-use-aws-kms-for-signing.md
```

Each ADR should include:

```markdown
# ADR-0001: Decision title

## Status

Proposed / Accepted / Superseded / Rejected

## Context

## Decision

## Alternatives considered

## Consequences

### Positive

### Negative

### Risks

## Validation required

## Related agreements
```

---

# 7. Delivery package

Architecture can be technically feasible while still being impractical to deliver. The delivery files close that gap.

## Expected files

### `IMPLEMENTATION-STRATEGY.md`

Defines:

* Delivery phases.
* MVP boundary.
* Build versus buy decisions.
* Integration order.
* Platform dependencies.
* Adoption model.
* Rollout strategy.

### `MILESTONES.md`

Prefer outcome-based milestones:

```markdown
## Milestone 1 — Architecture validated

Completion criteria:
- Critical ADRs accepted.
- Security threat model reviewed.
- Two highest-risk assumptions tested.
- MVP scope approved.
```

### `VALIDATION-PLAN.md`

Maps claims and assumptions to validation activities.

| Item                      | Validation method                 | Owner         | Required before       |
| ------------------------- | --------------------------------- | ------------- | --------------------- |
| Policy evaluation latency | Performance prototype             | Platform team | MVP approval          |
| Cross-account deployment  | AWS sandbox PoC                   | Cloud team    | Architecture approval |
| Tenant isolation          | Threat model and penetration test | Security      | Production            |

---

# 8. Agent-specific inputs

You can store reusable agent instructions in `80-agent-inputs/`, but they should reference source documents rather than duplicate their content.

## `OUTPUT-CONTRACTS.md`

Define what each agent must produce.

```markdown
# Output Contracts

## Sales deck agent

Produces:
- Editable `.pptx`
- Slide-by-slide source mapping
- Speaker notes
- Claims validation report
- List of missing information

Must not:
- Invent customer metrics
- Present hypotheses as confirmed facts
- alter approved product positioning

## Architecture review agent

Produces:
- Feasibility verdict
- Blocking issues
- Risk-ranked findings
- Contradictions between requirements and architecture
- Required ADRs
- Recommended proofs of concept
```

## Example `SALES-DECK-AGENT.md`

It should define:

* Role.
* Goal.
* Permitted source files.
* Required slide structure.
* Claim-validation rules.
* Visual requirements.
* Output format.
* Conditions requiring warnings.
* Traceability expectations.

The agent should produce a source map such as:

| Slide              | Source                                                  |
| ------------------ | ------------------------------------------------------- |
| Problem statement  | `20-product/PROBLEM-STATEMENT.md`, section 2            |
| Deployment options | `50-architecture/DEPLOYMENT-ARCHITECTURE.md`, section 4 |
| Expected benefits  | `30-executive/EXPECTED-OUTCOMES.md`                     |

---

# Recommended document authority model

Assign an authority level to documents.

| Level                 | Documents                                       | Meaning                                      |
| --------------------- | ----------------------------------------------- | -------------------------------------------- |
| **L1: Authoritative** | Agreements, constraints, accepted ADRs          | Cannot be contradicted by derived content    |
| **L2: Defining**      | Charter, requirements, NFRs                     | Defines solution boundaries and expectations |
| **L3: Analytical**    | Architecture report, feasibility, business case | Interprets and evaluates L1–L2 content       |
| **L4: Communicative** | Executive brief, sales deck inputs, FAQ         | Audience-specific representation             |
| **L5: Generated**     | PowerPoint, summaries, scripts                  | Disposable and reproducible outputs          |

This hierarchy makes conflict resolution straightforward. When a deck conflicts with an accepted ADR, the ADR wins.

---

# Leaner starting structure

The full structure is appropriate for a substantial enterprise initiative. For a smaller solution, begin with:

```text
solution-docs/
├── README.md
├── AGREEMENTS.md
├── CHARTER.md
├── GLOSSARY.md
│
├── product/
│   ├── PRODUCT-OVERVIEW.md
│   ├── PROBLEM-STATEMENT.md
│   ├── VALUE-PROPOSITION.md
│   └── USE-CASES.md
│
├── executive/
│   ├── EXECUTIVE-BRIEF.md
│   └── BUSINESS-CASE.md
│
├── sales-deck/
│   ├── DECK-BRIEF.md
│   ├── SLIDE-OUTLINE.md
│   ├── PROOF-POINTS.md
│   └── VISUAL-DIRECTION.md
│
├── architecture/
│   ├── ARCHITECTURE-REPORT.md
│   ├── NON-FUNCTIONAL-REQUIREMENTS.md
│   ├── FEASIBILITY-ASSESSMENT.md
│   └── adrs/
│
└── governance/
    ├── CLAIMS-REGISTER.md
    ├── OPEN-QUESTIONS.md
    └── TRACEABILITY-MATRIX.md
```

## Minimum required files

For the workflow you described, I would consider these eleven files the practical minimum:

1. `AGREEMENTS.md`
2. `CHARTER.md`
3. `PRODUCT-OVERVIEW.md`
4. `PROBLEM-STATEMENT.md`
5. `EXECUTIVE-BRIEF.md`
6. `BUSINESS-CASE.md`
7. `DECK-BRIEF.md`
8. `SLIDE-OUTLINE.md`
9. `ARCHITECTURE-REPORT.md`
10. `FEASIBILITY-ASSESSMENT.md`
11. `TRACEABILITY-MATRIX.md`

The most important design choice is separating the **canonical solution definition** from its **audience-specific projections**. That lets executive, sales, architecture, and delivery documents evolve without creating multiple competing definitions of the same solution.
