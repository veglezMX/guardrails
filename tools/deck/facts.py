"""Facts every deck draws from, stated once.

Only material that is genuinely the same regardless of who is in the room lives
here — the three deployment modes, the enforcement states, the five phases, the
domain entities, the validation criteria, the risk and ownership rows. Framing,
emphasis and slide order belong to the individual deck modules, not here.

Wording traces to solution-docs/ and is inside the boundary
solution-docs/70-governance/CLAIMS-REGISTER.md enforces. Changing a string here
changes it in every deck, which is the point — do not fork a fact to soften it
for one audience.
"""

import re

from lib import ACCENT, ALLOW, BLOCK, LOG_ONLY, WARN

# --- deployment modes (AGR-002) -------------------------------------------
# tag, name, who operates it, how it works, best fit
MODES = [
    ("Mode A", "Remote Evaluation",
     "You run the gateway.\nWe run the guardrail runtime.",
     "Team-owned LiteLLM calls our hosted Evaluation API before and after each model "
     "invocation.",
     "Already operate a gateway; want to offload guardrail operations."),
    ("Mode B", "Guarded Gateway",
     "We run the gateway\nand the guardrail runtime.",
     "One provider-operated endpoint: guardrails, routing, invocation, and audit "
     "inside our boundary.",
     "No gateway of your own; want a managed, OpenAI-compatible path."),
    ("Mode C", "Customer-Managed Runtime",
     "You run both, in your\nown AWS account.",
     "The same runtime — policy cache, adapters, local audit — deployed inside your "
     "account.",
     "Regulated, residency-bound, high-volume, or air-gapped workloads."),
]

# --- ownership vs exposure (AGR-008, AGR-009, AGR-014, AGR-016) -----------
# Deliberately two separate columns: reduced operational burden never implies
# reduced data exposure.
EXPOSURE_ROWS = [
    ["", "Who operates it", "Where evaluated content goes"],
    ["Mode A\nRemote Evaluation",
     "You operate the gateway and hold your own model-provider credentials. We operate "
     "the Evaluation API, detection engines, and policy execution.",
     ("Prompts, responses, and any other evaluated content are transmitted to the "
      "hosted Evaluation API on every evaluated call. This is not a private-data or "
      "data-local model.", BLOCK)],
    ["Mode B\nGuarded Gateway",
     "We operate the gateway and the guardrail runtime, and hold or broker "
     "model-provider credentials. You operate the application.",
     ("Content passes through the provider-operated gateway — guardrails, routing, and "
      "model invocation all sit inside our boundary.", BLOCK)],
    ["Mode C\nCustomer-Managed Runtime",
     "You operate the runtime infrastructure, availability, networking, and local audit "
     "storage. We supply the runtime software and policy schemas.",
     ("Raw content stays inside your AWS account by default. Only telemetry you "
      "explicitly configure leaves — down to none at all.", ALLOW)],
]

RETENTION_CAVEAT = (
    "In the hosted runtime, raw prompts and responses are not persisted by "
    "default — decision metadata is. Any retention requires explicit tenant-level "
    "configuration; it is a default, not an absolute guarantee."
)

# --- enforcement ladder (AGR-011, ADR-0004) -------------------------------
# name, what the state does, colour
ENFORCEMENT_STATES = [
    ("Disabled", "Bound, not\nevaluating", LOG_ONLY),
    ("Observe", "Evaluates and\nrecords. Never\nacts on traffic.", ACCENT),
    ("Warn", "Surfaces a\nviolation without\nstopping it", WARN),
    ("Redact", "Transforms the\ncontent it\nmust remove", WARN),
    ("Block", "Stops the\nrequest or\nresponse", BLOCK),
]

# --- five-phase delivery (AGR-020, DEC-008) -------------------------------
# tag, name, objective, mode delivered
PHASES = [
    ("Phase 1", "Integration proof",
     "Prove policy and evaluation can be separated from LiteLLM and reused across "
     "gateways.", "Mode A"),
    ("Phase 2", "Internal multi-team platform",
     "Multiple internal teams onboard and operate distinct workloads.", "Mode A"),
    ("Phase 3", "Guarded gateway",
     "A fully hosted model and guardrail path for teams without a gateway.", "Mode B"),
    ("Phase 4", "Customer-managed runtime",
     "Local evaluation and content isolation inside the customer's account.", "Mode C"),
    ("Phase 5", "Advanced agent protection",
     "Guardrail coverage beyond prompt and response evaluation.", "All modes"),
]

