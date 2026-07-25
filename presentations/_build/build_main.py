"""Main internal proposal deck for AI Guardrails."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deckkit import *  # noqa

OUT = "/home/veglez/own/ai/guardrails/presentations/AI-Guardrails-Internal-Proposal.pptx"

prs = new_deck()
P = [0]


def pg():
    P[0] += 1
    return P[0]


# ------------------------------------------------------------------ 1 title
title_slide(
    prs,
    "Internal team proposal",
    "AI Guardrails",
    "One reusable guardrail policy, applied consistently across every LLM gateway "
    "a team already runs — hosted, managed, or in the customer's own account.",
    "Derived from solution-docs/ (L1 AGREEMENTS · CONSTRAINTS · DECISIONS)   ·   "
    "Status: proposal for internal review   ·   2026-07-24\n"
    "Effort and duration figures in section 5 are planning estimates authored for this deck — "
    "they are NOT part of the source-of-truth register.")

# ------------------------------------------------------------------ 2 agenda
s, y = content_slide(prs, "Agenda", "What we are asking you to review",
                     "Five sections. Sections 3–5 are the ones that need a decision.", pg())
cards(s, y, [
    {"tag": "01", "title": "Capabilities", "color": ACCENT,
     "lines": ["37 cataloged capabilities", "17 core (Phases 1–2)", "20 committed later-phase",
               "13 explicit exclusions"]},
    {"tag": "02", "title": "Use cases & modes", "color": ACCENT2,
     "lines": ["3 deployment modes (A/B/C)", "7 actor-driven use cases",
               "Mode A is the front door", "B and C are graduation paths"]},
    {"tag": "03", "title": "Developer integration", "color": GREEN,
     "lines": ["One evaluation contract", "Connector · SDK · raw HTTP",
               "One companion deck per mode", "Copy-paste examples"]},
], h=Inches(2.35))
cards(s, y + Inches(2.54), [
    {"tag": "04", "title": "Delivery plan", "color": AMBER,
     "lines": ["Five phases, M1–M5", "Nine-criterion validation gate at M2",
               "Sequencing fixed; dates are new"]},
    {"tag": "05", "title": "Effort estimation", "color": RED,
     "lines": ["Full team roster, 9 roles", "~545 person-weeks / ~21,800 h",
               "Cost/hr fields left open for finance"]},
    {"tag": "→", "title": "Decision asked for", "color": INK,
     "lines": ["Approve Phase 1–2 execution", "Name the two pilot LiteLLM teams",
               "Supply blended rates to close the estimate"]},
], h=Inches(2.35))
note(s, "Frame it: sections 1-3 are 'what we are building and how you plug in'. "
        "Sections 4-5 are the ask. Everything except section 5 traces to solution-docs; "
        "section 5 numbers are ours and are explicitly labelled as such.")

# ------------------------------------------------------------------ 3 what it is
s, y = content_slide(prs, "Context", "What AI Guardrails is",
                     "A hosted policy and evaluation service that attaches to a gateway you already run.", pg())
bullets(s, MARGIN, y, Inches(7.0), [
    ("The consumer keeps their gateway, their model provider and their data path.", 0),
    ("The platform hosts policy storage, evaluation, and the audited decision.", 0),
    ("A policy is defined once — checks, thresholds, actions, failure behavior — and "
     "bound to a workload at a specific immutable version.", 0),
    ("LiteLLM is the first connector, Bedrock Guardrails the first engine. Neither is "
     "the product boundary: the contract is vendor-independent by design.", 0),
    ("Security sets the floor (mandatory policies); application teams may strengthen it, "
     "never weaken it.", 0),
], size=14)
rect(s, Inches(8.05), y - Inches(0.05), Inches(4.66), Inches(4.0), PANEL, radius=0.03)
box(s, Inches(8.32), y + Inches(0.20), Inches(4.1), Inches(0.3),
     "THE SIX-ENTITY DOMAIN MODEL (AGR-017)", size=9.5, color=ACCENT, bold=True)
for i, (k, v) in enumerate([
        ("Tenant", "an organization or team"),
        ("Workload", "an app, assistant, agent or gateway"),
        ("Policy", "versioned checks + actions"),
        ("Binding", "workload ↔ policy version + mode"),
        ("Evaluation", "a request to inspect content"),
        ("Decision", "allow · block · redact · log_only")]):
    yy = y + Inches(0.62) + Inches(0.54) * i
    box(s, Inches(8.32), yy, Inches(1.35), Inches(0.3), k, size=12, color=INK, bold=True)
    box(s, Inches(9.62), yy + Inches(0.02), Inches(2.85), Inches(0.4), v, size=11, color=SLATE)
callout(s, MARGIN, SH - Inches(1.85), Inches(7.0),
        "Mode A is never a private-data or data-local model. Evaluated content reaches the hosted "
        "Evaluation API regardless of who runs the gateway (AGR-009). Residency requirements route to Mode C.",
        h=Inches(1.0))
source(s, "solution-docs/20-product/SERVING-MODEL.md §1 · README §1 · AGREEMENTS AGR-003/004/017")

# ================================================================== SECTION 1
section_slide(prs, "01", "Capabilities",
              "37 cataloged capabilities, split by delivery status — plus the 13 things "
              "this product deliberately will not become.", pg())

s, y = content_slide(prs, "Capabilities", "The capability model in one picture",
                     "Everything a consumer touches rides over one Evaluation API.", pg())
lanes = [
    ("CONTROL PLANE", ACCENT, ["Tenants & workloads", "Policies & versions", "Bindings & rollouts",
                               "Promotion / rollback", "Dashboards"]),
    ("GUARDRAILS RUNTIME", ACCENT2, ["Evaluation API", "Policy resolver", "Decision engine",
                                     "Evaluator adapters", "Audit producer"]),
    ("EVALUATION ENGINES", GREEN, ["Bedrock Guardrails", "Deterministic org rules",
                                   "Sensitive-data (PII)", "Schema validators", "(future engines)"]),
    ("INTEGRATION CLIENTS", AMBER, ["LiteLLM connector", "Python SDK", "TypeScript SDK",
                                    "Raw HTTP API", "Guarded Gateway"]),
]
w = (CONTENT_W - Inches(0.28) * 3) / 4
for i, (name, col, rows) in enumerate(lanes):
    x = MARGIN + (w + Inches(0.28)) * i
    rect(s, x, y, w, Inches(3.6), PANEL, radius=0.04)
    rect(s, x, y, w, Inches(0.42), col)
    box(s, x + Inches(0.18), y + Inches(0.11), w - Inches(0.3), Inches(0.3),
         name, size=10.5, color=WHITE, bold=True)
    for j, r in enumerate(rows):
        box(s, x + Inches(0.22), y + Inches(0.68) + Inches(0.52) * j, w - Inches(0.4),
             Inches(0.4), "· " + r, size=11.5, color=INK)
box(s, MARGIN, y + Inches(3.85), CONTENT_W, Inches(0.5),
     "Parity rule: a connector or SDK may never add evaluation semantics the API does not itself expose. "
     "That is what keeps a policy identical across every integration path (AGR-005, AGR-006).",
     size=12, color=SLATE, line=1.25)
source(s, "50-architecture/INTEGRATION-ARCHITECTURE.md §2 · 20-product/SERVING-MODEL.md §4")

s, y = content_slide(prs, "Capabilities", "Core capabilities — Phases 1–2 (available at GA)",
                     "17 capabilities, CAP-001 … CAP-017. These are what the Phase 1–2 release gate is judged against.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Area", "Capabilities", "What it gives a team"],
      [["Policy governance", "CAP-001", "Versioned, immutable, rollback-able policies"],
       ["Workload & binding", "CAP-002, 003", "Register a workload; bind it to a policy version + mode"],
       ["Evaluation", "CAP-004, 005", "Input (pre-model) and output (post-model) evaluation"],
       ["Connectors & APIs", "CAP-006 … 008", "LiteLLM pre/post-call connector + generic HTTP API"],
       ["Evaluation engines", "CAP-009 … 011", "Bedrock adapter, deterministic rules, basic PII handling"],
       ["Decisions & audit", "CAP-012 … 015", "Normalized decisions, observe/enforce, latency, audit metadata"],
       ["Deployment", "CAP-016, 017", "Hosted runtime + architected path to customer-managed"],
       ["Availability", "CAP-035 (avail.)", "Multi-region availability, tenant pinned to one region"]],
      widths=[2.4, 2.4, 7.2], size=12, row_h=Inches(0.46))
source(s, "20-product/CAPABILITIES.md — Core capabilities, Phases 1–2")
note(s, "Do not overclaim: per-tenant residency ENFORCEMENT is later-phase; only availability is core.")

s, y = content_slide(prs, "Capabilities", "Committed later-phase capabilities — Phases 3–5",
                     "20 capabilities, CAP-018 … CAP-037. Estimated and committed, not built during Phases 1–2, not available today.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Theme", "Capabilities", "Phase"],
      [["Extended evaluation scope", "Retrieval content, indirect prompt injection, streaming output, tool-call validation, MCP authorization, memory-write, groundedness, automated reasoning, multilingual packs", "Mostly 5"],
       ["Extended governance", "Human approval workflows, policy signing, policy inheritance, customer evaluator plugins, policy marketplace", "3–5"],
       ["Deployment & commercial", "Dedicated hosted runtimes, air-gapped install, commercial metering, per-tenant residency enforcement, multi-region active-active, per-category decision metrics", "3–5"]],
      widths=[2.6, 8.6, 1.4], size=11.5, row_h=Inches(1.15))
callout(s, MARGIN, y + Inches(4.02), CONTENT_W,
        "Phase assignment is a scope decision, not an editorial one. Nothing in this table may be pulled "
        "forward into Phases 1–2 without a recorded change to the L1 registers (DEC-008, AGR-020).",
        color=AMBER, h=Inches(0.92))
source(s, "20-product/CAPABILITIES.md — Later-phase capabilities · DECISIONS DEC-008, DEC-018–020")

s, y = content_slide(prs, "Capabilities", "What this product deliberately is not",
                     "13 product-category exclusions. They are not a backlog — re-entering any of them needs a new agreement.", pg())
excl = ["General-purpose LLM gateway replacement", "Complete AI governance platform",
        "Model training or fine-tuning", "Proprietary prompt-injection model",
        "Proprietary moderation model", "Complete DLP platform",
        "End-user authentication", "Application business authorization",
        "Fully autonomous agent governance", "Organization-wide AWS enforcement",
        "Cross-cloud deployment", "Full compliance certification",
        "General MCP security platform"]
colw = (CONTENT_W - Inches(0.3) * 2) / 3
for i, e in enumerate(excl):
    col, row = i // 5, i % 5
    x = MARGIN + (colw + Inches(0.3)) * col
    yy = y + Inches(0.42) * row
    box(s, x, yy, colw, Inches(0.36), "✕  " + e, size=12, color=SLATE)
callout(s, MARGIN, y + Inches(2.45), CONTENT_W,
        "Two honesty caveats we carry into every conversation: (1) a cooperatively installed connector "
        "can always be disabled or misconfigured — 'mandatory' in Mode A is a governance guarantee, not a "
        "physical one (AGR-018); (2) a guardrail decision is never an authorization decision (AGR-010).",
        color=RED, label="NON-NEGOTIABLE FRAMING", h=Inches(1.15))
source(s, "20-product/CAPABILITIES.md · 70-governance/CLAIMS-REGISTER.md")

# ================================================================== SECTION 2
section_slide(prs, "02", "Use cases — the three modes",
              "Mode A is the front door. Modes B and C exist to answer two specific "
              "objections, not to compete for the first conversation.", pg())

s, y = content_slide(prs, "Modes", "Three deployment modes at a glance",
                     "One logical evaluation contract across all three. Only the network location of the Evaluation API changes.", pg())
cards(s, y, [
    {"tag": "MODE A · PHASES 1–2 · FRONT DOOR", "title": "Remote Evaluation", "color": ACCENT,
     "lines": ["You keep your own gateway (LiteLLM) or call the API directly",
               "Connector calls the hosted Evaluation API pre- and post-call",
               "Smallest footprint for both sides",
               "Content DOES reach the hosted API"]},
    {"tag": "MODE B · PHASE 3", "title": "Guarded Gateway", "color": ACCENT2,
     "lines": ["Answers: \"we have no gateway\"",
               "Provider-run, OpenAI-compatible endpoint",
               "Provider owns credentials, routing, retries, quotas",
               "Largest provider operational + compliance scope"]},
    {"tag": "MODE C · PHASE 4", "title": "Customer-Managed Runtime", "color": GREEN,
     "lines": ["Answers: \"content can't leave our account\"",
               "Runtime deploys into the customer's AWS account",
               "Local policy cache; runs without control plane",
               "Raw content stays in-account by default"]},
], h=Inches(3.05))
table(s, MARGIN, y + Inches(3.3), CONTENT_W,
      ["Concern", "Mode A", "Mode B", "Mode C"],
      [["Gateway owned by", "Consuming team", "Provider", "Consuming team"],
       ["Evaluation API runs", "Hosted (provider)", "Hosted, inside the gateway", "Customer AWS account"],
       ["Contract shape", "Unchanged", "Unchanged", "Unchanged"]],
      widths=[3.0, 3.4, 3.5, 3.4], size=11.5, row_h=Inches(0.34))
source(s, "20-product/SERVING-MODEL.md §2/§6 · 50-architecture/INTEGRATION-ARCHITECTURE.md §8")

s, y = content_slide(prs, "Modes", "Who owns what, per mode",
                     "Adopting a mode is a commitment on both sides. This table is the commitment.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Ownership item", "Mode A — Remote Evaluation", "Mode B — Guarded Gateway", "Mode C — Customer-Managed"],
      [["Gateway infra & availability", "Consuming team", "Platform operations", "Consuming team"],
       ["Model credentials & routing", "Consuming team", "Platform operations", "Consuming team"],
       ["Retry / fallback / model cost", "Consuming team", "Platform operations", "Consuming team"],
       ["Policy execution & decisions", "AI Guardrails team + Platform ops", "AI Guardrails team (in gateway)", "AI Guardrails team (shipped runtime)"],
       ["Audit storage", "Platform operations", "Platform operations", "Consuming team (local)"],
       ["Runtime upgrades", "Provider applies", "Provider applies", "Consuming team, unless managed support"],
       ["Policy approval, retention, audit rules", "Security & governance", "Security & governance", "Security & governance"]],
      widths=[3.2, 3.4, 3.3, 3.4], size=11, row_h=Inches(0.5))
source(s, "60-delivery/TEAM-AND-OWNERSHIP.md §4")

s, y = content_slide(prs, "Use cases", "Seven use cases, and when each becomes real",
                     "UC-001, 005, 006 and 007 together are the Phase 1–2 validation demonstration — all four, or the gate does not pass.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["ID", "Use case", "Mode", "Status"],
      [["UC-001", "Two independent LiteLLM instances share one central policy", "A", "Phases 1–2 · core demo"],
       ["UC-002", "Onboard in observe mode, then promote to enforce", "Any", "Phases 1–2"],
       ["UC-003", "Regulated workload keeps content inside its own account", "C", "Target state — Phase 4"],
       ["UC-004", "A team with no gateway uses the Guarded Gateway", "B", "Target state — Phase 3"],
       ["UC-005", "Central policy upgrade and immediate rollback", "Any", "Phases 1–2 · core demo"],
       ["UC-006", "Disable raw-content persistence for a sensitive workload", "Any", "Phases 1–2 · core demo"],
       ["UC-007", "Configured failure behavior on evaluation timeout", "Any", "Phases 1–2 · core demo"]],
      widths=[1.1, 7.3, 1.0, 3.0], size=11.5, row_h=Inches(0.42))
source(s, "20-product/USE-CASES.md — use case index")
note(s, "If asked 'what proves this works?': UC-001 is the falsifiable hypothesis — two organizationally "
        "independent LiteLLM instances, same policy version, consistent normalized decisions, no shared "
        "model-provider credentials. One instance tested twice does not count.")

s, y = content_slide(prs, "Use cases", "The canonical onboarding flow",
                     "Seven steps. This is what 'import a policy' means — and what it never means.", pg())
steps = ["Register\nworkload", "Select\npolicy", "Generate\nmanifest", "Install\nconnector",
         "Run in\nobserve", "Review\nresults", "Enable\nenforcement"]
n = len(steps)
bw = (CONTENT_W - Inches(0.22) * (n - 1)) / n
for i, st in enumerate(steps):
    x = MARGIN + (bw + Inches(0.22)) * i
    col = ACCENT if i < 4 else (AMBER if i < 6 else GREEN)
    rect(s, x, y, bw, Inches(1.25), PANEL, radius=0.06)
    rect(s, x, y, bw, Inches(0.06), col)
    box(s, x, y + Inches(0.22), bw, Inches(0.3), str(i + 1), size=13, color=col,
         bold=True, align=PP_ALIGN.CENTER)
    box(s, x + Inches(0.08), y + Inches(0.58), bw - Inches(0.16), Inches(0.6), st,
         size=11.5, color=INK, align=PP_ALIGN.CENTER, line=1.1)
box(s, MARGIN, y + Inches(1.55), CONTENT_W, Inches(0.4),
     "Enforcement progression on a binding:   Disabled  →  Observe  →  Warn  →  Redact  →  Block "
     "     (every promotion is explicit and audited; a new binding never defaults to Block)",
     size=12.5, color=INK, bold=True)
callout(s, MARGIN, y + Inches(2.15), CONTENT_W,
        "Import never requires: copying your LiteLLM database · transferring model-provider credentials · "
        "exposing gateway admin endpoints · replacing your model routing · granting the provider admin access "
        "to your gateway (AGR-019).", color=GREEN, label="WHAT IMPORT IS NOT", h=Inches(1.0))
source(s, "60-delivery/IMPLEMENTATION-STRATEGY.md §7 · AGREEMENTS AGR-011, AGR-019")

# ================================================================== SECTION 3
section_slide(prs, "03", "Developer integration",
              "One contract, three clients. Detailed, copy-paste integration walkthroughs "
              "ship as one companion deck per mode.", pg())

s, y = content_slide(prs, "Integration", "One contract, three clients",
                     "A policy behaves identically whether it is reached through the connector, an SDK, or a raw HTTP call.", pg())
cards(s, y, [
    {"tag": "PAVED PATH", "title": "LiteLLM connector", "color": ACCENT,
     "lines": ["Pre-call + post-call callbacks", "Config-only install, no app code change",
               "Enforces block / redact in the request path", "Propagates the correlation id"]},
    {"tag": "DIRECT", "title": "Python / TypeScript SDK", "color": ACCENT2,
     "lines": ["For apps not fronted by a supported gateway", "Request construction + timeout handling",
               "Same decision object as the connector", "No added evaluation semantics"]},
    {"tag": "ALWAYS OPEN", "title": "Raw HTTP Evaluation API", "color": GREEN,
     "lines": ["Any stack with an authenticated HTTP client", "The real product surface",
               "No consumer is locked out for lacking an SDK", "Conformance-testable"]},
], h=Inches(2.5))
table(s, MARGIN, y + Inches(2.78), CONTENT_W,
      ["The evaluation request carries", "The decision returns"],
      [["tenant id · workload id · stage (input|output) · content payload · correlation id · optional policy hint",
        "action (allow|block|redact|log_only) · allowed · reason codes · policy name+version · evaluator metadata · transformed content · correlation id · audit id · latency"]],
      widths=[5.0, 8.6], size=11, row_h=Inches(0.95))
box(s, MARGIN, y + Inches(4.38), CONTENT_W, Inches(0.4),
     "Companion decks — one per mode:  Mode-A-Remote-Evaluation.pptx  ·  Mode-B-Guarded-Gateway.pptx  ·  Mode-C-Customer-Managed-Runtime.pptx",
     size=12, color=ACCENT, bold=True)
source(s, "50-architecture/INTEGRATION-ARCHITECTURE.md §3, §5 · 20-product/SERVING-MODEL.md §4")

s, y = content_slide(prs, "Integration", "The pre-call / post-call pattern",
                     "The same sequence in all three modes — only the destination of the Evaluation API changes.", pg())
code_block(s, MARGIN, y, CONTENT_W, Inches(3.5), """
Application ──► Gateway / SDK ──► Evaluation API (stage=input, correlation_id=X)
                                  ◄── decision: allow | block | redact | log_only
     block   ──► safe rejection returned to the caller, model never invoked
     redact  ──► model invoked with the transformed content from the decision
     allow   ──► model invoked with the original content

