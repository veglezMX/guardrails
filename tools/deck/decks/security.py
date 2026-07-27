"""Security and compliance review deck.

Audience: security and compliance leadership, privacy, and risk — policy owners
with sign-off authority. The decision in the room is which deployment modes may
carry which data classes, and on what conditions.

Organised around exposure rather than narrative: where content goes, what is
kept, what the platform does not claim to do, and which risks remain residual.
Slides carry no internal document identifiers; the speaker notes carry all of
them. Governed by solution-docs/40-sales-deck/DECK-VARIANTS.md §4.
"""

from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

import diagrams as dg
import facts as f
from lib import (
    ACCENT, ACCENT_DIM, ALLOW, BLOCK, BODY_BOTTOM, BODY_Y, CONTENT_W, HAIRLINE,
    HEAD_FONT, LOG_ONLY, MARGIN, MUTED, SURFACE, SURFACE_ALT, TEXT, WARN, arrow, box,
    bullets, caption, notes, panel, para, slide_base, table, textbox,
)


def cover(prs, ctx):
    s = slide_base(prs)
    panel(s, MARGIN, 1.55, CONTENT_W, 3.55, fill=SURFACE_ALT)

    _, tf = textbox(s, MARGIN, 0.85, CONTENT_W, 0.45)
    para(tf, "AI GUARDRAILS", size=14, color=ACCENT, bold=True, first=True, space_after=0)

    _, tf = textbox(s, MARGIN + 0.55, 2.15, CONTENT_W - 1.6, 2.6)
    para(tf, "Who operates it and", size=34, bold=True, font=HEAD_FONT, first=True,
         space_after=6, line=1.05)
    para(tf, "where your content goes", size=34, bold=True, font=HEAD_FONT,
         space_after=6, line=1.05)
    para(tf, "are two different questions.", size=34, color=ACCENT, bold=True,
         font=HEAD_FONT, space_after=0, line=1.05)

    _, tf = textbox(s, MARGIN, 5.42, CONTENT_W, 0.9)
    para(tf, "Security and compliance review — content exposure, retention, enforcement "
             "posture, and the boundary of what a guardrail decision means", size=17,
         color=TEXT, first=True, space_after=8)
    para(tf, "Approval review  ·  Presenter: TBD  ·  Nothing in this deck is built, "
             "piloted, or carrying traffic", size=11.5, color=MUTED, space_after=0)

    caption(s, MARGIN, 6.92, CONTENT_W,
            "Placeholder styling — no brand kit exists yet; all colours and type are "
            "stand-ins pending brand.", size=9)
    notes(
        s,
        "DECK-VARIANTS.md §4; the ownership-versus-exposure framing is AGR-008, and the "
        "cover claim is its direct restatement.",
        "Nothing here is running. Every control described is designed and not yet "
        "implemented, so no statement in this deck is evidence of a control operating "
        "today.",
        "Open on the distinction the whole review turns on, then go straight to what "
        "you are asking this room to approve. Do not open with product framing — this "
        "audience did not come for it.",
        "'Is any of this in production?' — No. This is a design review ahead of a "
        "pilot; approval here is approval of an intended posture, not an assessment of "
        "an operating system.",
    )


def what_you_are_approving(prs, ctx):
    s = slide_base(
        prs,
        title="What we are asking you to approve",
        kicker="A mode-to-data-class mapping, and the conditions attached to it.",
        footer=ctx.src("DECK-VARIANTS.md §4 · AGR-002 · AGR-008 · AGR-009 · AGR-011"),
    )
    bullets(s, MARGIN, BODY_Y + 0.10, CONTENT_W, [
        "Which deployment modes may carry which data classes|Three modes exist and they "
        "differ on exactly one axis you care about: where evaluated content is "
        "processed. We are asking you to say which of your data classifications each "
        "mode may carry, and to name the ones that may not use any mode yet.",
        "The conditions that attach to that approval|Retention configuration, telemetry "
        "settings, which enforcement posture a workload must run under, and what "
        "evidence you require before a workload leaves observe mode.",
        "Which residual risks you are accepting, explicitly|Two are not fully "
        "mitigated and are presented as such later in this deck. We would rather have "
        "them accepted on the record than discovered later.",
    ], size=13.5, gap=0.78)

    panel(s, MARGIN, BODY_BOTTOM - 1.55, CONTENT_W, 1.42, fill=SURFACE_ALT, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.35, BODY_BOTTOM - 1.36, CONTENT_W - 0.7, 1.1)
    para(tf, "What we are not asking for", size=13, color=ACCENT, bold=True, first=True,
         space_after=5)
    para(tf, "Not funding, not a delivery commitment, and not a judgement on whether the "
             "platform should exist — those sit with the investment review. Not a "
             "compliance attestation either: no certification target exists, and none "
             "is claimed anywhere in this deck.", size=12.5, color=MUTED, space_after=0,
         line=1.25)
    notes(
        s,
        "DECK-VARIANTS.md §4 (the decision this deck asks for); the three modes are "
        "AGR-002; exposure framing AGR-008/AGR-009; observe-first is AGR-011; "
        "certification is explicitly out of scope per NFR-COMPL-002 and SCOPE.md.",
        "The two unmitigated risks are connector bypass under cooperative integration "
        "and hosted content exposure in the first two modes. Both have dedicated slides "
        "— do not pre-empt them here, but do not let the room think they are hidden "
        "either.",
        "State the ask in one breath, then say what is not being asked. The second half "
        "is what stops this becoming a general product debate.",
        "'Are you asking us to certify anything?' — No. There is no certification "
        "target and claiming one would violate our own claims boundary.",
    )


