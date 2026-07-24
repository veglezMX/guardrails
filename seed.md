# AI Guardrails Solution Charter

**Status:** Draft
**Product type:** Enterprise AI governance and runtime enforcement platform
**Primary cloud:** Amazon Web Services
**Initial integration target:** LiteLLM
**Deployment framework:** Remote Evaluation, Guarded Gateway, Customer-Managed Runtime

---

## 1. Purpose

AI Guardrails provides a reusable policy, evaluation, and enforcement layer for applications that consume large language models or execute agentic workflows.

The solution enables multiple teams to apply consistent organizational safeguards without requiring them to use the same model provider, application framework, or LiteLLM deployment.

The platform supports three operating models:

1. **Remote Evaluation** for teams that already own and operate an LLM gateway.
2. **Guarded Gateway** for teams that want a fully managed inference and guardrail service.
3. **Customer-Managed Runtime** for teams that must keep prompts, responses, and evaluation processing inside their own AWS environment.

The product must maintain a common policy model, evaluation contract, and runtime behavior across all three modes.

---

## 2. Product vision

Provide a model-independent and deployment-flexible guardrails platform that allows teams to adopt centrally governed AI safety policies while retaining the degree of operational ownership and data isolation appropriate to their workloads.

The solution should allow an organization to:

* Define guardrail policies once.
* Apply the same policies to multiple workloads.
* Support different LLM gateways and providers.
* Evaluate prompts and responses consistently.
* operate guardrails as a hosted or self-managed capability.
* Measure effectiveness before policies become blocking.
* Evolve from cooperative integration to mandatory enforcement.
* Avoid coupling policy definitions to LiteLLM or Amazon Bedrock.

---

## 3. Core architectural decision

The product will adopt the following three-mode model as its primary deployment framework.

### Mode A — Remote Evaluation

The consuming team operates its own LiteLLM or equivalent LLM gateway.

Its gateway invokes the hosted AI Guardrails Evaluation API before and after model inference.

```text
Application
    │
    ▼
Team-owned LiteLLM
    │
    ├── Input evaluation ─────► Hosted Guardrails Runtime
    │
    ├── Model invocation
    │
    └── Output evaluation ────► Hosted Guardrails Runtime
```

The consuming team owns:

* LiteLLM availability.
* Model-provider credentials.
* Model routing.
* Retry and fallback behavior.
* Gateway upgrades.
* Inference capacity.
* Model-provider costs.

The AI Guardrails provider owns:

* Evaluation API availability.
* Guardrail policy execution.
* Detection engines.
* Policy versions.
* Decision normalization.
* Guardrail audit metadata.
* Runtime scaling.

Mode A does not reduce how much tenant content reaches the hosted guardrail service. Prompts, responses, and any additional evaluated content are transmitted to the Evaluation API.

Its primary benefit is reduced operational ownership of the inference path.

Mode A should therefore be described as:

> Bring your own gateway while consuming a hosted guardrail runtime.

It must not be described as a private-data or data-local deployment model.

---

### Mode B — Guarded Gateway

The AI Guardrails provider operates both the LLM gateway and the guardrail runtime.

```text
Application
    │
    ▼
Provider-managed Guarded Gateway
    │
    ├── Input guardrails
    ├── Model routing
    ├── Model invocation
    ├── Output guardrails
    └── Audit and usage reporting
```

The provider owns:

* Gateway availability.
* Provider credentials or delegated AWS access.
* Model routing.
* Model-provider integrations.
* Inference retries and fallbacks.
* Guardrail execution.
* Rate limits.
* Usage quotas.
* Audit and operational telemetry.

The consuming team owns:

* Application behavior.
* Workload-specific configuration.
* Policy selection where permitted.
* End-user authorization.
* Business-level approval flows.

Mode B provides the strongest centralized control but also creates the largest operational, security, and compliance scope for the provider.

This mode is intended for teams that:

* Do not operate LiteLLM.
* Want a managed OpenAI-compatible endpoint.
* Require centrally managed model access.
* Prefer consolidated model and guardrail operations.
* Accept that the provider participates in the complete inference path.

---

