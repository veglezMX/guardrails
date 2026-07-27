"""Internal pitch deck — platform investment review.

Audience: platform engineering leadership, application/product engineering leads,
security and compliance leadership, and the executive sponsors who release
funding. The decision in the room is whether to approve the solution.

This is the original deck and the historical record of the proposal, so it is
the one deck that prints its sourcing on the slides themselves — every footer
names the agreements and documents behind the claim. Content is governed by
solution-docs/40-sales-deck/DECK-BRIEF.md and VISUAL-DIRECTION.md.

Each function below builds exactly one slide, in the order listed in SLIDES.
"""

from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

import diagrams as dg
import facts as f
from lib import (
    ACCENT, ALLOW, BLOCK, BODY_BOTTOM, BODY_Y, CONTENT_W, HEAD_FONT, LOG_ONLY, MARGIN,
    MUTED, SURFACE, SURFACE_ALT, TEXT, box, bullets, caption, notes, panel, para,
    slide_base, table, textbox,
)


def cover(prs, ctx):
    s = slide_base(prs)
    panel(s, MARGIN, 1.55, CONTENT_W, 3.55, fill=SURFACE_ALT)

    _, tf = textbox(s, MARGIN, 0.85, CONTENT_W, 0.45)
    para(tf, "AI GUARDRAILS", size=14, color=ACCENT, bold=True, first=True, space_after=0)

    _, tf = textbox(s, MARGIN + 0.55, 2.05, CONTENT_W - 1.6, 2.8)
    para(tf, "One guardrail policy.", size=34, bold=True, font=HEAD_FONT, first=True,
         space_after=6, line=1.05)
    para(tf, "Any gateway you already run.", size=34, bold=True, font=HEAD_FONT,
         space_after=6, line=1.05)
    para(tf, "The deployment mode your data needs —", size=34, bold=True,
         font=HEAD_FONT, space_after=6, line=1.05)
    para(tf, "not the one we happen to support best.", size=34, color=ACCENT, bold=True,
         font=HEAD_FONT, space_after=0, line=1.05)

    _, tf = textbox(s, MARGIN, 5.42, CONTENT_W, 0.9)
    para(tf, "Requesting approval: the AI Guardrails solution — full feature set, "
             "delivered in five phases", size=17, color=TEXT, first=True, space_after=8)
    para(tf, "Internal platform investment review  ·  Presenter: TBD  ·  2026-07-26  ·  "
             "Nothing in this deck is built or piloted today", size=11.5, color=MUTED,
         space_after=0)

    caption(s, MARGIN, 6.92, CONTENT_W,
            "Placeholder styling — no brand kit exists yet; all colours and type are "
            "stand-ins pending brand (VISUAL-DIRECTION §1).", size=9)
    notes(
        s,
        "DECK-BRIEF.md — Core message and required slide 1; the ask wording traces to "
        "AGR-020 and DEC-008, restated in EXECUTIVE-BRIEF §10.",
        "Nothing here is running in production. The platform is pre-approval and "
        "pre-build; do not let the confidence of the core message imply otherwise.",
        "Open on the core message, unpacked in one breath: today a policy is written "
        "for one gateway and dies there. We want to change that, and we are asking for "
        "the full solution across five gated phases. Then move to what is actually "
        "broken today.",
        "'Is this live?' — No. Delivery has not been approved or started. Everything in "
        "this deck is design intent plus a delivery plan, which is exactly what we are "
        "asking you to fund.",
    )


def the_problem(prs, ctx):
    s = slide_base(
        prs,
        title="Every team is inventing its own guardrails",
        kicker="And none of them can share a policy, a decision format, or an audit trail.",
        footer=ctx.src("CHARTER.md · EXECUTIVE-BRIEF §2 · AGR-001, AGR-005, AGR-011, "
                       "AGR-018"),
    )
    bullets(s, MARGIN, BODY_Y + 0.10, CONTENT_W, [
        "No shared safeguard layer|Each team independently decides how to detect and act on "
        "unsafe prompts, unsafe responses, and policy violations. No common policy "
        "definition, decision format, or audit trail exists across teams.",
        "Safeguards are welded to one gateway and one provider|Where guardrail logic exists, "
        "it sits inside a specific LiteLLM configuration or is shaped around a single "
        "provider's response format — so it cannot be reused by a team that runs "
        "something else.",
        "No graduated path from adoption to enforcement|Without a way to observe a policy's "
        "real behaviour before it can block traffic, a team's only choices are ship "
        "unguarded or risk an outage.",
        "Inconsistent data-exposure posture|Some teams can accept a hosted evaluation "
        "service; others need content to stay inside their own AWS account. No single "
        "product serves both under one policy model.",
        "Cooperative integrations get mistaken for controls|A voluntary connector that can "
        "be removed or misconfigured is sometimes treated as a guaranteed security "
        "boundary.",
    ], size=14, gap=0.53)
    notes(
        s,
        "EXECUTIVE-BRIEF §2 (Current problem), sourced from CHARTER.md; the five points "
        "map to AGR-001, AGR-005, AGR-011, AGR-008/AGR-002, and AGR-018 respectively.",
        "We have no quantified measure of how widespread this is internally — no survey, "
        "no incident count. State it as a structural gap, not as a measured crisis.",
        "Bridge from the cover: this is why that one sentence matters. Walk the five "
        "points quickly; the last one usually earns a nod from security. Next slide "
        "converts these into organisational impact.",
        "'Do we have examples?' — Not documented in the source material. The argument "
        "here is structural: nothing in how we work today would produce a shared policy "
        "even if every team wanted one.",
    )


