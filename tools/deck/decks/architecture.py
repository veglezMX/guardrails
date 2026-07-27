"""Architecture review board deck.

Audience: staff and principal engineers, architects, and the governance body that
signs off cross-cutting technical decisions. The decision in the room is whether
the contracts are sound — not whether to fund the work.

Written from the 50-architecture material and the accepted ADRs. Slides carry no
internal document identifiers; the speaker notes carry all of them. Governed by
solution-docs/40-sales-deck/DECK-VARIANTS.md §3.

Every slide is one headline and one visual. The supporting argument lives in the
speaker notes, because this deck is delivered by a presenter who did not write
it: a slide that carries the argument invites the room to read instead of
listen, and leaves the presenter nothing to say. Anything cut from a slide here
was moved into its notes, never dropped.
"""

from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches

import diagrams as dg
import facts as f
from lib import (
    ACCENT, ALLOW, BLOCK, BODY_Y, CONTENT_W, HAIRLINE, HEAD_FONT, MARGIN,
    MUTED, SURFACE, TEXT, WARN, box, bullets, caption, notes, panel, para, slide_base,
    table, textbox,
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
    panel(s, MARGIN, 1.55, CONTENT_W, 3.55, fill=SURFACE)

    _, tf = textbox(s, MARGIN, 0.85, CONTENT_W, 0.45)
    para(tf, "AI GUARDRAILS", size=14, color=ACCENT, bold=True, first=True, space_after=0)

    _, tf = textbox(s, MARGIN + 0.55, 2.15, CONTENT_W - 1.6, 2.6)
    para(tf, "One policy, many gateways.", size=34, bold=True, font=HEAD_FONT,
         first=True, space_after=6, line=1.05)
    para(tf, "That claim rests entirely on", size=34, bold=True, font=HEAD_FONT,
         space_after=6, line=1.05)
    para(tf, "the contracts this session reviews.", size=34, color=ACCENT, bold=True,
         font=HEAD_FONT, space_after=0, line=1.05)

    _, tf = textbox(s, MARGIN, 5.42, CONTENT_W, 0.9)
    para(tf, "Architecture review  ·  Presenter: TBD", size=17, color=TEXT,
         first=True, space_after=8)
    para(tf, "Nothing in this deck is built or running.", size=11.5, color=MUTED,
         space_after=0)

    caption(s, MARGIN, 6.92, CONTENT_W,
            "Placeholder styling — no brand kit exists yet.", size=9)
    notes(
        s,
        "DECK-VARIANTS.md §3; the contract set under review is ADR-0004, ADR-0005, "
        "ADR-0007 through ADR-0015, all Accepted pending owner ratification.",
        "Every ADR in this deck is Accepted pending owner ratification on a working "
        "branch — do not present any of them as immutable. The board can still change "
        "them, which is why the session exists.",
        "Open by naming what this session is: a design review with sign-off authority, "
        "not a status update and not a funding pitch. Name the five things under "
        "review — evaluation contract, policy schema, enforcement semantics, connector "
        "compatibility, runtime compatibility — then go straight to what is already "
        "decided versus what is open.",
        "'Has any of this been built?' — No. These are records of design decisions and "
        "their consequences. The first code lands in Phase 1, after this review.",
    )


def review_scope(prs, ctx):
    s = slide_base(
        prs,
        title="What this session reviews",
        kicker="Three categories. Only the third needs a decision from the board today.",
        footer=ctx.src("DECK-VARIANTS.md §3 · DOCUMENT-GOVERNANCE.md §6 · OPEN-QUESTIONS.md"),
    )
    table(s, MARGIN, BODY_Y + 0.45, CONTENT_W, 2.30, [
        ["Category", "Scope", "Asked of the board"],
        ["Settled by agreement", "Five agreements",
         ("Confirm. Reopening one is a register-level change.", MUTED)],
        ["Decided in design records", "Seven design records",
         ("Challenge them — the argument is in the accepted cost.", TEXT)],
        ["Open, and blocking", "Five questions",
         ("Rule on them, or name an owner and a date.", ACCENT)],
    ], col_w=[0.26, 0.24, 0.50], size=13, header_size=12, first_col_bold=True,
        row_h=[0.42, 0.62, 0.62, 0.62])
    notes(
        s,
        "DECK-VARIANTS.md §3 (review scope); the authority ladder is "
        "DOCUMENT-GOVERNANCE.md §6, with L1 registers above L3 architecture records.",
        "The ADRs are Accepted pending owner ratification on a working branch. This "
        "board's objection is still cheap today; after ratification it is a "
        "register-level change with a documented supersession.",
        "Read the three rows, then name what sits in each. Settled: standalone "
        "platform, policy separate from connector, one evaluation contract across all "
        "three modes, guardrails are not authorization, failure behavior is part of "
        "policy. Decided in records: wire format and versioning, policy schema, "
        "enforcement clamping, reason-code namespacing, policy storage and "
        "distribution, connector identity and integrity, runtime deployment target. "
        "Open: the closing section. Then land the governing rule — where a design "
        "record and an agreement disagree, the agreement governs and the record is "
        "corrected, never the reverse. That rule is what makes this review bounded.",
        "'Can an agreement be reopened?' — Yes, through the scope-change process with "
        "a recorded decision. Not informally in session.",
    )


def system_context(prs, ctx):
    s = slide_base(
        prs,
        title="System context",
        kicker="Who calls the platform, what it calls, and what it never touches.",
        footer=ctx.src("SYSTEM-CONTEXT.md · CONTAINER-ARCHITECTURE.md §1 · AGR-003, AGR-004"),
    )
    dg.context_diagram(
        s,
        consumers=["Team-owned LiteLLM\nwith connector",
                   "Application via the SDK",
                   "Any authenticated\nHTTP client"],
        dependencies=["Amazon Bedrock\nGuardrails",
                      "Deterministic and\nPII evaluators",
                      "Queue and object\nstore for audit"],
        core="AI Guardrails\n\nControl plane · Evaluation API · decision engine · "
             "evaluator adapters · audit producer",
        exclusions="Model invocation  ·  model-provider credentials  ·  end-user "
                   "authentication  ·  business authorization  ·  gateway "
                   "administration  ·  the caller's own retry and fallback behaviour.",
    )
    notes(
        s,
        "SYSTEM-CONTEXT.md external actors and dependencies; consumer list per "
        "INTEGRATION-ARCHITECTURE.md §4–§5; exclusions per AGR-010 and the connector "
        "non-responsibilities in INTEGRATION-ARCHITECTURE.md §4.3.",
        "In Mode A the model call never transits this platform — only the two "
        "evaluation calls do. Reviewers frequently assume a proxy topology; correct it "
        "here rather than at the topology slides.",
        "Establish the boundary before the internals. The red panel is the part people "
        "get wrong, and getting it wrong makes every later slide read differently. Say "
        "that the exclusions are permanent, not phase-one simplifications — model "
        "invocation stays outside the boundary in every cooperative mode.",
        "'Why is Bedrock Guardrails a dependency rather than the product?' — Because it "
        "sits behind an evaluator adapter (AGR-004). It is the first managed engine, "
        "not a permanent or exclusive one.",
    )


def container_architecture(prs, ctx):
    s = slide_base(
        prs,
        title="Container architecture",
        kicker="Control plane owns policy. Runtime decides. Engines detect. Audit "
               "leaves asynchronously.",
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
        kicker="A policy is expressible without any connector. A connector carries no "
               "policy.",
        footer=ctx.src("AGR-007 · AGR-005 · PRINCIPLES.md §5.3 · ADR-0012 §4"),
    )
    dg.policy_vs_connector(s)
    notes(
        s,
        "AGR-007 (policies are separate from connectors); portability consequence "
        "AGR-005; the client-configuration boundary is ADR-0012 §4, the published "
        "artifact boundary ADR-0008 §5.",
        "This is stated as a property of the distributed artifact, not only of the "
        "runtime design — a connector release that embedded a policy or a credential "
        "would be detectable in the artifact itself.",
        "If the board only stress-tests one thing, make it this. Every portability "
        "claim later in the deck is a consequence of it holding. The line to land: the "
        "client learns how long to wait and what to do when it cannot wait longer — it "
        "never learns what to detect or what to do about a finding, and that boundary "
        "is checkable at the artifact, not only in the design.",
        "'What stops policy leaking into the client over the config channel?' — The "
        "configuration payload carries timeouts and failure behaviour only, by schema. "
        "No thresholds, no evaluator selection, no detection rules (ADR-0012 §4).",
    )


def evaluation_contract(prs, ctx):
    s = slide_base(
        prs,
        title="The evaluation contract",
        kicker="Synchronous HTTPS, JSON, IAM-authenticated. Major version in the path.",
        footer=ctx.src("ADR-0005 §1–§5 · INTEGRATION-ARCHITECTURE.md §3.2, §3.3 · "
                       "ADR-0003 · ADR-0004"),
    )
    _mono_panel(s, MARGIN, BODY_Y + 0.25, 5.95, 3.55, "EVALUATION REQUEST", [
        "tenant_id · workload_id|What every evaluation and audit record resolves to.",
        "stage|input or output today.",
        "content|A union tagged by kind. Text is the only Phase 1 member.",
        "correlation_id|Threaded across input, model call and output.",
        "policy_ref|Advisory only. The runtime resolves server-side.",
    ])
    _mono_panel(s, 7.13, BODY_Y + 0.25, 5.45, 3.55, "DECISION", [
        "action · allowed|allow · block · redact · log_only.",
        "enforcement_state|The binding state that produced this decision.",
        "reason_codes · guardrail_category|Namespaced, engine-independent.",
        "policy_name · policy_version · evaluator_metadata|What decided, and which "
        "version.",
        "transformed_content|Present only when the action is redact.",
        "correlation_id · audit_id · evaluation_latency_ms",
    ])
    notes(
        s,
        "ADR-0005 §1 transport, §2 versioning classification, §3 generated OpenAPI, §5 "
        "bodies; field set from INTEGRATION-ARCHITECTURE.md §3.2 and §3.3; "
        "guardrail_category per ADR-0003, enforcement_state per ADR-0004.",
        "Adding a value to a response enum — a new action, a new stage — is classified "
        "major, not minor. That is a deliberately expensive classification and it is "
        "the one most likely to be argued with in this session.",
        "Walk the two payloads field by field and stop on three things. policy_ref is "
        "advisory — that single word is what stops a client negotiating itself weaker "
        "enforcement. content is a discriminated union, because a bare string would "
        "make every future stage a breaking change. enforcement_state is on the "
        "decision and not only in audit, because a client cannot implement warn "
        "without it at request time. Then add that the OpenAPI document is generated "
        "from the runtime's own models and committed as a golden file, so an "
        "unversioned contract change is a build failure rather than a review omission.",
        "'Why is a new enum value a major?' — Because a client that receives an action "
        "it does not know cannot apply it, and the contract must stay compatible across "
        "three deployment modes indefinitely (ADR-0005 §7, TECHNICAL-RISKS R8).",
    )


def contract_change_cost(prs, ctx):
    s = slide_base(
        prs,
        title="Widening the contract is deliberately expensive",
        kicker="Of the seven kinds of contract change, five force a new major version.",
        footer=ctx.src("ADR-0005 §2, §7 · TECHNICAL-RISKS.md R8 · AGR-006"),
    )
    dg.contract_change_classes(s)
    notes(
        s,
        "ADR-0005 §2 versioning classification table — the seven change kinds and what "
        "each requires; the enum-value rule and its bind-time consequence are §7; the "
        "one-contract-across-modes obligation is AGR-006.",
        "The counts are of change kinds in the classification table, not of changes "
        "anyone has made or expects to make. Nothing has been built, so there is no "
        "change history to chart — do not let this be read as a measured rate.",
        "This is the argument for the classification, in one shape: only two kinds of "
        "change are free — adding an optional request field and adding a response "
        "field. Everything else costs a new path prefix, and adding a value to a "
        "response enum additionally costs a bind-time compatibility refusal. That is "
        "the price of one contract serving all three deployment modes indefinitely. "
        "Invite the board to argue the enum row specifically; it is the expensive one "
        "and the one they are most likely to want relaxed.",
        "'Can two majors run at once?' — Yes, during a migration window. There is no "
        "header-based or media-type negotiation; the major lives in the path.",
    )


def status_codes(prs, ctx):
    s = slide_base(
        prs,
        title="A block is a success",
        kicker="The runtime never returns a server error to express an evaluation "
               "outcome.",
        footer=ctx.src("ADR-0005 §6 · AGR-012 · RFC 9457 problem responses"),
    )
    table(s, MARGIN, BODY_Y + 0.35, CONTENT_W, 2.85, [
        ["Status", "Meaning", "Body"],
        [("200", ALLOW, True), "Evaluation completed — including block, and including "
         "a substitute decision.", "Decision"],
        ["400 · 422", "Malformed, or well-formed but invalid.", "Problem"],
        ["401 · 403", "Authentication or authorization failure.", "Problem"],
        ["413", "Content exceeds the deployment's size ceiling.", "Problem"],
        ["429", "Quota or rate limit. Phase 2.", "Problem"],
        [("5xx", BLOCK, True), "The runtime is unavailable — no evaluation happened.",
         "Problem"],
    ], col_w=[0.13, 0.70, 0.17], size=12.5, header_size=12, first_col_bold=True,
        row_h=[0.38, 0.52, 0.38, 0.38, 0.38, 0.38, 0.42])
    notes(
        s,
        "ADR-0005 §6 status-code table and the 5xx rule; failure-path substitution per "
        "AGR-012; error envelope is RFC 9457 problem+json extended with reason_code.",
        "This is the rule most likely to be violated by an implementer under time "
        "pressure — returning 403 for a block is locally convenient and permanently "
        "corrosive. Worth stating as a conformance test, not a convention.",
        "One sentence carries the slide: enforcement outcomes are successes. Then fill "
        "in what the table does not say. A detector outage, an evaluation timeout or "
        "an unresolvable policy all produce a completed response carrying a substitute "
        "decision — never a server error — because encoding enforcement as an error "
        "would make every client's error path also its enforcement path. Problem "
        "responses carry a reason code from the same taxonomy as decisions, plus a "
        "correlation identifier. Then move to the policy document, which is where the "
        "failure behaviours come from.",
        "'How does a client distinguish a block from an outage?' — By status class. A "
        "completed response means the platform decided; a server error means it did "
        "not, and the client's own configured behaviour takes over.",
    )


def policy_document(prs, ctx):
    s = slide_base(
        prs,
        title="The policy document has no defaults",
        kicker="Validated before publication, published immutable. No tenant, no "
               "workload, no enforcement mode.",
        footer=ctx.src("ADR-0009 §1–§5 · AGR-012 · AGR-005 · AGR-017 · CON-004"),
    )
    bullets(s, MARGIN, BODY_Y + 0.55, CONTENT_W, [
        "Every section required, validated before publication|The runtime never "
        "resolves an invalid policy.",
        "Evaluators selected by capability, never by vendor|A vendor name in a policy "
        "document is a schema violation.",
        "One required key per failure mode, and no default for any|A policy missing "
        "any failure key is never published.",
    ], size=16, gap=0.92)
    notes(
        s,
        "ADR-0009 §1 schema and pre-publication validation, §2 required contents, §3 "
        "capability-not-vendor selection, §4 no-default failure keys; AGR-012 "
        "enumerates the failure modes; separation of binding attributes per AGR-017.",
        "The same no-default rule applies at the client: a client that has never "
        "retrieved its configuration and was given none refuses to evaluate rather "
        "than allowing (ADR-0012 §2). Reviewers usually accept the server rule and "
        "flinch at the client one.",
        "The third bullet is the one to spend time on, because it carries a real cost. "
        "'No undocumented fallback' and 'sensible defaults' are mutually exclusive: "
        "any default, however conservative, means an author who omits the section "
        "still gets behaviour, and that behaviour is undocumented from the author's "
        "point of view. So every policy author writes every failure key, including for "
        "modes they consider unlikely. That verbosity is the documentation the "
        "agreement requires — a real cost, accepted deliberately. Read the argument "
        "out, invite the objection, and let the board weigh it. Close by noting what "
        "the policy does not carry: no tenant, no workload, no enforcement mode — "
        "those live on the binding, which is what lets one policy version serve many "
        "workloads at different points on the ladder.",
        "'Could just the unlikely modes be defaulted?' — Then those are the ones nobody "
        "writes down, which is precisely the class of behaviour the agreement exists to "
        "eliminate. The alternatives are recorded in the decision record.",
    )


def enforcement_semantics(prs, ctx):
    s = slide_base(
        prs,
        title="Enforcement state is a ceiling, not a selector",
        kicker="The state lives on the binding, not on the policy.",
        footer=ctx.src("ADR-0004 §1–§5 · AGR-011 · DATA-ARCHITECTURE.md §1.5"),
    )
    dg.enforcement_ladder(s, f.ENFORCEMENT_STATES, highlight=1, y=BODY_Y + 0.75)
    notes(
        s,
        "ADR-0004 §1 state on the binding, §2 clamping table, §3 warn distinguished by "
        "a decision field rather than a fifth action, §4 disabled still audits; "
        "AGR-011; binding attributes per DATA-ARCHITECTURE.md §1.5.",
        "Disabled still produces a decision and an audit record, with evaluators "
        "skipped entirely. That is what makes a disabled workload provably disabled "
        "rather than merely silent — the audit trail distinguishes 'evaluation ran and "
        "was disabled by binding' from 'no evaluation occurred'.",
        "Walk the five states once and say what the ladder is for: the decision engine "
        "computes the action from the policy, then clamps it to what the state "
        "permits. The state is an upper bound on what may happen to traffic, not a "
        "selection of what to detect. The next slide is the mechanism.",
        "'Why is warn not an action?' — Because the caller proceeds either way. Warn "
        "differs from observe in whether the client surfaces the reason codes, and how "
        "it surfaces them is deliberately client-specific.",
    )


def decision_clamping(prs, ctx):
    s = slide_base(
        prs,
        title="The ceiling narrows the action, and nothing else",
        kicker="Findings, reason codes and category reach the caller unchanged at every "
               "state.",
        footer=ctx.src("ADR-0004 §2, §5 · AGR-011 · ADR-0003 · DATA-ARCHITECTURE.md §1.5"),
    )
    dg.clamp_pipeline(
        s,
        [
            ("Evaluators", "Run unless the binding is disabled", False),
            ("Policy", "States the action each finding calls for", False),
            ("Binding", "Carries the enforcement state — the ceiling", False),
            ("Clamp", "Takes the lesser of the two, never the greater", True),
            ("Decision", "One action, plus everything that produced it", False),
        ],
        "findings · reason_codes · guardrail_category",
        "bypass the clamp entirely and are reported unchanged",
        y=BODY_Y + 0.85,
    )
    notes(
        s,
        "ADR-0004 §2 clamping table, §5 clamping never alters findings; AGR-011; "
        "guardrail_category as a first-class decision field per ADR-0003; binding "
        "attributes per DATA-ARCHITECTURE.md §1.5.",
        "The disabled state is the exception at the left of the pipeline: evaluators "
        "do not run at all, so there are no findings to report — the audit record "
        "still distinguishes that from an evaluation that never occurred.",
        "Walk the pipeline left to right, then read the three rows that matter. A "
        "binding in observe with a policy calling for block delivers log_only. A "
        "binding in redact with a policy calling for block delivers redact. A binding "
        "in block with a policy calling for redact delivers redact — not block. That "
        "third one is the row people misread: a ceiling is an upper bound and never a "
        "floor, so clamping never escalates. Findings are reported unchanged in all "
        "three, which is what makes observe-mode measurement real measurement rather "
        "than a muted preview of enforcement.",
        "'Does a lower state suppress the findings as well as the action?' — No. The "
        "clamp applies to the action only. A workload in observe reports exactly what "
        "a workload in block would report; it simply does not act on it.",
    )


def reason_codes(prs, ctx):
    s = slide_base(
        prs,
        title="Reason codes are namespaced by category, at the adapter",
        kicker="Whatever the engine emits, the adapter normalises it before it reaches "
               "a decision.",
        footer=ctx.src("ADR-0003 · DEC-013 · AGR-004 · AGR-021 · DATA-ARCHITECTURE.md §1.7"),
    )
    dg.segmented_identifier(s, "GR-0NN : <evaluator-family> : <code>", [
        ("catalog category",
         "mandatory — no code may be emitted without one"),
        ("which evaluator produced the finding",
         "not which product the deployment happens to use"),
        ("that evaluator's own native code",
         "or a short stable slug"),
    ], y=BODY_Y + 0.90)
    notes(
        s,
        "ADR-0003 decision §1 canonical format, §2 guardrail_category as a first-class "
        "decision field, §3 reporting consequence; recorded as DEC-013; adapter seam "
        "per AGR-004; catalog traceability per AGR-021.",
        "The eighteen catalog categories are change-controlled; the individual "
        "guardrails inside a category are illustrative and are not. Do not let the "
        "catalog be read as a commitment — it is classification, and several entries "
        "are explicitly vision-uncommitted or out-of-boundary.",
        "A small decision with a large reporting consequence. The category is also "
        "mirrored onto its own decision field, so per-category metrics and "
        "catalog-to-decision traceability become a group or filter on a first-class "
        "field — no side table, no forked decision structure, no per-mode divergence "
        "of the contract. It is a normalisation rule inside the existing adapter seam "
        "plus an additive field on an existing entity: no new domain entity, no new "
        "stage, no new action. Worked example: a personal-data finding from the first "
        "managed engine and the same finding from a future evaluator carry the same "
        "category segment and are queryable together. Spend a minute, not five, unless "
        "the board wants to argue the namespacing.",
        "'What if an engine's native codes collide across families?' — They cannot "
        "collide across the full form, because the family segment disambiguates them "
        "and the adapter owns the mapping.",
    )


def policy_distribution(prs, ctx):
    s = slide_base(
        prs,
        title="Policy storage and distribution",
        kicker="Runtimes pull. The control plane never pushes. Evaluation reads local "
               "state only.",
        footer=ctx.src("ADR-0010 §1–§4 · ADR-0011 §1–§5 · AGR-006 · AGR-015 · NFR-REC-001"),
    )
    dg.pull_model(
        s,
        "Bindings — low-latency key-value store\nRead once per evaluation. Append-only.",
        "Policy documents — versioned object store\nImmutability is native.",
        [
            ("Runtimes pull on an interval",
             "The control plane never initiates a connection. One mechanism serves all "
             "three modes."),
            ("Resolution reads the local cache only",
             "No evaluation makes a synchronous control-plane call, in any mode."),
            ("A refresh failure is not an evaluation failure",
             "The runtime keeps evaluating against its last retrieved state."),
        ],
    )
    notes(
        s,
        "ADR-0010 §1 store split, §2 append-only bindings, §3 the stored artifact is "
        "the distribution artifact, §4 regionality follows the tenant; ADR-0011 §1 "
        "pull, §2 local-only resolution, §3 refresh failure, §4 staleness visibility, "
        "§5 the rollback-urgency statement; AGR-006, AGR-015; NFR-REC-001.",
        "The rollback wording in the non-functional register says 'immediate'. ADR-0011 "
        "§5 bounds it explicitly and forbids product or sales material from implying "
        "otherwise. If the board wants a tighter bound, the refresh interval is the "
        "knob and it has a cost.",
        "Give the reasoning behind each rule. Pull was chosen for the customer-managed "
        "case — a push mechanism would need inbound connectivity into a customer "
        "environment, and one mechanism across all three modes is what keeps Phase 4 a "
        "packaging exercise rather than a redesign. Local-only resolution exists "
        "because otherwise the control plane becomes a hot-path dependency whose "
        "availability bounds the evaluation path's. On refresh failure, a cached "
        "immutable version is never wrong for its version, only potentially "
        "superseded; staleness is measured, bounded, reported, and routed through the "
        "configured control-plane-unavailable behaviour once tolerance is exceeded. "
        "Then the honest caveat: rollback is immediate at the control plane, and "
        "runtimes converge within their refresh interval. That interval is a safety "
        "parameter, not a tuning knob.",
        "'What is the maximum tolerable staleness?' — Configured per deployment with an "
        "explicit value; the default figure is not set and must come from Phase 4 "
        "discovery with candidate adopters (ASM-013).",
    )


def connector_compatibility(prs, ctx):
    s = slide_base(
        prs,
        title="Compatibility is two-phase",
        kicker="Bind time checks intent. Request time checks reality.",
        footer=ctx.src("ADR-0007 §1–§5 · ADR-0013 §3 · ADR-0008 · FR-016 · AGR-018 · CON-012"),
    )
    table(s, MARGIN, BODY_Y + 0.55, CONTENT_W, 2.20, [
        ["Phase", "When", "Validates", "On failure"],
        ["Declared", "Binding creation",
         "Policy requirements against connector range and runtime capabilities.",
         ("Refuse the binding.", BLOCK)],
        ["Observed", "Every evaluation",
         "The identity presented against what the binding expects.",
         ("Major mismatch refuses. Drift proceeds, and is reported.", WARN)],
    ], col_w=[0.12, 0.18, 0.42, 0.28], size=12.5, header_size=12, first_col_bold=True,
        row_h=[0.40, 0.90, 0.90])
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
        "Two phases, one rule: check intent early, check reality continuously, never "
        "confuse a cooperative signal with a control. Three things are not on the "
        "slide. First, a workload is not onboarded until its installation is verified "
        "— it sits in a pending-verification state until the platform has observed at "
        "least one evaluation at every stage the bound policy declares, and "
        "verification is a precondition for promotion out of observe. Second, "
        "connectors are published artifacts on a named, externally reachable channel "
        "with verifiable integrity and documented privilege, and the published "
        "documentation states plainly what the connector does inside the consuming "
        "team's process, because that team's security review needs a factual "
        "description. Third, and most important: connector identity is self-reported "
        "and must never be cited as evidence that a workload's guardrails cannot be "
        "bypassed — a client that would lie about its version would equally decline to "
        "call at all.",
        "'Why not block on a version mismatch?' — Because blocking converts metadata "
        "drift into a production outage, which is a worse failure than the drift. A "
        "contract-major mismatch does refuse, because serving a shape the runtime does "
        "not understand is how silently wrong decisions are produced.",
    )


