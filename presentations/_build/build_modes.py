"""Per-mode developer integration decks: Mode A, Mode B, Mode C."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deckkit import *  # noqa

OUTDIR = "/home/veglez/own/ai/guardrails/presentations"
os.makedirs(OUTDIR, exist_ok=True)

ILLUSTRATIVE = ("Code on the following slides is ILLUSTRATIVE. The evaluation contract's shape "
                "(stages, decision fields, actions, reason codes) is fixed by "
                "50-architecture/INTEGRATION-ARCHITECTURE.md §3; concrete endpoint paths, field names "
                "and package names are not yet ratified and will be pinned by the published API spec.")


class Deck:
    def __init__(self, kicker, accent):
        self.prs = new_deck()
        self.kicker = kicker
        self.accent = accent
        self.n = 0

    def pg(self):
        self.n += 1
        return self.n

    def slide(self, title, sub=None):
        s = blank(self.prs)
        chrome(s, self.prs, self.kicker, self.pg(), accent=self.accent)
        y = head(s, title, sub)
        return s, y

    def save(self, name):
        p = os.path.join(OUTDIR, name)
        self.prs.save(p)
        print("wrote", p, "-", self.n + 1, "slides")


# ==================================================================== MODE A
d = Deck("Mode A · Remote Evaluation", ACCENT)
title_slide(d.prs, "Developer integration guide — 1 of 3",
            "Mode A — Remote Evaluation",
            "You keep your gateway. You install a cooperative connector, or call the hosted "
            "Evaluation API directly. This is the default adoption path.",
            "Phases 1–2 · available at GA   ·   Companion to AI-Guardrails-Internal-Proposal.pptx\n" + ILLUSTRATIVE)
d.n += 0

s, y = d.slide("When Mode A is the right choice", "And what each side signs up for.")
cards(s, y, [
    {"tag": "CHOOSE MODE A WHEN", "title": "You already run a gateway", "color": ACCENT,
     "lines": ["You operate LiteLLM, or any HTTP-capable stack",
               "You want policy consistency without changing your request path",
               "You can accept evaluated content reaching a hosted API",
               "You want the smallest integration footprint"]},
    {"tag": "PROVIDER OPERATES", "title": "The policy brain", "color": ACCENT2,
     "lines": ["Hosted Evaluation API", "Policy store, versioning, rollback",
               "Workload and binding registry", "Normalized decisions + audit metadata",
               "Control plane and dashboards"]},
    {"tag": "YOU OPERATE", "title": "The request-path plumbing", "color": GREEN,
     "lines": ["Install the connector or call the SDK/API", "Register your workload, bind a policy",
               "Configure your failure behavior", "Run observe, review, then promote",
               "Keep your gateway, credentials and routing"]},
], h=Inches(3.0))
callout(s, MARGIN, y + Inches(3.3), CONTENT_W,
        "Mode A is not a data-local model. Prompts, responses and any other evaluated content reach the hosted "
        "Evaluation API regardless of who runs the gateway (AGR-009). If your requirement is 'content must not "
        "leave our account', you want Mode C, not Mode A.", color=AMBER, h=Inches(1.0))

s, y = d.slide("Before you start", "Five prerequisites. Four of them are one-off per team.")
table(s, MARGIN, y, CONTENT_W,
      ["#", "Prerequisite", "Who provides it"],
      [["1", "A tenant on the AI Guardrails control plane", "Product operators"],
       ["2", "An IAM role / short-lived credential able to call the Evaluation API", "Your cloud team + platform"],
       ["3", "A workload record: id, owner, environment, risk class, gateway type, expected throughput", "You"],
       ["4", "A policy to bind — a curated pack, or one your security team authored", "Security & governance"],
       ["5", "An agreed failure behavior for timeouts and detector outages", "You + security & governance"]],
      widths=[0.7, 9.0, 3.6], size=12, row_h=Inches(0.5))
callout(s, MARGIN, y + Inches(3.35), CONTENT_W,
        "Never required: copying your LiteLLM database · handing over model-provider credentials · exposing "
        "gateway admin endpoints · replacing your model routing · granting the provider admin access to your gateway.",
        color=GREEN, label="WHAT WE WILL NEVER ASK FOR", h=Inches(0.9))

s, y = d.slide("Step 1 — Register the workload and bind a policy",
               "One-off, per workload. New bindings always start in observe mode.")
code_block(s, MARGIN, y, CONTENT_W, Inches(4.1), """
# Register the workload (control plane)
$ curl -X POST https://guardrails.internal/v1/workloads \\
    -H "Authorization: Bearer $GR_TOKEN" -H "Content-Type: application/json" \\
    -d '{
      "workload_id":   "checkout-assistant",
      "tenant_id":     "payments",
      "owner":         "team-payments",
      "environment":   "prod",
      "risk_class":    "high",
      "gateway_type":  "litellm",
      "deployment_mode": "remote_evaluation"
    }'