def what_it_is(prs, ctx):
    s = slide_base(
        prs,
        title="What the platform actually does",
        kicker="One slide, so the rest of the review can be about exposure.",
        footer=ctx.src("CLAIMS-REGISTER.md — product statement · AGR-001 · AGR-017"),
    )
    panel(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 1.95)
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(BODY_Y + 0.05),
                             Inches(0.075), Inches(1.95))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False
    _, tf = textbox(s, MARGIN + 0.50, BODY_Y + 0.32, CONTENT_W - 1.1, 1.5)
    para(tf, "A policy and enforcement platform that applies reusable safeguards to LLM "
             "applications and gateways. A team may connect its existing gateway to a "
             "hosted Evaluation API, consume a fully managed guarded gateway, or deploy "
             "the same guardrail runtime inside its own AWS account. Policies remain "
             "portable across all three, while operational ownership and content "
             "exposure are explicitly defined for each.", size=16, first=True,
         space_after=0, line=1.28)

    bullets(s, MARGIN, BODY_Y + 2.30, CONTENT_W, [
        "It evaluates content at two points|The prompt before the model call, and the "
        "response after it. Nothing else is evaluated in the first phases — not "
        "retrieved documents, not tool arguments, not conversation history.",
        "It returns a normalized decision|Allow, block, redact, or log only — with "
        "reason codes, the policy version that decided, and a correlation identifier "
        "that ties the decision to your own request logs.",
        "It records what it decided|Decision metadata is persisted. Raw prompts and "
        "responses are not, by default.",
    ], size=12.5, gap=0.58)
    notes(
        s,
        "Product statement quoted in substance from CLAIMS-REGISTER.md; standalone "
        "framing AGR-001; decision structure AGR-017 and INTEGRATION-ARCHITECTURE.md "
        "§3.3; content-scope limitation per DATA-ARCHITECTURE.md §3.3.",
        "The two-stage limitation is a first-phase scope boundary, not a permanent "
        "property. Retrieval, tool-call and tool-result stages are reserved in the "
        "contract and deferred to a later phase — say so if asked, and do not imply "
        "they are covered today.",
        "Resist the temptation to sell here. One slide, then move to the exposure "
        "material, which is what this room came for.",
        "'Does it evaluate our RAG content?' — Not in the first phases. Retrieved "
        "content is a permitted category the architecture must allow, but it is not "
        "evaluated in the initial scope.",
    )


def ownership_vs_exposure(prs, ctx):
    s = slide_base(
        prs,
        title="Ownership vs. exposure, per mode",
        kicker="Reduced operational burden never implies reduced data exposure. These "
               "are the exact statements the platform is held to.",
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
        "data-local), AGR-014 (no raw retention by default), AGR-016 "
        "(customer-controlled telemetry); per-mode statements in "
        "DEPLOYMENT-ARCHITECTURE.md §2.3, §3.3, §4.5.",
        "The Mode A row is not negotiable under pressure. If a residency requirement is "
        "in the room, the correct answer is the customer-managed runtime — which is "
        "Phase 4 work and is not available to anyone today.",
        "Read the exposure column as written rather than paraphrasing it. This is the "
        "slide the whole review is organised around; everything after it either "
        "elaborates or bounds it.",
        "'Can Mode A carry regulated content?' — Not on our recommendation. It "
        "transmits content to a hosted service on every evaluated call and must never "
        "be described as keeping content local.",
    )


def three_modes(prs, ctx):
    s = slide_base(
        prs,
        title="The three modes, in your terms",
        kicker="A choice about ownership and residency — not a maturity ladder, and not "
               "equally available today.",
        footer=ctx.src("AGR-002 · PRODUCT-OVERVIEW.md §4 · DEPLOYMENT-ARCHITECTURE.md §5 · "
                       "AGR-020"),
    )
    dg.mode_columns(s, f.MODES, highlight=2)
    caption(s, MARGIN, BODY_BOTTOM - 0.34, CONTENT_W,
            "Availability matters to this decision: Remote Evaluation is the only mode "
            "delivered in the first two phases. The guarded gateway is Phase 3 and the "
            "customer-managed runtime is Phase 4 — so a workload that needs in-account "
            "evaluation cannot start now, whatever this review approves.")
    notes(
        s,
        "AGR-002 three modes; per-mode ownership detail DEPLOYMENT-ARCHITECTURE.md §2–"
        "§4; phase sequencing AGR-020 and PRODUCT-ROADMAP.md.",
        "The highlighted column is the customer-managed runtime because it is the "
        "answer for residency-bound workloads — but highlighting it must not imply "
        "availability. It is Phase 4 and nothing before it is a substitute.",
        "Frame modes as a choice, then land the timing caveat immediately. A security "
        "approval granted for a mode that does not exist yet is a source of confusion "
        "later, not a win now.",
        "'Which mode will the pilot use?' — Remote Evaluation. It is the only one "
        "delivered in Phases 1–2, which is precisely why the exposure conversation "
        "matters now rather than at Phase 4.",
    )


def topology_a(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode A — where content actually goes",
        "The team keeps its gateway and its model credentials. The evaluation calls "
        "leave; the model call does not.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §2 · AGR-009 · DATA-ARCHITECTURE.md §3.1"),
        dg.mode_a_topology,
        "Prompts, responses, and any additional evaluated content are transmitted to "
        "the hosted Evaluation API on every evaluated call. This is not a private-data "
        "or data-local model. The model invocation itself never touches guardrail "
        "infrastructure — only the evaluation calls do.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §2.1 topology, §2.3 content-exposure statement; "
        "framing mandated by AGR-009; default handling per DATA-ARCHITECTURE.md §3.1.",
        "Decision metadata, evaluation latency and audit records flow to the hosted "
        "control plane as part of normal operation. That is not optional telemetry in "
        "this mode — it is how the service works, and it should be stated before "
        "someone infers otherwise from the customer-managed telemetry slide.",
        "Trace the two evaluation hops explicitly. The common misreading is that the "
        "platform proxies inference; correct it here, because it changes the exposure "
        "assessment materially.",
        "'Are retrieved documents or tool arguments transmitted?' — Only if a workload "
        "is configured to submit them, and the initial scope limits evaluated content "
        "to model input and output.",
    )


