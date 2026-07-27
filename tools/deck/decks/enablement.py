"""Adopting pilot team enablement deck.

Audience: the engineering team asked to run the second organizationally
independent gateway for the validation demonstration, plus their engineering
manager. They are not being sold to — they are being asked to do work, and the
deck is organised around what changes for them.

Slides carry no internal document identifiers; the speaker notes carry all of
them. Governed by solution-docs/40-sales-deck/DECK-VARIANTS.md §5.
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
    para(tf, "You have been asked to pilot this.", size=34, bold=True, font=HEAD_FONT,
         first=True, space_after=6, line=1.05)
    para(tf, "Here is exactly what that means,", size=34, bold=True, font=HEAD_FONT,
         space_after=6, line=1.05)
    para(tf, "and what it costs you.", size=34, color=ACCENT, bold=True, font=HEAD_FONT,
         space_after=0, line=1.05)

    _, tf = textbox(s, MARGIN, 5.42, CONTENT_W, 0.9)
    para(tf, "Pilot enablement — what you install, what changes in your request path, "
             "what you own, and what we need back from you", size=17, color=TEXT,
         first=True, space_after=8)
    para(tf, "Working session  ·  Presenter: TBD  ·  Nothing here is built yet — you "
             "would be the first workload on it", size=11.5, color=MUTED, space_after=0)

    caption(s, MARGIN, 6.92, CONTENT_W,
            "Placeholder styling — no brand kit exists yet; all colours and type are "
            "stand-ins pending brand.", size=9)
    notes(
        s,
        "DECK-VARIANTS.md §5; the two-independent-instance requirement is MVP-01 in "
        "SUCCESS-CRITERIA.md §1.2, resting on the unvalidated assumptions ASM-002 and "
        "ASM-003.",
        "This team volunteering is a currently unvalidated assumption that gates the "
        "first phase. If they decline, that is a real finding to carry back, not a "
        "failed meeting.",
        "Open by naming the cost honestly. This audience has been asked for effort "
        "before by a platform team and has learned to look for what was left out — "
        "leading with the cost is what earns the rest of the session.",
        "'Is this already running somewhere?' — No. Nothing is built. You would be the "
        "first workload, which is why we are asking rather than announcing.",
    )


def what_you_are_asked(prs, ctx):
    s = slide_base(
        prs,
        title="What we are asking you to do",
        kicker="Four things. None of them involves giving us access to anything you own.",
        footer=ctx.src("SUCCESS-CRITERIA.md §1.2 MVP-01, MVP-04 · VALIDATION-PLAN.md · "
                       "AGR-011 · ASM-002, ASM-003"),
    )
    bullets(s, MARGIN, BODY_Y + 0.10, CONTENT_W, [
        "Run your own gateway, exactly as you do now|The demonstration needs two "
        "organizationally independent deployments consuming the same policy version. "
        "Yours is the second. Independence is the point — one deployment exercised "
        "twice under different labels does not satisfy it.",
        "Install a connector into it|A published artifact you install and can verify "
        "before it runs. It calls an evaluation service before and after each model "
        "call and applies what comes back.",
        "Run in observe mode, and stay there until you decide otherwise|Evaluations "
        "execute and are recorded. Nothing is blocked, nothing is redacted, nothing "
        "changes in what your users receive.",
        "Review what observe mode found, and tell us whether it is usable|That review "
        "is the deliverable. Promotion to enforcement is your decision to accept, not "
        "ours to schedule.",
    ], size=13.5, gap=0.68)
    caption(s, MARGIN, BODY_BOTTOM - 0.42, CONTENT_W,
            "What we are not asking for: your model-provider credentials, "
            "administrative access to your gateway, a copy of your configuration "
            "database, or a change to how you route models. None of those is part of "
            "onboarding, and none of them ever becomes part of it.")
    notes(
        s,
        "The two-instance requirement is MVP-01, observe mode is MVP-04, both in "
        "SUCCESS-CRITERIA.md §1.2 and gated in VALIDATION-PLAN.md; observe-first is "
        "AGR-011; the exclusions are AGR-019; the assumption that a second team "
        "volunteers is ASM-002/ASM-003.",
        "Independence is organizational, not just technical — two instances run by the "
        "same team would not demonstrate what the phase exists to prove. If this team "
        "is inside the same reporting line as the proposing team, that is worth "
        "surfacing now rather than at validation.",
        "Four asks, then the exclusion list. The exclusion list is what most of the "
        "room is actually listening for.",
        "'How long are we committing for?' — Through the observe-mode review. No "
        "calendar dates exist in the plan yet, and I will not invent one.",
    )


def what_it_is(prs, ctx):
    s = slide_base(
        prs,
        title="What the thing actually is",
        kicker="A policy and enforcement service your gateway calls. Not a gateway, not "
               "a proxy, not a replacement for anything you run.",
        footer=ctx.src("CLAIMS-REGISTER.md — product statement · AGR-001 · AGR-007 · "
                       "AGR-017"),
    )
    panel(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 1.60)
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(BODY_Y + 0.05),
                             Inches(0.075), Inches(1.60))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False
    _, tf = textbox(s, MARGIN + 0.50, BODY_Y + 0.30, CONTENT_W - 1.1, 1.2)
    para(tf, "You send content to be evaluated. You get back a decision — allow, block, "
             "redact, or log only — with the reasons, the policy version that produced "
             "it, and a correlation identifier you can join to your own logs.", size=17,
         first=True, space_after=0, line=1.28)

    bullets(s, MARGIN, BODY_Y + 1.95, CONTENT_W, [
        "The policy is separate from the connector|The policy says what must be "
        "evaluated and how violations are handled. The connector says how your gateway "
        "invokes it. If you change gateway later, you re-implement a connector — you do "
        "not rewrite or re-approve the policy.",
        "The connector carries no policy, no detection logic, and no credentials|Not "
        "yours and not ours. It knows how long to wait and what to do when it cannot "
        "wait longer. It never learns what to detect.",
        "It evaluates the prompt and the response, and nothing else yet|Not your "
        "retrieval content, not tool arguments, not conversation history. Those are "
        "later-phase work.",
    ], size=12.5, gap=0.60)
    notes(
        s,
        "Product statement in substance from CLAIMS-REGISTER.md; standalone platform "
        "AGR-001; the policy/connector split AGR-007 with the artifact-level statement "
        "in ADR-0008 §5 and the configuration boundary in ADR-0012 §4; decision "
        "structure AGR-017; content-scope limitation DATA-ARCHITECTURE.md §3.3.",
        "The connector is provider code running inside their process with access to "
        "their users' content. Their security review of that artifact is a legitimate "
        "onboarding step, and the published documentation is written to be reviewed — "
        "say so before they have to ask.",
        "Keep this to the mental model. The detail they want is in the next three "
        "slides, and pre-empting it here makes this one dense.",
        "'Does it sit in front of our gateway?' — No. It is called by your gateway. "
        "Your model call never transits our infrastructure in this mode.",
    )


def request_path(prs, ctx):
    s = slide_base(
        prs,
        title="What changes in your request path",
        kicker="Two synchronous calls added around the model call you already make.",
        footer=ctx.src("INTEGRATION-ARCHITECTURE.md §4.1, §4.4 · ADR-0005 §6 · CON-016"),
    )
    caption(s, MARGIN, BODY_Y + 0.02, 5.0, "TODAY", size=10, color=MUTED)
    for i, (label, w) in enumerate([("Application", 2.20), ("Your gateway", 2.20),
                                    ("Model provider", 2.20)]):
        x = MARGIN + i * (2.20 + 0.55)
        box(s, x, BODY_Y + 0.28, w, 0.62, label, size=12)
        if i < 2:
            arrow(s, x + w + 0.02, BODY_Y + 0.59, x + w + 0.53, BODY_Y + 0.59,
                  color=HAIRLINE)

    caption(s, MARGIN, BODY_Y + 1.22, 5.0, "WITH THE CONNECTOR INSTALLED", size=10,
            color=ACCENT)
    steps = [
        ("Application", SURFACE, HAIRLINE),
        ("Your gateway", SURFACE, HAIRLINE),
        ("evaluate\ninput", SURFACE_ALT, ACCENT),
        ("Model provider", SURFACE, HAIRLINE),
        ("evaluate\noutput", SURFACE_ALT, ACCENT),
        ("Application", SURFACE, HAIRLINE),
    ]
    sw, sgap = 1.72, 0.30
    for i, (label, fill, border) in enumerate(steps):
        x = MARGIN + i * (sw + sgap)
        box(s, x, BODY_Y + 1.50, sw, 0.86, label, size=11.5, fill=fill, border=border)
        if i < len(steps) - 1:
            arrow(s, x + sw + 0.02, BODY_Y + 1.93, x + sw + sgap - 0.02, BODY_Y + 1.93,
                  color=ACCENT if i in (1, 3) else HAIRLINE)

    bullets(s, MARGIN, BODY_Y + 2.62, CONTENT_W, [
        "Both evaluation calls are synchronous and both are on your latency budget|"
        "There is no numeric budget yet — it is instrumented from the first phase and "
        "the targets get set from real traffic afterwards. Yours would be some of that "
        "traffic.",
        "Every stage has an explicit timeout, and what happens on expiry is configured, "
        "never assumed|A timeout is not an implicit allow. The behaviour is part of the "
        "policy and it is written down before anything runs.",
        "One correlation identifier threads both calls and the model call|So a decision "
        "in our audit trail and a request in your logs are the same request, joinable "
        "without guesswork.",
    ], size=12.5, gap=0.58)
    notes(
        s,
        "Request sequence per INTEGRATION-ARCHITECTURE.md §4.1; correlation and "
        "per-stage timeouts §4.4; mandatory timeouts CON-016; status semantics — a "
        "block is a completed evaluation, not an error — ADR-0005 §6; the missing "
        "numbers are ASM-011.",
        "On block at the input stage the connector returns a safe rejection without "
        "calling the model; on block at the output stage it returns a safe fallback "
        "instead of the model's response. In observe mode neither happens — the "
        "decision is recorded and the original content passes through.",
        "Draw the two extra hops and then say the honest thing about latency: we do not "
        "know yet, and their traffic is how we find out. That is a real cost and "
        "pretending otherwise damages everything after it.",
        "'What does this add to our p95?' — Unmeasured. Two synchronous calls, each "
        "with a configured timeout, and no target set. We would measure it on your "
        "traffic in observe mode before anyone talks about enforcement.",
    )


def your_mode(prs, ctx):
    s = dg.topology_slide(
        prs,
        "You are on Remote Evaluation",
        "You keep your gateway and your model credentials. We run the evaluation "
        "service.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §2 · AGR-002 · AGR-009 · AGR-014"),
        dg.mode_a_topology,
        "Your prompts and responses are transmitted to the hosted Evaluation API on "
        "every evaluated call. This is not a private-data or data-local model. Raw "
        "content is not persisted by default — decision metadata is — and any retention "
        "requires explicit configuration at the tenant level.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §2.1 topology and §2.3 content exposure; framing "
        "mandated by AGR-009; retention default AGR-014 and CON-014; the two other "
        "modes are AGR-002 and land in Phases 3 and 4.",
        "If this team's workload carries regulated or residency-bound content, Remote "
        "Evaluation is the wrong home for it and there is no interim configuration that "
        "fixes that. The customer-managed runtime is Phase 4 — do not offer it as an "
        "option available to them now.",
        "State the exposure before they ask. A pilot team that discovers this later "
        "feels misled, and they would be right to.",
        "'Does our content leave our environment?' — Yes, on every evaluated call, to "
        "the evaluation service. Your model call does not — that stays entirely in your "
        "environment with your credentials.",
    )


def what_you_install(prs, ctx):
    s = slide_base(
        prs,
        title="What you actually install",
        kicker="A published artifact and a configuration file. Both reviewable before "
               "anything runs.",
        footer=ctx.src("ADR-0008 §1–§5 · ADR-0006 §1–§4 · ADR-0012 §1–§5 · AGR-019"),
    )
    panel(s, MARGIN, BODY_Y + 0.05, 6.30, 3.60)
    box(s, MARGIN, BODY_Y + 0.05, 6.30, 0.52, "THE CONNECTOR", bold=True, size=12.5,
        fill=ACCENT_DIM, border=ACCENT)
    bullets(s, MARGIN + 0.28, BODY_Y + 0.86, 5.80, [
        "Published to a named channel your tooling already uses|Versioned "
        "independently of the platform, with release notes and a changelog.",
        "Integrity is verifiable before it executes|Verification is part of the "
        "installation instructions, not an option a careful team has to discover.",
        "Its privilege is documented, not implied|It observes prompt and response "
        "content, calls the evaluation service over the network, and applies returned "
        "decisions to the request. That is the description your security review gets.",
    ], size=12, gap=0.68, dot=ACCENT)

    panel(s, 7.13, BODY_Y + 0.05, 5.45, 3.60)
    box(s, 7.13, BODY_Y + 0.05, 5.45, 0.52, "THE CONFIGURATION FILE", bold=True,
        size=12.5, fill=SURFACE, border=HAIRLINE)
    bullets(s, 7.41, BODY_Y + 0.86, 4.95, [
        "A file, not a console screen|Schema-validated, committed to your repository, "
        "reviewed in a pull request, and diffable. Drift shows up as a diff rather "
        "than a support conversation.",
        "It states what you are bound to|Workload identity, policy name and immutable "
        "version, enforcement mode, endpoint, timeouts, failure behaviour, and which "
        "connector versions are compatible.",
        "It is generated, and regenerable|Losing it is an inconvenience, never an "
        "outage — and editing it cannot get you weaker enforcement, because the "
        "service resolves your policy itself.",
    ], size=12, gap=0.68, dot=LOG_ONLY)
    notes(
        s,
        "Distribution, integrity and documented privilege per ADR-0008 §1, §3, §4, §5; "
        "the manifest is ADR-0006 §1 file-not-screen, §2 derived-not-authoritative, §3 "
        "required contents, §4 prohibited contents enforcing AGR-019; client "
        "configuration retrieval ADR-0012 §1 and §5.",
        "A client that has never retrieved its configuration and was given none refuses "
        "to evaluate rather than allowing. That is deliberate — defaulting to allow "
        "would silently disable guardrails for a workload whose owner believes they are "
        "on. Expect this to be challenged as an outage risk.",
        "Both panels answer the same underlying question: what am I being asked to trust "
        "inside my own process. Let them read rather than narrating every line.",
        "'Can we review the connector source?' — Not decided in the material I have. "
        "What is committed is a published artifact with verifiable integrity and a "
        "documented description of what it does in your process.",
    )


def onboarding(prs, ctx):
    s = slide_base(
        prs,
        title="The sequence, end to end",
        kicker="Seven steps. You are not onboarded until we have seen evaluations at "
               "every stage your policy declares.",
        footer=ctx.src("AGR-019 · PRODUCT-OVERVIEW.md §8 · ADR-0007 §4 · "
                       "CLAIMS-REGISTER.md §2, §10"),
    )
    dg.onboarding_stepper(s, f.ONBOARDING_STEPS)
    panel(s, MARGIN, BODY_Y + 2.10, CONTENT_W, 1.95, fill=SURFACE_ALT, border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.32, CONTENT_W - 0.7, 1.6)
    para(tf, "None of these steps requires:", size=14, bold=True, first=True,
         space_after=8)
    para(tf, f.IMPORT_EXCLUSIONS, size=13, color=MUTED, space_after=8, line=1.3)
    para(tf, "Between installing and observing sits a verification state: a policy "
             "declaring input and output whose workload has only ever produced input "
             "evaluations is a half-wired installation, and it shows up as one rather "
             "than as a quiet success.", size=12.5, color=TEXT, space_after=0)
    notes(
        s,
        "AGR-019 meaning of import; sequence in PRODUCT-OVERVIEW.md §8; the "
        "pending-verification state and the stale-workload signal are ADR-0007 §4 and "
        "§5; approved wording CLAIMS-REGISTER.md §2 and §10.",
        "This flow is multi-step and reviewed. It is not one-click, instant, or "
        "zero-configuration, and the claims register prohibits describing it that way — "
        "including verbally in this room.",
        "Walk the steps quickly; spend the time on the verification state, because it "
        "is the part that catches a botched install before it becomes a false sense of "
        "coverage.",
        "'What if we install it and forget?' — A bound workload that stops producing "
        "evaluations is marked stale and reported. The interval is configured per "
        "workload, since a batch job and an interactive service cannot share a "
        "threshold.",
    )


def observe_first(prs, ctx):
    s = slide_base(
        prs,
        title="You start in observe — and stay there",
        kicker="Evaluations run and are recorded. Nothing is blocked, redacted, or "
               "changed in what your users receive.",
        footer=ctx.src("AGR-011 · ADR-0004 §1–§3, §5 · PRINCIPLES.md §5.6 · OQ-014"),
    )
    dg.enforcement_ladder(s, f.ENFORCEMENT_STATES, highlight=1)
    panel(s, MARGIN, BODY_Y + 2.85, CONTENT_W, 1.35)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 3.05, CONTENT_W - 0.7, 1.0)
    para(tf, "The state belongs to your binding, not to the policy.", size=16,
         color=ACCENT, bold=True, first=True, space_after=7)
    para(tf, "Another team can run the same approved policy version in enforce mode while "
             "you sit in observe — that is the whole point of the demonstration. The "
             "state caps the action and never escalates it, and it changes the action "
             "only: the findings and reason codes you see in observe are exactly the "
             "ones you would have acted on. Promotion is an explicit, audited action "
             "that you accept.", size=12.5, color=MUTED, space_after=0, line=1.25)
    notes(
        s,
        "AGR-011 observe-before-enforce; state on the binding and clamping semantics "
        "ADR-0004 §1, §2, §5; the warn state is distinguished by a decision field per "
        "§3; PRINCIPLES.md §5.6; the undefined promotion criteria are OQ-014.",
        "Security and governance own policy approval; this team accepts enforcement for "
        "its own workload. Neither side can promote silently. What is not yet defined is "
        "the evidence that justifies promotion — that gap is real and they should hear "
        "it from us.",
        "This slide removes the objection they came in with. Say plainly that nothing "
        "they own gets blocked without their agreement, then let the clamping detail do "
        "the rest.",
        "'Could someone promote us without asking?' — No. Promotion is an explicit "
        "audited transition and the consuming team accepts enforcement. The criteria "
        "for making that call are not yet defined, which is one of the things we would "
        "want your input on.",
    )


def reading_a_decision(prs, ctx):
    s = slide_base(
        prs,
        title="Reading what observe mode gives you",
        kicker="Every evaluation returns this, and records it. This is what you will be "
               "reviewing.",
        footer=ctx.src("INTEGRATION-ARCHITECTURE.md §3.3 · ADR-0003 · ADR-0004 · "
                       "DATA-ARCHITECTURE.md §4"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.55, [
        ["Field", "What you do with it"],
        ["action · allowed",
         "allow · block · redact · log_only. In observe every action arrives as "
         "log_only — the field tells you what would have happened."],
        ["enforcement_state",
         "The state that produced this decision, so a log_only in observe is "
         "distinguishable from a log_only in warn."],
        ["reason_codes · guardrail_category",
         "Why it decided, in a form that does not change if the detection engine behind "
         "it changes. Category is its own field, so you can count findings by category "
         "without parsing strings."],
        ["policy_name · policy_version",
         "Which immutable version decided. If behaviour changes between reviews, this "
         "is what tells you whether the policy moved or your traffic did."],
        ["evaluator_metadata", "Which engines and versions ran."],
        ["correlation_id · audit_id · latency",
         "Join to your own request logs; find the durable record; see what the call "
         "cost you."],
    ], col_w=[0.29, 0.71], size=11, first_col_bold=True,
        row_h=[0.34, 0.55, 0.46, 0.68, 0.55, 0.30, 0.50])
    caption(s, MARGIN, BODY_BOTTOM - 0.36, CONTENT_W,
            "A reason code looks like a category, then the evaluator family, then that "
            "evaluator's own code — so a finding from today's engine and the same "
            "finding from a future one group together rather than becoming two "
            "different things you have to reconcile.")
    notes(
        s,
        "Decision structure INTEGRATION-ARCHITECTURE.md §3.3 and ADR-0005 §5; "
        "reason-code namespacing ADR-0003 and DEC-013; enforcement_state on the "
        "decision ADR-0004 §3; the durable record fields DATA-ARCHITECTURE.md §4.",
        "In observe the clamping changes the action to log_only but leaves findings and "
        "reason codes untouched. That is what makes observe-mode review real "
        "measurement rather than a rehearsal — worth saying, because a team that thinks "
        "observe is a dry run will not take the review seriously.",
        "This is the slide that turns 'run it in observe' into something concrete. Spend "
        "time on the reason-code row: it is what their false-positive review is "
        "actually made of.",
        "'How do we know a finding is a false positive?' — You look at the reason code "
        "and the content it fired on. There is no tooling specified for that review "
        "yet, which is a gap we would want your help scoping.",
    )


def when_it_fails(prs, ctx):
    s = slide_base(
        prs,
        title="What happens when evaluation cannot complete",
        kicker="Configured per failure mode, before anything runs. There is no default "
               "and no silent fallback.",
        footer=ctx.src("AGR-012 · ADR-0009 §4 · ADR-0012 §2, §3 · OPERABILITY.md §7 · "
                       "ADR-0014 §3"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 2.60, [
        ["If this happens", "Your policy says which of these happens"],
        ["The evaluation call times out",
         "Allow and log · return a safe fallback · block the request"],
        ["A detector is unavailable",
         "Continue on the remaining detectors and log · use a cached decision · block"],
        ["Your policy cannot be resolved", "Block · require manual approval"],
        ["Audit delivery fails",
         "Allow and log locally for later reconciliation · block, if audit is critical"],
    ], col_w=[0.36, 0.64], size=11.5, first_col_bold=True,
        row_h=[0.36, 0.42, 0.46, 0.36, 0.46])
    bullets(s, MARGIN, BODY_Y + 2.85, CONTENT_W, [
        "Losing contact with us never un-configures your connector|It keeps applying "
        "the configuration it last retrieved. A platform outage does not turn your "
        "working gateway into a refusing one.",
        "Decisions your connector makes during an outage are recorded locally and "
        "delivered later|That local store is bounded, discards oldest first when full, "
        "and reports how many it discarded — so an outage is not quietly understated. "
        "It holds reasons and identifiers only, never your content.",
    ], size=12.5, gap=0.62)
    notes(
        s,
        "AGR-012 enumerates the failure modes; no-default schema rule ADR-0009 §4; the "
        "client keeps its last configuration per ADR-0012 §3, and refuses without one "
        "per §2; the same table is OPERABILITY.md §7; client-synthesized records and "
        "their bounded store are ADR-0014 §3.",
        "Client-origin records are explicitly not evidentiary — they say what a client "
        "reports having done, not what the platform observed, and delivery is "
        "best-effort because records can be lost if the process dies. Do not let this "
        "team believe their audit trail is complete through an outage.",
        "This is where a sceptical engineer decides whether we have thought it through. "
        "The second bullet — bounded, lossy, and honest about it — usually does more "
        "than the table above it.",
        "'What happens in observe if the service is down?' — Whatever your policy "
        "configures for a timeout. In observe nothing was going to be blocked anyway, "
        "so the practical answer is that traffic proceeds and the gap is recorded.",
    )


def what_you_own(prs, ctx):
    s = slide_base(
        prs,
        title="What you own, and what you do not",
        kicker="The recurring cost is small and specific. It is also real, and it does "
               "not go away after setup.",
        footer=ctx.src("TEAM-AND-OWNERSHIP.md §2.4, §3.4 · INTEGRATION-ARCHITECTURE.md "
                       "§4.3 · AGR-010 · AGR-019"),
    )
    panel(s, MARGIN, BODY_Y + 0.05, 6.30, 3.75)
    box(s, MARGIN, BODY_Y + 0.05, 6.30, 0.52, "YOURS", bold=True, size=12.5,
        fill=ACCENT_DIM, border=ACCENT)
    bullets(s, MARGIN + 0.28, BODY_Y + 0.86, 5.80, [
        "Installing the connector and keeping it healthy",
        "Registering the workload accurately|Environment, risk classification, gateway "
        "type, expected throughput — the platform reasons about your workload from what "
        "you declare.",
        "Reviewing observe-mode findings before accepting enforcement",
        "Everything about your application that was already yours|End-user "
        "authentication, business authorization, tenant isolation in your own data, and "
        "your retry and fallback behaviour for the model call itself.",
    ], size=12, gap=0.56, dot=ACCENT)

    panel(s, 7.13, BODY_Y + 0.05, 5.45, 3.75, fill=SURFACE_ALT)
    box(s, 7.13, BODY_Y + 0.05, 5.45, 0.52, "OURS", bold=True, size=12.5,
        fill=SURFACE, border=HAIRLINE)
    bullets(s, 7.41, BODY_Y + 0.86, 4.95, [
        "The evaluation contract, the runtime, and the connector itself",
        "Policy schemas, detection integration, and compatibility across versions",
        "The hosted service's availability, capacity and incident response",
        "Deployment artifacts and documentation",
    ], size=12, gap=0.56, dot=LOG_ONLY)

    caption(s, MARGIN, BODY_BOTTOM - 0.24, CONTENT_W,
            "Policy content itself is neither of ours: organization-level policy "
            "requirements, risk classification, approval and exception criteria belong "
            "to security and governance.")
    notes(
        s,
        "TEAM-AND-OWNERSHIP.md §2.4 consuming-team responsibilities and §3.4 the "
        "onboarding responsibility matrix; connector non-responsibilities "
        "INTEGRATION-ARCHITECTURE.md §4.3; the authorization boundary AGR-010; the "
        "onboarding boundary AGR-019.",
        "Ownership shifts if they ever move to the customer-managed runtime — they "
        "would then also own runtime infrastructure, availability, upgrades and local "
        "audit storage. Not relevant to this pilot, but relevant if they ask about "
        "later phases.",
        "Read the fourth item in the left column deliberately. Teams routinely assume a "
        "guardrail layer takes over some of their authorization surface; it does not, "
        "and finding that out later is expensive.",
        "'Is this an ongoing cost or a setup cost?' — Both, but the ongoing part is "
        "small: keep the connector healthy, keep the registration accurate, review "
        "findings before enforcement. The setup part is installation and review.",
    )


def what_success_means(prs, ctx):
    s = slide_base(
        prs,
        title="What this pilot has to demonstrate",
        kicker="Your instance is half of the evidence. These are the criteria your "
               "participation actually satisfies.",
        footer=ctx.src("SUCCESS-CRITERIA.md §1.2, §1.3 · VALIDATION-PLAN.md · AGR-020"),
    )
    bullets(s, MARGIN, BODY_Y + 0.10, 7.15, [
        "Two independent instances consume the same policy version|Yours and theirs, "
        "both bound to the identical policy name and version, both verified to resolve "
        "the same content.",
        "Neither instance shares model-provider credentials with the platform|Audited "
        "against the platform's own stored secrets. Yours never leave your environment.",
        "Both receive consistent normalized decisions|The same schema, actions, reason "
        "codes, policy version and correlation identifier — regardless of which "
        "instance asked.",
        "One workload runs in observe while another runs in enforce|Against the same "
        "policy version. That is the property that makes one policy usable by teams at "
        "different maturity levels.",
    ], size=13, gap=0.66, dot=ACCENT)

    panel(s, 8.35, BODY_Y + 0.05, 4.23, 3.95, fill=SURFACE_ALT, border=WARN)
    _, tf = textbox(s, 8.62, BODY_Y + 0.30, 3.70, 3.45)
    para(tf, "The gating rule", size=13, color=WARN, bold=True, first=True,
         space_after=6)
    para(tf, f.GATING_RULE, size=12, color=TEXT, space_after=12)
    para(tf, "Five more criteria cover upgrade and rollback, disabling raw-content "
             "persistence, failure behaviour under an injected fault, and packaging the "
             "runtime for customer-managed deployment. Those exercise the platform "
             "rather than your instance.", size=12, color=MUTED, space_after=12)
    para(tf, "These are targets in a plan. The demonstration has not been run, and how "
             "each criterion gets verified and who signs it off is not yet assigned.",
         size=11.5, color=MUTED, italic=True, space_after=0)
    notes(
        s,
        "SUCCESS-CRITERIA.md §1.2 criteria MVP-01 through MVP-05 as summarised here, "
        "MVP-06 through MVP-09 in the panel, §1.3 the gating rule; milestones in "
        "VALIDATION-PLAN.md; phase gating AGR-020.",
        "Verification method, environment and sign-off owner per criterion are not yet "
        "defined — a delivery-plan gap owed before the first phase exits. If this team "
        "asks who signs off their part, the honest answer is that it is not assigned.",
        "Frame this as what their effort buys, not as a test they might fail. Then be "
        "explicit that the demonstration has not been run by anyone.",
        "'What if the demonstration fails?' — Then the phase is not validated and the "
        "next one is not committed. That is the point of running it, and a negative "
        "result is a real outcome rather than an embarrassment.",
    )


def rough_edges(prs, ctx):
    s = slide_base(
        prs,
        title="The rough edges, before you find them",
        kicker="Five things that are genuinely unresolved. None of them is a reason not "
               "to pilot; all of them are things you would hit.",
        footer=ctx.src("ASM-011, ASM-012 · OQ-014, OQ-022 · AGR-018 · ASM-005 · "
                       "VALIDATION-PLAN.md"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.85, [
        ["What is unresolved", "What it means for you"],
        ["No latency target exists",
         "Two synchronous calls are added to your request path and nobody can tell you "
         "what they cost yet. Instrumentation lands with the first phase; your traffic "
         "is how the target gets set."],
        ["No availability objective exists",
         "The service you are calling has no published objective yet. Your policy's "
         "configured timeout behaviour is the thing that protects you in the meantime."],
        ["Promotion criteria are undefined",
         "There is no agreed answer to what evidence justifies moving from observe to "
         "enforce. You would be helping define it rather than following it."],
        ["The connector support window is undefined",
         "How long a connector version stays supported, and the deprecation policy, are "
         "not set. Upgrade cadence is therefore not something we can commit to yet."],
        ["Bypass is possible, by you",
         "The cooperative posture means the connector can be disabled, removed or "
         "misconfigured by your team. That is accepted for this phase — but it also "
         "means coverage depends on you, not on a control."],
    ], col_w=[0.30, 0.70], size=10.5, header_size=11, first_col_bold=True,
        row_h=[0.36, 0.72, 0.62, 0.62, 0.62, 0.72])
    caption(s, MARGIN, BODY_BOTTOM - 0.32, CONTENT_W,
            "These are on the register with owners where owners exist. They are stated "
            "here because a pilot team that meets them unannounced stops trusting "
            "everything else on the slides.")
    notes(
        s,
        "The numeric gaps are ASM-011 (latency) and ASM-012 (availability); promotion "
        "criteria OQ-014; connector support window OQ-022; the cooperative posture and "
        "its residual bypass risk are AGR-018 with ASM-005; verification ownership gaps "
        "in VALIDATION-PLAN.md.",
        "Do not soften any of these under pressure to close the commitment. A pilot "
        "team that agrees on a softened description will withdraw at the first "
        "contradiction, and that costs the phase more than a slower yes.",
        "This is the credibility slide. Deliver it flatly, without apology and without "
        "over-explaining — the list is the point.",
        "'Why would we take this on?' — Because the alternative is that guardrails "
        "arrive as a mandate designed without you. Piloting is how the constraints that "
        "matter to your team get into the design while it is still cheap to change.",
    )


def the_ask(prs, ctx):
    s = slide_base(
        prs,
        title="What we need from you",
        footer=ctx.src("DECK-VARIANTS.md §5 · AGR-011 · SUCCESS-CRITERIA.md §1.2 · "
                       "ASM-002, ASM-003"),
    )
    panel(s, MARGIN, BODY_Y - 0.05, CONTENT_W, 1.50, fill=SURFACE, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.40, BODY_Y + 0.20, CONTENT_W - 0.8, 1.05)
    para(tf, "Agree to run your gateway as the second independent instance, with the "
             "connector installed and the workload in observe mode.", size=19, bold=True,
         first=True, space_after=6, line=1.2)
    para(tf, "Nothing you own gets blocked, and no commitment to enforcement is implied "
             "by saying yes to this.", size=12.5, color=MUTED, space_after=0)

    _, tf = textbox(s, MARGIN, BODY_Y + 1.58, 6.05, 0.4)
    para(tf, "Specifically, from you", size=13, color=ACCENT, bold=True, first=True,
         space_after=0)
    bullets(s, MARGIN, BODY_Y + 2.00, 6.05, [
        "A named engineering contact who owns the integration",
        "A workload you are willing to put on it, and its accurate registration details",
        "A security review of the connector artifact on your side, on your timeline",
        "The observe-mode review, once there is something to review",
    ], size=12.5, gap=0.54, dot=ALLOW)

    _, tf = textbox(s, 7.03, BODY_Y + 1.58, 5.55, 0.4)
    para(tf, "What you are not agreeing to", size=13, color=MUTED, bold=True,
         first=True, space_after=0)
    bullets(s, 7.03, BODY_Y + 2.00, 5.55, [
        "Enforcement, on any timeline — that is a separate decision you make later",
        "Handing over credentials, gateway administration, or configuration",
        "A migration off your current gateway, now or ever",
        "A delivery date — none exists in the plan yet",
    ], size=12.5, gap=0.54, dot=LOG_ONLY)

    caption(s, MARGIN, BODY_BOTTOM - 0.20, CONTENT_W,
            "If you would rather not commit today, the useful outcome is the list of "
            "what would have to be true first — that becomes a delivery requirement "
            "rather than an objection we work around.")
    notes(
        s,
        "DECK-VARIANTS.md §5 (the decision this deck asks for); observe-first AGR-011; "
        "the two-instance criterion MVP-01 in SUCCESS-CRITERIA.md §1.2; the assumption "
        "that a second team volunteers is ASM-002/ASM-003.",
        "This team declining is a legitimate outcome that invalidates a load-bearing "
        "assumption behind the first phase. Report it as a finding rather than "
        "renegotiating the ask downward in the room.",
        "Make the ask, name what they are not agreeing to, and stop. The second column "
        "is what makes the first one answerable today.",
        "'Can we decide after our security review?' — Yes, and that is the expected "
        "sequence. What we need today is whether you are willing to run the review at "
        "all.",
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
            "The three deployment modes  ·  Vocabulary  ·  The questions teams ask")
    notes(
        s,
        "DECK-VARIANTS.md §5 appendix list.",
        "The appendix exists for follow-ups, not for filling time. This session works "
        "better short.",
        "Pause. If there is a named contact and a candidate workload, stop here.",
        "n/a — divider slide.",
    )


def three_modes(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — The three modes, and why you are on the first",
        kicker="A choice about ownership and residency. Not a maturity ladder, and not "
               "equally available.",
        footer=ctx.src("AGR-002 · AGR-020 · DEPLOYMENT-ARCHITECTURE.md §5 · "
                       "PRODUCT-ROADMAP.md"),
    )
    dg.mode_columns(s, f.MODES, highlight=0)
    caption(s, MARGIN, BODY_BOTTOM - 0.34, CONTENT_W,
            "Remote Evaluation is the only mode delivered in the first two phases. The "
            "guarded gateway is Phase 3 and the customer-managed runtime is Phase 4 — "
            "so if your workload eventually needs in-account evaluation, the pilot does "
            "not block that, but nothing available now provides it.")
    notes(
        s,
        "AGR-002 three modes; per-mode ownership DEPLOYMENT-ARCHITECTURE.md §2–§4; "
        "phase sequencing AGR-020 and PRODUCT-ROADMAP.md.",
        "The policy and the contract are the same across modes, so a workload that "
        "later moves to a different mode does not have its policy re-approved. That is "
        "the portability claim, and it is the reason the pilot is not a dead end.",
        "Reach for this if the team asks whether they are locking themselves into the "
        "hosted path.",
        "'If we later need in-account evaluation, do we start over?' — No. The policy "
        "and the connector contract do not change; the runtime's location does.",
    )


def domain_model(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — The vocabulary",
        kicker="Six terms. Worth having straight before the integration conversation.",
        footer=ctx.src("AGR-017 · GLOSSARY.md · DATA-ARCHITECTURE.md §1"),
    )
    dg.entity_grid(s, f.DOMAIN_ENTITIES)
    notes(
        s,
        "AGR-017 core domain model; per-entity attributes DATA-ARCHITECTURE.md §1.2–"
        "§1.7 and GLOSSARY.md.",
        "The binding is the one people miss, and it is the one that matters most to "
        "this team: it is where their enforcement state lives, which is why another "
        "team's state cannot affect theirs.",
        "Use this if workload, policy and binding start being used interchangeably.",
        "'What exactly is a workload?' — The application or service using guardrails, "
        "with its owner, environment, risk classification, gateway type, assigned "
        "policy and enforcement mode.",
    )


def questions(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — The questions teams ask",
        kicker="Pull only the pair that matches.",
        footer=ctx.src("FAQ.md · AGR-009, AGR-010, AGR-012, AGR-014, AGR-018"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 4.05, f.OBJECTION_ROWS_CLEAN,
          col_w=[0.28, 0.72], size=11.5, first_col_bold=True,
          row_h=[0.40, 0.72, 0.72, 0.78, 0.72, 0.78])
    notes(
        s,
        "FAQ.md — the five pairs most likely to be raised; each answer traces to "
        "AGR-009, AGR-014, AGR-018, AGR-010 and AGR-012 respectively.",
        "Answer the one that was asked and stop. This audience reads a recited table as "
        "a prepared defence rather than an answer.",
        "Only surface this if something lands that the main sequence did not cover.",
        "If a question arrives that is not here and not in the source material, say it "
        "is not decided and take it away. Improvising a commitment to a pilot team is "
        "the most expensive place to do it.",
    )


SLIDES = [
    cover,
    what_you_are_asked,
    what_it_is,
    request_path,
    your_mode,
    what_you_install,
    onboarding,
    observe_first,
    reading_a_decision,
    when_it_fails,
    what_you_own,
    what_success_means,
    rough_edges,
    the_ask,
    appendix_divider,
    three_modes,
    domain_model,
    questions,
]