# Bind it to an immutable policy version — starts in OBSERVE
$ curl -X POST https://guardrails.internal/v1/bindings \\
    -H "Authorization: Bearer $GR_TOKEN" -H "Content-Type: application/json" \\
    -d '{
      "workload_id":     "checkout-assistant",
      "policy":          "corp-baseline",
      "policy_version":  "3",
      "mode":            "observe",
      "rollback_version":"2"
    }'
""", size=11.5, label="control plane · one-off per workload")

s, y = d.slide("Step 2, path A — the LiteLLM connector",
               "The paved path. Configuration only: no application code changes.")
code_block(s, MARGIN, y, CONTENT_W, Inches(3.72), """
# litellm_config.yaml — pre-call and post-call callbacks
litellm_settings:
  callbacks: ["guardrails_connector.PreCall", "guardrails_connector.PostCall"]

guardrails:
  endpoint:     https://guardrails.internal/v1/evaluate
  workload_id:  checkout-assistant
  tenant_id:    payments
  auth:         iam                 # short-lived credentials, no static secret
  timeout_ms:   400                 # per-stage budget; failure behavior comes from policy
  stages:       [input, output]
  propagate_correlation_id: true    # reuses x-request-id when the caller supplies one

# install and restart the gateway
$ pip install ai-guardrails-litellm
$ litellm --config litellm_config.yaml
""", size=11.5, label="litellm_config.yaml")
callout(s, MARGIN, y + Inches(3.92), CONTENT_W,
        "The connector maps requests, invokes evaluation, enforces block/redact, propagates the correlation id, "
        "applies your timeout policy and emits health telemetry. It never contains policy logic, thresholds or "
        "detection rules — those live in the policy (AGR-007).", color=ACCENT, label="WHAT THE CONNECTOR DOES", h=Inches(0.85))

s, y = d.slide("Step 2, path B — the Python SDK",
               "For applications not fronted by a supported gateway.")
code_block(s, MARGIN, y, CONTENT_W, Inches(4.5), """
from ai_guardrails import Guardrails, Blocked

gr = Guardrails(endpoint="https://guardrails.internal",
                workload_id="checkout-assistant",
                tenant_id="payments",
                timeout_ms=400)

def handle(user_prompt: str, correlation_id: str) -> str:
    pre = gr.evaluate(stage="input", content=user_prompt,
                      correlation_id=correlation_id)
    if pre.action == "block":
        return safe_rejection(pre.reason_codes)      # model is never invoked
    prompt = pre.transformed_content or user_prompt  # 'redact' rewrites content

    answer = call_model(prompt)                      # your existing model call

    post = gr.evaluate(stage="output", content=answer,
                       correlation_id=correlation_id)
    if post.action == "block":
        return safe_fallback()
    return post.transformed_content or answer
""", size=11.5, label="python · pip install ai-guardrails")

s, y = d.slide("Step 2, path C — TypeScript SDK and raw HTTP",
               "Same contract, same decisions. No stack is locked out.")
code_block(s, MARGIN, y, Inches(6.55), Inches(4.4), """
import { Guardrails } from "@ai-guardrails/sdk";

const gr = new Guardrails({
  endpoint: "https://guardrails.internal",
  workloadId: "checkout-assistant",
  tenantId: "payments",
  timeoutMs: 400,
});

const pre = await gr.evaluate({
  stage: "input",
  content: userPrompt,
  correlationId,
});

if (pre.action === "block") {
  return safeRejection(pre.reasonCodes);
}
const prompt = pre.transformedContent ?? userPrompt;
""", size=11, label="typescript")
code_block(s, MARGIN + Inches(6.85), y, Inches(5.26), Inches(4.4), """
# raw HTTP — any stack
$ curl -X POST \\
  https://guardrails.internal/v1/evaluate \\
  -H "Authorization: Bearer $GR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "tenant_id":  "payments",
    "workload_id":"checkout-assistant",
    "stage":      "input",
    "content": {
      "text": "…user prompt…"
    },
    "correlation_id": "req-8f21c0"
  }'