def what_it_costs(prs, ctx):
    s = slide_base(
        prs,
        title="Why this is costing us",
        kicker="Five dimensions of impact — stated qualitatively, because no dollar "
               "figure exists in the source material.",
        footer=ctx.src("EXECUTIVE-BRIEF §3 · RISK-REGISTER.md · AGR-002 (Mode C)"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.9, [
        ["Dimension", "Impact of the current gap"],
        ["Governance consistency",
         "Security and compliance lack policy-version traceability, decision evidence, and "
         "any proof that policies are actually applied across teams."],
        ["Engineering duplication",
         "Every team that wants LLM safeguards builds or integrates its own detection and "
         "enforcement logic instead of reusing a shared policy."],
        ["Adoption risk",
         "With no observe-before-enforce path, teams choose between shipping without "
         "safeguards and risking broken production traffic from an unproven policy."],
        ["Compliance and residency",
         "Regulated, high-volume, or residency-sensitive workloads have no defined way to "
         "adopt central guardrails without their content leaving their own AWS environment."],
        ["False assurance",
         "Stakeholders may mistake a removable, cooperative integration for a hard "
         "security boundary."],
    ], col_w=[0.26, 0.74], size=12, first_col_bold=True,
        row_h=[0.42, 0.68, 0.68, 0.68, 0.68, 0.62])
    caption(s, MARGIN, BODY_BOTTOM - 0.42, CONTENT_W,
            "Incident cost, duplicated engineering hours, and compliance exposure in "
            "dollar terms are TBD — not present in the source material and deliberately "
            "not estimated here.")
    notes(
        s,
        "EXECUTIVE-BRIEF §3 (Business impact) verbatim in substance; false-assurance row "
        "traces to RISK-REGISTER.md 'False confidence' and AGR-018.",
        "The TBD is deliberate and worth saying out loud. We were asked for a business "
        "case and chose not to manufacture a number we cannot defend — that is a "
        "credibility asset in this room, not a gap.",
        "From 'here is what's broken' to 'here is what it costs us'. Land the TBD line "
        "explicitly, then pivot: the fix is architectural, and it has a name.",
        "'Why no ROI number?' — Because none exists in the source material and inventing "
        "one would violate our own claims register. Delivery and product leads owe the "
        "estimation over the full feature set before final sign-off (ASM-006, ASM-007, "
        "ASM-014).",
    )


def introducing(prs, ctx):
    s = slide_base(
        prs,
        title="Introducing AI Guardrails",
        footer=ctx.src("CLAIMS-REGISTER.md — Product statement (quoted verbatim) · AGR-001"),
    )
    panel(s, MARGIN, BODY_Y - 0.05, CONTENT_W, 2.95)
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(BODY_Y - 0.05),
                             Inches(0.075), Inches(2.95))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False

    _, tf = textbox(s, MARGIN + 0.55, BODY_Y + 0.28, CONTENT_W - 1.2, 2.5)
    para(tf, "AI Guardrails is an AWS-based policy and enforcement platform that applies "
             "reusable safeguards to LLM applications and gateways. Teams may connect "
             "their existing LiteLLM instance to a hosted Evaluation API, consume a fully "
             "managed Guarded Gateway, or deploy the same guardrail runtime within their "
             "own AWS account. Policies remain portable across all three modes, while "
             "operational ownership and content exposure are explicitly defined for each "
             "deployment.", size=19, first=True, space_after=10, line=1.25)
    para(tf, "— Product statement, CLAIMS-REGISTER.md", size=11.5, color=MUTED, italic=True,
         space_after=0)

    _, tf = textbox(s, MARGIN, 5.35, CONTENT_W, 0.9)
    para(tf, "A standalone platform — not a feature inside one team's LiteLLM.", size=20,
         color=ACCENT, bold=True, first=True, space_after=6)
    para(tf, "Its runtime, versioning, release cadence, and roadmap are independent of any "
             "single gateway's lifecycle.", size=13, color=MUTED, space_after=0)
    notes(
        s,
        "CLAIMS-REGISTER.md product statement, quoted verbatim per DECK-BRIEF required "
        "slide 4; the standalone line is AGR-001.",
        "The statement names three modes in one breath — resist explaining them here. "
        "Slide 7 does that properly, and pre-empting it makes this slide dense.",
        "Read the statement rather than paraphrasing it; it is the wording the claims "
        "register approves. Then land the standalone point, because it is the decision "
        "this room is actually being asked to bless. Next: how it is put together.",
        "'Why not just add this to our LiteLLM?' — Because that couples policy "
        "enforcement to one team's gateway lifecycle and blocks reuse by teams on "
        "different gateways or providers (AGR-001).",
    )


def architecture_glance(prs, ctx):
    s = slide_base(
        prs,
        title="How it works, at a glance",
        kicker="Three layers: a control plane that owns policy, a runtime that decides, "
               "and evaluation engines that detect.",
        footer=ctx.src("CONTAINER-ARCHITECTURE.md §1 (container map) · AGR-004"),
    )
    dg.logical_architecture(s)
    notes(
        s,
        "CONTAINER-ARCHITECTURE.md §1, itself reproduced from original charter §10 "
        "(logical architecture); evaluation-engine list per AGR-004.",
        "Bedrock Guardrails is the first managed engine, not the permanent or exclusive "
        "one — normalisation into our own decision structure happens in the evaluator "
        "adapters, which is what keeps the contract vendor-independent.",
        "Give them one mental model and move on: policy is authored centrally, "
        "distributed asynchronously, and executed at the runtime that whichever entry "
        "point calls. The next slide explains why that split is the whole trick.",
        "'What is the full component inventory?' — Deliberately not on this slide. "
        "CONTAINER-ARCHITECTURE.md §2 has the container catalogue if we want to go "
        "deeper after the deck.",
    )


