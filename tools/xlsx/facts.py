"""Delivery data for the workbook.

Two kinds of content live here, and they are not the same thing:

DERIVED — milestones, completion criteria, dependencies, release gates, roles.
    Transcribed from solution-docs (MILESTONES.md, DEPENDENCIES.md,
    RELEASE-CRITERIA.md, TEAM-AND-OWNERSHIP.md §1). Changing one here without
    changing the register is a defect.

ASSUMED — the SIZE and ROLE SHARE on each work item.
    solution-docs contains no effort, duration, headcount or budget figures
    anywhere, and says so deliberately: "effort and duration figures remain TBD
    until supplied" (IMPLEMENTATION-STRATEGY.md §), "does not contain committed
    dates, quarters, durations, headcounts, or budgets" (MILESTONES.md §), with
    the estimation itself owed under ASM-006, ASM-007 and ASM-014.

    So every size below is a judgement made here, not a fact read from
    anywhere. The rules applied, stated so they can be argued with:

      * Size reflects the apparent scope of the checklist item as written —
        S a bounded change, M a component, L a subsystem or a contract surface,
        XL a subsystem the platform has never built before.
      * Role share follows the mandates in TEAM-AND-OWNERSHIP.md §1: build work
        to the AI Guardrails team, infrastructure and running to platform
        operations, approval and verification to security and governance,
        onboarding and validation participation to consuming teams.

    Both are editable in the workbook. Neither is evidence.
"""

# --- roles (TEAM-AND-OWNERSHIP.md §1) --------------------------------------
# key, display name, one-line mandate
ROLES = [
    ("AIG", "AI Guardrails team",
     "Architecture, policy schemas, evaluation contract, runtime, connectors, "
     "integrations, compatibility, telemetry, documentation, deployment artifacts"),
    ("OPS", "Platform operations",
     "The provider's own hosted infrastructure day to day — deployment, monitoring, "
     "capacity, networking, incident response"),
    ("SEC", "Security and governance",
     "Organization-level policy requirements; approves policy, risk, retention and "
     "audit rules"),
    ("CONS", "Consuming teams",
     "Applications and workloads bound to policies, including any internal gateway "
     "they operate themselves"),
]

ROLE_KEYS = [key for key, _, _ in ROLES]

# --- size vocabulary -------------------------------------------------------
# key, label, what it means, placeholder person-days
SIZES = [
    ("S", "Small", "A bounded change inside one component.", 5),
    ("M", "Medium", "One component, built and tested.", 15),
    ("L", "Large", "A subsystem, or a contract surface with published semantics.", 40),
    ("XL", "Extra large", "A subsystem of a kind this platform has not built before.",
     80),
]

# --- milestones (MILESTONES.md §2–§7) --------------------------------------
# key, name, phase, primary mode, objective, entry condition
MILESTONES = [
    ("M1", "Integration proof", "Phase 1", "Mode A — Remote Evaluation only",
     "Prove that guardrail policy and evaluation can be separated from LiteLLM and "
     "reused across multiple gateways.",
     "None — this is the delivery entry point."),
    ("M2", "Internal multi-team platform", "Phase 2", "Mode A — Remote Evaluation",
     "Allow multiple internal teams to onboard and operate distinct workloads.",
     "M1 complete: contract, policy format, Bedrock adapter, LiteLLM connector, "
     "observe mode, basic auditing and internal deployment all verified."),
    ("M3", "Guarded gateway", "Phase 3", "Mode B — Guarded Gateway",
     "Offer a fully hosted model and guardrail path for teams without their own "
     "gateway.",
     "M2 complete — the full Phase 1–2 validation demonstration passed. Phase 3 lies "
     "beyond that gate and must not be resourced ahead of it."),
    ("M4", "Customer-managed runtime", "Phase 4", "Mode C — Customer-Managed Runtime",
     "Support workloads that require local evaluation and content isolation.",
     "M2 complete, including the packaging validation. Does not require M3, though "
     "delivery sequencing treats Phase 4 as following Phase 3."),
    ("M5", "Advanced agent protection", "Phase 5",
     "All modes — new evaluation stages, not a new deployment mode",
     "Extend guardrail coverage beyond prompt and response evaluation.",
     "M4 complete and validated. A separate product expansion — must not be "
     "scheduled or resourced as part of M1–M4 delivery."),
]