""", size=11, label="curl")

s, y = d.slide("The decision you get back", "Identical in every mode and from every client.")
code_block(s, MARGIN, y, Inches(7.4), Inches(4.3), """
{
  "action":          "redact",
  "allowed":          true,
  "reason_codes":   ["pii.email_address",
                     "pii.payment_card"],
  "policy":          "corp-baseline",
  "policy_version":  "3",
  "evaluators": [
    {"name": "bedrock-guardrails", "version": "2026-05"},
    {"name": "org-deterministic",  "version": "1.4"}
  ],
  "transformed_content": {"text": "…redacted…"},
  "correlation_id":  "req-8f21c0",
  "audit_id":        "aud-01J9…",
  "latency_ms":       38
}
""", size=11.5, label="decision")
bullets(s, MARGIN + Inches(7.75), y, Inches(4.95), [
    ("action — allow · block · redact · log_only", 0),
    ("Four actions is the whole MVP surface. rewrite, discard, abstain and "
     "require_approval are reserved for later phases.", 1),
    ("reason_codes are stable across engines", 0),
    ("A Bedrock detection and a deterministic-rule detection of the same category "
     "surface the same code.", 1),
    ("policy_version names the exact immutable version", 0),
    ("latency_ms is a first-class field", 0),
    ("It rolls into platform P50/P95/P99 and into your observe-mode review.", 1),
], size=12.5)

s, y = d.slide("Failure behavior is part of the policy — not your catch block",
               "You choose it up front; the connector applies it. There is no undocumented fallback (AGR-012).")
code_block(s, MARGIN, y, Inches(6.55), Inches(3.5), """
# policy excerpt — failure behavior
failure_behavior:
  evaluation_timeout:   allow_and_log
  detector_unavailable: degrade_and_log
  policy_unresolvable:  block
  audit_delivery_failed: allow_and_log
  unsafe_transform:     safe_fallback

timeouts:
  input_ms:  400
  output_ms: 600
""", size=11.5, label="policy (yaml)")
table(s, MARGIN + Inches(6.85), y, Inches(5.26),
      ["Condition", "Options"],
      [["Evaluation API times out", "allow_and_log · block · cached decision"],
       ["A detector is unavailable", "degrade to remaining detectors · block"],
       ["Policy cannot be resolved", "block · require manual approval"],
       ["Audit delivery fails", "allow + log locally · block"],
       ["Response cannot be transformed", "safe fallback · block"]],
      widths=[4.2, 6.0], size=10.5, row_h=Inches(0.62))
callout(s, MARGIN, y + Inches(3.75), CONTENT_W,
        "Every failure event is captured in the audit record: which failure mode occurred and which path was taken. "
        "That is what makes UC-007 demonstrable and what feeds the timeout-rate reliability metric.",
        color=ACCENT, label="AUDITED", h=Inches(0.82))

s, y = d.slide("Step 3 — observe, review, then promote",
               "No binding ever starts blocking. Promotion is explicit and audited.")
steps = [("OBSERVE", "Evaluations run, decisions logged, nothing blocked", ACCENT),
         ("REVIEW", "False-positive rate, latency, decision distribution", AMBER),
         ("WARN / REDACT", "Partial enforcement on the request path", AMBER),
         ("BLOCK", "Full enforcement, accepted by the workload owner", GREEN)]
w = (CONTENT_W - Inches(0.25) * 3) / 4
for i, (t, sub, col) in enumerate(steps):
    x = MARGIN + (w + Inches(0.25)) * i
    rect(s, x, y, w, Inches(1.5), PANEL, radius=0.05)
    rect(s, x, y, w, Inches(0.06), col)
    box(s, x + Inches(0.18), y + Inches(0.26), w - Inches(0.36), Inches(0.3), t, size=13, color=col, bold=True)
    box(s, x + Inches(0.18), y + Inches(0.66), w - Inches(0.36), Inches(0.7), sub, size=11, color=SLATE, line=1.15)
code_block(s, MARGIN, y + Inches(1.8), CONTENT_W, Inches(1.55), """
# promote the binding — explicit, auditable, reversible
$ curl -X PATCH https://guardrails.internal/v1/bindings/checkout-assistant \\
    -H "Authorization: Bearer $GR_TOKEN" -d '{"mode": "block"}'

