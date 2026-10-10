# Test Strategy

## Framework and Runner

Use pytest for Python behavior/contract/integration suites and Playwright for real browser journeys. Keep the existing unittest/closed proof runners for predecessor compatibility. Versions are declared in (see tech-stack.md). New suite runner: `just evolution-test`; phase and release runners: `just evolution-gate N` and `just evolution-acceptance`.

## Test Layers

| Layer | Test type | Location | What it proves |
|---|---|---|---|
| Domain | Unit/property cases | `mvp/tests/evolution/unit/` | Compiler lattice, canonical digests, immutable lifecycle, list matching and transforms |
| Application | Port-based unit | `mvp/tests/evolution/unit/` | Approval, rollout, revocation, budget and audit orchestration |
| Wire adapters | Contract | `mvp/tests/evolution/contract/` | Strict v2 schemas, HTTP status, role scope, compatibility, safe errors |
| Storage/workers | Real integration | `mvp/tests/evolution/integration/` | PostgreSQL transactions/RLS, SQLite customer cache, classifier/OCR cancellation and recovery |
| Gateway/cluster | End-to-end | `mvp/tests/evolution/e2e/` | Two real gateways, installed CNI, actual dispatch boundaries and three modes |
| Browser | Playwright | `tests/evolution-browser/` | Real UI workflow, authorization, CSRF, accessibility and stale-state recovery |
| Detector quality | Held-out benchmark | `mvp/tests/evolution/quality/` | Frozen model/fixture metrics and exact deterministic algorithms |

## What Earns a Test

Each component's logic, every stated edge case and error condition, every model/wire validation, every tenant boundary and each supported stage/action combination earns a behavior test. Cover security invariants through observable effects: unchanged DB after rejection, no downstream model/tool dispatch, no content released and no canary in persisted artifacts. Avoid tests that merely restate field assignments or mock the subject under test.

## Mocking Boundary

Fake clock, randomness, external provider transport and port failures in unit tests. Never fake compiler/governance/transform logic or domain validation. Contract tests may use synthetic Bedrock protocol replies. End-to-end tests use real FastAPI, PostgreSQL, two real LiteLLM instances, Kubernetes networking and loaded local detector artifacts. Deterministic mock inference is deliberate for exact assertions; the separate real Ollama suite is mandatory and reported independently. Do not mock LiteLLM hooks in the proof of connector behavior.

## Fixtures

| Fixture | Provides | Scope |
|---|---|---|
| Principals | A/B tenant admins, separate author/approver/operator, auditor, workload and runtime identities | Per test/run, no shared secret reuse |
| Candidate graph | Pinned organization/tenant/workload policies including incompatible/revoked versions | Function |
| Network sinks | Allowed/denied service counters and DNS/redirect fault endpoints | Cluster test namespace |
| Detector corpus | Licensed/synthetic exact and held-out cases, split/provenance/hash manifest | Immutable artifact |
| Stream script | Complete, fragmented, invalid, delayed and disconnected gateway responses | Function |
| Media corpus | Synthetic PNG/JPEG rendered text, blank images, malformed bytes and resource-limit headers | Immutable artifact |
| Customer bundle | Valid, expired, tampered, wrong-tenant, old-generation and rotated-key bundles | Function |
| Recovery image | Consistent PostgreSQL backup with known safe metadata counts/digests | Isolated restore namespace |

Fixtures use only data-schema.md models; private credentials/content are generated for the run and omitted from reports. Clock-sensitive tests use controllable clocks at ports; real deadline/cancellation tests also measure wall time at the process boundary.

## Coverage Expectation

No arbitrary line-coverage percentage is a release requirement. Require every registered behavior case and zero missing/required-skipped IDs, enforced by `just evolution-gate N`. Test import/layer rules and migration compatibility explicitly. Retain coverage reporting as diagnostic only. Generated OpenAPI/assets and vendor libraries are not unit-tested as our code; their externally used interfaces receive integration tests.

## Phase Gates

| Phase | Gate command |
|---|---|
| 1 Governance foundation | `just evolution-gate 1` |
| 2 Shared service and integration | `just evolution-gate 2` |
| 3 Broader guardrail coverage | `just evolution-gate 3` |
| 4 Administration and operations | `just evolution-gate 4` |
| 5 Portable delivery modes | `just evolution-gate 5` |
| 6 Additional boundaries and release | `just evolution-gate 6` |

Exact acceptance, load/error thresholds and evidence controls are in (see quality-gates.md); do not duplicate them as different test defaults. Before release, run existing proof unit/contract/integration/acceptance commands against the preserved compatibility profile and `just test` for the legacy baseline. Documentation edits alone run the plan checker plus `just verify`, not runtime installation or expensive acceptance.