# --- onboarding, i.e. what "import" means (AGR-019) -----------------------
ONBOARDING_STEPS = [
    "Register\nworkload", "Select\npolicy", "Generate\nintegration\nmanifest",
    "Install\nconnector", "Run in\nobserve mode", "Review\nevaluation\nresults",
    "Enable\nenforcement",
]

IMPORT_EXCLUSIONS = (
    "copying another team's LiteLLM database  ·  transferring model-provider "
    "credentials  ·  exposing gateway administration endpoints  ·  replacing "
    "model-routing configuration  ·  granting the guardrail provider "
    "administrative access to the team's gateway"
)

# --- who the pilot ask can land on (AGR-003, SCOPE, SUCCESS-CRITERIA §1.2) --
# The connector is gateway-specific even though the contract is not. Stated as
# a fact rather than left to "your gateway", which reads as gateway-agnostic and
# is not what the validation demonstration is specified against.
GATEWAY_PREREQ = (
    "The first connector is the LiteLLM connector, and the validation "
    "demonstration is specified as two independently deployed LiteLLM instances. "
    "Workloads on any other gateway integrate through the generic HTTP Evaluation "
    "API and SDK — the same contract, the same decisions — but that path is not "
    "what the two-instance criterion is written against."
)

# --- who sees observe-mode findings (TEAM-AND-OWNERSHIP §3.4 RACI) ---------
# The consuming team is accountable for the review; everyone else is consulted
# or informed. Deliberately ends on what is undefined, because a pilot team asks
# this and the source material does not answer the last part.
FINDINGS_VISIBILITY = (
    "The consuming team owns the review of its own observe-mode findings and is "
    "accountable for it; the guardrail team and security and governance are "
    "consulted, and platform operations are informed. Decision metadata also "
    "lands in the provider-operated audit archive, scoped per tenant. What is not "
    "defined anywhere yet: who else inside a consuming organization receives a "
    "per-workload findings report, and what tooling produces one."
)

# --- trust and enforcement postures (AGR-018, SECURITY-ARCHITECTURE §3.1) --
# name, topology, who can bypass it, appropriate when, colour, is-where-we-start
POSTURES = [
    ("Cooperative integration", "Application → team LiteLLM with connector → model",
     "The consuming team can bypass it — by disabling, removing, or misconfiguring "
     "the connector.",
     "Teams are trusted; guardrails are an engineering standard, not a compliance "
     "mandate; the objective is adoption and evaluation.", BLOCK, True),
    ("Platform-managed enforcement",
     "Application → platform-managed gateway → guardrails → model",
     "Only someone with access to platform-owned gateway infrastructure or its GitOps "
     "pipeline — not the application team.",
     "The platform owns gateway infrastructure; application teams must not be able to "
     "change mandatory guardrail settings.", WARN, False),
    ("Network/IAM-enforced gateway",
     "All model traffic forced through a controlled gateway; applications hold no "
     "direct provider credentials or connectivity",
     "No application-side actor, absent a platform-layer network or IAM "
     "misconfiguration.",
     "Bypass prevention is a formal requirement; compliance evidence is needed; "
     "direct provider access must be prohibited.", ALLOW, False),
]

# --- core domain model (AGR-017) ------------------------------------------
DOMAIN_ENTITIES = [
    ("Tenant", "An organization, business unit, or consuming team. Carries ownership, "
               "AWS account, data classification, allowed Regions, retention "
               "configuration, and deployment mode."),
    ("Workload", "An application, assistant, agent, gateway, or inference service "
                 "using guardrails. Carries owner, environment, risk classification, "
                 "gateway type, assigned policy, and enforcement mode."),
    ("Policy", "What must be evaluated and how violations are handled — versioned and "
               "immutable, independent of any gateway or provider."),
    ("Policy binding", "The association between a workload and a specific policy "
                       "version: observe or enforce mode, allowed overrides, effective "
                       "date, rollback version."),
    ("Evaluation", "A request to inspect content at a particular stage of an "
                   "interaction."),
    ("Decision", "The normalized result: action (allow / block / redact / log_only), "
                 "allowed status, reason codes, policy name and version, evaluator "
                 "metadata, correlation and audit identifiers, latency."),
]