### Mode C — Customer-Managed Runtime

The guardrail runtime is deployed inside the consuming team’s AWS account.

```text
Customer AWS account

Application
    │
    ▼
Customer LiteLLM or application
    │
    ├── Customer-hosted Guardrails Runtime
    │       ├── Policy cache
    │       ├── Bedrock Guardrails adapter
    │       ├── Custom evaluators
    │       └── Local audit integration
    │
    └── Model provider
```

The consuming team owns:

* Runtime infrastructure.
* Runtime availability.
* Networking.
* Model credentials.
* Gateway configuration.
* Local audit storage.
* Runtime upgrades unless managed support is purchased.

The AI Guardrails provider owns:

* Runtime software.
* Policy schemas.
* Supported deployment artifacts.
* Policy compatibility.
* Product updates.
* Documentation.
* Optional hosted policy management.
* Optional support and upgrade services.

Mode C is intended for:

* Regulated workloads.
* Data residency requirements.
* High-volume workloads.
* Air-gapped environments.
* Teams that prohibit prompt or response content from leaving their account.
* Customers that require independent operational ownership.

The customer-managed runtime must be capable of executing policies without requiring a synchronous connection to the hosted control plane.

---

## 4. Definition of the import capability

The term **import** refers to adopting a reusable guardrail policy and integration configuration.

It does not mean importing, cloning, registering, or remotely controlling another team’s LiteLLM instance.

A consuming team imports or binds:

1. A versioned guardrail policy.
2. A workload identity.
3. An integration manifest.
4. A connector configuration.
5. An enforcement mode.

The import process must not require:

* Copying the team’s LiteLLM database.
* Transferring model-provider credentials.
* Exposing gateway administration endpoints.
* Replacing the team’s model-routing configuration.
* Giving the guardrail provider administrative access to the team’s gateway.

The conceptual onboarding flow is:

```text
Register workload
    │
    ▼
Select policy
    │
    ▼
Generate integration manifest
    │
    ▼
Install connector
    │
    ▼
Run in observe mode
    │
    ▼
Review evaluation results
    │
    ▼
Enable enforcement
```

---

## 5. Product principles

### 5.1 Policy portability

Guardrail policies must not depend directly on:

* LiteLLM configuration structures.
* Bedrock Guardrails response formats.
* A specific model provider.
* A specific programming language.
* A specific application framework.

Vendor-specific implementations must be placed behind adapters.

---

### 5.2 One contract across deployment modes

Remote, gateway, and customer-managed deployments must use the same logical evaluation contract.

The deployment location may change, but the following must remain compatible:

* Policy definitions.
* Request structures.
* Decision structures.
* Reason codes.
* Enforcement actions.
* Audit metadata.
* Policy-version semantics.

---

### 5.3 Policies are separate from integrations

A policy defines what must be evaluated and how violations should be handled.

A connector defines how a specific gateway or application invokes and enforces that policy.

```text
Policy:
    Detect prompt injection and block it.

Connector:
    Invoke the Evaluation API during LiteLLM pre-call execution.
```

This separation is mandatory.

---

### 5.4 Explicit content-exposure model

Every deployment must document:

* Where prompts are processed.
* Where responses are processed.
* Whether retrieved documents are transmitted.
* Whether tool arguments are transmitted.
* Whether content is persisted.
* Which telemetry leaves the runtime.
* Which AWS accounts and Regions process the request.

Operational ownership and data exposure must be communicated as separate concerns.

---

### 5.5 Guardrails are not authorization

Content evaluation must remain separate from business authorization.

A guardrail may determine that a tool call appears safe.

It does not determine whether the current user is permitted to execute that tool against a particular resource.

The consuming application remains responsible for:

* User authentication.
* Business authorization.
* Tenant isolation.
* Resource ownership.
* Transaction limits.
* Approval workflows.

Tool and agent authorization may become a later product capability, but it is outside the initial guardrail MVP.

---

### 5.6 Observable before enforceable

Every policy must support a non-blocking evaluation mode.

Recommended progression:

```text
Disabled
    → Observe
    → Warn
    → Redact
    → Block
```