def runtime_capability(prs, ctx):
    s = slide_base(
        prs,
        title="Runtimes advertise capabilities, never implementations",
        kicker="An unsatisfiable runtime refuses to start. An unsatisfiable binding is "
               "refused.",
        footer=ctx.src("ADR-0013 §1–§5 · ADR-0009 §3 · AGR-004 · CON-004 · AGR-012"),
    )
    dg.numbered_panels(s, [
        ("01", "A manifest states capabilities, not engines",
         "Derived from the runtime's real configuration, never hand-maintained."),
        ("02", "Unsatisfiable configuration fails at startup",
         "Not in production, and never by booting with a silently reduced manifest."),
        ("03", "Binding creation validates the policy against the target runtime",
         "An unsatisfiable binding is refused, naming the missing capabilities."),
    ], y0=BODY_Y + 0.30, gap=1.35, h=1.10)
    notes(
        s,
        "ADR-0013 §1 capabilities not implementations, §2 fail-to-start, §3 bind-time "
        "validation as the runtime half of ADR-0007's two-phase check, §4 re-check on "
        "capability change, §5 request-time shortfall as a configured failure mode; "
        "vocabulary shared with ADR-0009 §3; vendor boundary per AGR-004 and CON-004.",
        "A drifted hand-maintained capability list would be worse than no check at all, "
        "because it reports confidence it does not have. That is the reasoning behind "
        "deriving the manifest rather than declaring it.",
        "Three claims, one theme: make the failure visible at the cheapest moment — "
        "startup, then bind time, then only as a last resort request time under a "
        "configured behaviour. Fill in each. A runtime declares that it can evaluate a "
        "given detection family and capability without disclosing which engine does "
        "so, which keeps the vendor boundary intact where leaking it would be most "
        "tempting and lets a deployment change implementations without changing what "
        "it advertises. A container that boots while missing a detector is the same "
        "class of failure as undocumented fallback behaviour: something is quietly not "
        "happening and nothing says so. And bind-time validation converts a "
        "production-time degradation into an error seen by the operator making the "
        "change, with the context to fix it.",
        "'What happens to existing bindings when a capability is removed?' — A running "
        "deployment cannot un-create a binding. It reports the affected workloads as "
        "degraded so an operator can act; the check is repeatable against current "
        "state, not a one-time gate.",
    )


