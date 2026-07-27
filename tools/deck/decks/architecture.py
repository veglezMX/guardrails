"""Architecture review board deck.

Audience: staff and principal engineers, architects, and the governance body that
signs off cross-cutting technical decisions. The decision in the room is whether
the contracts are sound — not whether to fund the work.

Written from the 50-architecture material and the accepted ADRs. Slides carry no
internal document identifiers; the speaker notes carry all of them. Governed by
solution-docs/40-sales-deck/DECK-VARIANTS.md §3.
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


def _mono_panel(s, x, y, w, h, title, lines):
    """A labelled payload panel — field lists shown as they appear on the wire."""
    panel(s, x, y, w, h)
    box(s, x, y, w, 0.46, title, bold=True, size=11.5, fill=SURFACE, border=HAIRLINE)
    _, tf = textbox(s, x + 0.26, y + 0.62, w - 0.52, h - 0.8)
    for i, line in enumerate(lines):
        name, _, note = line.partition("|")
        para(tf, name.strip(), size=11.5, color=TEXT, first=(i == 0), space_after=1)
        if note:
            para(tf, note.strip(), size=10, color=MUTED, space_after=5, line=1.1)


def cover(prs, ctx):
    s = slide_base(prs)
    panel(s, MARGIN, 1.55, CONTENT_W, 3.55, fill=SURFACE_ALT)

    _, tf = textbox(s, MARGIN, 0.85, CONTENT_W, 0.45)
    para(tf, "AI GUARDRAILS", size=14, color=ACCENT, bold=True, first=True, space_after=0)

    _, tf = textbox(s, MARGIN + 0.55, 2.15, CONTENT_W - 1.6, 2.6)
    para(tf, "One policy, many gateways.", size=34, bold=True, font=HEAD_FONT,
         first=True, space_after=6, line=1.05)
    para(tf, "That claim rests entirely on", size=34, bold=True, font=HEAD_FONT,
         space_after=6, line=1.05)
    para(tf, "the contracts you are here to review.", size=34, color=ACCENT, bold=True,
         font=HEAD_FONT, space_after=0, line=1.05)

    _, tf = textbox(s, MARGIN, 5.42, CONTENT_W, 0.9)
    para(tf, "Architecture review — evaluation contract, policy schema, enforcement "
             "semantics, connector and runtime compatibility", size=17, color=TEXT,
         first=True, space_after=8)
    para(tf, "Design review  ·  Presenter: TBD  ·  Nothing in this deck is built or "
             "running", size=11.5, color=MUTED, space_after=0)

    caption(s, MARGIN, 6.92, CONTENT_W,
            "Placeholder styling — no brand kit exists yet; all colours and type are "
            "stand-ins pending brand.", size=9)
    notes(
        s,
        "DECK-VARIANTS.md §3; the contract set under review is ADR-0004, ADR-0005, "
        "ADR-0007 through ADR-0015, all Accepted pending owner ratification.",
        "Every ADR in this deck is Accepted pending owner ratification on a working "
        "branch — do not present any of them as immutable. This board can still change "
        "them, which is why the session exists.",
        "Open by naming what this session is: a design review with sign-off authority, "
        "not a status update and not a funding pitch. Then go straight to what is "
        "already decided versus what is open.",
        "'Has any of this been built?' — No. These are records of design decisions and "
        "their consequences. The first code lands in Phase 1, after this review.",
    )


def review_scope(prs, ctx):
    s = slide_base(
        prs,
        title="What we are asking you to review",
        kicker="Three categories. Only the third needs a decision from this room today.",
        footer=ctx.src("DECK-VARIANTS.md §3 · DOCUMENT-GOVERNANCE.md §6 · OPEN-QUESTIONS.md"),
    )
    table(s, MARGIN, BODY_Y + 0.10, CONTENT_W, 3.4, [
        ["Category", "What it covers", "What we want from you"],
        ["Settled by agreement",
         "Standalone platform · policy separate from connector · one evaluation "
         "contract across all three modes · guardrails are not authorization · "
         "failure behavior is part of policy.",
         ("Confirm you can live with them. Changing one is a register-level change, "
          "not a review-comment.", MUTED)],
        ["Decided in design records",
         "Wire format and versioning · policy document schema · enforcement-state "
         "clamping · reason-code namespacing · policy storage and distribution · "
         "connector identity, integrity and liveness · runtime deployment target.",
         ("Challenge them. Each is recorded with its alternatives and its accepted "
          "cost — the cost is where the argument usually is.", TEXT)],
        ["Open, and blocking",
         "The questions in the closing section: enforcement posture for the first "
         "external customers, evaluation-stage vocabulary, connector support window, "
         "and the numbers nobody has set.",
         ("Rule on them, or name an owner and a date.", ACCENT)],
    ], col_w=[0.20, 0.47, 0.33], size=11, first_col_bold=True,
        row_h=[0.38, 0.95, 1.05, 0.85])
    caption(s, MARGIN, BODY_BOTTOM - 0.44, CONTENT_W,
            "Where a design record and an agreement disagree, the agreement governs and "
            "the record is corrected — never the reverse. That rule is what makes this "
            "review bounded rather than open-ended.")
    notes(
        s,
        "DECK-VARIANTS.md §3 (review scope); the authority ladder is "
        "DOCUMENT-GOVERNANCE.md §6, with L1 registers above L3 architecture records.",
        "The ADRs are Accepted pending owner ratification on a working branch. This "
        "board's objection is still cheap today; after ratification it is a "
        "register-level change with a documented supersession.",
        "Set the frame: we are not asking for approval of everything, we are asking for "
        "challenge on the middle row and a ruling on the bottom one. Then walk the "
        "architecture from the outside in.",
        "'Can we reopen an agreement?' — Yes, through the scope-change process with a "
        "recorded decision. Not in this room informally.",
    )


def system_context(prs, ctx):
    s = slide_base(
        prs,
        title="System context",
        kicker="Who calls the platform, what it calls, and what it never touches.",
        footer=ctx.src("SYSTEM-CONTEXT.md · CONTAINER-ARCHITECTURE.md §1 · AGR-003, AGR-004"),
    )
    panel(s, MARGIN, BODY_Y + 0.10, 3.55, 3.30, fill=SURFACE_ALT)
    caption(s, MARGIN + 0.25, BODY_Y + 0.26, 3.1, "CONSUMERS", size=10, color=MUTED)
    for i, name in enumerate(["Team-owned LiteLLM\nwith connector",
                              "Application via the SDK",
                              "Any authenticated\nHTTP client"]):
        box(s, MARGIN + 0.28, BODY_Y + 0.66 + i * 0.86, 3.0, 0.70, name, size=11.5)

    box(s, 4.75, BODY_Y + 0.72, 3.85, 2.10,
        "AI Guardrails\n\nControl plane · Evaluation API · decision engine · evaluator "
        "adapters · audit producer", bold=True, size=14, border=ACCENT)
    arrow(s, 4.05, BODY_Y + 1.60, 4.72, BODY_Y + 1.60)
    arrow(s, 8.62, BODY_Y + 1.35, 9.55, BODY_Y + 1.35)

    panel(s, 9.58, BODY_Y + 0.10, 3.00, 3.30, fill=SURFACE_ALT)
    caption(s, 9.80, BODY_Y + 0.26, 2.6, "DEPENDENCIES", size=10, color=MUTED)
    for i, name in enumerate(["Amazon Bedrock\nGuardrails",
                              "Deterministic and\nPII evaluators",
                              "Queue and object\nstore for audit"]):
        box(s, 9.80, BODY_Y + 0.66 + i * 0.86, 2.55, 0.70, name, size=11.5,
            fill=SURFACE_ALT)

    panel(s, MARGIN, BODY_BOTTOM - 1.18, CONTENT_W, 1.08, fill=SURFACE_ALT, border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.32, BODY_BOTTOM - 1.02, CONTENT_W - 0.64, 0.85)
    para(tf, "Outside the boundary, permanently", size=11, color=BLOCK, bold=True,
         first=True, space_after=4)
    para(tf, "Model invocation in cooperative modes  ·  model-provider credentials  ·  "
             "end-user authentication  ·  business authorization  ·  gateway "
             "administration  ·  the consuming application's own retry and fallback "
             "behaviour for the model call.", size=12, color=MUTED, space_after=0)
    notes(
        s,
        "SYSTEM-CONTEXT.md external actors and dependencies; consumer list per "
        "INTEGRATION-ARCHITECTURE.md §4–§5; exclusions per AGR-010 and the connector "
        "non-responsibilities in INTEGRATION-ARCHITECTURE.md §4.3.",
        "In Mode A the model call never transits this platform — only the two "
        "evaluation calls do. Reviewers frequently assume a proxy topology; correct it "
        "here rather than at the topology slides.",
        "Establish the boundary before the internals. The red panel is the part people "
        "get wrong, and getting it wrong makes every later slide read differently.",
        "'Why is Bedrock Guardrails a dependency rather than the product?' — Because it "
        "sits behind an evaluator adapter (AGR-004). It is the first managed engine, "
        "not a permanent or exclusive one.",
    )


def container_architecture(prs, ctx):
    s = slide_base(
        prs,
        title="Container architecture",
        kicker="Control plane owns policy. Runtime decides. Evaluation engines detect. "
               "Audit leaves asynchronously.",
        footer=ctx.src("CONTAINER-ARCHITECTURE.md §1 · ADR-0015 · AGR-004"),
    )
    dg.logical_architecture(s)
    notes(
        s,
        "CONTAINER-ARCHITECTURE.md §1 container map; deployment target per ADR-0015 "
        "(ECS Fargate behind an ALB, container-based so Phase 4 is a packaging "
        "exercise); evaluation-engine list per AGR-004.",
        "Fargate over EKS is a deliberate deferral, not a conviction — the Phase 1–2 "
        "workload is two long-running services. ALB over API Gateway is the "
        "weakest-held decision in the record and revisiting it changes an entry point, "
        "not an architecture.",
        "Give them the layering once, then say the load-bearing part out loud: policy "
        "is distributed asynchronously and resolution reads local state only. That is "
        "what the next several slides depend on.",
        "'Why containers rather than functions?' — Because Phase 4 ships the same "
        "artifact into a customer account. A function-based hosted deployment would "
        "mean two packaging paths for one codebase, and they would drift (ADR-0015).",
    )


def policy_vs_connector(prs, ctx):
    s = slide_base(
        prs,
        title="The separation everything else rests on",
        kicker="A policy is expressible and testable without reference to any connector. "
               "A connector carries no policy.",
        footer=ctx.src("AGR-007 · AGR-005 · PRINCIPLES.md §5.3 · ADR-0012 §4"),
    )
    dg.policy_vs_connector(s)
    caption(s, MARGIN, BODY_BOTTOM - 0.52, CONTENT_W,
            "The client learns how long to wait and what to do when it cannot wait "
            "longer. It never learns what to detect or what to do about a finding — "
            "that boundary is checkable at the artifact, not only in the design.",
            size=13.5, color=TEXT)
    notes(
        s,
        "AGR-007 (policies are separate from connectors); portability consequence "
        "AGR-005; the client-configuration boundary is ADR-0012 §4, the published "
        "artifact boundary ADR-0008 §5.",
        "This is stated as a property of the distributed artifact, not only of the "
        "runtime design — a connector release that embedded a policy or a credential "
        "would be detectable in the artifact itself.",
        "If this board only stress-tests one thing, make it this. Every portability "
        "claim later in the deck is a consequence of it holding.",
        "'What stops policy leaking into the client over the config channel?' — The "
        "configuration payload carries timeouts and failure behaviour only, by schema. "
        "No thresholds, no evaluator selection, no detection rules (ADR-0012 §4).",
    )


def evaluation_contract(prs, ctx):
    s = slide_base(
        prs,
        title="The evaluation contract",
        kicker="Synchronous HTTPS, JSON, IAM-authenticated. Major version in the path. "
               "The OpenAPI document is generated from the runtime's own models.",
        footer=ctx.src("ADR-0005 §1–§5 · INTEGRATION-ARCHITECTURE.md §3.2, §3.3 · "
                       "ADR-0003 · ADR-0004"),
    )
    _mono_panel(s, MARGIN, BODY_Y + 0.05, 5.95, 3.30, "EVALUATION REQUEST", [
        "tenant_id · workload_id|The identity every evaluation, binding and audit "
        "record resolves to.",
        "stage|input or output today; the reserved vocabulary is larger.",
        "content|A discriminated union tagged by kind — text is the only Phase 1 "
        "member. A bare string would make every future stage a breaking change.",
        "correlation_id|Threaded through input evaluation, model call and output "
        "evaluation.",
        "policy_ref|Optional and advisory only. The runtime resolves and authorizes "
        "server-side; a client cannot assert a weaker policy.",
    ])
    _mono_panel(s, 7.13, BODY_Y + 0.05, 5.45, 3.30, "DECISION", [
        "action · allowed|allow · block · redact · log_only.",
        "enforcement_state|The binding state that produced this decision. On the "
        "decision, not only in audit, because a client cannot implement warn without "
        "it at request time.",
        "reason_codes · guardrail_category|Category-namespaced, engine-independent.",
        "policy_name · policy_version · evaluator_metadata|What decided, and which "
        "version of it.",
        "transformed_content|Present when and only when the action is redact.",
        "correlation_id · audit_id · evaluation_latency_ms",
    ])
    caption(s, MARGIN, BODY_BOTTOM - 0.38, CONTENT_W,
            "A generated OpenAPI document committed as a golden file makes an "
            "unversioned contract change a build failure rather than a review omission. "
            "The runtime cannot serve a shape its published contract does not describe.")
    notes(
        s,
        "ADR-0005 §1 transport, §2 versioning classification, §3 generated OpenAPI, §5 "
        "bodies; field set from INTEGRATION-ARCHITECTURE.md §3.2 and §3.3; "
        "guardrail_category per ADR-0003, enforcement_state per ADR-0004.",
        "Adding a value to a response enum — a new action, a new stage — is classified "
        "major, not minor. That is a deliberately expensive classification and it is "
        "the one most likely to be argued with in this room.",
        "Walk the two payloads field by field and stop on policy_ref being advisory: "
        "that single word is what stops a client negotiating itself weaker enforcement.",
        "'Why is a new enum value a major?' — Because a client that receives an action "
        "it does not know cannot apply it, and the contract must stay compatible across "
        "three deployment modes indefinitely (ADR-0005 §7, TECHNICAL-RISKS R8).",
    )


def status_codes(prs, ctx):
    s = slide_base(
        prs,
        title="A block is a success",
        kicker="The runtime never returns a server error to express an evaluation "
               "outcome.",
        footer=ctx.src("ADR-0005 §6 · AGR-012 · RFC 9457 problem responses"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.45, [
        ["Status", "Meaning", "Body"],
        [("200", ALLOW, True), "Evaluation completed — whatever its action, including "
         "block, and including a substitute decision produced by the configured "
         "failure handler.", "Decision"],
        ["400 · 422", "Malformed body; or well-formed but invalid — an unknown stage, "
         "an unknown content kind.", "Problem"],
        ["401 · 403", "Authentication or authorization failure.", "Problem"],
        ["413", "Content exceeds the deployment's size ceiling.", "Problem"],
        ["429", "Quota or rate limit. Phase 2.", "Problem"],
        [("5xx", BLOCK, True), "The runtime itself is unavailable — no evaluation "
         "happened at all, and the client's configured failure behaviour applies.",
         "Problem"],
    ], col_w=[0.11, 0.72, 0.17], size=11, first_col_bold=True,
        row_h=[0.34, 0.72, 0.52, 0.38, 0.38, 0.34, 0.62])
    caption(s, MARGIN, BODY_BOTTOM - 0.50, CONTENT_W,
            "A detector outage, an evaluation timeout, or an unresolvable policy all "
            "produce a completed response carrying a substitute decision — never a "
            "server error. Encoding enforcement as an error would make every client's "
            "error path also its enforcement path. Problem responses carry a reason "
            "code from the same taxonomy as decisions, and a correlation identifier.")
    notes(
        s,
        "ADR-0005 §6 status-code table and the 5xx rule; failure-path substitution per "
        "AGR-012; error envelope is RFC 9457 problem+json extended with reason_code.",
        "This is the rule most likely to be violated by an implementer under time "
        "pressure — returning 403 for a block is locally convenient and permanently "
        "corrosive. Worth stating as a conformance test, not a convention.",
        "One sentence carries the slide: enforcement outcomes are successes. Then move "
        "to the policy document, which is where the failure behaviours come from.",
        "'How does a client distinguish a block from an outage?' — By status class. A "
        "completed response means the platform decided; a server error means it did "
        "not, and the client's own configured behaviour takes over.",
    )


def policy_document(prs, ctx):
    s = slide_base(
        prs,
        title="The policy document, and why it has no defaults",
        kicker="Validated before publication, published immutable. A policy carries no "
               "tenant, no workload, and no enforcement mode.",
        footer=ctx.src("ADR-0009 §1–§5 · AGR-012 · AGR-005 · AGR-017 · CON-004"),
    )
    bullets(s, MARGIN, BODY_Y + 0.10, 7.05, [
        "Identity, stages, evaluators, failure behavior, audit behavior, compatibility|"
        "Every section required. Validation happens before publication, so the runtime "
        "never resolves an invalid policy.",
        "Evaluators are selected by capability, never by vendor|A detection family and "
        "a capability within it, plus threshold, stages and action. A vendor name "
        "anywhere in a policy document is a schema violation — detectable by "
        "validation rather than by review.",
        "One required key per failure mode, and no default for any of them|A policy "
        "version missing any failure-behavior key fails validation and is never "
        "published.",
    ], size=13, gap=0.70)

    panel(s, 8.35, BODY_Y + 0.05, 4.23, 3.55, fill=SURFACE_ALT, border=WARN)
    _, tf = textbox(s, 8.62, BODY_Y + 0.30, 3.70, 3.05)
    para(tf, "The accepted cost", size=13, color=WARN, bold=True, first=True,
         space_after=6)
    para(tf, "“No undocumented fallback” and “sensible defaults” are mutually "
             "exclusive. Any default — however conservative — would mean an author who "
             "omits the section still gets behaviour, and that behaviour would be "
             "undocumented from the author's point of view.", size=12, color=TEXT,
         space_after=10)
    para(tf, "So every policy author writes every failure key, including for modes they "
             "consider unlikely. That verbosity is the documentation the agreement "
             "requires. It is a real cost and it is accepted deliberately.", size=12,
         color=MUTED, space_after=0)

    caption(s, MARGIN, BODY_BOTTOM - 0.86, 7.05,
            "No tenant, no workload, no enforcement mode on the policy — those live on "
            "the binding. That is what lets one policy version serve many workloads at "
            "different points on the ladder.")
    notes(
        s,
        "ADR-0009 §1 schema and pre-publication validation, §2 required contents, §3 "
        "capability-not-vendor selection, §4 no-default failure keys; AGR-012 "
        "enumerates the failure modes; separation of binding attributes per AGR-017.",
        "The same no-default rule applies at the client: a client that has never "
        "retrieved its configuration and was given none refuses to evaluate rather "
        "than allowing (ADR-0012 §2). Reviewers usually accept the server rule and "
        "flinch at the client one.",
        "The right-hand panel is the argument, not a footnote. Read it, invite the "
        "objection, and let the board weigh the verbosity against the guarantee.",
        "'Could we default just the unlikely modes?' — Then those are the ones nobody "
        "writes down, which is precisely the class of behaviour the agreement exists to "
        "eliminate. The alternatives are recorded in the decision record.",
    )


def enforcement_semantics(prs, ctx):
    s = slide_base(
        prs,
        title="Enforcement state is a ceiling, not a selector",
        kicker="The state lives on the binding. The decision engine computes the action, "
               "then clamps it to what the state permits.",
        footer=ctx.src("ADR-0004 §1–§5 · AGR-011 · DATA-ARCHITECTURE.md §1.5"),
    )
    dg.enforcement_ladder(s, f.ENFORCEMENT_STATES, highlight=1, y=BODY_Y + 0.05)
    table(s, MARGIN, BODY_Y + 2.55, CONTENT_W, 1.55, [
        ["Binding state", "Evaluators run", "Ceiling", "Policy calls for block →",
         "Policy calls for redact →"],
        ["disabled", ("No", BLOCK), "allow", "allow", "allow"],
        ["observe · warn", ("Yes", ALLOW), "log_only", "log_only", "log_only"],
        ["redact", ("Yes", ALLOW), "redact", "redact", "redact"],
        ["block", ("Yes", ALLOW), "block", "block", ("redact", ACCENT)],
    ], col_w=[0.22, 0.16, 0.14, 0.24, 0.24], size=10.5, header_size=10.5,
        first_col_bold=True, row_h=[0.34, 0.30, 0.30, 0.30, 0.30])
    caption(s, MARGIN, BODY_BOTTOM - 0.30, CONTENT_W,
            "Clamping is monotonic and never escalates: a ceiling is an upper bound, "
            "never a floor. It changes the action only — findings and reason codes are "
            "reported unchanged, so observe-mode measurement is real measurement.")
    notes(
        s,
        "ADR-0004 §1 state on the binding, §2 clamping table, §3 warn distinguished by "
        "a decision field rather than a fifth action, §4 disabled still audits, §5 "
        "clamping never touches findings; AGR-011; binding attributes per "
        "DATA-ARCHITECTURE.md §1.5.",
        "Disabled still produces a decision and an audit record, with evaluators "
        "skipped entirely. That is what makes a disabled workload provably disabled "
        "rather than merely silent — the audit trail distinguishes 'evaluation ran and "
        "was disabled by binding' from 'no evaluation occurred'.",
        "The clamping table is the slide. Land the block-calls-for-redact row, because "
        "that is the one people misread as escalation.",
        "'Why is warn not an action?' — Because the caller proceeds either way. Warn "
        "differs from observe in whether the client surfaces the reason codes, and how "
        "it surfaces them is deliberately client-specific.",
    )


def reason_codes(prs, ctx):
    s = slide_base(
        prs,
        title="Reason codes are namespaced by category, at the adapter",
        kicker="Whatever the engine emits, the adapter normalises it into one form "
               "before it reaches a decision.",
        footer=ctx.src("ADR-0003 · DEC-013 · AGR-004 · AGR-021 · DATA-ARCHITECTURE.md §1.7"),
    )
    box(s, MARGIN + 1.55, BODY_Y + 0.20, 6.6, 0.82,
        "GR-0NN : <evaluator-family> : <code>", bold=True, size=20, fill=ACCENT_DIM,
        border=ACCENT)
    for i, (x, w, label) in enumerate([
        (MARGIN + 1.55, 2.2, "catalog category\nmandatory — no code may be emitted "
                             "without one"),
        (MARGIN + 3.75, 2.2, "which evaluator produced the finding\nnot which product "
                             "the deployment happens to use"),
        (MARGIN + 5.95, 2.2, "that evaluator's own native code\nor a short stable slug"),
    ]):
        _, tf = textbox(s, x, BODY_Y + 1.12, w, 0.9, align=PP_ALIGN.CENTER)
        head, _, sub = label.partition("\n")
        para(tf, head, size=11, color=ACCENT, bold=True, first=True, space_after=3,
             align=PP_ALIGN.CENTER)
        para(tf, sub, size=10, color=MUTED, space_after=0, align=PP_ALIGN.CENTER,
             line=1.15)

    panel(s, MARGIN, BODY_Y + 2.30, CONTENT_W, 1.75)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.52, CONTENT_W - 0.7, 1.4)
    para(tf, "The category is also mirrored onto its own decision field.", size=15,
         color=ACCENT, bold=True, first=True, space_after=7)
    para(tf, "Per-category metrics and catalog-to-decision traceability become a group "
             "or filter on a first-class field — no side table, no forked decision "
             "structure, no per-mode divergence of the contract. This is a "
             "normalisation rule inside the existing adapter seam plus an additive "
             "field on an existing entity: no new domain entity, no new stage, no new "
             "action.", size=12.5, color=MUTED, space_after=0, line=1.25)
    caption(s, MARGIN, BODY_BOTTOM - 0.24, CONTENT_W,
            "Example: a personal-data finding from the first managed engine and the "
            "same finding from a future evaluator carry the same category segment and "
            "are queryable together.")
    notes(
        s,
        "ADR-0003 decision §1 canonical format, §2 guardrail_category as a first-class "
        "decision field, §3 reporting consequence; recorded as DEC-013; adapter seam "
        "per AGR-004; catalog traceability per AGR-021.",
        "The eighteen catalog categories are change-controlled; the individual "
        "guardrails inside a category are illustrative and are not. Do not let the "
        "catalog be read as a commitment — it is classification, and several entries "
        "are explicitly vision-uncommitted or out-of-boundary.",
        "This is a small decision with a large reporting consequence. Spend a minute, "
        "not five, unless the board wants to argue the namespacing.",
        "'What if an engine's native codes collide across families?' — They cannot "
        "collide across the full form, because the family segment disambiguates them "
        "and the adapter owns the mapping.",
    )


def policy_distribution(prs, ctx):
    s = slide_base(
        prs,
        title="Policy storage and distribution",
        kicker="Split by how state is read. Runtimes pull; the control plane never "
               "pushes. Evaluation reads local state only.",
        footer=ctx.src("ADR-0010 §1–§4 · ADR-0011 §1–§5 · AGR-006 · AGR-015 · NFR-REC-001"),
    )
    box(s, MARGIN, BODY_Y + 0.10, 5.95, 0.90,
        "Bindings — low-latency key-value store\nRead once per evaluation, by workload "
        "identity. Append-only: rollback writes a new record and supersedes the prior "
        "one.", size=12.5, bold=True)
    box(s, 7.13, BODY_Y + 0.10, 5.45, 0.90,
        "Policy documents — versioned object store\nImmutability is native. The stored "
        "artifact and the distributed artifact are the same object.", size=12.5,
        bold=True)
    arrow(s, 6.62, BODY_Y + 1.05, 6.62, BODY_Y + 1.55, color=HAIRLINE)
    bullets(s, MARGIN, BODY_Y + 1.70, CONTENT_W, [
        "Runtimes pull on a recurring interval; the control plane never initiates a "
        "connection|Chosen for the customer-managed case: a push mechanism would need "
        "inbound connectivity into a customer environment. One mechanism serves all "
        "three modes, which is what keeps the customer-managed phase a packaging "
        "exercise rather than a redesign.",
        "Resolution reads the local cache only — no evaluation makes a synchronous "
        "control-plane call, in any mode|Otherwise the control plane becomes a hot-path "
        "dependency whose availability bounds the evaluation path's, and the "
        "customer-managed path becomes structurally different from the hosted one.",
        "A refresh failure is not an evaluation failure|The runtime keeps evaluating "
        "against its last successfully retrieved state. A cached immutable version is "
        "never wrong for its version — only potentially superseded. Staleness is "
        "measured, bounded, reported, and routed through the configured "
        "control-plane-unavailable behaviour once tolerance is exceeded.",
    ], size=12.5, gap=0.62)
    caption(s, MARGIN, BODY_BOTTOM - 0.28, CONTENT_W,
            "Immediate rollback means immediate at the control plane; runtimes converge "
            "within their refresh interval. That interval is a safety parameter, not a "
            "tuning knob.")
    notes(
        s,
        "ADR-0010 §1 store split, §2 append-only bindings, §3 the stored artifact is "
        "the distribution artifact, §4 regionality follows the tenant; ADR-0011 §1 "
        "pull, §2 local-only resolution, §3 refresh failure, §4 staleness visibility, "
        "§5 the rollback-urgency statement; AGR-006, AGR-015; NFR-REC-001.",
        "The rollback wording in the non-functional register says 'immediate'. ADR-0011 "
        "§5 bounds it explicitly and forbids product or sales material from implying "
        "otherwise. If this board wants a tighter bound, the refresh interval is the "
        "knob and it has a cost.",
        "Two stores, one distribution rule, one honest caveat. The caveat is the part "
        "worth their attention — it is the difference between a claim and a "
        "measurable property.",
        "'What is the maximum tolerable staleness?' — Configured per deployment with an "
        "explicit value; the default figure is not set and must come from Phase 4 "
        "discovery with candidate adopters (ASM-013).",
    )


def connector_compatibility(prs, ctx):
    s = slide_base(
        prs,
        title="Compatibility is two-phase",
        kicker="Bind time checks intent. Request time checks reality. A self-reported "
               "identity is diagnostic, never a security control.",
        footer=ctx.src("ADR-0007 §1–§5 · ADR-0013 §3 · ADR-0008 · FR-016 · AGR-018 · CON-012"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 2.15, [
        ["Phase", "When", "Validates", "On failure"],
        ["Declared", "Binding creation or policy promotion",
         "The policy version's requirements against the connector version range and "
         "the runtime capabilities recorded for that workload.",
         ("Refuse the binding. No manifest is generated.", BLOCK)],
        ["Observed", "Every evaluation",
         "The connector identity actually presented against what the binding expects.",
         ("Contract-major mismatch refuses the evaluation; a version drift inside the "
          "same major proceeds and is reported.", WARN)],
    ], col_w=[0.11, 0.19, 0.42, 0.28], size=11, first_col_bold=True,
        row_h=[0.34, 0.90, 0.85])
    bullets(s, MARGIN, BODY_Y + 2.40, CONTENT_W, [
        "A workload is not onboarded until its installation is verified|It sits in a "
        "distinct pending-verification state until the platform has observed at least "
        "one evaluation at every stage the bound policy declares. A policy declaring "
        "input and output whose workload has only ever produced input evaluations is "
        "an incomplete installation, and is visible as one. Verification is a "
        "precondition for promotion out of observe.",
        "Connectors are published artifacts on a named, externally reachable channel, "
        "with verifiable integrity and documented privilege|The published "
        "documentation states plainly what the connector does inside the consuming "
        "team's process, because that team's security review is a legitimate "
        "onboarding step that needs a factual description to review.",
    ], size=12.5, gap=0.64)
    caption(s, MARGIN, BODY_BOTTOM - 0.28, CONTENT_W,
            "Connector identity is self-reported and must never be cited as evidence "
            "that a workload's guardrails cannot be bypassed — a client that would lie "
            "about its version would equally decline to call at all.")
    notes(
        s,
        "ADR-0007 §1 two-phase table, §2 identity on the request as an optional field, "
        "§3 graded mismatch, §4 pending-verification state, §5 staleness; the runtime "
        "half of the check is ADR-0013 §3; distribution and integrity per ADR-0008; "
        "FR-016 is satisfied by neither phase alone; CON-012 and AGR-018 forbid citing "
        "identity as a control.",
        "Identity is an optional request field in the current major deliberately — "
        "making it required would break every existing client on the day it lands, for "
        "a field whose purpose is diagnostic. A client that omits it is recorded as "
        "unknown, not as compatible.",
        "Two tables, one rule: check intent early, check reality continuously, and "
        "never confuse a cooperative signal with a control.",
        "'Why not block on a version mismatch?' — Because blocking converts metadata "
        "drift into a production outage, which is a worse failure than the drift. A "
        "contract-major mismatch does refuse, because serving a shape the runtime does "
        "not understand is how silently wrong decisions are produced.",
    )


def runtime_capability(prs, ctx):
    s = slide_base(
        prs,
        title="Runtimes advertise capabilities, never implementations",
        kicker="Derived from actual configuration. An unsatisfiable runtime refuses to "
               "start; an unsatisfiable binding is refused.",
        footer=ctx.src("ADR-0013 §1–§5 · ADR-0009 §3 · AGR-004 · CON-004 · AGR-012"),
    )
    dg.numbered_panels(s, [
        ("01", "A manifest states capabilities, not engines",
         "A runtime declares that it can evaluate a given detection family and "
         "capability; it does not disclose which engine does so. That keeps the vendor "
         "boundary intact at the one interface where leaking it would be most tempting "
         "— and it means a deployment can change implementations without changing what "
         "it advertises, which is exactly the portability property being protected. "
         "The manifest is derived from the runtime's real configuration, never "
         "hand-maintained."),
        ("02", "Unsatisfiable configuration fails at startup, not in production",
         "A runtime configured to serve a capability by an implementation that is "
         "unavailable, misconfigured or unknown fails to start rather than booting "
         "with a silently reduced manifest. A container that boots while missing a "
         "detector is the same class of failure as undocumented fallback behaviour: "
         "something is quietly not happening, and nothing says so."),
        ("03", "Binding creation validates the policy against the target runtime",
         "An unsatisfiable binding is refused, naming the missing capabilities. That "
         "converts a production-time degradation into a bind-time error seen by the "
         "operator making the change, with the context to fix it. Capability removal "
         "from a running deployment reports the affected bindings rather than silently "
         "degrading them — and a shortfall that still reaches request time is one of "
         "the configured failure modes."),
    ])
    notes(
        s,
        "ADR-0013 §1 capabilities not implementations, §2 fail-to-start, §3 bind-time "
        "validation as the runtime half of ADR-0007's two-phase check, §4 re-check on "
        "capability change, §5 request-time shortfall as a configured failure mode; "
        "vocabulary shared with ADR-0009 §3; vendor boundary per AGR-004 and CON-004.",
        "A drifted hand-maintained capability list would be worse than no check at all, "
        "because it reports confidence it does not have. That is the reasoning behind "
        "deriving the manifest rather than declaring it.",
        "Three claims, one theme: make the failure visible at the cheapest moment. "
        "Startup, then bind time, then — only as a last resort — request time under a "
        "configured behaviour.",
        "'What happens to existing bindings when a capability is removed?' — A running "
        "deployment cannot un-create a binding. It reports the affected workloads as "
        "degraded so an operator can act; the check is repeatable against current "
        "state, not a one-time gate.",
    )


def three_modes(prs, ctx):
    s = slide_base(
        prs,
        title="Three deployment modes, one contract",
        kicker="The connector contract does not change across modes — only the network "
               "destination of the Evaluation API does.",
        footer=ctx.src("AGR-002 · AGR-006 · INTEGRATION-ARCHITECTURE.md §4.7 · ADR-0015 §3"),
    )
    dg.mode_columns(s, f.MODES, highlight=0)
    caption(s, MARGIN, BODY_BOTTOM - 0.34, CONTENT_W,
            "One packaging path serves the hosted deployment, the customer-managed "
            "runtime, and the later guarded gateway. A second packaging path for the "
            "same code would drift — which is how “packaged for customer-managed "
            "deployment without changing the connector contract” quietly becomes false.")
    notes(
        s,
        "AGR-002 three modes; one logical contract per AGR-006 and CON-005; the "
        "invariant connector contract is INTEGRATION-ARCHITECTURE.md §4.7; single "
        "packaging path per ADR-0015 §3; the packaging criterion is MVP-09.",
        "Modes are not a maturity ladder and the modes are not equally mature — the "
        "customer-managed runtime lands in Phase 4. A reviewer with a residency "
        "requirement in mind should hear that timing explicitly.",
        "Frame the modes as a deployment axis and move quickly to the topologies, which "
        "is where this audience actually wants to be.",
        "'Does the decision structure differ per mode?' — No. A caching optimisation in "
        "the hosted modes must not alter the externally observable decision or audit "
        "contract; that is a constraint, not a design preference.",
    )


def topology_a(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode A — Remote Evaluation",
        "Team-owned gateway, hosted runtime. Two evaluation calls per request; the "
        "model call never transits the platform.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §2 · AGR-002 · AGR-009"),
        dg.mode_a_topology,
        "Prompts, responses, and any additional evaluated content are transmitted to "
        "the hosted Evaluation API on every evaluated call. This is not a private-data "
        "or data-local model.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §2.1 topology, §2.2 ownership, §2.3 exposure; "
        "request sequence per INTEGRATION-ARCHITECTURE.md §4.1; framing mandated by "
        "AGR-009.",
        "Decision metadata, evaluation latency and audit records flow to the hosted "
        "control plane as normal operation — not optional telemetry in this mode.",
        "Trace the pre-call and post-call hops and stop on the fact that model "
        "invocation stays in the team's environment. That is the property the whole "
        "cooperative posture depends on.",
        "'What is the latency cost of two synchronous hops?' — Unmeasured. It is "
        "instrumented from Phase 1 and no target percentile value is set (ASM-011).",
    )


def topology_b(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode B — Guarded Gateway",
        "One provider-operated endpoint carrying guardrails, routing, invocation and "
        "audit. Phase 3.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §3 · AGR-002 · CONTAINER-ARCHITECTURE.md"),
        dg.mode_b_topology,
        "Prompts and responses are processed entirely within the provider-managed "
        "gateway. The provider participates in the complete inference path and holds "
        "or brokers model-provider credentials. Raw content is not persisted by "
        "default; decision metadata is.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §3.1 topology, §3.2 ownership, §3.3 exposure; the "
        "gateway itself is specified on the same container platform per ADR-0015.",
        "This mode puts the provider in the full inference path and creates the largest "
        "operational and security scope for us. It sits behind two validation gates for "
        "exactly that reason.",
        "Keep this brief unless the board wants to review Phase 3 scope — it is the "
        "mode furthest from the work in front of them.",
        "'Does the evaluation contract change when the runtime is in-process?' — No. "
        "The connector talks to the same contract; only the destination changes.",
    )


def topology_c(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode C — Customer-Managed Runtime",
        "The same container image, run inside the customer's account, evaluating "
        "against a local cache with no synchronous control-plane dependency. Phase 4.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §4, §9 · AGR-015 · AGR-016 · ADR-0011 · "
                "ADR-0015 §3"),
        dg.mode_c_topology,
        "Prompts and responses are evaluated entirely inside the customer's own AWS "
        "account. Raw content must never leave that runtime unless explicitly "
        "configured. Telemetry to the control plane is customer-selected, down to none "
        "at all — a fully supported configuration, not a degraded one.",
        ALLOW,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §4.1 topology, §4.4 no synchronous control-plane "
        "dependency, §4.5 exposure, §9 the container artifact; AGR-015, AGR-016; pull "
        "and local-only resolution per ADR-0011; shared packaging per ADR-0015 §3.",
        "The customer takes on runtime infrastructure, availability, upgrades and local "
        "audit storage. Policy artifacts reach this runtime over TLS, not as signed "
        "artifacts — policy signing is deferred and tracked as an open question.",
        "This is the slide that justifies several earlier decisions — pull over push, "
        "local-only resolution, containers over functions. Point back at them rather "
        "than re-arguing each.",
        "'What protects the policy artifact in transit to a customer runtime?' — "
        "Transport security today. Cryptographic policy signing is permitted future "
        "scope and is not built (OQ-012).",
    )


def extensibility(prs, ctx):
    s = slide_base(
        prs,
        title="Four seams, and everything grows through one of them",
        kicker="Adding coverage is a change at a named seam with a change process and a "
               "governance gate — never an ad-hoc extension.",
        footer=ctx.src("ADR-0002 · EXTENSIBILITY-ARCHITECTURE.md · GUARDRAIL-CATALOG.md · "
                       "AGR-021 · SCOPE.md §8"),
    )
    seams = [
        ("New evaluation\nstage", "Widens the contract. Reserved vocabulary exists; "
                                  "adding a value is a major version."),
        ("New evaluation\nengine", "Behind an adapter. Normalises into the existing "
                                   "decision structure and reason-code form."),
        ("New decision\naction", "Widens the action set every client must handle. The "
                                 "most expensive seam, deliberately."),
        ("New\nconnector", "Implements the invocation contract only. Never touches "
                           "policy semantics."),
    ]
    cw, gap_x = 2.81, 0.19
    for i, (name, desc) in enumerate(seams):
        x = MARGIN + i * (cw + gap_x)
        panel(s, x, BODY_Y + 0.10, cw, 2.05)
        box(s, x, BODY_Y + 0.10, cw, 0.86, name, bold=True, size=13,
            fill=ACCENT_DIM if i == 3 else SURFACE,
            border=ACCENT if i == 3 else HAIRLINE)
        _, tf = textbox(s, x + 0.20, BODY_Y + 1.08, cw - 0.40, 1.0)
        para(tf, desc, size=11, color=MUTED, first=True, space_after=0, line=1.2)

    panel(s, MARGIN, BODY_Y + 2.40, CONTENT_W, 1.55, fill=SURFACE_ALT)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.60, CONTENT_W - 0.7, 1.2)
    para(tf, "The category catalog is classification, not commitment.", size=14.5,
         color=ACCENT, bold=True, first=True, space_after=6)
    para(tf, "Eighteen change-controlled categories, each carrying an enforcement layer, "
             "the stages it uses, its evaluator family, and a phase status from a fixed "
             "vocabulary — core, later-phase committed, vision-uncommitted, or "
             "out-of-boundary. Individual guardrails inside a category are illustrative "
             "and not change-controlled. Promoting a vision entry to committed scope is "
             "a recorded scope-change decision, never a silent graduation, and "
             "out-of-boundary entries carry a named external owner and are never "
             "presented as future deliverables.", size=11.5, color=MUTED, space_after=0,
         line=1.25)
    notes(
        s,
        "ADR-0002 decision §1 catalog, §2 the four extension seams formalised in "
        "EXTENSIBILITY-ARCHITECTURE.md, §3 governance rules; catalog is "
        "GUARDRAIL-CATALOG.md (GR-001–GR-018) under AGR-021; graduation is the "
        "scope-change process in SCOPE.md §8; the first graduation is recorded as "
        "DEC-020.",
        "The catalog does not alter the defined-and-estimated feature set. If a "
        "reviewer reads eighteen categories as eighteen commitments, correct it "
        "immediately — several are explicitly uncommitted or out of boundary.",
        "Name the seams, then land the governance point: growth is bounded by a "
        "process, which is the actual answer to 'what stops this becoming a DLP "
        "platform'.",
        "'Which seam is most expensive?' — A new decision action. Every client in every "
        "mode must handle it, and the contract must stay compatible indefinitely, so it "
        "is classified as a major contract change.",
    )


def nfr_posture(prs, ctx):
    s = slide_base(
        prs,
        title="What is specified, and what is deliberately not",
        kicker="Mechanisms are specified. Numbers are not — and inventing them here "
               "would be the wrong kind of confidence.",
        footer=ctx.src("NON-FUNCTIONAL-REQUIREMENTS.md · OPERABILITY.md §2 · ASM-011, "
                       "ASM-012 · CON-016"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.55, [
        ["Area", "Specified", "Not set"],
        ["Latency",
         "Measured per stage, per mode, per detector. Timeout is mandatory at stage and "
         "detector level, and every detector budget is strictly inside its stage budget.",
         ("Every percentile target, and every timeout value.", WARN)],
        ["Availability",
         "A documented, monitored objective is required before general availability. "
         "Degraded-mode continuity is mandatory, and no policy may leave observe "
         "without declaring its degraded behaviour.",
         ("The objective itself, and the measurement window.", WARN)],
        ["Resilience",
         "Bounded retries inside the remaining stage budget, idempotent calls only, and "
         "a per-dependency circuit breaker whose state is an observable signal.",
         ("Failure thresholds and cool-down durations.", WARN)],
        ["Cache",
         "Hit rate is a required metric. Staleness is measured, bounded, and routed "
         "through the configured behaviour once tolerance is exceeded.",
         ("Tolerable staleness — owed by discovery with candidate adopters.", WARN)],
    ], col_w=[0.13, 0.58, 0.29], size=10.5, header_size=11, first_col_bold=True,
        row_h=[0.36, 0.80, 0.80, 0.72, 0.72])
    caption(s, MARGIN, BODY_BOTTOM - 0.44, CONTENT_W,
            "This is the honest position, not a gap we overlooked: no representative "
            "load data exists yet, and a target set without it would be a number we "
            "could not defend. Instrumentation lands in Phase 1 precisely so the "
            "targets can be set in Phase 2 from real traffic.")
    notes(
        s,
        "NON-FUNCTIONAL-REQUIREMENTS.md availability, reliability, performance and "
        "operability sections — NFR-AVAIL-001/002, NFR-PERF-001 through 007, "
        "NFR-OPS-001 through 005; timeout and retry mechanics in OPERABILITY.md §2.2–"
        "§2.6; the gaps are ASM-011 and ASM-012; mandatory timeouts per CON-016.",
        "If the board wants a latency number today, the correct answer is that we will "
        "not supply one. Setting a target before Phase 1 instrumentation would make it "
        "an aspiration presented as a requirement.",
        "This slide usually earns credibility rather than losing it — but only if it is "
        "delivered as a plan with a date-bearing owner, not as an apology.",
        "'When do we get the numbers?' — Instrumented from Phase 1, targets set in "
        "Phase 2 against real traffic, published before any mode reaches general "
        "availability.",
    )


def risks_and_tradeoffs(prs, ctx):
    s = slide_base(
        prs,
        title="Trade-offs already accepted",
        kicker="Each was decided against a named alternative, with the cost recorded "
               "rather than argued away.",
        footer=ctx.src("TRADE-OFFS.md §3–§10 · TECHNICAL-RISKS.md · ADR-0009 · ADR-0011 · "
                       "ADR-0015"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 3.75, [
        ["Decision", "Rejected alternative", "Cost we accepted"],
        ["No default failure behaviour anywhere",
         "Conservative built-in defaults",
         "Every policy author writes every failure key, including for modes they "
         "consider unlikely."],
        ["Pull-based distribution, local-only resolution",
         "Control-plane resolution per evaluation",
         "Rollback is immediate at the control plane and converges within the refresh "
         "interval — not instantaneously everywhere."],
        ["Containers on a managed service, not functions",
         "Serverless request/response target",
         "Higher idle cost for a Phase 1–2 workload, in exchange for one packaging path "
         "across all three modes."],
        ["A managed engine first, behind an adapter",
         "Custom evaluators from the start",
         "Detection quality and latency are bounded by someone else's service until "
         "further evaluators exist."],
        ["Cooperative connector as the starting posture",
         "Network- or identity-enforced gateway from day one",
         "Bypass is possible by the team that installed the connector. Accepted for "
         "Phases 1–2, not eliminated."],
    ], col_w=[0.28, 0.26, 0.46], size=10.5, header_size=11, first_col_bold=True,
        row_h=[0.36, 0.62, 0.72, 0.72, 0.62, 0.70])
    caption(s, MARGIN, BODY_BOTTOM - 0.30, CONTENT_W,
            "Adapters are the cross-cutting mechanism that stops most of these being "
            "re-litigated: vendor behaviour, engine choice and deployment configuration "
            "sit behind a seam, so changing one does not reopen the contract.")
    notes(
        s,
        "TRADE-OFFS.md §3 hosted vs customer-managed, §4 remote vs local checks, §5 "
        "cooperative vs enforced posture, §6 the two policy stores, §7 managed engine "
        "vs custom evaluators, §8 audit delivery, §9 adapters as the cross-cutting "
        "mechanism, §10 coverage breadth vs contract surface; the accepted costs are "
        "recorded in ADR-0009 §4, ADR-0011 §5 and ADR-0015 §2–§3.",
        "Every row is reversible at a stated price except the last, which is a posture "
        "choice rather than an architectural one and moves whenever the trust "
        "requirement moves.",
        "Invite disagreement row by row. A board that argues one of these into a "
        "different position is doing exactly what this session is for.",
        "'Which of these would you change first?' — The starting posture, if this board "
        "judges the residual bypass risk unacceptable for the pilot. It is the cheapest "
        "to change and the most consequential.",
    )


def open_questions(prs, ctx):
    s = slide_base(
        prs,
        title="What we need you to rule on",
        kicker="Open questions with no recorded decision. Each needs an answer or a "
               "named owner and a date.",
        footer=ctx.src("OPEN-QUESTIONS.md OQ-001, OQ-014, OQ-018, OQ-022, OQ-012 · "
                       "ASM-011, ASM-012"),
    )
    bullets(s, MARGIN, BODY_Y + 0.10, CONTENT_W, [
        "Which enforcement posture do the first external customers require?|Today's "
        "answer is that Phases 1–2 start cooperative. If the answer for external "
        "customers is platform-managed or network-enforced, that work moves earlier "
        "and the platform takes on gateway infrastructure sooner.",
        "What are the go/no-go criteria for promoting a workload out of observe?|The "
        "promotion is an explicit, audited action today. What evidence justifies it — "
        "false-positive rate, traffic volume, review sign-off — is not defined.",
        "Does the evaluation-stage vocabulary expand beyond the reserved set?|The "
        "content union is tagged precisely so new stages are additive. Whether the "
        "reserved seven are enough is a contract question, and widening the response "
        "enum is a major version.",
        "What is the connector version support window, and the deprecation policy?|"
        "Compatibility checks are meaningless without one — “supported” has no "
        "definition today.",
        "Do we need signed policy artifacts before the customer-managed runtime "
        "ships?|Transport security is the current safeguard for policy reaching a "
        "runtime we do not operate. Signing is permitted future scope and is not built.",
    ], size=12.5, gap=0.56)
    notes(
        s,
        "OPEN-QUESTIONS.md OQ-001 (enforcement posture for first external customers), "
        "OQ-014 (observe-to-enforce go/no-go), OQ-018 (stage vocabulary expansion), "
        "OQ-022 (connector support window), OQ-012 (policy signing and inheritance); "
        "the numeric gaps are ASM-011 and ASM-012.",
        "These are register entries with no owner and no date on several of them. That "
        "is the actual ask of this slide — a ruling is ideal, an owner and a date is "
        "acceptable, silence is not.",
        "Slow down here. This is the only slide where the board's output is required "
        "rather than optional. Work down the list and record what you get.",
        "'Can we defer all of them?' — OQ-001 and OQ-022 gate Phase 1 design work; the "
        "rest can wait. Deferring the first two means designing against an unknown "
        "posture and an undefined support commitment.",
    )


def the_ask(prs, ctx):
    s = slide_base(
        prs,
        title="What we are asking for",
        footer=ctx.src("DECK-VARIANTS.md §3 · DOCUMENT-GOVERNANCE.md §6 · OPEN-QUESTIONS.md"),
    )
    panel(s, MARGIN, BODY_Y - 0.05, CONTENT_W, 1.55, fill=SURFACE, border=ACCENT)
    _, tf = textbox(s, MARGIN + 0.40, BODY_Y + 0.20, CONTENT_W - 0.8, 1.05)
    para(tf, "Sign off the contract set: wire format and versioning, policy schema and "
             "its mandatory failure behavior, enforcement-state semantics, reason-code "
             "namespacing, and the two-phase compatibility model.", size=18, bold=True,
         first=True, space_after=6, line=1.2)
    para(tf, "Not a funding decision, and not a review of the delivery plan.", size=12.5,
         color=MUTED, space_after=0)

    _, tf = textbox(s, MARGIN, BODY_Y + 1.62, 6.05, 0.4)
    para(tf, "And, before this session ends", size=13, color=ACCENT, bold=True,
         first=True, space_after=0)
    bullets(s, MARGIN, BODY_Y + 2.05, 6.05, [
        "A ruling, or a named owner and a date, on each open question",
        "Named reviewers for the generated contract document and the policy schema "
        "before Phase 1 implementation starts",
        "Agreement that a contract change without a version bump is a build failure, "
        "not a review comment",
    ], size=12.5, gap=0.62, dot=ALLOW)

    _, tf = textbox(s, 7.03, BODY_Y + 1.62, 5.55, 0.4)
    para(tf, "What we are not asking for", size=13, color=MUTED, bold=True,
         first=True, space_after=0)
    bullets(s, 7.03, BODY_Y + 2.05, 5.55, [
        "Latency, availability or throughput targets — these are owed after Phase 1 "
        "instrumentation, not before",
        "A decision on any capability outside the committed feature set",
        "Approval to start building — that decision belongs to the investment review",
    ], size=12.5, gap=0.62, dot=LOG_ONLY)

    caption(s, MARGIN, BODY_BOTTOM - 0.16, CONTENT_W,
            "If sign-off is not reachable today, the useful outcome is a written list of "
            "the contract elements this board disputes — that is a shorter, more "
            "specific document than a general objection, and it is what a revision "
            "would be built from.")
    notes(
        s,
        "DECK-VARIANTS.md §3 (the decision this deck asks for); authority ladder per "
        "DOCUMENT-GOVERNANCE.md §6; the open-question list is OPEN-QUESTIONS.md.",
        "Sign-off here does not commit anyone to delivery — that decision sits with the "
        "investment review and has not been made either. Do not let the two be "
        "conflated in the minutes.",
        "Restate the three contract elements, take the rulings, and stop. If the board "
        "wants to keep going on a specific record, that is a follow-up session with a "
        "narrower agenda, not an extension of this one.",
        "'What if we sign off and then an agreement changes?' — The agreement governs "
        "and the design record is corrected. That is the ladder, and it is why the "
        "records are separate documents rather than one specification.",
    )


def appendix_divider(prs, ctx):
    s = slide_base(prs)
    _, tf = textbox(s, MARGIN, 3.05, CONTENT_W, 1.6)
    para(tf, "Appendix", size=44, bold=True, font=HEAD_FONT, first=True, space_after=10)
    para(tf, "Reference material for the records this session touches. Use only if "
             "raised.", size=15, color=MUTED, space_after=0)
    rule = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(2.85),
                              Inches(1.35), Inches(0.045))
    rule.fill.solid()
    rule.fill.fore_color.rgb = ACCENT
    rule.line.fill.background()
    rule.shadow.inherit = False
    caption(s, MARGIN, 5.35, CONTENT_W,
            "Core domain model  ·  Integration manifest and client bootstrap  ·  "
            "Audit coverage  ·  Trust and enforcement postures  ·  Onboarding sequence")
    notes(
        s,
        "DECK-VARIANTS.md §3 appendix list.",
        "The appendix is reference, not reserve narrative. Reaching for it unprompted "
        "turns a bounded review into an unbounded one.",
        "Pause. If the contract sign-off landed and the open questions are recorded, "
        "stop here.",
        "n/a — divider slide.",
    )


def domain_model(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Core domain model",
        kicker="Six entities. Every contract field above resolves to one of them.",
        footer=ctx.src("AGR-017 · GLOSSARY.md · DATA-ARCHITECTURE.md §1 · REQUIREMENTS.md"),
    )
    dg.entity_grid(s, f.DOMAIN_ENTITIES)
    notes(
        s,
        "AGR-017 core domain model; per-entity attributes in DATA-ARCHITECTURE.md §1.2–"
        "§1.7 and GLOSSARY.md; made testable in REQUIREMENTS.md.",
        "The binding is where enforcement state, allowed overrides, effective date and "
        "rollback version live — not the policy. That separation is load-bearing for "
        "the clamping model and for the portability claim.",
        "Use this if the vocabulary slips during the contract discussion.",
        "'Why is evaluation a first-class entity rather than a log line?' — Because "
        "stage, content kind and correlation are contract-visible and must be "
        "queryable, not reconstructed from records.",
    )


def manifest_and_bootstrap(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Integration manifest and client bootstrap",
        kicker="The manifest seeds a client at install time. The retrieval path keeps "
               "it current. Neither carries policy.",
        footer=ctx.src("ADR-0006 §1–§4 · ADR-0012 §1–§5 · AGR-019 · AGR-012 · CON-016"),
    )
    _mono_panel(s, MARGIN, BODY_Y + 0.05, 5.95, 3.55, "INTEGRATION MANIFEST", [
        "A file, not a screen|Schema-validated YAML a team commits, reviews in a pull "
        "request, and diffs. Drift is visible as a diff rather than as a support "
        "conversation.",
        "Derived, never authoritative|Generated by the control plane from an existing "
        "binding. Regenerable, and not authoritative at evaluation time — a team "
        "cannot obtain weaker enforcement by editing it.",
        "Carries|Tenant and workload identity · policy name and immutable version · "
        "enforcement mode · deployment mode and endpoint · contract major · connector "
        "type and supported range · per-stage timeouts · client failure behavior · "
        "required capabilities · source binding and generation timestamp.",
    ])
    _mono_panel(s, 7.13, BODY_Y + 0.05, 5.45, 3.55, "CLIENT BOOTSTRAP", [
        "Served on its own path, not on the evaluation response|Evaluation is "
        "synchronous and latency-critical; configuration is retrieved once, cached and "
        "refreshed in the background.",
        "A client with no configuration refuses to evaluate|It does not proceed with a "
        "built-in default and it does not allow. An unset failure behavior is a "
        "configuration error, not a permissive default.",
        "Losing contact never un-configures a client|A client keeps applying what it "
        "last retrieved. Otherwise a control-plane outage would turn every working "
        "client into a refusing one — a worse failure than briefly stale timeouts.",
        "Timeouts and failure behavior only|No detection rules, no thresholds, no "
        "evaluator selection.",
    ])
    notes(
        s,
        "ADR-0006 §1 file not screen, §2 derived not authoritative, §3 required "
        "contents, §4 the prohibited-contents list enforcing AGR-019; ADR-0012 §1 "
        "separate retrieval path, §2 refuse without configuration, §3 refresh failure, "
        "§4 no policy in configuration, §5 manifest seeds the client; timeouts per "
        "CON-016.",
        "The explicit-configuration escape hatch exists so a client can start where the "
        "platform is not yet reachable — but it makes the values a deliberate, visible "
        "choice rather than a hidden one. It is not a default.",
        "Reach for this when someone asks what a consuming team actually receives, or "
        "challenges the refuse-without-configuration rule.",
        "'Is refusing to evaluate not just an outage?' — It is a loud configuration "
        "error at install time rather than a silent guardrail bypass in production. "
        "Defaulting to allow would disable guardrails for a workload whose owner "
        "believes they are on.",
    )


def audit_coverage(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Audit coverage",
        kicker="Four classes of event produce a record. Origin and outcome are explicit "
               "dimensions on it.",
        footer=ctx.src("ADR-0014 §1–§4 · AGR-014 · DATA-ARCHITECTURE.md §4 · NFR-AUDIT-001"),
    )
    table(s, MARGIN, BODY_Y + 0.05, CONTENT_W, 2.25, [
        ["Event", "Produced by", "Record"],
        ["Completed evaluation, any action", "Runtime",
         "Full record: policy version, action, reason codes, category, evaluator "
         "versions, correlation, latency."],
        ["Failure-path substitute decision", "Runtime",
         "Same, plus which failure mode fired and which configured path was taken."],
        ["Request rejected before evaluation", "Runtime",
         "Marked as a rejection: reason and correlation only. No findings, no action, "
         "and never the content that failed validation."],
        ["Client-synthesized decision", ("Client", WARN),
         "Marked client-origin. Recorded locally and delivered when the runtime is "
         "reachable again."],
    ], col_w=[0.28, 0.13, 0.59], size=10.5, header_size=11, first_col_bold=True,
        row_h=[0.34, 0.52, 0.42, 0.52, 0.44])
    panel(s, MARGIN, BODY_Y + 2.50, CONTENT_W, 1.55, fill=SURFACE_ALT, border=WARN)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.70, CONTENT_W - 0.7, 1.2)
    para(tf, "A client-origin record is never presented as equivalent to a runtime "
             "record.", size=14, color=WARN, bold=True, first=True, space_after=6)
    para(tf, "It is self-reported by a cooperative component: it says what a client "
             "reports having done, not what the platform observed. Operationally "
             "valuable, not evidentiary. The local store is bounded, discards oldest "
             "first on overflow, and reports how many it discarded — because discarding "
             "silently would understate an outage in exactly the direction that flatters "
             "the platform. It holds reason and correlation only, never content, and "
             "delivery is best-effort because records may be lost if the client process "
             "dies.", size=11.5, color=MUTED, space_after=0, line=1.25)
    notes(
        s,
        "ADR-0014 §1 four event classes, §2 rejections carry no policy and no content, "
        "§3 client-synthesized records, §4 client-origin is not evidentiary; retention "
        "default per AGR-014; audit fields per DATA-ARCHITECTURE.md §4 and "
        "NFR-AUDIT-001.",
        "The client-side store lives on infrastructure this platform does not operate, "
        "where the no-raw-retention default cannot be enforced or audited. That is why "
        "it holds reason and correlation only, and why best-effort delivery must be "
        "stated rather than implied in any compliance discussion.",
        "Use this when a reviewer asks what happens to the audit trail during an "
        "outage — the honest answer has three qualifications and all of them are here.",
        "'Can a client-origin record satisfy a compliance question?' — No. It is "
        "operational evidence of what a client reports, and it must never be offered "
        "as proof of what the platform enforced.",
    )


def trust_postures(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Trust and enforcement postures",
        kicker="Orthogonal to deployment mode. A posture describes how bypass-resistant "
               "a given workload's integration actually is.",
        footer=ctx.src("AGR-018 · SECURITY-ARCHITECTURE.md §3.1–§3.3 · ASM-005 · OQ-001"),
    )
    dg.posture_rows(s, f.POSTURES)
    caption(s, MARGIN, BODY_BOTTOM - 0.28, CONTENT_W,
            "Whichever posture a workload actually runs under must be recorded and "
            "visible — and must never be described as stronger than it is.")
    notes(
        s,
        "AGR-018; the three-posture table and the honesty statement are "
        "SECURITY-ARCHITECTURE.md §3.1 and §3.3; cooperative start for Phases 1–2 per "
        "ASM-005; the external-customer posture is OQ-001.",
        "Connector-health telemetry shows an installed connector is reporting, not that "
        "no request bypassed it. It may never be cited as the reason a workload's "
        "guardrails cannot be bypassed.",
        "Reach for this when the open question about external-customer posture comes "
        "back, or when someone challenges the strength of the pilot.",
        "'Why not start at the strongest posture?' — Network- or identity-enforced "
        "gateway adds platform-layer work that does not help prove policy portability, "
        "which is what the first phase exists to prove.",
    )


def onboarding(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — The onboarding sequence",
        kicker="Register, bind, generate, install, observe, review, enforce.",
        footer=ctx.src("AGR-019 · PRODUCT-OVERVIEW.md §8 · ADR-0006 · ADR-0007 §4"),
    )
    dg.onboarding_stepper(s, f.ONBOARDING_STEPS)
    panel(s, MARGIN, BODY_Y + 2.10, CONTENT_W, 1.95, fill=SURFACE_ALT, border=BLOCK)
    _, tf = textbox(s, MARGIN + 0.35, BODY_Y + 2.32, CONTENT_W - 0.7, 1.6)
    para(tf, "Onboarding never requires:", size=14, bold=True, first=True,
         space_after=8)
    para(tf, f.IMPORT_EXCLUSIONS, size=13, color=MUTED, space_after=8, line=1.3)
    para(tf, "Between install and observe sits the verification state: the workload is "
             "not onboarded until the platform has seen an evaluation at every stage "
             "the bound policy declares.", size=12.5, color=TEXT, space_after=0)
    notes(
        s,
        "AGR-019 meaning of import; sequence reproduced in PRODUCT-OVERVIEW.md §8; the "
        "manifest step is ADR-0006; the verification state is ADR-0007 §4.",
        "This flow is multi-step and reviewed. It is not one-click, instant, or "
        "zero-configuration, and the claims register prohibits describing it that way.",
        "Use this if the review turns to what a consuming team experiences, or when the "
        "verification state needs a place on a timeline.",
        "'How much application change is involved?' — Designed to be minimal, primarily "
        "connector installation and configuration. We have no measurement of actual "
        "change volume, so no figure is offered.",
    )


SLIDES = [
    cover,
    review_scope,
    system_context,
    container_architecture,
    policy_vs_connector,
    evaluation_contract,
    status_codes,
    policy_document,
    enforcement_semantics,
    reason_codes,
    policy_distribution,
    connector_compatibility,
    runtime_capability,
    three_modes,
    topology_a,
    topology_b,
    topology_c,
    extensibility,
    nfr_posture,
    risks_and_tradeoffs,
    open_questions,
    the_ask,
    appendix_divider,
    domain_model,
    manifest_and_bootstrap,
    audit_coverage,
    trust_postures,
    onboarding,
]