# --- work items ------------------------------------------------------------
# Every row is one completion criterion from MILESTONES.md §3–§7, in document
# order. Fields: id, milestone, workstream, item, size, {role: share}.
# Shares must sum to 1.0 — verify.py enforces it.
WORK_ITEMS = [
    # ---- M1 — Integration proof --------------------------------------------
    ("WI-001", "M1", "Contract",
     "Evaluation contract defined and published — request structure, decision "
     "structure, reason codes, policy-version semantics, expressed independently of "
     "any gateway or vendor", "L", {"AIG": 1.0}),
    ("WI-002", "M1", "Contract",
     "Guardrail policy format defined as a versioned, vendor-independent schema",
     "L", {"AIG": 1.0}),
    ("WI-003", "M1", "Evaluation",
     "Amazon Bedrock Guardrails adapter, normalizing vendor responses into the "
     "platform's own decision structure before they reach any policy or connector",
     "L", {"AIG": 1.0}),
    ("WI-004", "M1", "Evaluation",
     "Basic deterministic evaluator behind the same evaluator-adapter pattern",
     "M", {"AIG": 1.0}),
    ("WI-005", "M1", "Connector",
     "LiteLLM pre-call connector, invoking input evaluation before model invocation",
     "L", {"AIG": 1.0}),
    ("WI-006", "M1", "Connector",
     "LiteLLM post-call connector, invoking output evaluation and applying safe "
     "output fallbacks", "L", {"AIG": 1.0}),
    ("WI-007", "M1", "Connector",
     "Conformance: connectors invoke the Evaluation API only — no policy logic, "
     "thresholds or detection rules embedded", "S", {"AIG": 0.8, "SEC": 0.2}),
    ("WI-008", "M1", "Runtime",
     "Observe mode implemented and made the default state for a new policy binding",
     "M", {"AIG": 1.0}),
    ("WI-009", "M1", "Audit",
     "Basic auditing: decision metadata logged for every evaluation — tenant, "
     "workload, policy version, stage, decision, reason codes, evaluator versions, "
     "timestamp, correlation identifier, retention flag", "L", {"AIG": 1.0}),
    ("WI-010", "M1", "Observability",
     "Evaluation latency measured per evaluation at P50, P95 and P99",
     "M", {"AIG": 0.6, "OPS": 0.4}),
    ("WI-011", "M1", "Infrastructure",
     "Runtime deployed internally within the minimal account model — one "
     "non-production and one production account", "L", {"OPS": 0.7, "AIG": 0.3}),
    ("WI-012", "M1", "Infrastructure",
     "TLS on all runtime communication between connectors, the Evaluation API and "
     "evaluation engines", "S", {"OPS": 0.6, "AIG": 0.2, "SEC": 0.2}),
    ("WI-013", "M1", "Validation",
     "Two independently deployed instances resolve decisions from the same published "
     "policy version, neither transmitting provider credentials — MVP-01, MVP-02, "
     "MVP-03", "M", {"AIG": 0.4, "CONS": 0.4, "OPS": 0.2}),
    ("WI-014", "M1", "Validation",
     "One of the two workloads runs in observe mode — MVP-04",
     "S", {"CONS": 0.6, "AIG": 0.4}),
    ("WI-015", "M1", "Validation",
     "The other workload runs in enforce mode and a violation triggers the "
     "corresponding action on the request path — MVP-05",
     "S", {"CONS": 0.5, "AIG": 0.3, "SEC": 0.2}),
    ("WI-016", "M1", "Validation",
     "Raw prompt and response persistence can be disabled and is verified absent from "
     "storage, caches and logs, with decision metadata still persisted — MVP-07",
     "M", {"SEC": 0.5, "AIG": 0.3, "OPS": 0.2}),
    ("WI-017", "M1", "Validation",
     "A forced detector or Evaluation API failure executes the configured failure "
     "behavior exactly as configured, captured in the audit record — MVP-08",
     "M", {"AIG": 0.7, "OPS": 0.3}),
    ("WI-018", "M1", "Packaging",
     "Runtime validated as buildable and deployable as a standalone artifact suitable "
     "for a future customer-managed environment, connector contract unchanged — "
     "MVP-09 (architectural)", "M", {"AIG": 0.7, "OPS": 0.3}),

    # ---- M2 — Internal multi-team platform ---------------------------------
    ("WI-019", "M2", "Control plane",
     "Tenant and workload registry exposing both domain entities as first-class, "
     "queryable records", "L", {"AIG": 1.0}),
    ("WI-020", "M2", "Control plane",
     "Policy bindings associating a workload with a specific policy version and an "
     "explicit enforcement-mode selection", "L", {"AIG": 1.0}),
    ("WI-021", "M2", "Security",
     "IAM-based authentication for platform consumers using least-privilege, "
     "short-lived credentials", "M", {"AIG": 0.5, "OPS": 0.3, "SEC": 0.2}),
    ("WI-022", "M2", "Security",
     "Per-tenant workload identities issued and enforced; none shared across tenants",
     "M", {"AIG": 0.6, "SEC": 0.4}),
    ("WI-023", "M2", "Control plane",
     "Quotas implemented and enforced per tenant and per workload",
     "M", {"AIG": 0.7, "OPS": 0.3}),
    ("WI-024", "M2", "Control plane",
     "Policy promotion: a new immutable version published and bound through a "
     "controlled, auditable step", "L", {"AIG": 1.0}),
    ("WI-025", "M2", "Control plane",
     "Policy rollback: a binding reverted to a prior immutable version with prior "
     "behavior restored, both transitions in audit metadata", "M", {"AIG": 1.0}),
    ("WI-026", "M2", "Runtime",
     "Promotion between ladder states is an explicit, auditable action — never an "
     "implicit side effect of another change", "M", {"AIG": 0.8, "SEC": 0.2}),
    ("WI-027", "M2", "Observability",
     "Service-level monitoring covering evaluation latency, timeout rate, decision "
     "distribution and policy-cache hit rate", "L", {"OPS": 0.6, "AIG": 0.4}),
    ("WI-028", "M2", "Control plane",
     "Basic administration workflow for tenant, workload and binding management — a "
     "minimal management API and version-controlled configuration are acceptable at "
     "this milestone", "M", {"AIG": 1.0}),
    ("WI-029", "M2", "Validation",
     "Two internal teams have independently registered a workload, bound it to the "
     "same shared policy version, and operate under distinct identities and distinct "
     "enforcement modes", "M", {"CONS": 0.5, "AIG": 0.3, "SEC": 0.2}),
    ("WI-030", "M2", "Validation",
     "A published policy version is upgraded for one workload and rolled back, both "
     "behaviors verified and both transitions in the audit trail — MVP-06",
     "S", {"AIG": 0.5, "CONS": 0.3, "SEC": 0.2}),

    # ---- M3 — Guarded gateway ----------------------------------------------
    ("WI-031", "M3", "Gateway",
     "A managed gateway operated end to end by the provider",
     "XL", {"AIG": 0.5, "OPS": 0.5}),
    ("WI-032", "M3", "Gateway",
     "Model-provider configuration implemented and operated by the provider, not the "
     "consuming team, for gateway traffic", "M", {"OPS": 0.7, "AIG": 0.3}),
    ("WI-033", "M3", "Gateway",
     "Per-tenant routing directing each tenant's inference traffic to the correct "
     "model configuration", "L", {"AIG": 0.6, "OPS": 0.4}),
    ("WI-034", "M3", "Gateway",
     "Cost and rate controls covering rate limits and usage quotas for the managed "
     "path", "L", {"AIG": 0.6, "OPS": 0.4}),
    ("WI-035", "M3", "Security",
     "Managed credential custody: the provider holds provider credentials for gateway "
     "traffic and the consuming team supplies none",
     "M", {"OPS": 0.5, "SEC": 0.3, "AIG": 0.2}),
    ("WI-036", "M3", "Operability",
     "A unified availability objective spanning model routing and guardrail "
     "evaluation is defined and documented — the objective, not a specific figure",
     "S", {"OPS": 0.6, "AIG": 0.4}),
    ("WI-037", "M3", "Validation",
     "At least one workload onboarded and operating end to end through the gateway "
     "with input guardrails, routing, invocation, output guardrails and reporting on "
     "one request path", "M", {"AIG": 0.4, "OPS": 0.3, "CONS": 0.3}),
    ("WI-038", "M3", "Governance",
     "Content-exposure documentation for the gateway mode published, separating "
     "operational ownership from data exposure", "S", {"SEC": 0.5, "AIG": 0.5}),
    ("WI-039", "M3", "Contract",
     "The evaluation contract, decision structure, reason codes and policy-version "
     "semantics verified unchanged for gateway traffic", "S", {"AIG": 1.0}),

    # ---- M4 — Customer-managed runtime -------------------------------------
    ("WI-040", "M4", "Packaging",
     "Runtime container image published as a supported deployment artifact",
     "M", {"AIG": 0.7, "OPS": 0.3}),
    ("WI-041", "M4", "Packaging",
     "Helm chart published for Kubernetes-based customer deployment",
     "M", {"AIG": 0.6, "OPS": 0.4}),
    ("WI-042", "M4", "Packaging",
     "ECS deployment template published", "M", {"AIG": 0.6, "OPS": 0.4}),
    ("WI-043", "M4", "Packaging",
     "Infrastructure module published for provisioning the customer-managed runtime",
     "L", {"OPS": 0.6, "AIG": 0.4}),
    ("WI-044", "M4", "Runtime",
     "Local policy cache enabling execution without a synchronous control-plane "
     "connection", "L", {"AIG": 1.0}),
    ("WI-045", "M4", "Runtime",
     "Policy synchronization implemented as an asynchronous process that never blocks "
     "the evaluation path", "L", {"AIG": 1.0}),
    ("WI-046", "M4", "Runtime",
     "Degraded-mode behavior for a stale local cache defined as an explicit part of "
     "the workload's failure behavior", "M", {"AIG": 0.8, "SEC": 0.2}),
    ("WI-047", "M4", "Runtime",
     "Customer-controlled telemetry spanning the full range down to a fully supported "
     "no-telemetry configuration", "L", {"AIG": 0.7, "SEC": 0.3}),
    ("WI-048", "M4", "Validation",
     "Raw content verified never to leave the customer-managed runtime unless the "
     "customer has explicitly configured that transmission",
     "M", {"SEC": 0.6, "AIG": 0.4}),
    ("WI-049", "M4", "Control plane",
     "Upgrade compatibility checks validating that a runtime upgrade stays compatible "
     "with the policy versions bound to that deployment", "M", {"AIG": 1.0}),
    ("WI-050", "M4", "Validation",
     "One customer-managed deployment validated end to end inside a "
     "customer-controlled account: local evaluation, cache behavior during a simulated "
     "outage, telemetry configuration, upgrade check",
     "L", {"AIG": 0.4, "CONS": 0.4, "OPS": 0.2}),
    ("WI-051", "M4", "Contract",
     "Connector invocation contract verified unchanged between hosted and "
     "customer-managed deployments — MVP-09 in shipped form", "S", {"AIG": 1.0}),

    # ---- M5 — Advanced agent protection ------------------------------------
    ("WI-052", "M5", "Evaluation",
     "Retrieval-content evaluation available as an evaluator behind the existing "
     "adapter pattern, without redesigning the evaluation contract",
     "L", {"AIG": 1.0}),
    ("WI-053", "M5", "Evaluation",
     "Indirect prompt-injection detection available as a new evaluator behind the "
     "same adapter pattern", "XL", {"AIG": 1.0}),
    ("WI-054", "M5", "Evaluation",
     "Tool-call validation using the tool_call evaluation stage", "L", {"AIG": 1.0}),
    ("WI-055", "M5", "Evaluation",
     "Tool-result evaluation using the tool_result evaluation stage",
     "L", {"AIG": 1.0}),
    ("WI-056", "M5", "Evaluation",
     "Memory-write controls using the memory_write evaluation stage, fitted to the "
     "existing Evaluation and Decision entities", "L", {"AIG": 1.0}),
    ("WI-057", "M5", "Control plane",
     "Human approval workflows for the require_approval decision action, which is "
     "reserved in the schema but has no owning workflow before this milestone",
     "XL", {"AIG": 0.7, "SEC": 0.3}),
    ("WI-058", "M5", "Evaluation",
     "Agent delegation restrictions", "L", {"AIG": 1.0}),
    ("WI-059", "M5", "Integration",
     "MCP integration scoped narrowly to the platform's own tool-call evaluation, "
     "explicitly distinguished from a general-purpose MCP security platform",
     "L", {"AIG": 0.8, "SEC": 0.2}),
    ("WI-060", "M5", "Integration",
     "Advanced authorization integration, without redefining a guardrail decision as "
     "a business-authorization decision", "L", {"AIG": 0.7, "SEC": 0.3}),
]

