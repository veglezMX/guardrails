"""Immutable request adapters over one deployment-owned tenant Rules pool."""
from __future__ import annotations

import hashlib
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .errors import EvolutionError
from .models import Capability, ContentPart, Finding, Rule
from .plugins import PinnedPiiEvaluator
from .rules import CONTENT_LIST_RULE_PREFIX, MAX_FINDINGS, REVISIONS, RulesBinding

MAX_WIRE_BYTES = 6 * 1024 * 1024
PINNED_RULE_ARTIFACTS = MappingProxyType({
    "evolution/rules.py": "7065f051204302256abce3d3cd9c91294a12f8bb099f181bffc530a9f4917476",
    "evolution/rules_worker.py": "1164f9a3f176b74dbe5207ae29ef082ecdc25d5305764d726a38c53315fe86bb",
    "evaluators/pii.py": "ae5ac4a0aac37d0b2d4833e00c0bb5d661d46b4e2b82fd97fe5bf8810a11c297",
    "local_contract.py": "69fc3df12bfb63376ebbaf34e6b1f38fad41b542aff93723eaec5c2147f470f5",
})
CAPABILITY_IDS = MappingProxyType({"pii": "rules-pii", "secret": "rules-secret", "term": "rules-term", "topic": "rules-topic", "json_schema": "rules-json-schema"})
ERRORS = frozenset({"validation_failed", "policy_unresolved", "capability_missing", "unsupported_surface", "payload_too_large", "inspection_incomplete", "unsafe_transformation", "evaluation_timeout", "evaluator_unavailable"})


def _verify_rule_artifacts():
    root = Path(__file__).resolve().parents[1]
    for name, expected in PINNED_RULE_ARTIFACTS.items():
        try:
            valid = hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
        except OSError:
            valid = False
        if not valid:
            raise EvolutionError("evaluator_unavailable")


def _result(result, field):
    if type(result) is dict and set(result) == {"error"} and type(result["error"]) is str and result["error"] in ERRORS:
        raise EvolutionError(result["error"])
    if type(result) is not dict or set(result) != {field} or type(result[field]) is not list:
        raise EvolutionError("inspection_incomplete")
    return result[field]


def _finding_shape(found, part, rule):
    kind = rule.kind if rule is not None else "term"
    categories = set(rule.config.entities) if kind == "pii" else set(rule.config.detectors) if kind == "secret" else {"json_schema"} if kind == "json_schema" else {"term", "topic", "content_allowlist"}
    reasons = {"pii_detected"} if kind == "pii" else {"secret_detected"} if kind == "secret" else {"schema_invalid"} if kind == "json_schema" else {"content_denied", "content_not_allowed"}
    if found.category not in categories or found.reason_code not in reasons or found.score != 1.0:
        raise EvolutionError("inspection_incomplete")
    if kind == "json_schema":
        if found.start is not None or found.json_pointer is not None:
            raise EvolutionError("inspection_incomplete")
        return
    if part.kind == "text":
        value = part.text
        if found.json_pointer is not None:
            raise EvolutionError("inspection_incomplete")
    elif part.kind == "json" and isinstance(found.json_pointer, str):
        value = part.value
        try:
            for encoded in found.json_pointer.split("/")[1:] if found.json_pointer else ():
                token = encoded.replace("~1", "/").replace("~0", "~")
                if isinstance(value, dict):
                    value = value[token]
                elif isinstance(value, (tuple, list)) and token.isascii() and token.isdecimal() and str(int(token)) == token:
                    value = value[int(token)]
                else:
                    raise ValueError
        except (KeyError, IndexError, ValueError):
            raise EvolutionError("inspection_incomplete") from None
    else:
        raise EvolutionError("inspection_incomplete")
    if type(value) is not str or (found.end is not None and found.end > len(value)):
        raise EvolutionError("inspection_incomplete")
    span_required = kind == "pii" or kind == "secret" and found.category != "private_key"
    if (span_required and found.start is None) or (found.category in {"topic", "content_allowlist"} and found.start is not None):
        raise EvolutionError("inspection_incomplete")