# --- Phase 1–2 validation criteria (SUCCESS-CRITERIA §1.2, §1.3) ----------
MVP_CRITERIA = [
    ["ID", "Criterion", "What “met” looks like"],
    ["MVP-01", "Two separate LiteLLM instances consume the same policy.",
     "Both independently deployed, both bound to identical policy name and version, "
     "both verified to resolve the same policy content."],
    ["MVP-02", "Neither instance shares model-provider credentials with the platform.",
     "An audit of the platform's stored secrets shows no model-provider keys for "
     "either workload."],
    ["MVP-03", "Both receive consistent normalized decisions.",
     "Equivalent content returns the same decision schema: action, allowed status, "
     "reason codes, policy name and version, evaluator metadata, correlation id."],
    ["MVP-04", "One workload can run in observe mode.",
     "Workload A's binding is set to observe; evaluations execute and produce "
     "decisions, with no enforcement applied to the request path."],
    ["MVP-05", "Another workload can run in enforce mode.",
     "Workload B's binding is set to enforce or stricter; a violation triggers the "
     "corresponding action on the request path."],
    ["MVP-06", "A policy can be upgraded and rolled back.",
     "A new immutable version is published and bound, produces different documented "
     "behaviour, then is reverted with prior behaviour restored — both transitions "
     "captured in audit metadata."],
    ["MVP-07", "Raw-content persistence can be disabled.",
     "With retention configured off, evaluated prompts and responses appear in no "
     "runtime store, cache, or log after evaluation; decision metadata still persists."],
    ["MVP-08", "Evaluation failures follow a configured failure policy.",
     "With a detector or the Evaluation API forced to fail, the workload's configured "
     "behaviour executes exactly as configured and the path is captured in audit."],
    ["MVP-09", "The runtime packages for customer-managed deployment without changing "
               "the connector contract.",
     "The runtime builds and validates as a deployable artifact while the connector's "
     "invocation contract stays unchanged. Validates packaging feasibility only — "
     "Mode C itself is Phase 4."],
]

GATING_RULE = (
    "All nine criteria must be demonstrated together. A subset passing does not "
    "satisfy the demonstration, and neither does one deployment exercised twice."
)

# --- principal risks (EXECUTIVE-BRIEF §8, RISK-REGISTER) ------------------
RISK_ROWS = [
    ["Risk", "What it is", "What we do about it"],
    ["Connector bypass",
     "A team disables, removes, or misconfigures its own connector, so requests reach "
     "the provider unevaluated.",
     "Configuration validation and connector-health telemetry; platform-managed or "
     "network/IAM-enforced postures where bypass prevention is a formal requirement. "
     "Not eliminated under cooperative integration — accepted, not hidden."],
    ["False confidence",
     "A passing evaluation is mistaken for complete AI security, or a safety decision "
     "for a business authorization decision.",
     "Content evaluation and authorization stated as separate concerns; unsupported "
     "risk categories documented; never marketed as a complete security boundary."],
    ["Latency",
     "Remote input and output evaluation adds synchronous network and processing time "
     "to every evaluated call.",
     "Per-evaluator latency measured; only relevant evaluators applied; stage-specific "
     "timeouts defined as policy; Customer-Managed Runtime for latency-sensitive "
     "workloads. No numeric budget set yet — TBD."],
    ["Hosted data exposure",
     "Modes A and B transmit tenant content to a provider-operated service.",
     "Explicit per-mode data-flow documentation; no raw-content retention by default; "
     "Customer-Managed Runtime where content must stay in-account."],
    ["Policy fragmentation",
     "Workload-specific overrides accumulate until organizational consistency is gone.",
     "Override categories limited; a named owner required per exception; policy "
     "versions tracked."],
    ["Product over-expansion",
     "The platform absorbs gateway management, full governance, or agent authorization "
     "before the core hypothesis is validated.",
     "Five-phase sequencing with per-phase validation; standing scope exclusions stay "
     "excluded; adoption and effectiveness measured before each phase proceeds."],
]

NFR_GAP = (
    "No numeric latency budget or availability objective exists yet (ASM-011, "
    "ASM-012). Both are owed before a workload is promoted out of Observe — "
    "neither is estimated here."
)