def policy_vs_connector(prs, ctx):
    s = slide_base(
        prs,
        title="Policy vs. connector",
        kicker="This separation is mandatory — it is what lets one policy span many "
               "gateways.",
        footer=ctx.src("AGR-007 · PRINCIPLES.md §5.3 · AGR-005"),
    )
    dg.policy_vs_connector(s)
    caption(s, MARGIN, BODY_BOTTOM - 0.52, CONTENT_W,
            "What this means for your team: when you change gateway, you re-implement a "
            "connector — you do not rewrite, re-approve, or re-audit your policy.",
            size=13.5, color=TEXT)
    notes(
        s,
        "AGR-007 (policies are separate from connectors) — both example strings are the "
        "agreement's own wording; portability consequence is AGR-005.",
        "Adding a connector for a gateway we have not integrated is still real work: it "
        "means implementing the invocation contract. The claim is that policy semantics "
        "do not change, not that every gateway already works out of the box.",
        "This is the load-bearing architectural idea in the deck. If they only remember "
        "one slide, make it this one. Next: the three ways a team can actually adopt it.",
        "'So it works with every gateway?' — No. It works with gateways that have a "
        "connector; LiteLLM is the first. The point is that adding one never touches "
        "policy semantics (AGR-003, AGR-007).",
    )


def three_modes(prs, ctx):
    s = slide_base(
        prs,
        title="Three ways to adopt",
        kicker="A genuine choice based on ownership and residency needs — not a maturity "
               "ladder.",
        footer=ctx.src("AGR-002 · PRODUCT-OVERVIEW.md §4 · DEPLOYMENT-ARCHITECTURE.md §5"),
    )
    dg.mode_columns(s, f.MODES, highlight=0)
    caption(s, MARGIN, BODY_BOTTOM - 0.34, CONTENT_W,
            "One policy definition and one evaluation contract sit above all three. "
            "Mode C lands in Phase 4 — the modes are not equally mature today.")
    notes(
        s,
        "AGR-002 (three deployment modes) and PRODUCT-OVERVIEW.md §4; per-mode ownership "
        "detail in DEPLOYMENT-ARCHITECTURE.md §2–§4.",
        "Mode A transmits prompts and responses to the hosted Evaluation API on every "
        "evaluated call. That caveat gets its own slide next — do not let this slide "
        "leave the impression that A is the 'light touch on data' option.",
        "Frame this as a choice, not a ladder — nobody is expected to graduate from A to "
        "C. Then go straight into the question security is already forming: where does "
        "my content actually go?",
        "'Which mode would we use?' — Almost certainly Mode A for the Phase 1–2 pilot, "
        "since it is the only one delivered in the first phases and it fits teams that "
        "already run LiteLLM.",
    )


def ownership_vs_exposure(prs, ctx):
    s = slide_base(
        prs,
        title="Ownership vs. exposure",
        kicker="Two separate questions. Reduced operational burden never implies reduced "
               "data exposure.",
        footer=ctx.src("AGR-008 · AGR-009 · AGR-014 · AGR-016 · "
                       "DEPLOYMENT-ARCHITECTURE.md §2.3, §3.3, §4.5"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.5, f.EXPOSURE_ROWS,
          col_w=[0.19, 0.40, 0.41], size=11.5, first_col_bold=True,
          row_h=[0.40, 1.05, 1.05, 1.05])
    caption(s, MARGIN, BODY_BOTTOM - 0.50, CONTENT_W, f.RETENTION_CAVEAT)
    notes(
        s,
        "AGR-008 (ownership and exposure are separate concerns), AGR-009 (Mode A is not "
        "data-local), AGR-014 (no raw retention by default), AGR-016 (customer-controlled "
        "telemetry); per-mode statements in DEPLOYMENT-ARCHITECTURE.md §2.3, §3.3, §4.5.",
        "Do not soften the Mode A row under pressure. If someone with a residency "
        "requirement is in the room, the correct answer is Mode C — which lands in "
        "Phase 4, not today.",
        "This is the slide security and compliance came for. Slow down, read the Mode A "
        "cell as written, and let the honesty register. Then move to why adoption is "
        "safe.",
        "'Can we run Mode A for regulated workloads?' — No. Route those to "
        "Customer-Managed Runtime. Mode A must never be described as keeping content "
        "local (AGR-009).",
    )


def observe_before_enforce(prs, ctx):
    s = slide_base(
        prs,
        title="Safe by design: observe before enforce",
        kicker="Every policy supports a non-blocking mode. No team is asked to accept "
               "unproven blocking behaviour on day one.",
        footer=ctx.src("AGR-011 · PRINCIPLES.md §5.6 · CLAIMS-REGISTER.md §1"),
    )
    dg.enforcement_ladder(s, f.ENFORCEMENT_STATES, highlight=1)

    panel(s, MARGIN, BODY_Y + 2.85, CONTENT_W, 1.35)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 3.05, CONTENT_W - 0.7, 1.0)
    para(tf, "Onboarding defaults every new binding to Observe — never Block.", size=16,
         color=ACCENT, bold=True, first=True, space_after=8)
    para(tf, "Promotion between states is an explicit, auditable action, never an implicit "
             "side effect of another change. A team measures false positives, latency, and "
             "business impact on its own traffic before anything it owns can be blocked.",
         size=12.5, color=MUTED, space_after=0)
    notes(
        s,
        "AGR-011 (observe-before-enforce progression); approved wording in "
        "CLAIMS-REGISTER.md §1 'Observe-before-enforce'.",
        "The five states are per policy, per workload — two workloads can sit at "
        "different states against the same policy version, which is exactly what the "
        "Phase 1–2 demonstration exercises.",
        "This slide removes the adoption-risk objection before anyone raises it. Say "
        "plainly: nobody gets blocked on day one. Then go to the scope boundary, which "
        "is where we spend our credibility deliberately.",
        "'Who decides when a policy is promoted?' — Security and governance own policy "
        "approval; the consuming team reviews observe-mode findings and accepts "
        "enforcement. Both transitions are audited.",
    )