def topology_b(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode B — the widest provider boundary",
        "One provider-operated endpoint carrying guardrails, routing, model invocation "
        "and audit. Phase 3.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §3 · AGR-002 · AGR-014"),
        dg.mode_b_topology,
        "Prompts and responses are processed entirely within the provider-managed "
        "gateway. The provider participates in the complete inference path and holds "
        "or brokers model-provider credentials. Raw content is not persisted by "
        "default; decision metadata is.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §3.1 topology, §3.2 ownership split, §3.3 "
        "content-exposure statement; retention default AGR-014.",
        "This mode carries the largest provider-side scope of the three — full "
        "inference path plus credential custody. It sits behind two validation gates "
        "for that reason and is not available to approve for use today.",
        "Keep this short unless the room wants to pre-approve a Phase 3 posture. The "
        "useful outcome here is usually a condition recorded for later, not a decision.",
        "'Who holds the model credentials?' — The provider does, or holds delegated "
        "access. That is a genuine change in provider scope and is why the mode sits "
        "behind gates.",
    )


def topology_c(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode C — evaluation inside your account",
        "The same runtime, deployed in the customer's own AWS account, evaluating "
        "against a local policy cache. Phase 4.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §4 · AGR-015 · AGR-016 · CON-013"),
        dg.mode_c_topology,
        "Prompts and responses are evaluated entirely inside the customer's own AWS "
        "account. Raw content must never leave that runtime unless explicitly "
        "configured. Evaluation does not require a synchronous control-plane "
        "connection, and telemetry to the control plane is customer-selected — down to "
        "none at all, which is a fully supported configuration rather than a degraded "
        "one.",
        ALLOW,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §4.1 topology, §4.4 no synchronous control-plane "
        "dependency, §4.5 content exposure; AGR-015, AGR-016, CON-013; telemetry "
        "categories in DATA-ARCHITECTURE.md §3.4.",
        "Policy artifacts reach this runtime protected by transport security, not as "
        "cryptographically signed artifacts. Policy signing is permitted future scope "
        "and is not built — a reviewer focused on supply-chain integrity will ask, and "
        "the honest answer is that it is an open item.",
        "This is the slide for a residency-bound workload. Make the asynchronous policy "
        "sync explicit, and be equally explicit that Phase 4 is when it arrives.",
        "'What can the customer choose to send us?' — Health status, product and policy "
        "version, aggregated counts, reason-code totals, latency, sanitized decision "
        "metadata — or nothing.",
    )


def content_lifecycle(prs, ctx):
    s = slide_base(
        prs,
        title="What is transmitted, and what is kept",
        kicker="The default posture in the hosted runtime, stated as eight obligations.",
        footer=ctx.src("DATA-ARCHITECTURE.md §3.1, §3.2 · AGR-008 · AGR-014 · CON-014 · "
                       "NFR-PRIV-001, NFR-PRIV-002, NFR-PRIV-003"),
    )
    bullets(s, MARGIN, BODY_Y + 0.05, 6.30, [
        "Content is processed only for evaluation|Read to run evaluators and produce a "
        "decision, and for no other purpose by default.",
        "Raw prompts and responses are not persisted|No raw-content retention occurs "
        "unless a tenant explicitly configures it.",
        "Decision metadata is persisted|The decision and its audit record are kept "
        "regardless of whether raw content is retained.",
        "User and conversation identifiers are hashed where possible|Before they are "
        "carried into audit or telemetry.",
    ], size=12.5, gap=0.62)
    bullets(s, 7.13, BODY_Y + 0.05, 5.45, [
        "Secrets are removed from telemetry|Any secret material incidentally present "
        "in evaluated content must not reach a telemetry pipeline.",
        "Encrypted in transit and at rest|Transport security throughout; managed keys "
        "at rest.",
        "Retention is configured per tenant|Not fixed platform-wide.",
        "Transmission is documented per deployment|Where content is processed, whether "
        "it is persisted, which telemetry leaves, and which accounts and Regions "
        "handle the request — as a dedicated statement.",
    ], size=12.5, gap=0.62)
    caption(s, MARGIN, BODY_BOTTOM - 0.52, CONTENT_W,
            "Content categories the architecture permits: user prompts, system prompts, "
            "conversation history, retrieved documents, model responses, tool arguments, "
            "tool responses, structured output. Categories evaluated in the first "
            "phases: user prompts at the input stage and model responses at the output "
            "stage. The rest are permitted by the design and not evaluated yet.")
    notes(
        s,
        "DATA-ARCHITECTURE.md §3.1 default behaviour (eight obligations, from the "
        "original charter §13), §3.2 content categories, §3.3 the initial content-scope "
        "limitation; retention default AGR-014 and CON-014; measurable targets "
        "NFR-PRIV-001 through NFR-PRIV-003.",
        "Data-architecture and security reviews are required to verify that no "
        "incidental raw-content persistence occurs in logs, caches or telemetry "
        "pipelines. That verification has not been performed — there is nothing running "
        "to verify.",
        "Work down the left column, then the right. The final caption is the one that "
        "answers the question this room usually asks third: what exactly do you look at.",
        "'Is “not persisted by default” a guarantee?' — It is a default and a design "
        "obligation, not an absolute. Any retention requires explicit tenant-level "
        "configuration, and the audit record states whether raw content was retained "
        "for that evaluation.",
    )