# --- ownership groups (TEAM-AND-OWNERSHIP §1) -----------------------------
OWNERSHIP_ROWS = [
    ["Group", "What they own"],
    ["AI Guardrails team",
     "Product architecture, policy schemas, the evaluation contract, the guardrail "
     "runtime, the LiteLLM connector, Bedrock integration, compatibility, deployment "
     "artifacts, and documentation."],
    ["Platform operations",
     "The provider's own hosted infrastructure day to day — deployment, monitoring, "
     "capacity, networking, and production incident response."],
    ["Security and governance",
     "Organization-level policy requirements, risk classification, policy approval, "
     "exception criteria, retention and audit rules."],
    ["Consuming teams",
     "Application behaviour, connector installation in cooperative mode, workload "
     "registration accuracy, end-user authentication and business authorization, and "
     "review of observe-mode findings before accepting enforcement."],
]

# --- scope boundary (AGR-010, AGR-013, AGR-018, SCOPE.md) -----------------
IS_ITEMS = [
    "Content evaluation of prompts and responses against configured policies",
    "Enforcement actions — allow, block, redact, log only — applied per binding",
    "Policy versioning, bindings, and audit evidence across teams and gateways",
    "One evaluation contract, hosted or customer-managed",
]

IS_NOT_ITEMS = [
    "Not authorization|A decision that content appears safe says nothing about "
    "whether this user may perform this action. Authentication, business "
    "authorization, and approval workflows stay with your application.",
    "Not a bypass-proof boundary in cooperative mode|A connector a team installs "
    "itself can be disabled, removed, or misconfigured.",
    "Not cross-cloud|AWS is the primary and initial cloud. The policy contract is "
    "cloud-independent; the hosted runtime is not.",
    "Not a complete AI-governance or DLP platform|Also excluded: gateway "
    "replacement, a proprietary moderation model, general MCP security, "
    "compliance certification.",
]

# --- objection pairs (FAQ.md) ---------------------------------------------
OBJECTION_ROWS = [
    ["Question", "Answer"],
    ["Is Remote Evaluation data-local?",
     "No. In Mode A, prompts, responses, and any evaluated content are transmitted to "
     "the hosted Evaluation API. Workloads with residency requirements belong in "
     "Customer-Managed Runtime (AGR-009)."],
    ["Does my prompt data get stored?",
     "Not by default. The hosted runtime persists decision metadata, not raw prompts "
     "and responses; identifiers are hashed where possible. Retention requires "
     "explicit tenant-level configuration (AGR-014)."],
    ["Is the connector a hard security boundary?",
     "Not under cooperative integration — a team that installs its own connector can "
     "disable, remove, or misconfigure it. Platform-managed and network/IAM-enforced "
     "postures exist for workloads that need bypass resistance (AGR-018)."],
    ["Does it do user authorization?",
     "No. A decision that content appears safe does not determine whether this user "
     "may perform this action. Authentication, business authorization, tenant "
     "isolation, and approval workflows stay with the application (AGR-010)."],
    ["What happens on evaluation timeout?",
     "Whatever the workload's policy configures. Failure behaviour — timeout, detector "
     "unavailability, unresolved policy, control-plane outage — is a mandatory part of "
     "every policy, and the path taken is captured in the audit record (AGR-012)."],
    ["Who sees our observe-mode findings?",
     "The consuming team owns the review and is accountable for it; the guardrail "
     "team and security and governance are consulted. Decision metadata is stored "
     "in the provider-operated audit archive, scoped per tenant. No routing of a "
     "per-workload findings report to anyone else is defined, and no review "
     "tooling is specified (TEAM-AND-OWNERSHIP.md §3.4)."],
]

# The same pairs with trailing internal citations stripped, for decks that must
# not show internal identifiers on a slide. Matched by pattern rather than by an
# enumerated list, so adding a row above cannot leak an identifier by omission.
_CITATION = re.compile(
    r"\s*\((?:[A-Z]{2,}-\d+|[A-Z][A-Z0-9-]{3,}\.md(?:\s*§\s?[\d.]+)?)\)")

OBJECTION_ROWS_CLEAN = [
    [_CITATION.sub("", cell) for cell in row] for row in OBJECTION_ROWS
]