def scope_boundary(prs, ctx):
    s = slide_base(
        prs,
        title="What this is — and isn't",
        kicker="Stated on the slide, not buried in an appendix.",
        footer=ctx.src("AGR-010 · AGR-013 · AGR-018 · SCOPE.md · PRODUCT-OVERVIEW.md §9"),
    )
    dg.scope_columns(s, f.IS_ITEMS, f.IS_NOT_ITEMS)
    notes(
        s,
        "AGR-010 (guardrails are not authorization), AGR-018 (trust and enforcement "
        "model), AGR-013 (AWS as primary cloud), SCOPE.md product-category exclusions — "
        "there is no cross-cloud or multi-cloud deployment anywhere in the feature set "
        "(SCOPE.md items 11 and 12) — and PRODUCT-OVERVIEW.md §9.",
        "The single largest risk named in the charter is false confidence — teams "
        "reading a passing guardrail evaluation as complete AI security. Also worth "
        "separating if the cross-cloud line is challenged: the policy schema, decision "
        "structure, and reason codes do not depend on a cloud (AGR-005), but the hosted "
        "control plane, runtime, and first evaluation engine are AWS-bound. It is a "
        "funded-scope boundary, not a contract limitation.",
        "Frame the right-hand column as precision, not limitation: it tells an adopting "
        "team exactly which risks stay theirs. That earns the right to make the "
        "differentiation claim on the next slide.",
        "'We're on Azure — are we excluded?' — From running the guardrail runtime "
        "yourselves, yes; Customer-Managed Runtime is specified for the customer's own "
        "AWS account. An application anywhere can still call a gateway carrying a "
        "connector. Moving cross-cloud into scope needs the SCOPE.md §8 scope-change "
        "process and a recorded decision — not a commitment made here.",
    )


def differentiators(prs, ctx):
    s = slide_base(
        prs,
        title="Why this is different",
        kicker="Three of the six differentiators — the ones this room will test hardest.",
        footer=ctx.src("DIFFERENTIATORS.md §1, §2, §5 · AGR-003, AGR-004, AGR-005, "
                       "AGR-006, AGR-010, AGR-018"),
    )
    dg.numbered_panels(s, [
        ("01", "Model- and gateway-independent contract",
         "The policy schema, evaluation request, decision structure, and reason codes do "
         "not depend on LiteLLM's configuration, Bedrock's response format, a model "
         "provider, a language, or a framework. Vendor behaviour sits behind adapters — "
         "so a policy author writes intent, not integration mechanics."),
        ("02", "Policy portability across all three modes",
         "The same policy, the same contract, and the same decision semantics hold whether "
         "the runtime is ours or yours. Changing deployment mode is not a re-integration "
         "project and does not invalidate a policy your security team already approved."),
        ("05", "Honest scoping, stated in the product",
         "Not authorization. Not bypass-proof in cooperative mode. Not a complete DLP or "
         "governance platform. Publishing the boundary — rather than logging it "
         "internally — is what makes the rest of the claims worth believing."),
    ])
    notes(
        s,
        "DIFFERENTIATORS.md §1, §2, §5 — the three DECK-BRIEF recommends for this "
        "audience; underlying agreements AGR-003/004/005 (§1), AGR-006 (§2), "
        "AGR-010/AGR-018 (§5).",
        "Differentiator 1 is not a claim that every provider or gateway is supported "
        "today. LiteLLM and Bedrock Guardrails are the initial validated integrations; "
        "the claim is about where the seams are.",
        "Three differentiators, not six — the other three (ownership vs exposure, "
        "observe-before-enforce, one contract) already had their own slides. Next: what "
        "we would actually build, and in what order.",
        "'Who else does this?' — No competitive analysis exists in the source material "
        "and I will not invent one. The differentiation argument here is structural, "
        "against how we build guardrails today.",
    )


def roadmap(prs, ctx):
    s = slide_base(
        prs,
        title="The path we're proposing",
        kicker="All five phases are in the approved, estimated scope. Execution is "
               "sequenced, and every phase must validate before the next proceeds.",
        footer=ctx.src("AGR-020 · DEC-008 · PRODUCT-ROADMAP.md · EXECUTIVE-BRIEF §6, §9"),
    )
    dg.phase_roadmap(s, f.PHASES, highlight=0)
    caption(s, MARGIN, BODY_BOTTOM - 0.38, CONTENT_W,
            "Nothing is built or available today. Phases 2–5 are sequenced future work, "
            "each gated on the prior phase's validation. No calendar dates exist in the "
            "source material — durations are TBD.")
    notes(
        s,
        "AGR-020 (five-phase delivery) and DEC-008 (full feature set defined and "
        "estimated); phase objectives from PRODUCT-ROADMAP.md 'Phase sequence at a "
        "glance'.",
        "Phases 3 and 4 carry materially larger operational and security scope for us as "
        "the provider — Mode B puts us in the full inference path. The gates exist "
        "precisely to control that, and funding releases may follow them.",
        "The key sentence: we are asking for the whole solution, and we are asking to be "
        "held to a gate at every step. Anchor 'we are here' visually, then go to what "
        "the first gate actually requires.",
        "'Why approve five phases instead of just Phase 1?' — Because a valid delivery "
        "estimation requires the full feature set to be defined and estimated (DEC-008). "
        "Approving scope is not the same as releasing all funding at once.",
    )