def three_modes(prs, ctx):
    s = slide_base(
        prs,
        title="Three deployment modes, one contract",
        kicker="Only the network destination of the Evaluation API changes.",
        footer=ctx.src("AGR-002 · AGR-006 · INTEGRATION-ARCHITECTURE.md §4.7 · ADR-0015 §3"),
    )
    dg.mode_columns(s, f.MODES, highlight=0)
    notes(
        s,
        "AGR-002 three modes; one logical contract per AGR-006 and CON-005; the "
        "invariant connector contract is INTEGRATION-ARCHITECTURE.md §4.7; single "
        "packaging path per ADR-0015 §3; the packaging criterion is MVP-09.",
        "Modes are not a maturity ladder and the modes are not equally mature — the "
        "customer-managed runtime lands in Phase 4. A reviewer with a residency "
        "requirement in mind should hear that timing explicitly.",
        "Frame the modes as a deployment axis and move quickly to the topologies, "
        "which is where this audience actually wants to be. One line worth saying: a "
        "single packaging path serves the hosted deployment, the customer-managed "
        "runtime and the later guarded gateway, because a second packaging path for "
        "the same code would drift — which is how 'packaged for customer-managed "
        "deployment without changing the connector contract' quietly becomes false.",
        "'Does the decision structure differ per mode?' — No. A caching optimisation in "
        "the hosted modes must not alter the externally observable decision or audit "
        "contract; that is a constraint, not a design preference.",
    )