# something regressed? revert to the recorded rollback version, immediately
$ curl -X POST https://guardrails.internal/v1/bindings/checkout-assistant/rollback
""", size=11.5, label="promotion / rollback")
callout(s, MARGIN, y + Inches(3.55), CONTENT_W,
        "Rollback repoints the binding at a prior immutable version. It never requires a connector reinstall, an "
        "application redeploy, or re-authoring a policy.", color=GREEN, label="ROLLBACK IS A BINDING OPERATION", h=Inches(0.82))

s, y = d.slide("Your integration checklist", "Done means all of these, not most of them.")
bullets(s, MARGIN, y, Inches(6.4), [
    ("Workload registered with accurate owner, environment and risk class", 0),
    ("Binding created against a specific immutable policy version", 0),
    ("Connector or SDK installed; correlation id propagates input → model → output", 0),
    ("Per-stage timeouts set and failure behavior agreed with security", 0),
    ("Observe-mode findings reviewed: false positives, latency, decision distribution", 0),
    ("Promotion to enforce performed explicitly, with the rollback version recorded", 0),
    ("Connector-health telemetry visible to your on-call", 0),
], size=13)
callout(s, MARGIN + Inches(6.8), y, Inches(5.9),
        "A cooperative connector can be disabled, removed or misconfigured by whoever operates the gateway. "
        "Connector-health telemetry is a signal, never proof, of enforcement. Mode A's 'mandatory' is a governance "
        "guarantee, not a physical one — bypass-proof enforcement means Mode B or a network/IAM-enforced posture "
        "(AGR-018).", color=RED, label="SAY THIS OUT LOUD IN EVERY REVIEW", h=Inches(2.2))
callout(s, MARGIN + Inches(6.8), y + Inches(2.45), Inches(5.9),
        "Guardrails are not authorization. An 'allow' decision never substitutes for your own authentication, "
        "business authorization, tenant isolation or approval workflows (AGR-010).",
        color=RED, label="AND THIS ONE", h=Inches(1.35))
d.save("Mode-A-Remote-Evaluation.pptx")


# ==================================================================== MODE B
d = Deck("Mode B · Guarded Gateway", ACCENT2)
title_slide(d.prs, "Developer integration guide — 2 of 3",
            "Mode B — Guarded Gateway",
            "You have no gateway and do not want one. Point your application at a "
            "provider-operated, OpenAI-compatible endpoint that already has the guardrails inside it.",
            "Phase 3 — target state, NOT available today   ·   Companion to AI-Guardrails-Internal-Proposal.pptx\n" + ILLUSTRATIVE)

s, y = d.slide("When Mode B is the right choice", "It answers exactly one objection: “we have no gateway.”")
cards(s, y, [
    {"tag": "CHOOSE MODE B WHEN", "title": "You want one managed path", "color": ACCENT2,
     "lines": ["You do not operate LiteLLM or any gateway",
               "You want a managed OpenAI-compatible endpoint",
               "You want centrally managed model access",
               "You accept the provider sitting in the full inference path"]},
    {"tag": "PROVIDER OPERATES", "title": "Everything on the path", "color": ACCENT,
     "lines": ["Gateway availability and routing", "Model-provider credentials",
               "Input guardrails → model → output guardrails", "Retries, fallbacks, rate limits, quotas",
               "Audit and usage reporting"]},
    {"tag": "YOU OPERATE", "title": "Your application", "color": GREEN,
     "lines": ["Application behavior", "Workload-specific configuration",
               "Policy selection where permitted", "End-user authentication",
               "Business authorization and approval flows"]},
], h=Inches(3.05))
callout(s, MARGIN, y + Inches(3.35), CONTENT_W,
        "Mode B is the strongest centralized control and also the largest operational, security and compliance "
        "scope taken on by the provider. It is a Phase 3 deliverable: nothing here is available at the Phase 1–2 launch.",
        color=AMBER, h=Inches(0.9))

s, y = d.slide("The whole integration is a base URL",
               "Registration and policy binding are identical to Mode A. Only the request path changes.")
code_block(s, MARGIN, y, Inches(6.55), Inches(4.3), """
from openai import OpenAI