def success_criteria(prs, ctx):
    s = slide_base(
        prs,
        title="What success looks like",
        kicker="Phases 1–2 will be considered validated when all nine criteria are "
               "demonstrated together — not a subset.",
        footer=ctx.src("SUCCESS-CRITERIA.md §1 (MVP-01–MVP-09) · VALIDATION-PLAN.md · "
                       "AGR-020"),
    )
    criteria = [
        "Two separate LiteLLM instances consume the same policy version|MVP-01 — two "
        "genuinely independent deployments, not one exercised twice under different labels",
        "Neither instance shares model-provider credentials with the platform|MVP-02 — "
        "audited against the platform's own stored secrets",
        "Both receive consistent normalized decisions|MVP-03 — same schema, actions, "
        "reason codes, policy version, correlation identifier",
        "One workload runs in observe mode|MVP-04 — evaluations execute, nothing is acted "
        "on in the request path",
        "Another runs in enforce mode against that same policy version|MVP-05 — a "
        "violation triggers the configured enforcement action",
    ]
    bullets(s, MARGIN, BODY_Y + 0.15, 7.15, criteria, size=13, gap=0.62, dot=ACCENT)

    panel(s, 8.35, BODY_Y + 0.10, 4.23, 3.95)
    _, tf = textbox(s, 8.62, BODY_Y + 0.38, 3.70, 3.45)
    para(tf, "The gating rule", size=13, color=ACCENT, bold=True, first=True, space_after=6)
    para(tf, f.GATING_RULE, size=12, color=TEXT, space_after=14)
    para(tf, "The other four", size=13, color=ACCENT, bold=True, space_after=6)
    para(tf, "MVP-06 upgrade and rollback · MVP-07 raw-content persistence can be disabled "
             "· MVP-08 failures follow the configured failure policy · MVP-09 the runtime "
             "packages for customer-managed deployment without changing the connector "
             "contract. Full text in the appendix.", size=12, color=MUTED, space_after=0)

    caption(s, MARGIN, BODY_BOTTOM - 0.52, CONTENT_W,
            "These are targets in the delivery plan, not achieved results — the "
            "demonstration has not been run. Full-solution success means all five phases "
            "delivered and validated. Verification method, environment, and sign-off "
            "owner per criterion are TBD in VALIDATION-PLAN.md.")
    notes(
        s,
        "SUCCESS-CRITERIA.md §1.2 criteria table (MVP-01 through MVP-09) and §1.3 gating "
        "rule; gated as delivery milestones in VALIDATION-PLAN.md.",
        "MVP-09 validates packaging feasibility only — full customer-managed deployment "
        "is Phase 4 work, not Phases 1–2. Do not let it be heard as 'Mode C in phase 2'.",
        "Use future and conditional phrasing throughout: 'will be considered validated "
        "when'. This slide is what makes the ask falsifiable, which is the point. Next: "
        "what it costs each group in effort.",
        "'What if only seven of the nine pass?' — Then Phases 1–2 are not validated and "
        "Phase 3 scope is not committed. The gating rule is all nine together, using two "
        "genuinely independent deployments.",
    )


def principal_risks(prs, ctx):
    s = slide_base(
        prs,
        title="Principal risks, and what we do about them",
        kicker="Our own risk register, on the table rather than in an appendix.",
        footer=ctx.src("EXECUTIVE-BRIEF §8 · RISK-REGISTER.md · AGR-012 · AGR-018 · "
                       "ASM-011, ASM-012"),
    )
    table(s, MARGIN, BODY_Y + 0.02, CONTENT_W, 4.0, f.RISK_ROWS,
          col_w=[0.15, 0.37, 0.48], size=10.5, header_size=11, first_col_bold=True,
          row_h=[0.36, 0.72, 0.58, 0.72, 0.58, 0.50, 0.62])
    caption(s, MARGIN, BODY_BOTTOM - 0.40, CONTENT_W, f.NFR_GAP)
    notes(
        s,
        "EXECUTIVE-BRIEF §8 (principal risks) with mitigations as written there; fuller "
        "register with status and owners in RISK-REGISTER.md. Latency and availability "
        "gaps are ASM-011 and ASM-012.",
        "Connector bypass is not fully mitigated and must not be presented as if it were "
        "— under cooperative integration the residual risk is accepted for teams where "
        "that posture is appropriate (ASM-005). Say so if pushed.",
        "Coming off the success criteria, pre-empt the obvious follow-up: what could go "
        "wrong. Presenting our own risk register earns more credibility than waiting to "
        "be asked. Then move to what this costs each group in effort.",
        "'Which of these worries you most?' — Connector bypass and latency. Bypass "
        "because Phases 1–2 are deliberately cooperative; latency because it is inherent "
        "to remote evaluation and we have not yet measured it against real pilot traffic.",
    )