A team must be able to measure false positives, latency, and business impact before enabling blocking behavior.

---

### 5.7 Failure behavior is part of policy

Runtime failure behavior must be explicit.

A workload must define what happens when:

* The Evaluation API times out.
* A detector is unavailable.
* The policy cannot be resolved.
* The control plane is unavailable.
* Audit delivery fails.
* A response cannot be safely transformed.

Examples include:

* Allow and log.
* Return a safe fallback.
* Block the request.
* Use a cached policy.
* Require manual approval.

There must be no undocumented fallback behavior.

---

## 6. Product scope

### 6.1 In scope

The initial product scope includes:

* Versioned guardrail policies.
* Workload registration.
* Policy-to-workload bindings.
* Input evaluation.
* Output evaluation.
* LiteLLM integration.
* Generic HTTP Evaluation API.
* Amazon Bedrock Guardrails integration.
* Deterministic organization rules.
* Basic sensitive-data handling.
* Normalized evaluation decisions.
* Observe and enforce modes.
* Evaluation latency metrics.
* Decision metadata auditing.
* Hosted runtime deployment.
* A path to customer-managed deployment.

---

### 6.2 Future scope

The product architecture should permit, but not initially require:

* Retrieval-content evaluation.
* Indirect prompt-injection detection.
* Streaming output evaluation.
* Tool-call validation.
* MCP authorization.
* Memory-write validation.
* Groundedness evaluation.
* Automated reasoning.
* Multilingual policy packs.
* Human approval workflows.
* Dedicated hosted runtimes.
* Air-gapped installation.
* Policy signing.
* Policy inheritance.
* Customer-specific evaluator plugins.
* Commercial metering.
* Policy marketplace capabilities.

---

### 6.3 Out of scope for the MVP

The MVP will not attempt to provide:

* A general-purpose LLM gateway replacement.
* A complete AI governance platform.
* Model training or fine-tuning.
* A proprietary prompt-injection model.
* A proprietary moderation model.
* A complete DLP platform.
* End-user authentication.
* Application business authorization.
* Fully autonomous agent governance.
* Organization-wide AWS enforcement.
* Cross-cloud deployment.
* Multi-region active-active availability.
* Full compliance certification.
* A general MCP security platform.

These capabilities may be introduced only after the core integration and policy model have been validated.

---

## 7. Initial users and stakeholders

### Platform teams

Platform teams need:

* Central guardrail policy definitions.
* Reusable connectors.
* Deployment consistency.
* Audit visibility.
* Controlled policy rollouts.
* Low operational overhead.

### Application teams

Application teams need:

* A simple onboarding process.
* Minimal application changes.
* Predictable latency.
* Clear failure behavior.
* Understandable violation reasons.
* A way to test policies before blocking users.

### Security and compliance teams

Security and compliance teams need:

* Policy ownership.
* Decision evidence.
* Policy-version traceability.
* Content-retention controls.
* Exception tracking.
* Evidence that policies are being applied.

### Product operators

Product operators need:

* Tenant and workload registration.
* Service-health visibility.
* Usage metrics.
* Runtime capacity management.
* Policy rollout controls.
* Deployment compatibility tracking.

---

## 8. Trust and enforcement model

The product must explicitly distinguish cooperative integration from mandatory enforcement.

### Cooperative integration

The consuming team installs and maintains the connector.

```text
Application → Team LiteLLM with connector → Model
```

This is appropriate when:

* Teams are trusted.
* Guardrails are an engineering standard.
* Bypass prevention is not a formal requirement.
* The initial objective is adoption and evaluation.

The connector can be disabled, removed, or misconfigured.

A cooperative connector must not be represented as a hard security boundary.

---

### Platform-managed enforcement

The platform team manages the LiteLLM configuration or gateway deployment.

```text
Application → Platform-managed gateway → Guardrails → Model
```

This is appropriate when:

* The platform owns gateway infrastructure.
* Application teams should not change mandatory guardrail settings.
* Centralized provider credentials are acceptable.
* Configuration consistency is required.

Controls may include:

* Read-only gateway configuration.
* GitOps-managed deployment.
* Restricted IAM roles.
* Removal of model credentials from application workloads.
* Configuration validation.
* Connector-health telemetry.

---

### Network and IAM-enforced gateway

All supported model traffic must pass through a controlled gateway.

This is appropriate when:

* Bypass prevention is required.
* Teams are considered semi-trusted.
* Compliance evidence is required.
* Direct provider access must be prohibited.

Possible controls include:

* Applications have no direct Bedrock permissions.
* Applications do not possess provider API credentials.
* Egress policies restrict direct provider connectivity.
* Only the gateway role may invoke approved model services.
* Guardrail configuration is centrally managed.
* Mandatory policies cannot be overridden by consumers.

This is the strongest enforcement model but is not required for the first cooperative MVP.

---

## 9. Core domain model

### Tenant

Represents an organization, business unit, or consuming team.

A tenant contains:

* Tenant identifier.
* Ownership information.
* AWS account information.
* Data classification.
* Allowed Regions.
* Retention configuration.
* Deployment mode.
* Authentication configuration.

---

### Workload

Represents an application, assistant, agent, gateway, or inference service using guardrails.

A workload contains:

* Workload identifier.
* Tenant identifier.
* Owner.
* Environment.
* Risk classification.
* Gateway type.
* Deployment mode.
* Expected throughput.
* Streaming requirements.
* Assigned policy.
* Enforcement mode.

---

### Policy

Defines the checks and actions applied to evaluated content.

A policy contains:

* Policy name.
* Policy version.
* Evaluation stages.
* Evaluator configuration.
* Enforcement actions.
* Thresholds.
* Failure behavior.
* Audit behavior.
* Compatibility metadata.

---

### Policy binding

Associates a workload with a policy version.

A binding may also define:

* Observe or enforce mode.
* Allowed workload-specific overrides.
* Effective date.
* Rollback version.
* Runtime compatibility requirements.

---

### Evaluation

Represents a request to inspect content at a particular stage.

Supported initial stages:

* `input`
* `output`

Future stages may include:

* `retrieval`
* `tool_call`
* `tool_result`
* `memory_write`
* `final_response`

---

### Decision

Represents the normalized result of an evaluation.

Initial actions:

* `allow`
* `block`
* `redact`
* `log_only`

Future actions:

* `rewrite`
* `discard`
* `abstain`
* `require_approval`

A decision must contain:

* Action.
* Allowed status.
* Reason codes.
* Policy name and version.
* Evaluator metadata.
* Transformed content where applicable.
* Correlation identifier.
* Audit identifier.
* Evaluation latency.

---

## 10. Logical architecture

```text
                              ┌─────────────────────────┐
                              │ Guardrails Control Plane│
                              │                         │
                              │ Tenants                 │
                              │ Workloads               │
                              │ Policies                │
                              │ Bindings                │
                              │ Rollouts                │
                              └────────────┬────────────┘
                                           │
                                Policy distribution
                                           │
                                           ▼
┌────────────────┐              ┌─────────────────────────┐
│ LiteLLM        │              │ Guardrails Runtime      │
│ Connector      │─────────────►│                         │
└────────────────┘              │ Evaluation API          │
                                │ Policy resolver         │
┌────────────────┐              │ Decision engine         │
│ Generic SDK    │─────────────►│ Evaluator adapters      │
└────────────────┘              │ Audit producer          │
                                └────────────┬────────────┘
┌────────────────┐                           │
│ Guarded Gateway│───────────────────────────┘
└────────────────┘                           │
                                             ▼
                                 ┌────────────────────────┐
                                 │ Evaluation engines     │
                                 │                        │
                                 │ Bedrock Guardrails     │
                                 │ Deterministic rules    │
                                 │ PII evaluators         │
                                 │ Schema validators      │
                                 └────────────────────────┘
```

---

## 11. AWS implementation direction

The MVP should use the minimum AWS architecture required to validate the product.

Suggested initial services:

| Capability             | Initial AWS service                      |
| ---------------------- | ---------------------------------------- |
| Evaluation runtime     | ECS Fargate or EKS                       |
| Public or internal API | API Gateway or Application Load Balancer |
| Policy storage         | DynamoDB or versioned S3                 |
| Workload registry      | DynamoDB                                 |
| Guardrail evaluation   | Amazon Bedrock Guardrails                |
| Runtime credentials    | IAM roles                                |
| Secrets                | AWS Secrets Manager                      |
| Encryption             | AWS KMS                                  |
| Operational metrics    | CloudWatch                               |
| Audit event buffering  | Amazon SQS                               |
| Audit archive          | Amazon S3                                |
| Container registry     | Amazon ECR                               |

The MVP does not require separate management, production, and audit AWS accounts.

A single non-production account and one production account are sufficient initially, provided IAM, storage, and environment boundaries remain explicit.

Additional account separation should be introduced when justified by:

* External customers.
* Compliance requirements.
* Independent security ownership.
* Immutable audit requirements.
* Production scale.
* Dedicated tenant deployments.

---

## 12. LiteLLM integration

LiteLLM is the first supported gateway adapter, not the product boundary.

The LiteLLM connector is responsible for:

* Mapping LiteLLM requests into the evaluation contract.
* Invoking input evaluation before inference.
* Enforcing block or redact decisions.
* Invoking output evaluation after inference.
* Applying safe output fallbacks.
* Propagating correlation identifiers.
* Handling evaluation timeouts.
* Emitting connector-health telemetry.

The connector is not responsible for:

* Defining organization policy.
* Implementing detection models.
* Managing model credentials.
* Persisting audit records.
* Performing user authorization.
* Managing policy versions.

The product must later support additional integrations without changing policy semantics.

---

## 13. Data handling requirements

### Default behavior

The hosted runtime should:

* Process content only for evaluation.
* Avoid persisting raw prompts and responses by default.
* Persist decision metadata.
* Hash user and conversation identifiers where possible.
* Remove secrets from telemetry.
* Encrypt data in transit and at rest.
* Support tenant-specific retention configuration.
* Clearly document which content is transmitted.

### Content categories that may be evaluated

Depending on workload configuration:

* User prompts.
* System prompts.
* Conversation history.
* Retrieved documents.
* Model responses.
* Tool arguments.
* Tool responses.
* Structured-output payloads.

The MVP should initially limit support to user/model input and model output.

### Customer-managed telemetry

Customer-managed runtimes must allow the customer to choose whether the hosted control plane receives:

* Health status.
* Product version.
* Policy version.
* Aggregated evaluation counts.
* Reason-code totals.
* Latency metrics.
* Sanitized decision metadata.
* No telemetry.

Raw content must never leave a customer-managed runtime unless explicitly configured.

---

## 14. Security requirements

The product must support:

* TLS for all runtime communication.
* IAM-based authentication for AWS consumers.
* Short-lived credentials.
* Least-privilege runtime roles.
* KMS encryption.
* Per-tenant workload identities.
* Policy authorization checks.
* Request correlation.
* Rate limiting.
* Input-size limits.
* Timeout limits.
* Audit-event integrity.
* Dependency and container scanning.

The product must not rely exclusively on:

* API keys stored in LiteLLM configuration.
* Tenant-supplied identifiers without authentication.
* Client-provided policy versions without authorization.
* Connector telemetry as evidence of mandatory enforcement.

---

## 15. Operational requirements

### Availability

The guardrail runtime must define:

* Availability objective.
* Timeout behavior.
* Retry behavior.
* Cached-policy behavior.
* Degraded operating mode.
* Circuit-breaker behavior.

### Performance

The platform must measure:

* P50 evaluation latency.
* P95 evaluation latency.
* P99 evaluation latency.
* Evaluation throughput.
* Timeout rate.
* Detector-specific latency.
* Decision distribution.
* Policy-cache hit rate.

### Policy rollout

Policies must support:

* Immutable versions.
* Observe mode.
* Controlled promotion.
* Workload-scoped rollout.
* Immediate rollback.
* Compatibility validation.
* Evaluation-result comparison between versions.

### Auditing

Audit metadata must include:

* Tenant.
* Workload.
* Policy version.
* Evaluation stage.
* Decision.
* Reason codes.
* Evaluator versions.
* Timestamp.
* Correlation identifier.
* Whether raw content was retained.