def topology_a(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode A — Remote Evaluation",
        "Team-owned gateway, hosted runtime. The model call never transits the platform.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §2 · AGR-002 · AGR-009"),
        dg.mode_a_topology,
        "Prompts and responses are transmitted to the hosted Evaluation API on every "
        "evaluated call. This is not a private-data or data-local model.",
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
        "cooperative posture depends on. Two evaluation calls per request, and the "
        "gateway holds the team's own model-provider credentials throughout.",
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
        "gateway, which holds or brokers model-provider credentials.",
        BLOCK,
    )
    notes(
        s,
        "DEPLOYMENT-ARCHITECTURE.md §3.1 topology, §3.2 ownership, §3.3 exposure; the "
        "gateway itself is specified on the same container platform per ADR-0015.",
        "This mode puts the provider in the full inference path and creates the largest "
        "operational and security scope for the provider. It sits behind two "
        "validation gates for exactly that reason.",
        "Keep this brief unless the board wants to review Phase 3 scope — it is the "
        "mode furthest from the work in front of them. Add only that raw content is "
        "not persisted by default here either; decision metadata is.",
        "'Does the evaluation contract change when the runtime is in-process?' — No. "
        "The connector talks to the same contract; only the destination changes.",
    )


def topology_c(prs, ctx):
    s = dg.topology_slide(
        prs,
        "Mode C — Customer-Managed Runtime",
        "The same container image inside the customer's account, with no synchronous "
        "control-plane dependency. Phase 4.",
        ctx.src("DEPLOYMENT-ARCHITECTURE.md §4, §9 · AGR-015 · AGR-016 · ADR-0011 · "
                "ADR-0015 §3"),
        dg.mode_c_topology,
        "Prompts and responses are evaluated entirely inside the customer's own AWS "
        "account. Telemetry to the control plane is customer-selected, down to none.",
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
        "than re-arguing each. Two additions: raw content must never leave that runtime "
        "unless explicitly configured, and no telemetry at all is a fully supported "
        "configuration rather than a degraded one.",
        "'What protects the policy artifact in transit to a customer runtime?' — "
        "Transport security today. Cryptographic policy signing is permitted future "
        "scope and is not built (OQ-012).",
    )