Model ──► Gateway / SDK ──► Evaluation API (stage=output, correlation_id=X)
                            ◄── decision: allow | block | redact | log_only
     block   ──► safe fallback response returned instead of the raw model output
     redact  ──► transformed response returned
     allow   ──► original response returned

Timeout / detector down / policy unresolvable ──► the workload's CONFIGURED failure
behavior runs. There is no undocumented fallback (AGR-012).
""", size=12.5, label="evaluation flow")
callout(s, MARGIN, y + Inches(3.75), CONTENT_W,
        "Two evaluations share one correlation id, so the input decision, the model call, the output decision "
        "and both audit records join into a single traceable request.", color=ACCENT, label="CORRELATION", h=Inches(0.85))
source(s, "50-architecture/INTEGRATION-ARCHITECTURE.md §4.1, §4.4, §4.6")

# ================================================================== SECTION 4
section_slide(prs, "04", "Delivery plan",
              "Five phases, five milestones, one hard validation gate. "
              "Sequencing is agreed; the calendar in this section is our proposal.", pg())

s, y = content_slide(prs, "Delivery", "Five phases — sequencing is fixed by agreement",
                     "Each phase's deliverables must be completed and validated before the next phase's scope is committed (AGR-020).", pg())
phases = [
    ("PHASE 1", "Integration proof", ACCENT,
     ["Evaluation contract", "Policy format", "Bedrock adapter", "LiteLLM connector",
      "Observe mode", "Basic auditing", "Internal deployment"]),
    ("PHASE 2", "Multi-team platform", ACCENT,
     ["Tenant + workload registry", "Policy bindings", "IAM auth", "Quotas",
      "Promotion + rollback", "Service monitoring", "Basic admin workflow"]),
    ("PHASE 3", "Guarded gateway", ACCENT2,
     ["Managed gateway", "Model-provider config", "Per-tenant routing", "Cost + rate controls",
      "Managed credentials", "Residency enforcement", "Active-active"]),
    ("PHASE 4", "Customer-managed runtime", GREEN,
     ["Runtime container", "Helm chart", "ECS template", "IaC module",
      "Local policy cache", "Async policy sync", "Customer telemetry"]),
    ("PHASE 5", "Advanced agent protection", AMBER,
     ["Retrieval evaluation", "Tool-call validation", "Tool-result evaluation", "Memory-write control",
      "Human approvals", "MCP integration", "Delegation limits"]),
]
w = (CONTENT_W - Inches(0.22) * 4) / 5
for i, (tag, name, col, items) in enumerate(phases):
    x = MARGIN + (w + Inches(0.22)) * i
    rect(s, x, y, w, Inches(4.05), PANEL, radius=0.04)
    rect(s, x, y, w, Inches(0.06), col)
    box(s, x + Inches(0.16), y + Inches(0.22), w - Inches(0.3), Inches(0.25), tag,
         size=9.5, color=col, bold=True)
    box(s, x + Inches(0.16), y + Inches(0.50), w - Inches(0.3), Inches(0.6), name,
         size=13.5, color=INK, bold=True, line=1.05)
    for j, it in enumerate(items):
        box(s, x + Inches(0.16), y + Inches(1.22) + Inches(0.38) * j, w - Inches(0.3),
             Inches(0.34), "· " + it, size=10.5, color=SLATE)
box(s, MARGIN, y + Inches(4.25), CONTENT_W, Inches(0.4),
     "Phases 1–2 = the core platform (Mode A).   Phase 3 = Mode B.   Phase 4 = Mode C.   "
     "Phase 5 is committed and estimated, but sequenced last and never resourced inside Phases 1–4.",
     size=12, color=SLATE)
source(s, "20-product/PRODUCT-ROADMAP.md · 60-delivery/MILESTONES.md")

s, y = content_slide(prs, "Delivery", "The gate: nine criteria, all together, at M2",
                     "Phases 1–2 are not validated when the components exist. They are validated when all nine of these hold simultaneously.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["#", "Criterion", "First demonstrable"],
      [["MVP-01", "Two separate LiteLLM instances consume the same policy", "M1"],
       ["MVP-02", "Neither instance shares model-provider credentials with the platform", "M1"],
       ["MVP-03", "Both receive consistent normalized decisions", "M1"],
       ["MVP-04", "One workload runs in observe mode", "M1"],
       ["MVP-05", "Another workload runs in enforce mode", "M1"],
       ["MVP-06", "A policy can be upgraded and rolled back", "M2  ← the gate closes here"],
       ["MVP-07", "Raw content persistence can be disabled", "M1"],
       ["MVP-08", "Evaluation failures follow a configured failure policy", "M1"],
       ["MVP-09", "Runtime can be packaged for customer-managed use, contract unchanged", "M1 (shipped at M4)"]],
      widths=[1.1, 8.6, 3.7], size=11.5, row_h=Inches(0.38))
callout(s, MARGIN, y + Inches(4.0), CONTENT_W,
        "Hard dependency on you: two GENUINELY INDEPENDENT LiteLLM deployments — different teams, different "
        "instances, different provider credentials. One deployment tested twice does not satisfy MVP-01. "
        "We need those two teams named before Phase 1 starts.", color=RED, label="THE ONE ASK THAT BLOCKS US", h=Inches(0.95))
source(s, "60-delivery/MILESTONES.md §8 · 10-charter/SUCCESS-CRITERIA.md §1.2–1.3")

s, y = content_slide(prs, "Delivery", "Proposed schedule and gates",
                     "Sequencing comes from AGR-020. The week counts below are this deck's planning estimate and are not in the source-of-truth set.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Milestone", "Phase", "Proposed duration", "Cumulative", "Exit gate"],
      [["M1", "Integration proof", "10 weeks", "wk 10", "8 of 9 MVP criteria verified"],
       ["M2", "Multi-team platform", "10 weeks", "wk 20", "All 9 criteria — Phases 1–2 VALIDATED"],
       ["M3", "Guarded gateway", "14 weeks", "wk 34", "≥1 workload live end-to-end on Mode B"],
       ["M4", "Customer-managed runtime", "12 weeks", "wk 46", "≥1 deployment validated in a customer account"],
       ["M5", "Advanced agent protection", "16 weeks", "wk 62", "All Phase 5 stages behind the same adapter pattern"]],
      widths=[1.6, 3.6, 2.6, 1.8, 5.0], size=12, row_h=Inches(0.52))
box(s, MARGIN, y + Inches(3.35), CONTENT_W, Inches(0.5),
     "≈ 62 working weeks end to end if phases run strictly back to back (~14 calendar months, before holidays and "
     "gate-review time). M3 and M4 are independent extensions of the M2 baseline and could overlap if staffed separately.",
     size=12, color=SLATE, line=1.25)
callout(s, MARGIN, y + Inches(4.0), CONTENT_W,
        "Funding decision granularity: approve M1+M2 now; treat M3 and M4 as separate, later-gated decisions. "
        "That is the framing the executive brief already uses.", color=ACCENT, label="RECOMMENDATION", h=Inches(0.85))
source(s, "Durations: planning estimate authored for this deck. Phase content: 60-delivery/MILESTONES.md")

s, y = content_slide(prs, "Delivery", "What gates us — dependencies and sequencing risks", None, pg())
table(s, MARGIN, y, CONTENT_W,
      ["Dependency / risk", "Why it gates delivery", "Mitigation in the plan"],
      [["Two independent LiteLLM instances", "MVP-01 cannot be exercised without them", "Name and commit both pilot teams before M1 starts"],
       ["Bedrock Guardrails availability", "First evaluation engine; adapter cannot be validated without it", "All vendor shape absorbed at the adapter boundary"],
       ["Two-account AWS model provisioned", "Nothing deploys without it", "Minimal model is sufficient; no speculative 3rd account"],
       ["Security & governance engagement", "No policy can be authored or promoted without approval", "Engage from Phase 1, not at go-live"],
       ["Connector bypass", "A cooperative connector can be removed or misconfigured", "Disclosed, never marketed as a hard boundary"],
       ["False confidence", "Teams may read 'evaluation passed' as 'AI is secure'", "LiteLLM first, generic API second, against a stable contract"],
       ["Product over-expansion", "Phase 3–5 scope creeping into Phases 1–2", "Phase assignment changes require an L1 register change"]],
      widths=[3.4, 5.3, 5.3], size=11, row_h=Inches(0.5))
source(s, "60-delivery/IMPLEMENTATION-STRATEGY.md §6, §9 · 70-governance/RISK-REGISTER.md")

# ================================================================== SECTION 5
section_slide(prs, "05", "Effort estimation",
              "A full delivery team, sized per phase, in person-weeks and hours. "
              "Rate and cost columns are deliberately left open.", pg())

s, y = content_slide(prs, "Effort", "The team this needs",
                     "Nine roles. Consuming teams participate but are not a program headcount line.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Role", "Owns", "Peak FTE"],
      [["Product lead / PM", "Scope discipline, onboarding experience, success metrics, register hygiene", "0.5"],
       ["Tech lead / architect", "Evaluation contract, policy schema, domain model, ADRs, review gate", "1.0"],
       ["Backend / platform engineers ×3", "Runtime, policy resolver, decision engine, evaluator adapters, control plane", "3.0"],
       ["Connector / SDK engineer", "LiteLLM pre/post-call connector, Python + TypeScript SDKs, conformance suite", "1.0"],
       ["Cloud / DevOps engineer", "AWS footprint, IaC, CI/CD, container + Helm + ECS packaging (Phase 4)", "1.0–2.0"],
       ["Security engineer", "IAM, KMS, tenant isolation, content-exposure review, threat modelling", "0.3–1.0"],
       ["QA / test automation", "Contract conformance, the nine-criterion demo harness, regression, load", "1.0"],
       ["Technical writer / DevRel", "Integration docs, runbooks, per-mode developer guides", "0.3–0.5"],
       ["SRE / platform operations", "Monitoring, capacity, incident response — scales up from Phase 2", "0.5–1.0"]],
      widths=[3.5, 8.3, 1.4], size=11, row_h=Inches(0.42))
source(s, "Roles derived from 30-executive/INVESTMENT-SUMMARY.md §3 and 60-delivery/TEAM-AND-OWNERSHIP.md. "
          "FTE figures are this deck's estimate — the source documents state headcount as TBD.")

s, y = content_slide(prs, "Effort", "Effort by phase — person-weeks and hours",
                     "Person-weeks = Σ(role FTE × phase weeks). Hours = person-weeks × 40. Cost columns are for finance to fill.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Phase", "Weeks", "Team FTE", "Person-weeks", "Hours", "Blended $/hr", "Phase cost"],
      [["1 — Integration proof", "10", "8.1", "81", "3,240", "________", "________"],
       ["2 — Multi-team platform", "10", "8.5", "85", "3,400", "________", "________"],
       ["Core subtotal (Phases 1–2)", "20", "—", "166", "6,640", "________", "________"],
       ["3 — Guarded gateway", "14", "9.5", "133", "5,320", "________", "________"],
       ["4 — Customer-managed runtime", "12", "8.5", "102", "4,080", "________", "________"],
       ["5 — Advanced agent protection", "16", "9.0", "144", "5,760", "________", "________"],
       ["TOTAL — full five-phase scope", "62", "—", "545", "21,800", "________", "________"]],
      widths=[4.0, 1.2, 1.5, 2.2, 1.8, 2.0, 2.0], size=11.5, row_h=Inches(0.44))
box(s, MARGIN, y + Inches(3.28), CONTENT_W, Inches(0.4),
     "Add a risk contingency on top of the hours above before quoting: +20% recommended for Phases 1–2 "
     "(new contract, new domain), +25–30% for Phases 3–5 (larger operational surface, unresolved open questions).",
     size=12, color=AMBER, bold=True, line=1.25)
callout(s, MARGIN, y + Inches(3.88), CONTENT_W,
        "Excluded from these hours: AWS run cost (usage-driven, workload volume is unknown — ASM-001); "
        "Bedrock Guardrails invocation cost; consuming-team effort to install connectors and review observe-mode "
        "findings; compliance certification; any commercial/billing platform.", color=RED, label="NOT IN THE ESTIMATE", h=Inches(0.95))
note(s, "If challenged on the numbers: they are a bottom-up FTE×duration model built for this deck. "
        "The solution docs deliberately say TBD because no figures exist in the seed. These are ours, "
        "labelled as ours, and are meant to be argued with.")

s, y = content_slide(prs, "Effort", "Cost worksheet — fill the rates, the totals compute",
                     "Per-role rates let procurement model a blend rather than a single average.", pg())
table(s, MARGIN, y, CONTENT_W,
      ["Role", "Total person-weeks", "Total hours", "Rate $/hr", "Total cost"],
      [["Product lead / PM", "31.5", "1,260", "________", "________"],
       ["Tech lead / architect", "62.0", "2,480", "________", "________"],
       ["Backend / platform engineers (3)", "174.0", "6,960", "________", "________"],
       ["Connector / SDK engineer", "36.0", "1,440", "________", "________"],
       ["Cloud / DevOps engineer", "81.0", "3,240", "________", "________"],
       ["Security engineer", "37.0", "1,480", "________", "________"],
       ["QA / test automation", "62.0", "2,480", "________", "________"],
       ["Technical writer / DevRel", "29.0", "1,160", "________", "________"],
       ["SRE / platform operations", "33.0", "1,320", "________", "________"],
       ["TOTAL", "545.5", "21,820", "—", "________"]],
      widths=[5.0, 2.6, 2.0, 2.0, 2.0], size=11, row_h=Inches(0.36))
box(s, MARGIN, y + Inches(4.05), CONTENT_W, Inches(0.4),
     "Plus, as separate lines finance should add:   AWS run cost (TBD, usage-driven)   ·   "
     "Bedrock Guardrails invocations (TBD)   ·   contingency %   ·   post-GA run-and-maintain",
     size=12, color=SLATE)
source(s, "Role totals are the per-phase FTE model rolled up; they sum to the 545 person-weeks on the previous slide "
          "(rounding aside). All rates and costs intentionally blank.")

s, y = content_slide(prs, "Effort", "What the estimate assumes",
                     "State these out loud before anyone quotes the number.", pg())
bullets(s, MARGIN, y, Inches(6.3), [
    ("Phases run strictly sequentially, with a validation gate between each.", 0),
    ("A 40-hour person-week; no overtime absorbed into the model.", 0),
    ("The team is dedicated, not time-sliced across other programs.", 0),
    ("Bedrock Guardrails is available in the target account and region from day one.", 0),
    ("The two-account AWS model is provisioned before Phase 1 build starts.", 0),
    ("Two pilot consuming teams are committed and available for the M2 demonstration.", 0),
], size=13)
bullets(s, Inches(7.3), y, Inches(5.4), [
    ("Open questions that could move the number:", 0, RED),
    ("OQ-001 — which enforcement posture the first external customers require", 1),
    ("OQ-009 — the trigger for a third AWS account", 1),
    ("OQ-014 — objective go/no-go criteria for observe → enforce", 1),
    ("OQ-012 — policy signing and inheritance design", 1),
    ("Unset SLA, latency and size limits (CON-015, CON-016)", 1),
], size=13)
callout(s, MARGIN, SH - Inches(1.9), CONTENT_W,
        "Governance note: solution-docs/ states headcount, duration and budget as TBD on purpose — no figure exists "
        "in the seed. Everything numeric in this section was authored for this deck. If leadership adopts these numbers, "
        "they belong in ASSUMPTIONS.md, not in this file.", color=AMBER, label="WHERE THESE NUMBERS LIVE", h=Inches(1.0))

# ------------------------------------------------------------------ close
s, y = content_slide(prs, "Close", "What we need from this room", None, pg())
cards(s, y, [
    {"tag": "DECISION 1", "title": "Approve Phases 1–2", "color": ACCENT,
     "lines": ["20 weeks, 166 person-weeks, 6,640 hours",
               "Gate: all nine MVP criteria at M2",
               "M3 and M4 stay separate, later decisions"]},
    {"tag": "DECISION 2", "title": "Name the two pilot teams", "color": ACCENT2,
     "lines": ["Two organizationally independent LiteLLM instances",
               "Different provider credentials",
               "This is a hard blocker on MVP-01"]},
    {"tag": "DECISION 3", "title": "Supply the rates", "color": GREEN,
     "lines": ["Per-role or blended $/hr",
               "Plus a contingency percentage",
               "Then the cost worksheet closes itself"]},
], h=Inches(2.6))
bullets(s, MARGIN, y + Inches(2.95), CONTENT_W, [
    ("Read next: the three per-mode integration decks — Mode A (Remote Evaluation), "
     "Mode B (Guarded Gateway), Mode C (Customer-Managed Runtime).", 0),
    ("Read before saying anything externally: 70-governance/CLAIMS-REGISTER.md.", 0),
    ("Everything non-numeric in this deck traces to solution-docs/. Where the two ever "
     "disagree, solution-docs wins and this deck gets corrected.", 0),
], size=13)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
prs.save(OUT)
print("wrote", OUT, "-", len(prs.slides.__iter__.__self__._sldIdLst), "slides")
