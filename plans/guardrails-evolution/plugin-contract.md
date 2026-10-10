# Plugin Contract

## Trigger Condition

The product extends through stage, engine, action and connector seams.

## Scope Boundary

Only locally installed, pinned plugins are supported. Policies cannot load code, install packages or execute arbitrary prompts. The injection engine is a pretrained classifier, so there is no guardrail LLM prompt catalog in this plan.

## Interfaces

```python
class EvaluatorPort(Protocol):
    def capabilities(self) -> tuple[Capability, ...]: ...
    async def evaluate(self, rule: Rule, parts: tuple[ContentPart, ...],
                       deadline_monotonic: float) -> tuple[Finding, ...]: ...
    async def close(self) -> None: ...

class ConnectorPort(Protocol):
    def normalize(self, payload: JsonObject, stage: Stage) -> EvaluationRequest: ...
    def enforce(self, decision: Decision, payload: JsonObject) -> JsonObject: ...
```

Signatures use domain types (see data-schema.md); wire adapters convert explicitly. Evaluators return findings, never choose workload identity or bypass policy aggregation. Actions transform through the runtime's validated transformation port; engines cannot return arbitrary replacement network requests. Stages declare input kinds, context requirements and permitted actions in Capability.

## Lifecycle and Isolation

Load at startup from an explicit registry and artifact lock. Check API major version, artifact digest and capabilities, initialize/prewarm, mark ready, then accept traffic. Activation verifies required capabilities. Unload stops admission, cancels pending jobs and releases memory. No hot loading from a tenant request. A rolling engine update uses a new revision and a new approved policy, retaining the old worker until pinned contexts expire.

Pure deterministic rules run in bounded worker processes. Classifier/OCR workers run separate non-root containers with read-only model artifacts, no network except the declared input service, no shell, bounded CPU/memory/concurrency and no raw-content log sinks. Termination after the budget must reclaim the worker; timing out the awaiting coroutine alone is insufficient.

## Compatibility

Plugin API v1 serves guardrails-v2. Additive capability fields require minor version; changed stage semantics or signature requires new major. Unknown fields/actions/revisions reject activation. Connector conformance includes identity, transforms, unsupported surface handling, cancellation, no-bypass cases and applied-policy attribution. Capability declarations require passing evidence, not merely installed code.

## Cross-References

(see component-specs.md §EvaluatorRegistry), (see data-schema.md §Capability), (see guardrail-coverage.md).
