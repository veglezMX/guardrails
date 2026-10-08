# State and failure behavior

## Normal finding decisions

The policy chooses a requested action. Take the strongest action across checks, then clamp normal findings to the state ceiling. Reasons and findings survive clamping; `would_be_action` records the unclamped result. Clean content returns allow. Disabled skips detectors but still authenticates, validates, resolves configuration and audits.

| State | Ceiling | Sensitive fixture with policy requested_action=block | Content/model behavior |
| --- | --- | --- | --- |
| disabled | allow | allow | No findings; unchanged content; audit disabled operation status |
| observe | log_only | log_only | Findings in audit; original input/output unchanged |
| warn | log_only | log_only | Same as Observe plus caller-visible warning in gateway guardrails metadata |
| redact | redact | redact | Replace all validated spans with `[REDACTED]`; input before model, output before delivery |
| block | block | block | Input never invokes model; blocked output text never reaches caller |

The state is a ceiling, not a command to invent findings or strengthen a weaker policy action. A policy requesting redact still redacts in Block. In Redact, a block-level finding without safe spans follows transformation_failure. States are not aliases for the four actions (see data-schema.md §Enumerations).

## Failure matrix

Configured failure behavior is separate from normal state ceilings. A configured block may block even in Observe or Disabled; tests and docs must make that explicit. Authentication failures, unsupported shapes and malformed caller requests always reject, and cannot be turned into fail-open by policy.

| Condition | Configuration owner | Behavior and required evidence |
| --- | --- | --- |
| Runtime detector deadline expires | Published policy evaluation_timeout | Runtime block or log_only fallback; actual stalled worker is terminated/replaced; no fabricated successful finding |
| Runtime unavailable, malformed response, unknown action or context lost | Cached/pinned ClientConfig evaluation_unavailable | Connector block or log_only fallback with source=connector; allow-and-log input must actually call the mock |
| Client HTTP deadline | Cached/pinned ClientConfig evaluation_timeout | As above; no retry of a content evaluation |
| No resolvable binding | Explicit deployment unresolved_policy_behavior | Default block; fixture may select allow_and_log explicitly; authenticated request never invents a policy version |
| Management unavailable, snapshot age <=60 s | Last validated snapshot | Continue normal evaluation and report age/degraded state; no synchronous dependency |
| Management unavailable, snapshot too old | Cached policy management_unavailable | Apply block or log_only explicitly; use known cached identity and disclose delayed revocation, never authenticate an unknown token |
| Cold runtime without a snapshot | Explicit deployment configuration | Not ready; return sanitized 503; client applies its known configuration or refuses if never configured |
| Audit write fails after a decision | Published policy audit_failure | If block, strengthen to block. If allow_and_log, retain the strongest already-computed action and valid transformed content; never downgrade a block/redact. Return audit_status=failed and operational audit_write_failed status. |
| Invalid/missing spans, failed transformation or invalid transformed response | Published/pinned transformation_failure | Block or explicit log_only fallback using original content. Fail-open may expose original PII and is reported as a failed transformation, never as successful redaction. Mandatory floors may prohibit this configuration. |
| Management audit write fails | Management transaction | Roll back mutation; sanitized 503. No success response for an unaudited mutation. |
| Upstream model fails or times out | Gateway transport contract | Sanitized 502/504; do not fabricate a guarded model response. Context expires if not released. |

Unknown/malformed responses cannot weaken a prior valid restrictive input decision. A completed input block terminates the turn. If input fails open without a runtime context, mark the whole turn as unpinned: invoke the model, then use the captured output evaluation_unavailable action and a connector fallback record. Do not silently claim successful output evaluation or select a new policy mid-turn. A valid input context pins output configuration, even during updates.

## Audit failure precedence

For a successfully determined action, compute the audit fallback as a monotonic escalation: `final_action=max(computed_action, configured_audit_action)` where allow_and_log maps to log_only and block maps to block. Preserve valid transformed content if final action remains redact. If the transformation itself is invalid, follow the explicit transformation_failure rule first, then audit that result. Never recurse into audit on an audit failure. Emit one content-free diagnostic to stderr; if neither database nor connector spool can persist an event, report audit_status=failed instead of claiming durable logging.

## Mandatory floor demonstration

Start with empty floors to demonstrate all five states. Publish a policy with both PII types at input and output and block as the requested action. Return both bindings to Observe, then install a floor requiring that policy ID, both types/stages, at least log_only action, Observe state, no content retention and configured fail-closed transformation handling. Subsequent attempts to disable, unbind, remove a type/stage, select a disallowed policy/version, weaken required failure behavior or roll back below minimum ordinal must fail. Raising a floor against an incompatible current binding must fail atomically. Synthetic state changes always carry actor, reason and fixture-run reference.