def who_owns_what(prs, ctx):
    s = slide_base(
        prs,
        title="Who owns what",
        kicker="What this costs your team in effort, before you ask.",
        footer=ctx.src("TEAM-AND-OWNERSHIP.md §1 · EXECUTIVE-BRIEF §7"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.6, f.OWNERSHIP_ROWS,
          col_w=[0.24, 0.76], size=12, first_col_bold=True,
          row_h=[0.42, 0.85, 0.72, 0.72, 0.90])
    caption(s, MARGIN, BODY_BOTTOM - 0.34, CONTENT_W,
            "For an adopting team the recurring cost is small and specific: install and "
            "keep a connector healthy, register the workload accurately, and review "
            "observe-mode findings before enforcement is accepted.")
    notes(
        s,
        "TEAM-AND-OWNERSHIP.md §1 (four ownership groups) and EXECUTIVE-BRIEF §7 "
        "(organizational impact).",
        "Ownership shifts by deployment mode — in Mode C the consuming team also owns "
        "runtime infrastructure, availability, upgrades, and local audit storage. "
        "TEAM-AND-OWNERSHIP.md §4 has the per-mode breakdown if pressed.",
        "Answer the unasked question — 'what does this cost my team?' — before the ask, "
        "so the ask lands without a hidden liability attached to it. Then close.",
        "'What does a pilot team actually have to do?' — Register the workload, select "
        "the policy, install the connector, run in observe, and review findings. No "
        "credentials transferred, no gateway admin access granted (AGR-019).",
    )


def the_ask(prs, ctx):
    s = slide_base(
        prs,
        title="The ask",
        footer=ctx.src("EXECUTIVE-BRIEF §9–§10 · AGR-020 · DEC-008 · AGR-011 · "
                       "ASM-002, ASM-003 · SCOPE.md"),
    )
    panel(s, MARGIN, BODY_Y - 0.05, CONTENT_W, 1.70, fill=SURFACE, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.40, BODY_Y + 0.18, CONTENT_W - 0.8, 1.15)
    para(tf, "Approve the AI Guardrails solution — full feature set, delivered in five "
             "phases, estimated over that full feature set, with execution sequenced so "
             "each phase is validated before the next proceeds.", size=19, bold=True,
         first=True, space_after=6, line=1.2)
    para(tf, "Built as a standalone policy and enforcement platform — not embedded in the "
             "existing internal LiteLLM instance.", size=12.5, color=MUTED, space_after=0)

    _, tf = textbox(s, MARGIN, BODY_Y + 1.75, 6.05, 0.4)
    para(tf, "And, if we can close it in this room", size=13, color=ACCENT, bold=True,
         first=True, space_after=0)
    bullets(s, MARGIN, BODY_Y + 2.18, 6.05, [
        "One more team, beyond ours, willing to operate an organizationally independent "
        "LiteLLM instance for the Phase 1–2 validation demonstration",
        "Agreement that the pilot workload runs in Observe mode first — never Block on "
        "day one",
    ], size=12.5, gap=0.74, dot=ALLOW)

    _, tf = textbox(s, 7.03, BODY_Y + 1.75, 5.55, 0.4)
    para(tf, "What we are not asking for today", size=13, color=MUTED, bold=True,
         first=True, space_after=0)
    bullets(s, 7.03, BODY_Y + 2.18, 5.55, [
        "A specific budget figure or headcount — TBD until the full-feature-set "
        "estimation is supplied",
        "Calendar dates or delivery commitments",
        "Anything in the SCOPE.md exclusions — gateway replacement, complete AI "
        "governance or DLP, business authorization, general MCP security, "
        "cross-cloud, certification",
    ], size=12.5, gap=0.74, dot=LOG_ONLY)

    caption(s, MARGIN, BODY_BOTTOM - 0.20, CONTENT_W,
            "Fallback if we cannot close today: schedule a technical deep-dive or "
            "architecture review with the requesting teams, using the 50-architecture "
            "material as the working document.")
    notes(
        s,
        "EXECUTIVE-BRIEF §9 (recommended next step) and §10 (decision required); "
        "AGR-020 and DEC-008 for scope framing; AGR-011 for the Observe-first condition; "
        "ASM-002/ASM-003 for the second-team assumption.",
        "The two-independent-teams requirement is a named, currently unvalidated "
        "assumption that gates Phase 1. If nobody in the room volunteers, that is a real "
        "finding, not a formality we skipped.",
        "Restate the decision in one sentence, name the two in-room commitments, then be "
        "quiet and let them decide. If it stalls, take the deep-dive fallback rather "
        "than negotiating scope live.",
        "'Can we approve Phase 1 only?' — We can start execution at Phase 1 regardless; "
        "what we need approved is the scope and estimation basis for the full solution. "
        "Funding releases can follow the phase gates.",
    )


def appendix_divider(prs, ctx):
    s = slide_base(prs)
    _, tf = textbox(s, MARGIN, 3.05, CONTENT_W, 1.6)
    para(tf, "Appendix", size=44, bold=True, font=HEAD_FONT, first=True, space_after=10)
    para(tf, "Backup material — use only if raised in Q&A. Not part of the 20–30 minute "
             "core narrative.", size=15, color=MUTED, space_after=0)
    rule = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(2.85),
                              Inches(1.35), Inches(0.045))
    rule.fill.solid()
    rule.fill.fore_color.rgb = ACCENT
    rule.line.fill.background()
    rule.shadow.inherit = False
    caption(s, MARGIN, 5.35, CONTENT_W,
            "Mode A / B / C topologies  ·  Onboarding (“import”) flow  ·  Trust "
            "and enforcement postures  ·  Phase 1–2 validation criteria in full  ·  "
            "Objection handling  ·  Domain model")
    notes(
        s,
        "DECK-BRIEF.md — backup slides are explicitly excluded from the core deck's "
        "maximum slide count.",
        "Do not present the appendix by default; reaching for it unprompted turns a "
        "25-minute decision meeting into a technical review.",
        "Pause here. If the ask closed, stop. If a specific objection is live, jump "
        "straight to the slide that answers it.",
        "n/a — divider slide.",
    )