def extensibility(prs, ctx):
    s = slide_base(
        prs,
        title="Four seams, and everything grows through one of them",
        kicker="Adding coverage is a change at a named seam with a governance gate — "
               "never an ad-hoc extension.",
        footer=ctx.src("ADR-0002 · EXTENSIBILITY-ARCHITECTURE.md · GUARDRAIL-CATALOG.md · "
                       "AGR-021 · SCOPE.md §8"),
    )
    dg.seam_cards(s, [
        ("New evaluation\nstage", "Widens the contract. Adding a value is a major "
                                  "version."),
        ("New evaluation\nengine", "Behind an adapter. Normalises into the existing "
                                   "decision structure."),
        ("New decision\naction", "Widens the action set every client must handle. The "
                                 "most expensive seam."),
        ("New\nconnector", "Implements the invocation contract only. Never touches "
                           "policy semantics."),
    ], highlight=3, y=BODY_Y + 0.70, h=2.40)
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
        "platform'. The catalog itself is classification, not commitment — eighteen "
        "change-controlled categories, each carrying an enforcement layer, the stages "
        "it uses, its evaluator family, and a phase status from a fixed vocabulary: "
        "core, later-phase committed, vision-uncommitted, or out-of-boundary. "
        "Individual guardrails inside a category are illustrative and not "
        "change-controlled. Promoting a vision entry to committed scope is a recorded "
        "scope-change decision, never a silent graduation, and out-of-boundary entries "
        "carry a named external owner and are never presented as future deliverables.",
        "'Which seam is most expensive?' — A new decision action. Every client in every "
        "mode must handle it, and the contract must stay compatible indefinitely, so it "
        "is classified as a major contract change.",
    )