@dataclass(frozen=True)
class RulesProcessEvaluator:
    """Binding changes request bytes; all jobs use the same tenant pool."""
    pool: RulesProcessPool
    binding: RulesBinding

    def __post_init__(self):
        if not isinstance(self.pool, RulesProcessPool) or not isinstance(self.binding, RulesBinding) or self.binding.stage not in {"input", "output"}:
            raise EvolutionError("capability_missing")

    def _payload(self, operation, parts, deadline):
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise EvolutionError("validation_failed")
        if not isinstance(parts, tuple) or not 1 <= len(parts) <= 64 or any(not isinstance(part, ContentPart) for part in parts):
            raise EvolutionError("validation_failed")
        return {"operation": operation, "policy": self.binding.policy_bytes.decode("utf-8"), "stage": self.binding.stage,
                "parts": [part.model_dump(mode="json", warnings="error") for part in parts],
                "deadline": min(deadline, time.monotonic() + self.pool.timeout_ms / 1000)}

    def _findings(self, result, parts, rule=None):
        rows = _result(result, "findings")
        if len(rows) > MAX_FINDINGS:
            raise EvolutionError("inspection_incomplete")
        findings = tuple(Finding.model_validate(row) for row in rows)
        by_id = {part.part_id: part for part in parts}
        allowed_lists = {CONTENT_LIST_RULE_PREFIX + value for value in self.binding.policy().residency.content_lists} if rule is None else set()
        for found in findings:
            if found.part_id not in by_id or (rule is not None and (found.rule_id != rule.rule_id or found.evaluator_revision != rule.evaluator_revision)) or (rule is None and (found.rule_id not in allowed_lists or found.evaluator_revision != "lexical-v1")):
                raise EvolutionError("inspection_incomplete")
            _finding_shape(found, by_id[found.part_id], rule)
        if len(set(findings)) != len(findings):
            raise EvolutionError("inspection_incomplete")
        return findings

    async def evaluate(self, rule: Rule, parts: tuple[ContentPart, ...], deadline_monotonic: float) -> tuple[Finding, ...]:
        if not isinstance(rule, Rule):
            raise EvolutionError("capability_missing")
        payload = self._payload("evaluate", parts, deadline_monotonic)
        payload["rule"] = rule.model_dump(mode="json", warnings="error")
        return await self.pool._submit(payload, deadline_monotonic, lambda result: self._findings(result, parts, rule))

    async def content_lists(self, parts: tuple[ContentPart, ...], deadline_monotonic: float) -> tuple[Finding, ...]:
        payload = self._payload("content_lists", parts, deadline_monotonic)
        return await self.pool._submit(payload, deadline_monotonic, lambda result: self._findings(result, parts))

    async def transform(self, parts: tuple[ContentPart, ...], findings: tuple[Finding, ...], deadline_monotonic: float) -> tuple[ContentPart, ...]:
        if not isinstance(findings, tuple) or not 1 <= len(findings) <= MAX_FINDINGS or any(not isinstance(found, Finding) for found in findings):
            raise EvolutionError("unsafe_transformation")
        payload = self._payload("transform", parts, deadline_monotonic)
        payload["findings"] = [found.model_dump(mode="json", warnings="error") for found in findings]

        def decode(result):
            rows = _result(result, "parts")
            if len(rows) != len(parts):
                raise EvolutionError("inspection_incomplete")
            output = tuple(ContentPart.model_validate(row) for row in rows)
            for before, after in zip(parts, output, strict=True):
                if before.model_dump(exclude={"text", "value"}) != after.model_dump(exclude={"text", "value"}):
                    raise EvolutionError("inspection_incomplete")
            return output

        return await self.pool._submit(payload, deadline_monotonic, decode)


class RulesProcessPool(PinnedPiiEvaluator):
    """Long-lived fixed tenant pool; binding never starts another process."""
    _max_wire_bytes = MAX_WIRE_BYTES
    _max_result_bytes = MAX_WIRE_BYTES

    @staticmethod
    def _verify():
        _verify_rule_artifacts()

    @staticmethod
    def _worker_command():
        return (sys.executable, "-m", "guardrails.evolution.rules_worker", "--worker")

    def bound(self, binding: RulesBinding) -> RulesProcessEvaluator:
        return RulesProcessEvaluator(self, binding)

    async def evaluate(self, rule, parts, deadline_monotonic):
        raise EvolutionError("policy_unresolved")

    def capabilities(self):
        status = "enabled" if self.health()["ready"] else "unavailable"
        return tuple(Capability(capability_id=CAPABILITY_IDS[kind], stage=stage, part_kinds=("text", "json"),
            actions=("allow", "warn", "block") if kind in {"topic", "json_schema"} else ("allow", "warn", "redact", "block"),
            evaluator_revision=revision, max_input_bytes=65536, timeout_ms=self.timeout_ms, zone=self.zone, status=status)
            for kind, revision in REVISIONS.items() for stage in ("input", "output"))