---

## 16. MVP definition

The MVP exists to prove that one reusable policy can be applied consistently to multiple independent LiteLLM deployments.

### MVP components

1. Guardrail policy format.
2. Policy storage.
3. Workload registration.
4. Policy-to-workload binding.
5. Evaluation API.
6. Amazon Bedrock Guardrails adapter.
7. Basic deterministic evaluator.
8. LiteLLM pre-call connector.
9. LiteLLM post-call connector.
10. Observe and enforce modes.
11. Decision metadata logging.
12. Evaluation latency metrics.

### MVP demonstration

The MVP is considered successful when:

* Two separate LiteLLM instances consume the same policy.
* Neither instance shares model-provider credentials with the guardrail platform.
* Both receive consistent normalized decisions.
* One workload can run in observe mode.
* Another workload can run in enforce mode.
* A policy can be upgraded and rolled back.
* Raw content persistence can be disabled.
* Evaluation failures follow a configured failure policy.
* The runtime can later be packaged for customer-managed deployment without changing the connector contract.

---

## 17. MVP non-goals

The MVP will not require:

* A full administration portal.
* A policy-signing infrastructure.
* PrivateLink.
* Three-account AWS separation.
* A billing platform.
* Dedicated tenant runtime deployments.
* MCP tool authorization.
* Organization-wide Bedrock enforcement.
* Complex policy inheritance.
* Streaming guardrail enforcement.
* Air-gapped deployment.
* Multi-cloud support.
* A general-purpose AI governance inventory.

A minimal management API and version-controlled configuration are acceptable during the MVP.

---

## 18. Delivery phases

### Phase 1 — Integration proof

Objective:

> Prove that guardrail policy and evaluation can be separated from LiteLLM and reused across multiple gateways.

Deliverables:

* Evaluation contract.
* Policy format.
* Bedrock Guardrails adapter.
* LiteLLM connector.
* Observe mode.
* Basic auditing.
* Internal deployment.

---

### Phase 2 — Internal multi-team platform

Objective:

> Allow multiple internal teams to onboard and operate distinct workloads.

Deliverables:

* Tenant and workload registry.
* Policy bindings.
* IAM authentication.
* Quotas.
* Policy promotion and rollback.
* Service-level monitoring.
* Basic administration workflow.

---

### Phase 3 — Guarded gateway

Objective:

> Offer a fully hosted model and guardrail path for teams without their own gateway.

Deliverables:

* Managed LiteLLM gateway.
* Model-provider configuration.
* Per-tenant routing.
* Cost and rate controls.
* Managed credentials.
* Unified inference and guardrail availability objectives.

---

### Phase 4 — Customer-managed runtime

Objective:

> Support workloads that require local evaluation and content isolation.

Deliverables:

* Runtime container.
* Helm chart.
* ECS deployment template.
* Infrastructure module.
* Local policy cache.
* Policy synchronization.
* Customer-controlled telemetry.
* Upgrade compatibility checks.

---

### Phase 5 — Advanced agent protection

Objective:

> Extend guardrail coverage beyond prompt and response evaluation.

Potential deliverables:

* Retrieval-content evaluation.
* Tool-call validation.
* Tool-result evaluation.
* Memory-write controls.
* Human approvals.
* Agent delegation restrictions.
* MCP integration.
* Advanced authorization integration.

This phase must be treated as a separate product expansion, not as an MVP prerequisite.

---

## 19. Success metrics

### Adoption

* Number of onboarded workloads.
* Number of active tenants.
* Percentage of supported LiteLLM instances using the connector.
* Time required to onboard a workload.
* Percentage of workloads using a shared policy.

### Reliability

* Evaluation API availability.
* Evaluation timeout rate.
* P95 evaluation latency.
* Policy-resolution failure rate.
* Audit delivery success rate.

### Effectiveness

* Number of policy interventions.
* False-positive review rate.
* Confirmed harmful-input detection rate.
* Confirmed sensitive-output prevention rate.
* Percentage of policies promoted from observe to enforce.
* Number of policy rollbacks caused by incorrect behavior.