def nfr_posture(prs, ctx):
    s = slide_base(
        prs,
        title="What is specified, and what is deliberately not",
        kicker="Mechanisms are specified. Numbers are not.",
        footer=ctx.src("NON-FUNCTIONAL-REQUIREMENTS.md · OPERABILITY.md §2 · ASM-011, "
                       "ASM-012 · CON-016"),
    )
    table(s, MARGIN, BODY_Y + 0.40, CONTENT_W, 2.90, [
        ["Area", "Specified", "Not set"],
        ["Latency", "Measured per stage, mode and detector. Timeouts mandatory.",
         ("Every percentile target and timeout value.", WARN)],
        ["Availability", "A monitored objective required before general availability.",
         ("The objective and the measurement window.", WARN)],
        ["Resilience", "Bounded retries, idempotent calls, per-dependency breaker.",
         ("Thresholds and cool-down durations.", WARN)],
        ["Cache", "Hit rate required. Staleness measured and bounded.",
         ("Tolerable staleness.", WARN)],
    ], col_w=[0.15, 0.53, 0.32], size=12.5, header_size=12, first_col_bold=True,
        row_h=[0.40, 0.62, 0.62, 0.62, 0.62])
    notes(
        s,
        "NON-FUNCTIONAL-REQUIREMENTS.md availability, reliability, performance and "
        "operability sections — NFR-AVAIL-001/002, NFR-PERF-001 through 007, "
        "NFR-OPS-001 through 005; timeout and retry mechanics in OPERABILITY.md §2.2–"
        "§2.6; the gaps are ASM-011 and ASM-012; mandatory timeouts per CON-016.",
        "If the board wants a latency number today, the correct answer is that none "
        "is available. Setting a target before Phase 1 instrumentation would make it "
        "an aspiration presented as a requirement.",
        "Deliver this as a plan with a date-bearing owner, not as an apology — done "
        "that way it usually earns credibility rather than losing it. The framing: "
        "this is the stated position, not an oversight. No representative load data "
        "exists yet, and a target set without it would be a number nobody could "
        "defend. Instrumentation lands in Phase 1 precisely so the targets can be set "
        "in Phase 2 from real traffic. Detail worth adding per row: every detector "
        "budget sits strictly inside its stage budget; degraded-mode continuity is "
        "mandatory and no policy may leave observe without declaring its degraded "
        "behaviour; the circuit-breaker state is an observable signal; and tolerable "
        "staleness is owed by discovery with candidate adopters.",
        "'When are the numbers set?' — Instrumented from Phase 1, targets set in "
        "Phase 2 against real traffic, published before any mode reaches general "
        "availability.",
    )