client = OpenAI(
    base_url="https://gateway.guardrails.internal/v1",
    api_key=os.environ["GR_GATEWAY_KEY"],   # NOT a model-provider key
)

resp = client.chat.completions.create(
    model="corp-default",                   # provider-routed alias
    messages=[{"role": "user", "content": prompt}],
    extra_headers={
        "x-gr-workload-id":   "support-bot",
        "x-gr-correlation-id": correlation_id,
    },
)

print(resp.choices[0].message.content)
""", size=11.5, label="python · openai sdk, unchanged")
code_block(s, MARGIN + Inches(6.85), y, Inches(5.26), Inches(4.3), """
// typescript
const client = new OpenAI({
  baseURL: "https://gateway.guardrails" +
           ".internal/v1",
  apiKey: process.env.GR_GATEWAY_KEY,
});

// curl
$ curl https://gateway.guardrails.internal\\
    /v1/chat/completions \\
  -H "Authorization: Bearer $GR_GATEWAY_KEY" \\
  -H "x-gr-workload-id: support-bot" \\
  -d '{"model":"corp-default",
       "messages":[{"role":"user",
                    "content":"…"}]}'
""", size=11, label="typescript / curl")

s, y = d.slide("What comes back", "A standard completion, plus the guardrail decision as metadata.")
code_block(s, MARGIN, y, Inches(7.3), Inches(3.9), """
HTTP/1.1 200 OK
x-gr-decision-input:  allow
x-gr-decision-output: redact
x-gr-reason-codes:    pii.payment_card
x-gr-policy:          corp-baseline@3
x-gr-correlation-id:  req-8f21c0
x-gr-audit-id:        aud-01J9…
x-gr-latency-ms:      41

{ "choices": [ { "message": {
      "role": "assistant",
      "content": "…redacted response…" } } ] }