### Product portability

* Percentage of policy definitions that are deployment-mode independent.
* Number of supported connectors.
* Percentage of runtime code shared across hosted and customer-managed modes.
* Time required to deploy a customer-managed runtime.

---

## 20. Key risks

### Connector bypass

A consumer may remove or misconfigure the LiteLLM connector.

Mitigation depends on trust level:

* Cooperative onboarding for trusted teams.
* Configuration validation.
* Managed gateway deployment.
* Restricted model credentials.
* IAM and network enforcement for mandatory environments.

---

### False confidence

Teams may interpret successful guardrail evaluation as complete AI security.

Mitigation:

* Clearly separate content evaluation, authorization, data isolation, and application security.
* Document unsupported risk categories.
* Maintain risk-specific evaluation datasets.
* Avoid marketing guardrails as a complete security boundary.

---

### Latency

Remote input and output evaluation adds synchronous network and processing latency.

Mitigation:

* Measure per-evaluator latency.
* Apply only relevant evaluators.
* Support local deterministic checks.
* Use customer-managed runtime for latency-sensitive workloads.
* Define stage-specific timeouts.

---

### Hosted data exposure

Remote evaluation transmits tenant content to the hosted runtime.

Mitigation:

* Explicit data-flow documentation.
* No raw-content retention by default.
* Regional deployments.
* Private connectivity when justified.
* Customer-managed runtime option.

---

### Policy fragmentation

Too many workload-specific overrides may weaken organizational consistency.

Mitigation:

* Limit override categories.
* Require ownership for exceptions.
* Version policy packs.
* Track effective policy per workload.

---

### Product over-expansion

The solution may attempt to include gateway management, AI governance, authorization, agent orchestration, and compliance before validating the core integration.

Mitigation:

* Maintain strict MVP boundaries.
* Treat future capabilities as separately approved phases.
* Measure adoption and policy effectiveness before expanding scope.

---

## 21. Ownership boundaries

### AI Guardrails team

Responsible for:

* Product architecture.
* Policy schemas.
* Evaluation contract.
* Guardrail runtime.
* LiteLLM connector.
* Bedrock integration.
* Policy compatibility.
* Runtime telemetry.
* Documentation.
* Supported deployment artifacts.

### Platform operations

Responsible for:

* Hosted infrastructure.
* Runtime deployment.
* Service monitoring.
* Capacity management.
* Network configuration.
* Production incident response.

### Security and governance

Responsible for:

* Organization-level policy requirements.
* Risk classifications.
* Policy approval.
* Exception criteria.
* Retention requirements.
* Audit requirements.

### Consuming teams

Responsible for:

* Application behavior.
* Correct connector installation in cooperative mode.
* Workload registration accuracy.
* End-user authentication.
* Business authorization.
* Model selection where applicable.
* Review of observe-mode findings.
* Acceptance of enforcement behavior.

---

## 22. Decision summary

The AI Guardrails solution will be built as a standalone policy and enforcement platform rather than as functionality embedded inside the existing internal LiteLLM instance.

LiteLLM will serve as the first supported connector and may also be used to provide the managed Guarded Gateway mode.

Amazon Bedrock Guardrails will serve as an initial managed evaluation engine, but the public product contract will remain vendor-independent.

The product will support three primary deployment modes:

1. Remote Evaluation.
2. Guarded Gateway.
3. Customer-Managed Runtime.

The MVP will focus on reusable policy import, input/output evaluation, LiteLLM integration, observe/enforce modes, and normalized audit decisions.

Advanced infrastructure isolation, policy distribution security, agent authorization, and organization-wide enforcement will be introduced only when justified by adoption, compliance, or commercial requirements.

---

## 23. Product statement

> AI Guardrails is an AWS-based policy and enforcement platform that applies reusable safeguards to LLM applications and gateways. Teams may connect their existing LiteLLM instance to a hosted Evaluation API, consume a fully managed Guarded Gateway, or deploy the same guardrail runtime within their own AWS account. Policies remain portable across all three modes, while operational ownership and content exposure are explicitly defined for each deployment.