# --- external and internal dependencies (DEPENDENCIES.md §2) ---------------
# dependency, type, needed for, owner, status, why it gates
DEPENDENCIES = [
    ("AWS account provisioning — non-production and production", "Internal",
     "Phase 1 kickoff — all infrastructure work", "Platform operations lead",
     "Unconfirmed",
     "Every other technical dependency is provisioned into one of these two "
     "accounts. It is the first link in the delivery chain, not one dependency "
     "among many."),
    ("Amazon Bedrock Guardrails access in target Regions", "External",
     "Phase 1 — Bedrock Guardrails adapter", "Architecture lead", "Unconfirmed",
     "The first managed evaluation engine cannot be integrated or exercised without "
     "access in the Regions the runtime will serve."),
    ("Stable evaluation contract before connector work", "Technical",
     "Phase 1 — LiteLLM connector", "Technical lead", "Not started",
     "Building the connector against a moving contract forces a matching connector "
     "revision on every change — and makes decision consistency across two "
     "independent instances unverifiable. Freeze and version the contract before "
     "connector work starts, not after."),
    ("Consuming-team gateway availability and willingness to install the connector",
     "External", "Phase 1–2 — validation demonstration",
     "Integration lead / Delivery lead", "Unconfirmed",
     "The demonstration needs two organizationally independent instances. Both their "
     "existence and that team's cooperation are outside the delivery team's control."),
    ("Security review sign-off", "Internal",
     "Any workload's observe-to-enforce promotion; any external onboarding",
     "Security and governance lead", "Not yet exercised",
     "No workload leaves observe without it, and the criteria that would satisfy it "
     "are themselves undefined today."),
    ("KMS and Secrets Manager setup", "Technical",
     "Phase 1 — encryption at rest and in transit, runtime credential storage",
     "Platform operations lead / Technical lead", "Not started",
     "The runtime, policy storage and audit pipeline cannot be deployed with "
     "encryption and credential handling in place without them. Part of Phase 1 "
     "environment setup, not a later hardening pass."),
    ("CI with dependency and container scanning", "Technical",
     "Every build from Phase 1 onward — release-blocking", "Technical lead",
     "Not started",
     "Required on every build and re-verified for each new deployment artifact, "
     "including the container, chart and template Phase 4 introduces. This one never "
     "resolves — it must stay continuously satisfied."),
]