""", size=11.5, label="response")
bullets(s, MARGIN + Inches(7.65), y, Inches(5.05), [
    ("A blocked request returns a safe rejection, not a model error", 0),
    ("Your error handling distinguishes 'policy blocked' from 'model failed' via the "
     "decision headers and reason codes.", 1),
    ("Same reason codes as Mode A and Mode C", 0),
    ("Same audit record shape", 0),
    ("Your model choice is bounded", 0),
    ("You can select only from what the provider has integrated and routed.", 1),
], size=12.5)

s, y = d.slide("What does not change when you move A → B", "This is the point of the one-contract rule.")
table(s, MARGIN, y, CONTENT_W,
      ["Unchanged", "Changed"],
      [["Policy format and policy versions", "Who operates the gateway — the provider, not you"],
       ["The evaluation contract and decision structure", "Who holds model-provider credentials — the provider"],
       ["Reason codes and audit-record shape", "Who owns retries, fallbacks, capacity and model cost"],
       ["Observe → warn → redact → block progression", "Who is accountable for inference-path availability"],
       ["Rollback semantics on a binding", "Your model-selection freedom — bounded to routed models"],
       ["The guardrails-are-not-authorization boundary", "Content exposure framing — the provider is now on the whole path"]],
      widths=[6.6, 7.0], size=11.5, row_h=Inches(0.52))
callout(s, MARGIN, y + Inches(3.75), CONTENT_W,
        "A team migrating between Mode A, B and C changes only which client calls the Evaluation API. "
        "Policies, decisions, reason codes and audit records are unaffected (AGR-006).",
        color=GREEN, label="PORTABILITY", h=Inches(0.82))

s, y = d.slide("Before you plan on Mode B", "Status and caveats, stated plainly.")
bullets(s, MARGIN, y, CONTENT_W, [
    ("Phase 3 deliverable. Not available at the Phase 1–2 launch, and not part of the "
     "Phase 1–2 validation demonstration — which exercises Mode A only.", 0, RED),
    ("The provider participates in the complete inference path. That must be represented "
     "accurately in any security review, never understated.", 0),
    ("Content exposure documentation for Mode B is a named M3 completion criterion — "
     "operational ownership and data exposure are tracked as separate concerns (AGR-008).", 0),
    ("Availability: a unified objective spanning model routing and guardrail evaluation is "
     "defined at M3. Specific SLA figures remain TBD until an accountable owner sets them.", 0),
    ("Guardrail decisions are still not authorization decisions. Mode B changes who runs the "
     "path, not what a decision means (AGR-010).", 0),
    ("If the driver is 'mandatory controls must be bypass-proof', Mode B or a network/IAM-enforced "
     "posture is the honest answer — never a Mode A promise.", 0),
], size=13)
d.save("Mode-B-Guarded-Gateway.pptx")


# ==================================================================== MODE C
d = Deck("Mode C · Customer-Managed Runtime", GREEN)
title_slide(d.prs, "Developer integration guide — 3 of 3",
            "Mode C — Customer-Managed Runtime",
            "The guardrail runtime deploys inside your own AWS account. Raw prompts and "
            "responses stay in-account by default, and evaluation keeps working with no control-plane connection.",
            "Phase 4 — target state, NOT available today   ·   Companion to AI-Guardrails-Internal-Proposal.pptx\n" + ILLUSTRATIVE)

s, y = d.slide("When Mode C is the right choice", "It answers exactly one objection: “content can't leave our account.”")
cards(s, y, [
    {"tag": "CHOOSE MODE C WHEN", "title": "Content must stay put", "color": GREEN,
     "lines": ["Regulated workloads", "Data-residency requirements",
               "Very high evaluation volume", "Air-gapped or heavily restricted environments"]},
    {"tag": "YOU OPERATE", "title": "The runtime and its account", "color": ACCENT,
     "lines": ["Runtime infrastructure and availability", "Model credentials and routing",
               "Local audit storage", "Runtime upgrades (unless managed support is purchased)"]},
    {"tag": "PROVIDER OPERATES", "title": "The software and the schemas", "color": ACCENT2,
     "lines": ["Runtime software and deployment artifacts", "Policy schemas and compatibility",
               "Control plane for policy authoring", "Optional hosted policy management"]},
], h=Inches(3.0))
callout(s, MARGIN, y + Inches(3.3), CONTENT_W,
        "Raw content never leaves your account unless you explicitly configure that transmission (CON-013). "
        "The telemetry channel back to the control plane is yours to configure — down to, and including, no telemetry at all.",
        color=GREEN, h=Inches(0.9))

s, y = d.slide("Step 1 — Deploy the runtime into your account",
               "Four supported artifacts. Pick the one that matches your platform.")
code_block(s, MARGIN, y, Inches(6.55), Inches(4.2), """
# Kubernetes — Helm
$ helm repo add ai-guardrails \\
    https://charts.guardrails.internal
$ helm install guardrails \\
    ai-guardrails/guardrails-runtime \\
    -n guardrails --create-namespace \\
    -f values.yaml

# values.yaml
tenantId: payments
policyCache:
  enabled: true
  syncIntervalSeconds: 300
  staleAfterSeconds: 86400
telemetry:
  mode: aggregate        # none | health | aggregate
bedrock:
  region: eu-west-1
""", size=11, label="helm")
code_block(s, MARGIN + Inches(6.85), y, Inches(5.26), Inches(4.2), """
# ECS / Terraform — IaC module
module "guardrails_runtime" {
  source  = "ai-guardrails/runtime/aws"
  version = "1.0.0"

  tenant_id      = "payments"
  vpc_id         = var.vpc_id
  subnet_ids     = var.private_subnet_ids
  kms_key_arn    = aws_kms_key.gr.arn
  telemetry_mode = "none"

  policy_cache = {
    sync_interval_seconds = 300
    stale_after_seconds   = 86400
  }
}
""", size=11, label="terraform")

s, y = d.slide("Step 2 — Point your client at the local runtime",
               "This is the only application-visible difference from Mode A: the endpoint.")
code_block(s, MARGIN, y, Inches(6.55), Inches(3.3), """
# litellm_config.yaml — same connector, local endpoint
guardrails:
  endpoint: http://guardrails-runtime.guardrails.svc:8080/v1/evaluate
  workload_id: checkout-assistant
  tenant_id:   payments
  auth:        iam
  timeout_ms:  250          # local hop, tighter budget
  stages:      [input, output]
