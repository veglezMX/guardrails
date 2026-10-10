# Variance Matrix

## Trigger Condition

Deployment mode, enforcement state and telemetry selection change behavior.

## Scope Boundary

This matrix covers the three local deployment modes. It does not certify AWS deployment or parity with every provider endpoint.

## Deployment Modes

| Concern | Remote | Guarded gateway | Customer runtime |
|---|---|---|---|
| Gateway ownership | Team | Provider operates LiteLLM | Team |
| Policy/approval authority | Shared control plane | Shared control plane | Shared control plane, signed offline import supported |
| Evaluation contract | v2 HTTPS | Same v2 through connector | Same v2 locally |
| Default raw-content processing | Shared local runtime | Operated gateway and shared local runtime | Customer's declared local zone |
| Model credentials | Team gateway only | Operated gateway secrets | Team gateway only |
| Policy cache | 60-second signed snapshot TTL | Same | Explicit signed expiry, default ≤24h offline |
| Shared-state persistence | PostgreSQL | PostgreSQL | Local SQLite cache/spool; shared management PostgreSQL |
| Quotas | Shared admission | Shared admission plus gateway token/cost budgets | Local admission; no claim of globally exact offline budget |
| Revocation | Online propagation bounded by TTL | Same | Next sync or expiry; never instantaneous while offline |
| No telemetry | Provider retains local operational metadata as declared | Same | Sends no telemetry; local metadata still obeys customer retention |
| Advanced surfaces | Capability-gated | Same connector | Same engine manifest; missing engines reject activation |

## Enforcement and Telemetry Axes

The five-state action matrix is authoritative in (see guardrail-coverage.md §Enforcement State Matrix). None telemetry sends zero metrics, events or usage. Aggregate sends counts/duration buckets without IDs, content or policy terms; batches minimum 20 events to avoid single-event disclosure. Metadata sends allowlisted AuditEvent fields excluding actor identifiers unless explicitly enabled. Neither mode sends raw content. Offline buffers respect size/age limits; dropping aggregate telemetry records a local loss counter and never blocks evaluation. Required local audit failure follows policy independently of outbound telemetry.

## Cross-References

(see data-schema.md §RuntimeInstall), (see component-specs.md §CustomerRuntime), (see integration-protocols.md).