def risks_and_tradeoffs(prs, ctx):
    s = slide_base(
        prs,
        title="Trade-offs already accepted",
        kicker="Each was decided against a named alternative, with the cost recorded.",
        footer=ctx.src("TRADE-OFFS.md §3–§10 · TECHNICAL-RISKS.md · ADR-0009 · ADR-0011 · "
                       "ADR-0015"),
    )
    table(s, MARGIN, BODY_Y + 0.40, CONTENT_W, 3.05, [
        ["Decision", "Rejected alternative", "Accepted cost"],
        ["No default failure behaviour", "Conservative built-in defaults",
         "Every policy author writes every failure key."],
        ["Pull, local-only resolution", "Control-plane resolution per evaluation",
         "Rollback converges within the refresh interval."],
        ["Containers, not functions", "Serverless request/response target",
         "Higher idle cost, for one packaging path."],
        ["A managed engine first", "Custom evaluators from the start",
         "Detection quality bounded by someone else's service."],
        ["Cooperative connector", "Enforced gateway from day one",
         "Bypass is possible by the installing team."],
    ], col_w=[0.30, 0.32, 0.38], size=12.5, header_size=12, first_col_bold=True,
        row_h=[0.40, 0.53, 0.53, 0.53, 0.53, 0.53])
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
        "different position is doing exactly what this session is for. Expand where "
        "asked: the failure keys must be written including for modes an author "
        "considers unlikely; rollback is immediate at the control plane but not "
        "instantaneous everywhere; the idle cost buys one packaging path across all "
        "three modes; bypass is accepted for Phases 1–2 and not eliminated. Close on "
        "the cross-cutting point — adapters are what stops most of these being "
        "re-litigated, because vendor behaviour, engine choice and deployment "
        "configuration sit behind a seam, so changing one does not reopen the contract.",
        "'Which of these would change first?' — The starting posture, if the board "
        "judges the residual bypass risk unacceptable for the pilot. It is the cheapest "
        "to change and the most consequential.",
    )


def open_questions(prs, ctx):
    s = slide_base(
        prs,
        title="What the board is asked to rule on",
        kicker="No recorded decision on any of these. Each needs an answer, or an owner "
               "and a date.",
        footer=ctx.src("OPEN-QUESTIONS.md OQ-001, OQ-014, OQ-018, OQ-022, OQ-012 · "
                       "ASM-011, ASM-012"),
    )
    bullets(s, MARGIN, BODY_Y + 0.50, CONTENT_W, [
        "Which enforcement posture do the first external customers require?",
        "What are the go/no-go criteria for promoting a workload out of observe?",
        "Does the evaluation-stage vocabulary expand beyond the reserved set?",
        "What is the connector version support window, and the deprecation policy?",
        "Are signed policy artifacts required before the customer-managed runtime ships?",
    ], size=16, gap=0.66)
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
        "rather than optional. Work down the list, giving today's answer for each. "
        "Posture: Phases 1–2 start cooperative; if external customers need "
        "platform-managed or network-enforced, that work moves earlier and the "
        "platform takes on gateway infrastructure sooner. Promotion: it is an "
        "explicit, audited action today, but what evidence justifies it — "
        "false-positive rate, traffic volume, review sign-off — is not defined. Stage "
        "vocabulary: the content union is tagged precisely so new stages are additive, "
        "but whether the reserved seven suffice is a contract question and widening "
        "the response enum is a major version. Support window: compatibility checks "
        "are meaningless without one, and 'supported' has no definition today. "
        "Signing: transport security is the current safeguard for policy reaching a "
        "runtime the provider does not operate; signing is permitted future scope and "
        "is not built. Record what the board decides.",
        "'Can all of them be deferred?' — OQ-001 and OQ-022 gate Phase 1 design work; "
        "the rest can wait. Deferring the first two means designing against an unknown "
        "posture and an undefined support commitment.",
    )