def audit_record(prs, ctx):
    s = slide_base(
        prs,
        title="The audit record is the evidence",
        kicker="Every evaluation produces one. These are the fields you would rely on.",
        footer=ctx.src("DATA-ARCHITECTURE.md §4 · NFR-AUDIT-001 · ADR-0003 · ADR-0014 · "
                       "AGR-014"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.90, [
        ["Field", "What it gives you"],
        ["Tenant · workload", "Whose traffic, and which registered workload."],
        ["Policy version", "The specific immutable version applied — the traceability "
                           "answer to “which rules were in force at the time”."],
        ["Evaluation stage", "Input or output; future stages when they exist."],
        ["Decision · reason codes · guardrail category",
         "What was decided and why, in an engine-independent form. Category is a "
         "first-class field, so per-category reporting is a filter rather than an "
         "external mapping table."],
        ["Evaluator versions", "Which engines and adapters ran."],
        ["Timestamp · correlation identifier",
         "When, and the identifier that joins the record to your own request logs "
         "across connector, runtime and any downstream system."],
        ["Whether raw content was retained",
         ("Recorded per evaluation, defaulting to no. This is the field that makes the "
          "retention posture auditable rather than asserted.", ACCENT)],
    ], col_w=[0.31, 0.69], size=11, first_col_bold=True,
        row_h=[0.34, 0.34, 0.52, 0.32, 0.72, 0.32, 0.55, 0.60])
    caption(s, MARGIN, BODY_BOTTOM - 0.32, CONTENT_W,
            "Records are buffered through a queue and archived to object storage. When "
            "audit delivery itself fails, what happens is a configured behaviour on the "
            "workload's policy — not an implicit choice by the runtime.")
    notes(
        s,
        "DATA-ARCHITECTURE.md §4 audit metadata fields and §2.3 the buffered "
        "queue-to-archive pipeline; completeness target NFR-AUDIT-001; delivery-failure "
        "behaviour NFR-AUDIT-002; category namespacing ADR-0003 and DEC-013; record "
        "classes ADR-0014; retention default AGR-014.",
        "Records produced by a client rather than the runtime — during an outage — are "
        "marked client-origin and are explicitly not evidentiary. They say what a "
        "cooperative component reports having done, not what the platform observed. "
        "That distinction matters in exactly this room.",
        "This is the slide that answers 'what would we actually have in an "
        "investigation'. Land the last row hardest: retention is recorded per "
        "evaluation, which is what makes the default checkable.",
        "'Can we get these records into our own SIEM?' — Not decided. The archive "
        "exists; the export path and its format are not specified in the current "
        "material and I will not invent one.",
    )


def not_data_local(prs, ctx):
    s = slide_base(
        prs,
        title="Remote Evaluation is not a data-local model",
        kicker="Stated plainly, on the slide, because it is the claim most likely to be "
               "misheard.",
        footer=ctx.src("AGR-009 · AGR-008 · DATA-ARCHITECTURE.md §3.1 · "
                       "CLAIMS-REGISTER.md §1"),
    )
    panel(s, MARGIN, BODY_Y + 0.10, CONTENT_W, 1.70, fill=SURFACE, border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.40, BODY_Y + 0.36, CONTENT_W - 0.8, 1.25)
    para(tf, "In Remote Evaluation, prompts, responses, and any other evaluated content "
             "are transmitted to the hosted Evaluation API on every evaluated call — "
             "regardless of who operates the gateway in front of it.", size=18,
         bold=True, first=True, space_after=8, line=1.2)
    para(tf, "Operating your own gateway reduces your operational dependency on us. It "
             "does not reduce what we see.", size=13, color=MUTED, space_after=0)

    bullets(s, MARGIN, BODY_Y + 2.10, CONTENT_W, [
        "The correct destination for a residency-bound workload is the customer-managed "
        "runtime|Where content is evaluated inside your account and raw content must "
        "never leave it unless you explicitly configure that. It is Phase 4.",
        "Until then, a residency-bound workload has no mode it can adopt|That is the "
        "honest position. There is no interim configuration of Remote Evaluation that "
        "makes it residency-appropriate, and we will not describe one.",
        "This statement is binding on every artifact, not just this deck|Product, "
        "sales and executive material carry the same wording. It is enforced by the "
        "claims boundary rather than left to the author.",
    ], size=12.5, gap=0.66)
    notes(
        s,
        "AGR-009 (remote evaluation is not a private-data or data-local model), the "
        "ownership/exposure split AGR-008, the default-handling rule "
        "DATA-ARCHITECTURE.md §3.1, and the approved phrasing in CLAIMS-REGISTER.md §1.",
        "There is no partial answer available here — no field-level redaction before "
        "transmission, no on-premise pre-filter in the current scope. If someone asks "
        "for one, it is a scope-change conversation, not a configuration option.",
        "Give this slide time. A room that hears this stated voluntarily tends to trust "
        "the rest of the deck; a room that extracts it under questioning does not.",
        "'Could we redact before it leaves our environment?' — Not in the current "
        "scope. Redaction is an action the platform applies after evaluating content it "
        "has received, not a client-side pre-filter.",
    )


def not_authorization(prs, ctx):
    s = slide_base(
        prs,
        title="A guardrail decision is not an authorization decision",
        kicker="Content appearing safe says nothing about whether this user may take "
               "this action.",
        footer=ctx.src("AGR-010 · SECURITY-ARCHITECTURE.md §11 · GUARDRAIL-CATALOG.md "
                       "GR-010 · SCOPE.md"),
    )
    panel(s, MARGIN, BODY_Y + 0.05, 6.30, 3.85)
    box(s, MARGIN, BODY_Y + 0.05, 6.30, 0.55, "STAYS WITH YOUR APPLICATION", bold=True,
        size=12.5, fill=SURFACE, border=BLOCK)
    bullets(s, MARGIN + 0.28, BODY_Y + 0.92, 5.80, [
        "End-user authentication",
        "Business authorization",
        "Tenant isolation at the application and business-data layer",
        "Resource-ownership checks",
        "Transaction limits",
        "Approval workflows",
    ], size=12.5, gap=0.46, dot=BLOCK)

    panel(s, 7.13, BODY_Y + 0.05, 5.45, 3.85, fill=SURFACE_ALT)
    _, tf = textbox(s, 7.43, BODY_Y + 0.30, 4.85, 3.35)
    para(tf, "What the platform's own identity controls do", size=13, color=ACCENT,
         bold=True, first=True, space_after=7)
    para(tf, "They authenticate and authorize calls into the guardrail platform itself: "
             "who may call the Evaluation API, with what identity, against which policy, "
             "and how that call is protected in transit and at rest.", size=12.5,
         color=TEXT, space_after=12, line=1.25)
    para(tf, "They are not, and are not designed to be, a substitute for your "
             "application's authorization layer. Tool and agent authorization — "
             "deciding that a specific action is permitted for a specific user against "
             "a specific resource — is excluded from the first phases entirely and "
             "appears no earlier than the final phase, as content and argument "
             "evaluation rather than permission to execute.", size=12.5, color=MUTED,
         space_after=0, line=1.25)
    caption(s, MARGIN, BODY_BOTTOM - 0.26, CONTENT_W,
            "The category catalog encodes this as classification: identity, "
            "authorization and tenant isolation are marked out-of-boundary with a named "
            "external owner — listed so coverage conversations get an honest answer, "
            "never as a roadmap item.")
    notes(
        s,
        "AGR-010 (guardrails are not authorization); the responsibility table and the "
        "architectural argument are SECURITY-ARCHITECTURE.md §11; catalog "
        "classification GR-010 as out-of-boundary; later-phase tool-call evaluation is "
        "CAP-021/CAP-022 under AGR-020.",
        "The largest named risk in the charter is false confidence — a passing "
        "evaluation being read as complete AI security. This slide exists to remove "
        "that reading before the room forms it, not to defend a limitation.",
        "Present the left column as a list of things that remain the application's "
        "problem, and say plainly that we would rather lose the coverage claim than "
        "have someone rely on it.",
        "'Will it ever do authorization?' — No. It is excluded by agreement, and the "
        "catalog marks it out-of-boundary with an external owner. Later phases evaluate "
        "the content of a proposed tool call; they never grant permission to execute it.",
    )


def connector_bypass(prs, ctx):
    s = slide_base(
        prs,
        title="Connector bypass is a residual risk, not a mitigated one",
        kicker="A cooperative connector is evidence of an engineering standard being "
               "followed. It is not proof that evaluation cannot be bypassed.",
        footer=ctx.src("AGR-018 · SECURITY-ARCHITECTURE.md §3.3, §9, §10.1 THR-001 · "
                       "CON-012 · ASM-005"),
    )
    dg.posture_rows(s, f.POSTURES, badge="THE PILOT STARTS HERE")
    caption(s, MARGIN, BODY_BOTTOM - 0.28, CONTENT_W,
            "Only the strongest posture makes bypass require a platform-side "
            "misconfiguration rather than a consumer-side action.")
    notes(
        s,
        "AGR-018 trust and enforcement model; the three postures are "
        "SECURITY-ARCHITECTURE.md §3.1 and the honesty statement §3.3; the telemetry "
        "prohibition is §9 item 4 and CON-012(d); the threat is THR-001 in §10.1, whose "
        "residual risk is recorded as not eliminated by design; the cooperative start "
        "for Phases 1–2 is ASM-005.",
        "Which posture external customers will require is an open question with no "
        "recorded decision. If this room requires platform-managed or network-enforced "
        "enforcement for the pilot, that is a real finding that moves platform work "
        "earlier — it is not a request we will refuse.",
        "This is the slide most likely to produce a condition on the approval. Present "
        "the residual risk first and the mitigations second, not the other way round.",
        "'Can you detect a bypass?' — Not reliably under cooperative integration. "
        "Connector telemetry stopping is a signal that gets treated as a bypass "
        "indicator rather than a runtime outage, but absence of that signal is not "
        "evidence of coverage.",
    )


def threats(prs, ctx):
    s = slide_base(
        prs,
        title="Threat model, and what remains after mitigation",
        kicker="Four threats to the platform's own attack surface. The residual column "
               "is the one worth your time.",
        footer=ctx.src("SECURITY-ARCHITECTURE.md §10.1 · CON-012 · OQ-012 · "
                       "ARCHITECTURE-REPORT.md §18"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.85, [
        ["Threat", "Mitigation", "Residual"],
        ["Connector bypass",
         "Configuration validation and connector-health telemetry; progression to a "
         "stronger enforcement posture as trust requirements rise.",
         ("Not eliminated under cooperative integration, by design. Accepted rather "
          "than hidden.", BLOCK)],
        ["Credential exposure",
         "Identity-based, short-lived, least-privilege credentials; managed encryption "
         "at rest; explicit rejection of static gateway keys as a sole control.",
         ("Impact within a leaked credential's active lifetime is reduced, not "
          "eliminated. Anomaly detection is operational, not architectural.", WARN)],
        ["Tenant spoofing",
         "Per-tenant workload identity that is never shared across tenants, "
         "authenticated on every call; policy authorization tied to the authenticated "
         "identity rather than a caller-asserted one.",
         ("Isolation in the hosted modes is logical, not infrastructure-level. Only "
          "the customer-managed runtime gives physical separation.", WARN)],
        ["Unauthorized policy selection",
         "The runtime resolves and authorizes the effective policy server-side; a "
         "client-supplied version not bound to the authenticated workload is rejected.",
         ("Verifies authorization to use a version, not the cryptographic authenticity "
          "of the artifact reaching a customer runtime. Transport security is today's "
          "safeguard.", WARN)],
    ], col_w=[0.17, 0.44, 0.39], size=10, header_size=11, first_col_bold=True,
        row_h=[0.34, 0.62, 0.78, 0.85, 0.85])
    caption(s, MARGIN, BODY_BOTTOM - 0.30, CONTENT_W,
            "Two further risks — false confidence and hosted data exposure — are "
            "addressed by the authorization boundary and the exposure model rather than "
            "by a control, and both have their own slides in this deck.")
    notes(
        s,
        "SECURITY-ARCHITECTURE.md §10.1 threat catalog THR-001 through THR-004 with "
        "residual-risk column as written; the sole-control prohibitions are §9 and "
        "CON-012; policy signing is deferred under OQ-012; the logical-isolation "
        "position is ARCHITECTURE-REPORT.md §18.",
        "None of these mitigations exist yet. They are the designed controls, and a "
        "reviewer is entitled to treat design intent as unverified until Phase 1 "
        "delivers something testable.",
        "Read the residual column, not the mitigation column — the room can read "
        "mitigations. What they need from you is the honest edge of each one.",
        "'What about a malicious insider at the provider?' — Not modelled in this "
        "catalog, which is scoped to the platform's own attack surface. The structural "
        "answer for a workload that must assume that threat is the customer-managed "
        "runtime.",
    )


def must_not_rely_on(prs, ctx):
    s = slide_base(
        prs,
        title="Four things we will not let ourselves rely on",
        kicker="Each may exist as a convenience or a diagnostic. None may be cited as "
               "the reason a workload is protected.",
        footer=ctx.src("CON-012 · SECURITY-ARCHITECTURE.md §9 · AGR-018"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.55, [
        ["Not the sole control", "Why it is insufficient alone", "What carries it instead"],
        ["Static keys held in gateway configuration",
         "Static secrets in application-owned configuration are easy to leak, copy, or "
         "leave unrotated.",
         "Identity-based, short-lived, least-privilege authentication."],
        ["Tenant-supplied identifiers without authentication",
         "An unauthenticated identifier can be spoofed by any caller who knows or "
         "guesses it.",
         "Per-tenant workload identity issued and authenticated at onboarding."],
        ["Client-provided policy versions without authorization",
         "A caller could otherwise request evaluation against a weaker or stale version "
         "it is not entitled to.",
         "Server-side policy authorization against the workload's active binding."],
        ["Connector telemetry as evidence of enforcement",
         "Telemetry shows an installed connector is reporting; it cannot prove no "
         "request bypassed it.",
         "The enforcement posture actually in force for that workload."],
    ], col_w=[0.28, 0.38, 0.34], size=10.5, header_size=11, first_col_bold=True,
        row_h=[0.36, 0.68, 0.62, 0.72, 0.62])
    caption(s, MARGIN, BODY_BOTTOM - 0.46, CONTENT_W,
            "This is a constraint on how the platform may be described as much as on "
            "how it is built: none of the four may appear in product, sales, or "
            "compliance material as the reason a workload's guardrails cannot be "
            "bypassed.")
    notes(
        s,
        "CON-012 (no exclusive reliance on weak security proxies), restated "
        "architecturally in SECURITY-ARCHITECTURE.md §9 with the same four rows; the "
        "telemetry row is the concrete application of AGR-018.",
        "Each of the four still exists in the architecture for a legitimate reason — a "
        "key may gate a low-risk internal tool alongside identity-based auth, "
        "connector telemetry is genuinely useful for operational health. The rule is "
        "about what may be cited, not what may exist.",
        "This slide usually shortens the review. It tells this room we have already "
        "removed the arguments they were preparing to attack.",
        "'Who enforces this?' — The claims boundary governs every derived artifact, and "
        "the deck build itself fails on prohibited phrasing. It is a check, not a "
        "convention.",
    )


def observe_before_enforce(prs, ctx):
    s = slide_base(
        prs,
        title="Nothing blocks on day one",
        kicker="Every policy supports a non-blocking state, and promotion is an "
               "explicit, audited action.",
        footer=ctx.src("AGR-011 · ADR-0004 · PRINCIPLES.md §5.6 · OQ-014"),
    )
    dg.enforcement_ladder(s, f.ENFORCEMENT_STATES, highlight=1)
    panel(s, MARGIN, BODY_Y + 2.85, CONTENT_W, 1.35)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 3.05, CONTENT_W - 0.7, 1.0)
    para(tf, "The state is a ceiling on the action, held per workload — never a property "
             "of the policy.", size=15, color=ACCENT, bold=True, first=True,
         space_after=7)
    para(tf, "Two workloads can run the same approved policy version at different states, "
             "so a team measures false positives and business impact on its own traffic "
             "before anything it owns can be blocked. Clamping changes the action only: "
             "findings and reason codes are reported unchanged, so observe-mode "
             "measurement is real measurement rather than a rehearsal.", size=12.5,
         color=MUTED, space_after=0, line=1.25)
    caption(s, MARGIN, BODY_BOTTOM - 0.06, CONTENT_W,
            "What evidence justifies promoting a workload out of observe is not yet "
            "defined — it is an open question this review is well placed to answer.",
            size=11)
    notes(
        s,
        "AGR-011 observe-before-enforce; the clamping semantics and the "
        "state-on-the-binding rule are ADR-0004 §1–§2 and §5; PRINCIPLES.md §5.6; the "
        "undefined promotion criteria are OQ-014.",
        "Security and governance own policy approval; the consuming team reviews "
        "observe-mode findings and accepts enforcement. Both transitions are audited. "
        "What is missing is the criteria, not the control.",
        "Use the caption as an invitation — this room defining the promotion criteria "
        "is a better outcome than us proposing them.",
        "'Who decides when a policy starts blocking?' — Security and governance approve "
        "the policy; the consuming team accepts enforcement for its workload. Neither "
        "can do it silently.",
    )


def failure_behavior(prs, ctx):
    s = slide_base(
        prs,
        title="What happens when evaluation fails is your decision",
        kicker="Failure behavior is part of the policy. There is no undocumented "
               "fallback, and no runtime default.",
        footer=ctx.src("AGR-012 · ADR-0009 §4 · OPERABILITY.md §7 · ADR-0012 §2"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.30, [
        ["Failure condition", "Behaviours a policy may configure"],
        ["The Evaluation API times out", "Allow and log · return a safe fallback · block "
                                         "the request"],
        ["A detector is unavailable", "Degrade to the remaining detectors and log · use "
                                      "a cached policy · block"],
        ["The policy cannot be resolved", "Block · require manual approval"],
        ["The control plane is unavailable", "Continue on the cached policy · block"],
        ["Audit delivery fails", "Allow and log locally for later reconciliation · "
                                 "block, where audit is critical"],
        ["A response cannot be safely transformed", "Return a safe fallback · block"],
    ], col_w=[0.38, 0.62], size=11.5, first_col_bold=True,
        row_h=[0.36, 0.42, 0.46, 0.36, 0.36, 0.46, 0.36])
    panel(s, MARGIN, BODY_Y + 3.50, CONTENT_W, 0.90, fill=SURFACE_ALT, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.32, BODY_Y + 3.64, CONTENT_W - 0.64, 0.7)
    para(tf, "A policy that omits any one of these fails validation and is never "
             "published. The runtime never chooses for you, and the audit record states "
             "which path was taken.", size=12.5, color=TEXT, first=True, space_after=0,
         line=1.2)
    notes(
        s,
        "AGR-012 enumerates the failure modes; the no-default schema rule is ADR-0009 "
        "§4; the binding rules and the same table are OPERABILITY.md §7; the client-side "
        "equivalent — a client with no configuration refuses to evaluate rather than "
        "allowing — is ADR-0012 §2.",
        "Fail-open versus fail-closed is a policy owner's decision, per workload and "
        "per failure mode. That is a governance responsibility landing on this room's "
        "side of the boundary, and it is worth naming as such.",
        "This is the slide where security realises it owns something. Say plainly that "
        "the platform deliberately refuses to make this choice on their behalf.",
        "'What is the safest default?' — There is no default, deliberately. A default "
        "would be behaviour the policy author never wrote down, which is exactly what "
        "the agreement forbids.",
    )


def scope_boundary(prs, ctx):
    s = slide_base(
        prs,
        title="What this is — and isn't",
        kicker="On the slide, not in an appendix.",
        footer=ctx.src("AGR-010 · AGR-013 · AGR-018 · SCOPE.md · "
                       "SECURITY-ARCHITECTURE.md §12"),
    )
    dg.scope_columns(s, f.IS_ITEMS, f.IS_NOT_ITEMS)
    notes(
        s,
        "AGR-010, AGR-013, AGR-018 and the SCOPE.md product-category exclusions; the "
        "security-specific out-of-scope list is SECURITY-ARCHITECTURE.md §12 — private "
        "connectivity, additional account separation, policy signing, MCP tool "
        "authorization, organization-wide provider enforcement, air-gapped "
        "installation, compliance certification, and customer-supplied evaluator "
        "plugins.",
        "None of those absences is a gap against what was promised — they are scope "
        "boundaries. But several of them are things this room may consider "
        "preconditions, and pulling one forward requires a recorded scope-change "
        "decision rather than a commitment made in this session.",
        "Frame the right column as precision: it tells you exactly which risks stay "
        "yours. Then close on the ask.",
        "'Is there any compliance certification?' — No, and none is targeted. It is "
        "explicitly out of scope, and any material implying otherwise is wrong.",
    )


def who_owns_what(prs, ctx):
    s = slide_base(
        prs,
        title="Who owns what",
        kicker="Including the parts that land on your side of the line.",
        footer=ctx.src("TEAM-AND-OWNERSHIP.md §1, §4, §5 · AGR-019"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.6, f.OWNERSHIP_ROWS,
          col_w=[0.24, 0.76], size=12, first_col_bold=True,
          row_h=[0.42, 0.85, 0.72, 0.72, 0.90])
    caption(s, MARGIN, BODY_BOTTOM - 0.34, CONTENT_W,
            "Ownership shifts by deployment mode and by enforcement posture: in the "
            "customer-managed runtime the consuming team also owns runtime "
            "infrastructure, availability, upgrades and local audit storage.")
    notes(
        s,
        "TEAM-AND-OWNERSHIP.md §1 four ownership groups, §4 shifts by deployment mode, "
        "§5 ownership under each enforcement posture; the onboarding boundary is "
        "AGR-019.",
        "The security and governance row is the one this room is accepting: "
        "organization-level policy requirements, risk classification, policy approval, "
        "exception criteria, and retention and audit rules. Nothing in the platform "
        "makes those decisions on their behalf.",
        "Read the third row aloud. The rest of the table is context for it.",
        "'What does a consuming team actually have to do?' — Register the workload, "
        "select the policy, install the connector, run in observe, review findings. No "
        "credentials transferred and no gateway administration granted.",
    )


def the_ask(prs, ctx):
    s = slide_base(
        prs,
        title="The decision we are asking for",
        footer=ctx.src("DECK-VARIANTS.md §4 · AGR-002 · AGR-011 · AGR-014 · AGR-016 · "
                       "OQ-001, OQ-014"),
    )
    panel(s, MARGIN, BODY_Y - 0.05, CONTENT_W, 1.50, fill=SURFACE, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.40, BODY_Y + 0.20, CONTENT_W - 0.8, 1.05)
    para(tf, "Tell us which data classes may run under Remote Evaluation for the pilot, "
             "and which may not run under any mode until in-account evaluation exists.",
         size=18, bold=True, first=True, space_after=6, line=1.2)
    para(tf, "A conditional approval is a good outcome. A list of conditions is a better "
             "one than a yes without them.", size=12.5, color=MUTED, space_after=0)

    _, tf = textbox(s, MARGIN, BODY_Y + 1.58, 6.05, 0.4)
    para(tf, "Conditions we expect you to set", size=13, color=ACCENT, bold=True,
         first=True, space_after=0)
    bullets(s, MARGIN, BODY_Y + 2.00, 6.05, [
        "Retention configuration for the pilot tenant, and who may change it",
        "The enforcement posture a workload must reach before it carries anything "
        "beyond the lowest data class",
        "The evidence required before a workload is promoted out of observe",
    ], size=12.5, gap=0.62, dot=ALLOW)

    _, tf = textbox(s, 7.03, BODY_Y + 1.58, 5.55, 0.4)
    para(tf, "Residual risks we are asking you to accept, on the record", size=13,
         color=MUTED, bold=True, first=True, space_after=0)
    bullets(s, 7.03, BODY_Y + 2.00, 5.55, [
        "Bypass is possible by the team that installed the connector, under the "
        "cooperative posture the pilot starts in",
        "Content transits a hosted service on every evaluated call in Remote Evaluation",
        "Isolation between tenants in the hosted modes is logical, not "
        "infrastructure-level",
    ], size=12.5, gap=0.62, dot=BLOCK)

    caption(s, MARGIN, BODY_BOTTOM - 0.18, CONTENT_W,
            "If you cannot approve any data class today, the useful outcome is the list "
            "of controls that would change that answer — it becomes a design input "
            "rather than an objection.")
    notes(
        s,
        "DECK-VARIANTS.md §4 (the decision this deck asks for); modes AGR-002; "
        "observe-first AGR-011; retention AGR-014 and CON-014; telemetry AGR-016; the "
        "posture and promotion questions are OQ-001 and OQ-014.",
        "If this room requires a stronger enforcement posture for the pilot, that "
        "changes delivery sequencing and is a genuine finding — not something to "
        "negotiate down in the room.",
        "State the ask, name the conditions you expect, put the residual risks on the "
        "record, and stop talking. Silence here is productive.",
        "'Can we approve conditionally?' — That is the expected outcome. Name the "
        "conditions and we will carry them as delivery requirements rather than "
        "preferences.",
    )


def appendix_divider(prs, ctx):
    s = slide_base(prs)
    _, tf = textbox(s, MARGIN, 3.05, CONTENT_W, 1.6)
    para(tf, "Appendix", size=44, bold=True, font=HEAD_FONT, first=True, space_after=10)
    para(tf, "Reference material. Use only if raised.", size=15, color=MUTED,
         space_after=0)
    rule = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(2.85),
                              Inches(1.35), Inches(0.045))
    rule.fill.solid()
    rule.fill.fore_color.rgb = ACCENT
    rule.line.fill.background()
    rule.shadow.inherit = False
    caption(s, MARGIN, 5.35, CONTENT_W,
            "Core domain model  ·  Objection handling  ·  Onboarding sequence")
    notes(
        s,
        "DECK-VARIANTS.md §4 appendix list.",
        "Do not present the appendix by default. In this room, volunteering extra "
        "material reads as filling silence rather than answering a question.",
        "Pause. If the mode approval and its conditions are recorded, stop here.",
        "n/a — divider slide.",
    )


def domain_model(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Core domain model",
        kicker="Six entities. Every retention, binding and audit statement above "
               "attaches to one of them.",
        footer=ctx.src("AGR-017 · GLOSSARY.md · DATA-ARCHITECTURE.md §1"),
    )
    dg.entity_grid(s, f.DOMAIN_ENTITIES)
    notes(
        s,
        "AGR-017 core domain model; per-entity attributes DATA-ARCHITECTURE.md §1.2–"
        "§1.7 and GLOSSARY.md.",
        "Retention configuration, data classification, allowed Regions and deployment "
        "mode are all tenant attributes — which is why retention is configured per "
        "tenant rather than platform-wide.",
        "Use this if the vocabulary slips, particularly the distinction between a "
        "policy and a binding.",
        "'Where does data classification live?' — On the tenant, alongside AWS account, "
        "allowed Regions and retention configuration.",
    )


def objection_handling(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — The questions this room usually asks",
        kicker="Pull only the pair that matches the question raised.",
        footer=ctx.src("FAQ.md · AGR-009, AGR-010, AGR-012, AGR-014, AGR-018"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 4.52, f.OBJECTION_ROWS_CLEAN,
          col_w=[0.28, 0.72], size=11, header_size=11, first_col_bold=True,
          row_h=[0.34, 0.64, 0.64, 0.70, 0.64, 0.70, 0.86])
    notes(
        s,
        "FAQ.md — the pairs most likely to be raised by a security and compliance "
        "audience; the first five trace to AGR-009, AGR-014, AGR-018, AGR-010 and "
        "AGR-012 respectively, and the findings-visibility pair to the "
        "TEAM-AND-OWNERSHIP.md §3.4 responsibility matrix.",
        "Answer the specific question and stop. Reading the table aloud signals "
        "defensiveness, and this audience reads that accurately.",
        "Only surface this if a question lands that the main sequence did not resolve.",
        "If a question arrives that is not on this list and not in the source material, "
        "say it is not decided and take it away. Do not improvise a control.",
    )


def onboarding(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — What onboarding involves",
        kicker="Adopting a versioned policy for a workload — never adopting or "
               "controlling a team's gateway.",
        footer=ctx.src("AGR-019 · PRODUCT-OVERVIEW.md §8 · ADR-0006 §4 · "
                       "CLAIMS-REGISTER.md §2, §10"),
    )
    dg.onboarding_stepper(s, f.ONBOARDING_STEPS)
    panel(s, MARGIN, BODY_Y + 2.10, CONTENT_W, 1.95, fill=SURFACE_ALT, border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.32, CONTENT_W - 0.7, 1.6)
    para(tf, "Onboarding never requires:", size=14, bold=True, first=True, space_after=8)
    para(tf, f.IMPORT_EXCLUSIONS, size=13, color=MUTED, space_after=8, line=1.3)
    para(tf, "The configuration a team receives is a reviewable, version-controlled file "
             "— so what a workload is bound to is visible in that team's own repository "
             "rather than only in our console.", size=12.5, color=TEXT, space_after=0)
    notes(
        s,
        "AGR-019 meaning of import; sequence in PRODUCT-OVERVIEW.md §8; the manifest's "
        "prohibited contents are ADR-0006 §4, which is the enforcement point for "
        "AGR-019; approved wording CLAIMS-REGISTER.md §2 and §10.",
        "The manifest is derived and not authoritative at evaluation time — a team "
        "cannot obtain weaker enforcement by editing its own copy. Worth stating "
        "explicitly, because a reviewable file in the team's repository invites exactly "
        "that question.",
        "Reach for this when someone asks what a consuming team is actually given, or "
        "whether the platform gains access to their environment.",
        "'Does the platform get administrative access to their gateway?' — No. That is "
        "prohibited by agreement and enforced by what the generated configuration may "
        "contain.",
    )


SLIDES = [
    cover,
    what_you_are_approving,
    what_it_is,
    ownership_vs_exposure,
    three_modes,
    topology_a,
    topology_b,
    topology_c,
    content_lifecycle,
    audit_record,
    not_data_local,
    not_authorization,
    connector_bypass,
    threats,
    must_not_rely_on,
    observe_before_enforce,
    failure_behavior,
    scope_boundary,
    who_owns_what,
    the_ask,
    appendix_divider,
    domain_model,
    objection_handling,
    onboarding,
]