def topology_a(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Appendix — Mode A: Remote Evaluation",
        "Bring your own gateway while consuming a hosted guardrail runtime.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §2 · AGR-002 · AGR-009"),
        dg.mode_a_topology,
        "Prompts, responses, and any additional evaluated content are transmitted to the "
        "hosted Evaluation API on every evaluated call. This is not a private-data or "
        "data-local model. The model invocation itself never touches AI Guardrails "
        "infrastructure — only the evaluation calls do.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §2.1 topology, §2.2 ownership split, §2.3 "
        "content-exposure statement; framing mandated by AGR-009.",
        "Decision metadata, evaluation latency, and audit records flow to the hosted "
        "control plane as part of normal operation — that is not optional telemetry in "
        "this mode, it is how the service works.",
        "Use this when someone wants the Mode A data path drawn rather than described. "
        "Trace the two evaluation calls and be explicit that the model call stays in "
        "their environment.",
        "'Is retrieved content or are tool arguments sent?' — Retrieved content only if "
        "the workload is configured to submit it; the initial scope limits evaluated "
        "content to model input and output. Tool-call evaluation is future scope.",
    )


def topology_b(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Appendix — Mode B: Guarded Gateway",
        "One provider-operated endpoint: guardrails, routing, invocation, and audit "
        "inside our boundary.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §3 · AGR-002"),
        dg.mode_b_topology,
        "Prompts and responses are processed entirely within the provider-managed gateway. "
        "The provider participates in the complete inference path and holds or brokers "
        "model-provider credentials. Raw content is not persisted by default; decision "
        "metadata is.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §3.1 topology, §3.2 ownership split, §3.3 "
        "content-exposure statement; mode defined by AGR-002.",
        "Mode B creates the largest operational, security, and compliance scope for us "
        "as the provider — that is the trade for the lowest integration burden on the "
        "consuming team. It lands in Phase 3.",
        "Use this for teams that do not operate a gateway at all, or when someone asks "
        "what the fully managed option looks like.",
        "'Who holds the model credentials here?' — We do, or we hold delegated AWS "
        "access. That is a real change in provider scope and is exactly why this is "
        "Phase 3, behind two validation gates.",
    )


def topology_c(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Appendix — Mode C: Customer-Managed Runtime",
        "The same runtime, deployed inside the customer's own AWS account.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §4 · AGR-002 · AGR-015 · AGR-016 · CON-013"),
        dg.mode_c_topology,
        "Prompts and responses are evaluated entirely inside the customer's own AWS "
        "account. Raw content must never leave that runtime unless explicitly configured. "
        "The runtime evaluates against a local policy cache and does not require a "
        "synchronous control-plane connection.",
        ALLOW,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §4.1 topology, §4.2 component responsibilities, §4.4 "
        "no synchronous control-plane dependency, §4.5 content exposure; AGR-015, "
        "AGR-016, CON-013.",
        "Mode C is delivered in Phase 4 — it is not available today, and a team that "
        "needs it cannot start on it now. Also: the customer takes on runtime "
        "infrastructure, availability, upgrades, and local audit storage.",
        "This is the slide for a residency-bound or regulated workload. Make the "
        "asynchronous policy sync explicit — evaluation never blocks on our control "
        "plane.",
        "'What if the policy cache goes stale?' — Degraded-mode behaviour is part of the "
        "workload's configured failure behaviour (AGR-012); there is no undocumented "
        "default.",
    )


def onboarding(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — What “import” actually means",
        kicker="Adopting a versioned policy for a workload — never adopting, cloning, or "
               "controlling another team's gateway.",
        footer=ctx.src("AGR-019 · PRODUCT-OVERVIEW.md §8 · CLAIMS-REGISTER.md §2, §10"),
    )
    dg.onboarding_stepper(s, f.ONBOARDING_STEPS)

    panel(s, MARGIN, BODY_Y + 2.10, CONTENT_W, 1.95, fill=SURFACE_ALT, border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.32, CONTENT_W - 0.7, 1.6)
    para(tf, "Importing a policy never requires:", size=14, bold=True, first=True,
         space_after=8)
    para(tf, f.IMPORT_EXCLUSIONS, size=13, color=MUTED, space_after=8, line=1.3)
    para(tf, "“Import” means adopting a versioned policy, a workload identity, an "
             "integration manifest, a connector configuration, and an enforcement mode. "
             "Nothing more.", size=12.5, color=TEXT, space_after=0)
    notes(
        s,
        "AGR-019 (meaning of “import”); onboarding sequence reproduced in "
        "PRODUCT-OVERVIEW.md §8; approved wording in CLAIMS-REGISTER.md §2 and §10.",
        "This flow is multi-step and reviewed — it is not one-click, instant, or "
        "zero-configuration, and the claims register prohibits describing it that way.",
        "Reach for this when a prospective pilot team hears “import” and "
        "assumes we are taking over their gateway. The red panel is the whole point of "
        "the slide.",
        "'How much application change is involved?' — Designed to be minimal: primarily "
        "connector installation and configuration rather than application rewrites. We "
        "have no measurement of actual change volume, so I will not quantify it.",
    )


