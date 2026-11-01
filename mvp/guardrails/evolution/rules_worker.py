"""Closed deterministic Rules protocol, executed only in tenant pool workers."""
from __future__ import annotations

import math
import sys

from .errors import EvolutionError
from .models import (
    ContentPart,
    Finding,
    PolicyVersion,
    Rule,
    canonical_json_bytes,
    strict_json_loads,
)
from .rules import (
    MAX_FINDINGS,
    RulesBinding,
    evaluate_content_lists,
    evaluate_rule,
    transform_rule_findings,
)

MAX_WIRE_BYTES = 6 * 1024 * 1024
OPERATIONS = frozenset({"evaluate", "content_lists", "transform"})


def execute(payload):
    """Validate every private protocol field before running a bound operation."""
    if type(payload) is not dict or payload.get("operation") not in OPERATIONS:
        raise EvolutionError("validation_failed")
    operation = payload["operation"]
    fields = {"operation", "policy", "stage", "parts", "deadline"}
    fields |= {"rule"} if operation == "evaluate" else {"findings"} if operation == "transform" else set()
    if set(payload) != fields or type(payload["policy"]) is not str or payload["stage"] not in {"input", "output"}:
        raise EvolutionError("validation_failed")
    deadline = payload["deadline"]
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise EvolutionError("validation_failed")
    policy_raw = payload["policy"].encode("utf-8")
    policy = PolicyVersion.model_validate(strict_json_loads(policy_raw, max_bytes=1024 * 1024))
    binding = RulesBinding(policy, payload["stage"])
    if binding.policy_bytes != policy_raw:
        raise EvolutionError("policy_unresolved")
    if type(payload["parts"]) is not list or not 1 <= len(payload["parts"]) <= 64:
        raise EvolutionError("validation_failed")
    parts = tuple(ContentPart.model_validate(part) for part in payload["parts"])
    if operation == "evaluate":
        findings = evaluate_rule(binding, Rule.model_validate(payload["rule"]), parts, deadline)
    elif operation == "content_lists":
        findings = evaluate_content_lists(binding, parts, deadline)
    else:
        if type(payload["findings"]) is not list or not 1 <= len(payload["findings"]) <= MAX_FINDINGS:
            raise EvolutionError("unsafe_transformation")
        findings = tuple(Finding.model_validate(row) for row in payload["findings"])
        transformed = transform_rule_findings(binding.policy(), binding.stage, parts, findings, deadline)
        return {"parts": [part.model_dump(mode="json") for part in transformed]}
    return {"findings": [finding.model_dump(mode="json") for finding in findings]}


def main():
    import resource

    from .rules_process import _verify_rule_artifacts

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 31))
    _verify_rule_artifacts()
    print('{"api_major":1,"ready":true}', flush=True)
    while raw := sys.stdin.buffer.readline(MAX_WIRE_BYTES + 2):
        if len(raw) > MAX_WIRE_BYTES + 1 or not raw.endswith(b"\n"):
            return
        try:
            result = execute(strict_json_loads(raw, max_bytes=MAX_WIRE_BYTES + 1))
        except EvolutionError as error:
            result = {"error": error.code}
        except (ValueError, TypeError, RecursionError):
            result = {"error": "validation_failed"}
        except Exception:  # noqa: BLE001 - never serialize arbitrary worker exceptions
            result = {"error": "evaluator_unavailable"}
        encoded = canonical_json_bytes(result) + b"\n"
        if len(encoded) > MAX_WIRE_BYTES:
            encoded = b'{"error":"inspection_incomplete"}\n'
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    main()
