# Guardrails Evolution — Plan Index

**Plan:** `guardrails-evolution` · **Location:** `plans/guardrails-evolution/`

## What is Guardrails Evolution?

A comprehensive continuation of the completed local technical proof for two teams consuming guardrails from independently owned LiteLLM gateways. It covers the nine selected workstreams through six executable phases, with policy governance first. The build contract starts at AGENT.md; no implementation task is complete merely because this folder exists.

## Documents

| # | Document | Purpose |
|---|---|---|
| 0 | AGENT.md | How to start, execute, close gaps and preserve progress |
| 1 | index.md | Which document answers each implementation question |
| 2 | architecture.md | Decisions, scope and ownership boundaries |
| 3 | data-schema.md | Domain records, constraints and state machines |
| 4 | folder-structure.md | Physical file locations and production actions |
| 5 | component-specs.md | Inputs, outputs, dependencies and behavior of 28 components |
| 6 | tech-stack.md | Dependency constraints, configuration, wiring and commands |
| 7 | ecosystem-reference.md | Verified baseline and external interfaces |
| 8 | input-output-contract.md | System boundaries and administrator/application workflows |
| 9 | test-strategy.md | Test layers, fixtures and mocking boundaries |
| 10 | progress.md | Six phases, tasks, gate state and next action |
| 11 | sessions/ | Append-only implementation session history |
| 12 | variance-matrix.md | Behavior across modes, enforcement and telemetry |
| 13 | migration-map.md | SQLite/v1 transition, coexistence and rollback |
| 14 | integration-protocols.md | Gateway, worker, model and synchronization protocols |
| 15 | plugin-contract.md | Engine/stage/action/connector extension contracts |
| 16 | quality-gates.md | Executable acceptance, quality, operational and evidence thresholds |
| 17 | api-endpoints.md | HTTP routes, request/response schemas and errors |
| 18 | tenancy-model.md | Tenant scope, permissions and data isolation |
| 19 | guardrail-coverage.md | Exact detectors, list semantics and supported surfaces |
| 20 | deployment-topology.md | Minikube topology, traffic and resource constraints |
| 21 | admin-experience.md | Browser journeys, role states and accessibility |
| 22 | delivery-estimate.md | Staffing assumptions, effort ranges and critical path |

## Assumptions and Open Questions

The user explicitly selected all nine workstreams, two independent team gateways, shared local content processing, local deployment with Minikube if suitable, policy versioning/approval priority, automated acceptance, all four allow/deny categories and the proposed author→different approver→operator workflow. These are decisions, not assumptions.

| # | Assumption or question | Affects | Status |
|---|---|---|---|
| A-1 | New plan name guardrails-evolution; preserve the completed proof and build from ad59059 | architecture.md, migration-map.md | assumed |
| A-2 | Retain Python/FastAPI and introduce PostgreSQL for shared state, SQLite for customer cache | tech-stack.md, data-schema.md | assumed |
| A-3 | Linux Minikube/Docker/Calico with initial 6 CPU/8 GiB allocation, subject to actual preflight and capacity gate | deployment-topology.md | assessed and assumed |
| A-4 | Server-rendered Jinja/htmx administration with local service identity, no new corporate SSO dependency | admin-experience.md, tech-stack.md | assumed |
| A-5 | No raw retention in supplied v2 profiles; external processing disabled until explicit approved configuration | architecture.md, data-schema.md | assumed from standing privacy requirements |
| A-6 | Local real model default qwen2.5:0.5b via Ollama; exact artifact pins resolved and verified in preparation | integration-protocols.md, tech-stack.md | assumed |
| A-7 | Pretrained local injection classifier and declared held-out quality thresholds; lexical topics are explicitly bounded | guardrail-coverage.md | assumed |
| A-8 | Initial multimodal increment is PNG/JPEG text inspection in English/Spanish; audio/video unsupported and rejected | guardrail-coverage.md | assumed |
| A-9 | All enforcing stream output is buffered before release; no early token release guarantee is offered | api-endpoints.md, integration-protocols.md | assumed |
| A-10 | Deny wins; allows intersect across layers; content allow enforcement is explicit and never exempts other checks | guardrail-coverage.md | assumed |
| A-11 | Customer offline validity at most 24h by default; shared snapshot lease 60s; quotas, deadlines and metadata retention use specified defaults | data-schema.md, integration-protocols.md | assumed |
| A-12 | Operator is a separate permission, but a third distinct person beyond author and approver is not required | tenancy-model.md | interpretation of confirmed workflow |
| A-13 | Bedrock adapter implementation and local contracts included; live external verification is optional and separately gated | integration-protocols.md | assumed under local residency |
| A-14 | pytest/Playwright, real integration boundaries, closed case inventory, no arbitrary line-coverage target | test-strategy.md | assumed |
| A-15 | Local load, recovery and detector thresholds are engineering defaults, not production SLAs or population accuracy claims | quality-gates.md | assumed |
| A-16 | Estimates use four engineers plus QA/scrum master and 70% effective engineering capacity; staffing is not a new commitment | delivery-estimate.md | inherited planning default |
| A-17 | No commit, push, merge or deployment is part of plan generation; previous commit request applied to completed Phase 5 | AGENT.md | scope interpretation |
| A-18 | TLS front for local Ollama, seven encrypted local backups, and fixed-minute/per-second quota windows | deployment-topology.md, component-specs.md, data-schema.md | assumed |
| Q-0 | No unresolved critical interview questions; exact artifact patch versions/digests are implementation outputs, not missing product decisions | tech-stack.md | closed |

## Extension Selection

Variance, migration, integration, plugin, quality, API and tenancy extensions are triggered. Coverage, topology, UI journeys and delivery estimate are task-specific additions. No platform matrix: only a Linux local deployment and browser interface are targeted, not multiple native application platforms. No prompt catalog: guardrail logic uses rules/classification/OCR and a managed guardrail API, not generative LLM prompts; sample inference belongs to the gateway integration fixture.

## Plan History

| Date or session | Change |
|---|---|
| 2026-10-09 interview; 2026-10-10 generation UTC | All selected workstreams and follow-up decisions translated into a new plan; predecessor proof preserved |