def trust_postures(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Trust and enforcement postures",
        kicker="Orthogonal to deployment mode. A posture describes how bypass-resistant a "
               "given workload's integration actually is.",
        footer=ctx.src("AGR-018 · SECURITY-ARCHITECTURE.md §3 · ASM-005 · OQ-001"),
    )
    dg.posture_rows(s, f.POSTURES)
    caption(s, MARGIN, BODY_BOTTOM - 0.28, CONTENT_W,
            "Whichever posture a workload actually runs under must be recorded and "
            "visible — and must never be described as stronger than it is.")
    notes(
        s,
        "AGR-018 (trust and enforcement model); the three-posture table is "
        "SECURITY-ARCHITECTURE.md §3.1; cooperative start for Phases 1–2 per ASM-005.",
        "A cooperative connector is not a hard security boundary and the residual bypass "
        "risk is accepted, not eliminated. Which posture external customers will require "
        "is an open question (OQ-001), not a decision we have made.",
        "Reach for this when someone challenges the enforcement strength of the pilot. "
        "The honest answer is that Phases 1–2 start cooperative, deliberately.",
        "'Why not start with the strongest posture?' — Network/IAM enforcement is "
        "explicitly not required for the first cooperative delivery. Pulling it forward "
        "adds platform-layer work that does not help prove policy portability, which is "
        "what Phase 1 exists to prove.",
    )


def validation_criteria(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Phase 1–2 validation criteria in full",
        kicker="All nine must be demonstrated together, using two genuinely independent "
               "LiteLLM deployments.",
        footer=ctx.src("SUCCESS-CRITERIA.md §1.2, §1.3 · VALIDATION-PLAN.md · AGR-020"),
    )
    table(s, MARGIN, BODY_Y + 0.02, CONTENT_W, 4.2, f.MVP_CRITERIA,
          col_w=[0.09, 0.34, 0.57], size=9.5, header_size=10.5, first_col_bold=True,
          row_h=[0.30, 0.42, 0.42, 0.52, 0.42, 0.42, 0.52, 0.52, 0.46, 0.52])
    notes(
        s,
        "SUCCESS-CRITERIA.md §1.2 criteria table and §1.3 gating rule; gated as delivery "
        "milestones in VALIDATION-PLAN.md.",
        "Verification method, environment, and sign-off owner per criterion are TBD and "
        "must be defined in VALIDATION-PLAN.md and gated in RELEASE-CRITERIA.md. Do not "
        "imply the criteria are already testable as written.",
        "Use this when someone wants the four criteria that were kept off slide 13, or "
        "wants to read the exact wording rather than the summary.",
        "'Who signs each one off?' — Not yet assigned. That is a delivery-plan gap we owe "
        "before Phase 1 exit, not something I will name in the room.",
    )


def objection_handling(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Objection handling",
        kicker="Pull only the pair that matches the objection raised.",
        footer=ctx.src("FAQ.md · AGR-009, AGR-010, AGR-012, AGR-014, AGR-018"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 4.05, f.OBJECTION_ROWS,
          col_w=[0.28, 0.72], size=11.5, first_col_bold=True,
          row_h=[0.40, 0.72, 0.72, 0.78, 0.72, 0.78])
    notes(
        s,
        "FAQ.md — the five pairs most likely to be raised by this audience; each answer "
        "traces to the agreement cited inline.",
        "Answer the specific objection and stop. Reading the whole table aloud signals "
        "defensiveness and burns time the Q&A needs.",
        "Only surface this if a question lands that the core deck did not resolve. "
        "FAQ.md has the fuller versions if a follow-up goes deeper.",
        "If a question arrives that is not on this list and not in the source material, "
        "say it is not decided yet and take it away — do not improvise a commitment.",
    )


def domain_model(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Core domain model",
        kicker="Six entities. Everything in the architecture is expressed in these terms.",
        footer=ctx.src("AGR-017 · GLOSSARY.md · REQUIREMENTS.md (FR-001, FR-007, FR-008, "
                       "FR-012–FR-016)"),
    )
    dg.entity_grid(s, f.DOMAIN_ENTITIES)
    notes(
        s,
        "AGR-017 (core domain model); per-entity attributes in GLOSSARY.md, made testable "
        "in REQUIREMENTS.md.",
        "The full attribute lists are deliberately not on this slide — GLOSSARY.md holds "
        "them. This is orientation, not a schema.",
        "Use this if the audience loses the thread on tenant / workload / policy / "
        "binding / evaluation / decision vocabulary partway through the deck.",
        "'Why does the binding exist as a separate thing?' — Because it is what lets two "
        "independent workloads run the same policy version in different enforcement "
        "modes — exactly what the Phase 1–2 demonstration has to show.",
    )


SLIDES = [
    cover,
    the_problem,
    what_it_costs,
    introducing,
    architecture_glance,
    policy_vs_connector,
    three_modes,
    ownership_vs_exposure,
    observe_before_enforce,
    scope_boundary,
    differentiators,
    roadmap,
    success_criteria,
    principal_risks,
    who_owns_what,
    the_ask,
    appendix_divider,
    topology_a,
    topology_b,
    topology_c,
    onboarding,
    trust_postures,
    validation_criteria,
    objection_handling,
    domain_model,
]