# --- release gates (RELEASE-CRITERIA.md) -----------------------------------
# id, name, gate, owner, milestone
RELEASE_GATES = [
    ("RG-MVP-01", "Two instances, one policy",
     "Two independently deployed instances, each with its own connector "
     "installation, bound to the identical policy name and version and verified to "
     "resolve the same policy content.", "Delivery lead", "M1"),
    ("RG-MVP-02", "No shared provider credentials",
     "Neither instance's model-provider credentials are held, stored or transmitted "
     "to the guardrail platform; verified against the platform's own stored secrets.",
     "Security and governance lead", "M1"),
    ("RG-MVP-03", "Consistent normalized decisions",
     "Equivalent content submitted through both instances returns the identical "
     "normalized decision schema: action, allowed status, reason codes, policy name "
     "and version, evaluator metadata, correlation identifier.", "Delivery lead", "M1"),
    ("RG-MVP-04", "Observe-mode workload",
     "One workload's binding is set to observe; evaluations execute and produce "
     "decisions with no enforcement applied on the request path.", "Delivery lead",
     "M1"),
    ("RG-MVP-05", "Enforce-mode workload",
     "The other workload's binding is set to enforce or stricter; a violation "
     "triggers the corresponding action on the request path.", "Delivery lead", "M1"),
    ("RG-MVP-06", "Policy upgrade and rollback",
     "A new immutable version is published and bound, producing a documented "
     "behavior change; the binding is then reverted with prior behavior restored, "
     "both transitions captured in audit metadata.", "Delivery lead", "M2"),
    ("RG-MVP-07", "Raw-content persistence disablement",
     "With retention configured off, no raw content is found in any runtime store, "
     "cache or log after evaluation; decision metadata is still persisted.",
     "Security and governance lead", "M1"),
    ("RG-MVP-08", "Configured failure policy honored",
     "With a detector or the Evaluation API forced into a defined failure condition, "
     "the configured behavior executes exactly as configured and the path taken is "
     "captured in audit.", "Delivery lead", "M1"),
    ("RG-MVP-09", "Repackageable for customer-managed deployment",
     "The runtime packages as a deployable artifact suitable for a customer-managed "
     "environment while the connector's invocation contract stays unchanged.",
     "Delivery lead", "M1"),
    ("RG-NFR-01", "Availability objective defined and monitored",
     "A documented availability objective for the hosted Evaluation API exists and "
     "has been published to pilot workload owners. The figure itself is TBD — this "
     "gate requires the objective to exist, not a number to be met.",
     "Platform operations lead", "M2"),
    ("RG-NFR-02", "Evaluation latency measured and accepted",
     "End-to-end evaluation latency is measured continuously per stage and per "
     "deployment mode, and accepted by the pilot workload owners.",
     "Platform operations lead", "M2"),
    ("RG-NFR-03", "Audit delivery measured, failure behavior defined",
     "The proportion of evaluations producing a delivered audit record is measured, "
     "and the behavior on delivery failure is defined in policy.",
     "Security and governance lead (with Platform operations lead)", "M2"),
    ("RG-SEC-01", "TLS coverage",
     "Every runtime communication path is TLS-protected — application to connector, "
     "connector to Evaluation API, runtime to evaluation engines, runtime to control "
     "plane.", "Security and governance lead", "M1"),
    ("RG-SEC-02", "Identity-based, least-privilege, short-lived authentication",
     "Every platform consumer authenticates with least-privilege, short-lived "
     "credentials rather than long-lived static secrets.",
     "Security and governance lead", "M2"),
    ("RG-SEC-03", "No secrets in telemetry",
     "No telemetry payload emitted by the runtime carries secret material — "
     "operational metrics, connector-health signals, and customer-selected telemetry "
     "alike.", "Security and governance lead", "M1"),
    ("RG-SEC-04", "Container and dependency scan clean",
     "Every runtime image and declared dependency in the release is scanned for "
     "known vulnerabilities before release.", "Security and governance lead", "M1"),
]

# What the workbook must not imply, restated on the Read me sheet.
STANDING_CAVEATS = [
    "No calendar dates, quarters or durations appear anywhere in this workbook. "
    "Milestone order is dependency sequencing, not scheduling — the source material "
    "fixes the order and says nothing about when.",
    "Nothing is built or piloted. Every work item is planned work, not work in "
    "progress, and no percentage-complete column exists because there is nothing to "
    "report against.",
    "The effort model is an assumption you own, not an estimate the programme has "
    "produced. The estimation over the full feature set is owed by delivery and "
    "product leads and has not been supplied.",
    "Phases 3, 4 and 5 must not be resourced ahead of the Phase 1–2 validation gate. "
    "Their totals appear here because the feature set is defined and estimated over "
    "all five phases — not because the work may start.",
]
