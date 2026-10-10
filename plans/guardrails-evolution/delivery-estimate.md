# Delivery Estimate and Dependencies

## Trigger Condition

A comprehensive next-phase plan benefits from a resource estimate, critical path and explicit risk treatment. These are planning ranges, not delivery commitments or measurements of agent execution time.

## Scope Boundary

Estimate only this local evolution plan. It does not include AWS landing, production HA, multi-region deployment, formal compliance or wider media formats. All nine selected workstreams remain included.

## Estimate

Assume the previously discussed four full-time engineers, one QA specialist and one scrum master. Effective engineering capacity default is 70% of four engineers after review/integration overhead, or 2.8 engineer-weeks per calendar week. QA participates throughout; scrum master time is coordination, not implementation capacity.

| Phase | Engineer-weeks | Principal uncertainty |
|---|---:|---|
| 1 Governance foundation | 7–10 | Correct composition, transaction/approval races, migration |
| 2 Shared service and integration | 6–9 | Local CNI/TLS, independent gateway wiring, quotas and real model resources |
| 3 Broader guardrail coverage | 6–10 | Model packaging and held-out quality gate |
| 4 Administration and operations | 6–9 | Browser permission states and reliable restore/upgrade drills |
| 5 Portable delivery modes | 5–8 | Offline trust rotation, telemetry control, gateway accounting |
| 6 Additional boundaries and release | 7–11 | Installed LiteLLM streaming/tool behavior, media limits, complete regression |
| Total | 37–57 | Excludes unforeseen scope changes |

At assumed capacity, approximately 14–21 calendar weeks including rounding, with gated phases sequential and independent tasks within a phase potentially parallel. This is a rough-order estimate from specified work, not a deadline. Actual implementation may use a different staffing/agent model; re-estimate after Phase 2's cluster and compatibility evidence. Do not divide measured agent runtime or token counts by these human estimates.

## Critical Path and Risk Actions

1. Compiler/identity/approval correctness enables safe activation; shared runtime cannot skip that gate.
2. Minikube, CNI and actual LiteLLM compatibility establish the integration foundation. Diagnose resource/network failures before expanding detectors.
3. Freeze model and held-out corpus before judging quality. A failed benchmark is unresolved work, not permission to claim broad injection protection.
4. Implement UI on tested API workflows; restore and migration compatibility must precede portable bundles.
5. Reuse tested runtime/connector for customer and operated modes; keep ownership and telemetry differences explicit.
6. Complete advanced boundaries, then freeze executable/lock/fixture hashes for two whole-release runs.

## Cross-References

(see progress.md), (see quality-gates.md), (see architecture.md).