def the_ask(prs, ctx):
    s = slide_base(
        prs,
        title="The decision requested",
        footer=ctx.src("DECK-VARIANTS.md §3 · DOCUMENT-GOVERNANCE.md §6 · OPEN-QUESTIONS.md"),
    )
    dg.ask_columns(
        s,
        "Sign off the contract set: wire format and versioning, policy schema and its "
        "mandatory failure behavior, enforcement-state semantics, reason-code "
        "namespacing, and the two-phase compatibility model.",
        "Not a funding decision, and not a review of the delivery plan.",
        asking=[
            "A ruling, or an owner and a date, on each open question",
            "Named reviewers for the contract document and the policy schema",
            "Agreement that an unversioned contract change is a build failure",
        ],
        not_asking=[
            "Latency, availability or throughput targets",
            "A decision on any capability outside the committed feature set",
            "Approval to start building",
        ],
    )
    notes(
        s,
        "DECK-VARIANTS.md §3 (the decision this deck asks for); authority ladder per "
        "DOCUMENT-GOVERNANCE.md §6; the open-question list is OPEN-QUESTIONS.md.",
        "Sign-off here does not commit anyone to delivery — that decision sits with the "
        "investment review and has not been made either. Do not let the two be "
        "conflated in the minutes.",
        "Restate the contract elements, take the rulings, and stop. If sign-off is not "
        "reachable today, the useful outcome is a written list of the contract "
        "elements the board disputes — a shorter, more specific document than a "
        "general objection, and what a revision would be built from. If the board "
        "wants to keep going on a specific record, that is a follow-up session with a "
        "narrower agenda, not an extension of this one.",
        "'What if sign-off happens and an agreement then changes?' — The agreement "
        "governs and the design record is corrected. That is the ladder, and it is why "
        "the records are separate documents rather than one specification.",
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
    notes(
        s,
        "DECK-VARIANTS.md §3 appendix list.",
        "The appendix is reference, not reserve narrative. Reaching for it unprompted "
        "turns a bounded review into an unbounded one.",
        "Pause. If the contract sign-off landed and the open questions are recorded, "
        "stop here. What follows, if raised: core domain model, integration manifest "
        "and client bootstrap, audit coverage, trust and enforcement postures, and the "
        "onboarding sequence.",
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
        kicker="The manifest seeds a client at install time. Neither carries policy.",
        footer=ctx.src("ADR-0006 §1–§4 · ADR-0012 §1–§5 · AGR-019 · AGR-012 · CON-016"),
    )
    _mono_panel(s, MARGIN, BODY_Y + 0.25, 5.95, 3.45, "INTEGRATION MANIFEST", [
        "A file, not a screen|Schema-validated YAML, committed and reviewed in a pull "
        "request. Drift is visible as a diff.",
        "Derived, never authoritative|Generated from an existing binding. A team cannot "
        "obtain weaker enforcement by editing it.",
        "Carries|Identity · policy name and version · enforcement mode · endpoint · "
        "contract major · connector range · timeouts · failure behavior · required "
        "capabilities.",
    ])
    _mono_panel(s, 7.13, BODY_Y + 0.25, 5.45, 3.45, "CLIENT BOOTSTRAP", [
        "Served on its own path|Retrieved once, cached, refreshed in the background.",
        "No configuration means refuse to evaluate|It does not fall back to a default "
        "and it does not allow.",
        "Losing contact never un-configures a client|It keeps applying what it last "
        "retrieved.",
        "Timeouts and failure behavior only|No detection rules, thresholds or evaluator "
        "selection.",
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
        "challenges the refuse-without-configuration rule. The reasoning: evaluation is "
        "synchronous and latency-critical, so configuration is retrieved on its own "
        "path rather than riding the evaluation response; and if losing contact "
        "un-configured a client, a control-plane outage would turn every working "
        "client into a refusing one — a worse failure than briefly stale timeouts.",
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
    table(s, MARGIN, BODY_Y + 0.40, CONTENT_W, 2.60, [
        ["Event", "Produced by", "Record"],
        ["Completed evaluation, any action", "Runtime",
         "Policy version, action, reason codes, category, evaluator versions, "
         "correlation, latency."],
        ["Failure-path substitute decision", "Runtime",
         "The same, plus which failure mode fired and which path was taken."],
        ["Request rejected before evaluation", "Runtime",
         "Reason and correlation only. Never the content that failed validation."],
        ["Client-synthesized decision", ("Client", WARN),
         "Marked client-origin. Delivered when the runtime is reachable again."],
    ], col_w=[0.29, 0.13, 0.58], size=12, header_size=12, first_col_bold=True,
        row_h=[0.40, 0.55, 0.55, 0.55, 0.55])
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
        "outage — the honest answer has three qualifications. A client-origin record "
        "is never presented as equivalent to a runtime record: it is self-reported by "
        "a cooperative component, so it says what a client reports having done, not "
        "what the platform observed. Operationally valuable, not evidentiary. The "
        "local store is bounded, discards oldest first on overflow, and reports how "
        "many it discarded — because discarding silently would understate an outage in "
        "exactly the direction that flatters the platform. And delivery is best-effort, "
        "because records may be lost if the client process dies.",
        "'Can a client-origin record satisfy a compliance question?' — No. It is "
        "operational evidence of what a client reports, and it must never be offered "
        "as proof of what the platform enforced.",
    )


def trust_postures(prs, ctx):
    s = slide_base(
        prs,
        title="Appendix — Trust and enforcement postures",
        kicker="Orthogonal to deployment mode. A posture describes how bypass-resistant "
               "an integration actually is.",
        footer=ctx.src("AGR-018 · SECURITY-ARCHITECTURE.md §3.1–§3.3 · ASM-005 · OQ-001"),
    )
    dg.posture_rows(s, f.POSTURES)
    notes(
        s,
        "AGR-018; the three-posture table and the honesty statement are "
        "SECURITY-ARCHITECTURE.md §3.1 and §3.3; cooperative start for Phases 1–2 per "
        "ASM-005; the external-customer posture is OQ-001.",
        "Connector-health telemetry shows an installed connector is reporting, not that "
        "no request bypassed it. It may never be cited as the reason a workload's "
        "guardrails cannot be bypassed.",
        "Reach for this when the open question about external-customer posture comes "
        "back, or when someone challenges the strength of the pilot. The rule that "
        "goes with the table: whichever posture a workload actually runs under must be "
        "recorded and visible, and must never be described as stronger than it is.",
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
    dg.onboarding_stepper(s, f.ONBOARDING_STEPS, y=BODY_Y + 1.10)
    notes(
        s,
        "AGR-019 meaning of import; sequence reproduced in PRODUCT-OVERVIEW.md §8; the "
        "manifest step is ADR-0006; the verification state is ADR-0007 §4.",
        "This flow is multi-step and reviewed. It is not one-click, instant, or "
        "zero-configuration, and the claims register prohibits describing it that way.",
        "Use this if the review turns to what a consuming team experiences, or when the "
        "verification state needs a place on a timeline. Two things to add. Between "
        f"install and observe sits the verification state: {f.IMPORT_EXCLUSIONS} "
        "Onboarding never requires any of that, and the workload is not onboarded "
        "until the platform has seen an evaluation at every stage the bound policy "
        "declares.",
        "'How much application change is involved?' — Designed to be minimal, primarily "
        "connector installation and configuration. No measurement of actual change "
        "volume exists, so no figure is offered.",
    )


SLIDES = [
    cover,
    review_scope,
    system_context,
    container_architecture,
    policy_vs_connector,
    evaluation_contract,
    contract_change_cost,
    status_codes,
    policy_document,
    enforcement_semantics,
    decision_clamping,
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
