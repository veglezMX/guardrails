# Evaluator adapter contract

## LocalPIIEvaluator interface

Provider-authored evaluators implement `evaluate(content: TextBatch, check: CheckSpec) -> tuple[Finding, ...]` and `capabilities() -> EvaluatorCapabilities`. The capability record declares evaluator ID, detector-format version, supported input/output stages, GR-003 category and both PII types. The publication service validates policy selections against this record before persisting; runtime repeats validation before accepting snapshots (see data-schema.md §CheckSpec).

An evaluator reports findings; the DecisionEngine owns state ceilings, aggregation, failure behavior, transformation and audit. Evaluators never select workload identities, invoke models, emit a final decision, perform network I/O or persist input. The only proof adapter is local_pii with the exact formats in input-output-contract.md. Its deterministic output is independent of gateway/model choice.

## Safety and compatibility

Finding spans use code-point offsets and known segment IDs. The engine validates every finding before transformation. Emit only `GR-003:local_pii:email` and `GR-003:local_pii:phone`; mirror GR-003 into decision guardrail_category. Operational failure codes are separate from detector reasons (see data-schema.md §Enumerations).

Run jobs behind the bounded, deadline-enforcing worker adapter. Test invalid spans, overlapping spans, exceptions, timeout and worker replacement through that boundary. Pass no credentials or file handles into worker payloads. Every registered adapter must pass contract tests before publication capability exposure.

DEC-015 prohibits third-party/customer evaluator code in-process in the committed MVP. This release has no dynamic plugin upload, arbitrary import paths, external plugin marketplace or policy signing facility. Retained Bedrock/deterministic/JSON prototype modules do not become proof capabilities merely by remaining in the tree. A new adapter or detector format changes the capability manifest and requires explicit compatibility review and new fixtures; it does not silently change an immutable policy's semantics.