""", size=11.5, label="litellm_config.yaml")
code_block(s, MARGIN + Inches(6.85), y, Inches(5.26), Inches(3.3), """
# python sdk — identical, new base URL
gr = Guardrails(
  endpoint="http://guardrails-runtime" +
           ".guardrails.svc:8080",
  workload_id="checkout-assistant",
  tenant_id="payments",
  timeout_ms=250,
)
# every call site below is unchanged
""", size=11, label="python")
callout(s, MARGIN, y + Inches(3.55), CONTENT_W,
        "The connector's invocation contract does not change between hosted and customer-managed deployment. "
        "That property is validated architecturally at M1/M2 (MVP-09) and shipped at M4.",
        color=ACCENT, label="ONE CONTRACT, THREE LOCATIONS", h=Inches(0.82))

s, y = d.slide("Running without the control plane", "The local cache is what makes Mode C survivable.")
bullets(s, MARGIN, y, Inches(6.4), [
    ("Policies are cached locally and synchronized asynchronously.", 0),
    ("Evaluation never blocks on a control-plane call.", 0),
    ("A control-plane outage does not stop evaluation.", 0),
    ("The runtime keeps executing the cached policy version.", 1),
    ("A stale cache is a policy question, not an accident.", 0),
    ("You declare what happens once the cache passes its staleness bound — "
     "keep evaluating, block, or require approval.", 1),
    ("Promotion and rollback propagate asynchronously.", 0),
    ("Your failure behavior must say what happens if a rollback is issued while the "
     "cache is stale.", 1),
], size=13)
code_block(s, MARGIN + Inches(6.8), y, Inches(5.9), Inches(3.0), """
# policy excerpt — Mode C additions
failure_behavior:
  control_plane_unavailable: use_cached_policy
  cache_stale:               block
  audit_delivery_failed:     allow_and_log_locally

policy_cache:
  stale_after_seconds: 86400
""", size=11, label="policy (yaml)")
callout(s, MARGIN, y + Inches(3.4), CONTENT_W,
        "Degraded-mode behavior for a stale cache must be an explicit part of the workload's failure behavior. "
        "An undocumented fallback is a defect, not a default (AGR-012, AGR-015).",
        color=AMBER, h=Inches(0.82))

s, y = d.slide("Proving content stayed in your account",
               "The telemetry channel is the only line that leaves — and you draw it.")
table(s, MARGIN, y, CONTENT_W,
      ["Telemetry mode", "What crosses the boundary", "Typical use"],
      [["none", "Nothing at all — a fully supported configuration", "Air-gapped, maximal restriction"],
       ["health", "Health status, product version, policy version", "Minimum needed for support"],
       ["aggregate", "+ evaluation counts, reason-code totals, latency metrics", "Default for most regulated teams"],
       ["metadata", "+ sanitized decision metadata", "Teams wanting central dashboards"],
       ["raw content", "Never included unless explicitly configured by you", "Rarely — requires a deliberate decision"]],
      widths=[2.2, 7.0, 4.4], size=11.5, row_h=Inches(0.48))
bullets(s, MARGIN, y + Inches(3.1), CONTENT_W, [
    ("Verify before go-live: audit records land in your own store; the audit field "
     "'raw content retained' reads false; the only egress is the telemetry mode you chose.", 0),
    ("Upgrade compatibility checks validate that a runtime upgrade still supports the policy "
     "versions currently bound to your deployment.", 0),
], size=12.5)

s, y = d.slide("Before you plan on Mode C", "Status and caveats, stated plainly.")
bullets(s, MARGIN, y, CONTENT_W, [
    ("Phase 4 deliverable. Not available at the Phase 1–2 launch. What Phases 1–2 prove is only "
     "that the runtime CAN later be packaged this way without changing the connector contract.", 0, RED),
    ("You take on infrastructure ownership: availability, capacity, networking, and runtime "
     "upgrades — unless managed support is purchased.", 0),
    ("Air-gapped installation is a stronger guarantee than Mode C's standard 'no synchronous "
     "control-plane connection required', and is a separate later-phase capability (CAP-033).", 0),
    ("Dedicated per-tenant runtimes are also separate and later-phase (CAP-032).", 0),
    ("Mode C is the correct answer for 'no content leaves our network' — not PrivateLink, which "
     "remains a separate, unscheduled item.", 0),
    ("Same policies, same decisions, same reason codes, same audit shape as Modes A and B. "
     "Moving between modes is a client change, not a policy rewrite.", 0, GREEN),
], size=13)
d.save("Mode-C-Customer-Managed-Runtime.pptx")
