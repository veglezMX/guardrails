"""Pure deterministic rules for execution inside the bounded tenant worker.

No worker, network client or mutable policy cache lives here. The process-pool
adapter owns admission, cancellation and the public EvaluatorPort interface.
"""
from __future__ import annotations

import ipaddress
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType

from .errors import EvolutionError
from .models import (
    ContentPart,
    Finding,
    PolicyVersion,
    Rule,
    canonical_json_bytes,
    resource_digest,
    strict_json_loads,
    validate_json_value,
    validate_schema,
)

REVISIONS = MappingProxyType({"pii": "pii-v2", "secret": "secrets-v1", "term": "lexical-v1", "topic": "lexical-v1", "json_schema": "schema-v1"})
TOPICS = MappingProxyType({"finance": ("invoice", "budget", "payment"), "medical": ("diagnosis", "prescription", "treatment"), "software": ("source code", "compiler", "repository")})
STAGES = frozenset({"input", "output", "retrieval", "tool_arguments", "tool_result"})
CONTENT_LIST_RULE_PREFIX = "content-list:"
MAX_FINDINGS = 4096
_CARD = re.compile(r"[0-9](?:[0-9 -]*[0-9])?", re.ASCII)
_IPV4 = re.compile(r"[0-9]+(?:\.[0-9]+){3,}", re.ASCII)
_AWS = re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])", re.ASCII)
_GITHUB = re.compile(r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9_])", re.ASCII)
_BEGIN = re.compile(r"-----BEGIN (PRIVATE KEY|RSA PRIVATE KEY|EC PRIVATE KEY|OPENSSH PRIVATE KEY)-----")


def _fail(code="validation_failed"):
    raise EvolutionError(code)


def _deadline(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        _fail()
    if time.monotonic() >= deadline:
        _fail("evaluation_timeout")


@dataclass(frozen=True, init=False)
class RulesBinding:
    """Canonical bytes isolate a job from later mutations of caller objects."""
    policy_bytes: bytes
    stage: str

    def __init__(self, policy: PolicyVersion, stage: str):
        from .compiler import policy_digest

        if not isinstance(policy, PolicyVersion) or type(stage) is not str or stage not in STAGES:
            _fail()
        try:
            raw = canonical_json_bytes(policy.model_dump(mode="json", warnings="error"))
            checked = PolicyVersion.model_validate(strict_json_loads(raw, max_bytes=1024 * 1024))
            if policy_digest(checked) != checked.compiled_digest:
                _fail("policy_unresolved")
            for resource in (checked.residency, checked.quota, *checked.resolved_lists, *checked.resolved_destinations, *checked.providers, *checked.tools):
                if resource_digest(resource) != resource.digest or resource.tenant_id not in {checked.tenant_id, "_platform"}:
                    _fail("policy_unresolved")
            lists = {item.list_id: item for item in checked.resolved_lists}
            for list_id in checked.residency.content_lists:
                if list_id not in lists or lists[list_id].kind != "content":
                    _fail("policy_unresolved")
            for rule in checked.rules:
                if rule.kind in REVISIONS:
                    validate_rule(rule, checked)
        except (ValueError, TypeError, RecursionError):
            _fail()
        object.__setattr__(self, "policy_bytes", raw)
        object.__setattr__(self, "stage", stage)

    def policy(self):
        return PolicyVersion.model_validate(strict_json_loads(self.policy_bytes, max_bytes=1024 * 1024))


def validate_rule(rule: Rule, policy: PolicyVersion | None = None):
    """Compiler/activation hook: reject unsupported revisions and unresolved lists."""
    if not isinstance(rule, Rule):
        _fail("capability_missing")
    try:
        Rule.model_validate(rule.model_dump(mode="json", warnings="error"))
        if REVISIONS.get(rule.kind) != rule.evaluator_revision:
            _fail("capability_missing")
        if rule.kind == "json_schema":
            validate_schema(rule.config.schema)
        if rule.kind in {"term", "topic"}:
            if policy is None:
                _fail("policy_unresolved")
            selected = next((item for item in policy.resolved_lists if item.list_id == rule.config.list_id), None)
            if selected is None or selected.kind != "content" or resource_digest(selected) != selected.digest:
                _fail("policy_unresolved")
    except (ValueError, TypeError, RecursionError):
        _fail()


def _parts(parts):
    if not isinstance(parts, tuple) or not 1 <= len(parts) <= 64 or any(not isinstance(part, ContentPart) for part in parts):
        _fail()
    if len({part.part_id for part in parts}) != len(parts):
        _fail()
    total = 0
    for part in parts:
        try:
            ContentPart.model_validate(part.model_dump(mode="json", warnings="error"))
        except (ValueError, TypeError, RecursionError):
            _fail()
        if part.kind not in {"text", "json"}:
            _fail("unsupported_surface")
        total += len(part.text.encode("utf-8") if part.kind == "text" else canonical_json_bytes(part.value))
    if total > 65536:
        _fail("payload_too_large")


def string_leaves(parts: tuple[ContentPart, ...]):
    """Return (part, JSON pointer or None, original string), never object keys."""
    _parts(parts)

    def walk(value, pointer):
        if type(value) is str:
            yield pointer, value
        elif isinstance(value, dict):
            for key in sorted(value):
                yield from walk(value[key], pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                yield from walk(child, pointer + "/" + str(index))

    return tuple((part, pointer, text) for part in parts for pointer, text in (((None, part.text),) if part.kind == "text" else walk(part.value, "")))


def _normalized(text, deadline):
    """Map complete normalization clusters; partial expansions are unsafe.

    Combining sequences and Hangul composition stay in one cluster. An exact
    whole-string normalization cross-check makes an unproved boundary fail
    closed for transformation without suppressing detection.
    """
    chunks, spans, start, cluster = [], [], 0, ""

    def flush(end):
        normalized = unicodedata.normalize("NFKC", cluster).casefold()
        chunks.append(normalized)
        spans.extend([(start, end)] * len(normalized))

    for index, char in enumerate(text):
        if index % 128 == 0:
            _deadline(deadline)
        decomposed = unicodedata.normalize("NFKD", char)
        joins = bool(cluster and decomposed and unicodedata.combining(decomposed[0]))
        if cluster and not joins and decomposed:
            previous = unicodedata.normalize("NFKC", cluster)
            joins = bool(previous and len(unicodedata.normalize("NFC", previous[-1] + decomposed[0])) == 1)
        if cluster and not joins:
            flush(index)
            cluster, start = "", index
        cluster += char
    if cluster:
        flush(len(text))
    whole = unicodedata.normalize("NFKC", text).casefold()
    return whole, tuple(spans) if "".join(chunks) == whole else None


def _matches(text, literal, deadline, *, whole_words=False, normalized_text=None):
    normalized, mapping = _normalized(text, deadline) if normalized_text is None else normalized_text
    needle = unicodedata.normalize("NFKC", literal).casefold()
    if not needle:
        _fail()
    offset = 0
    while (start := normalized.find(needle, offset)) >= 0:
        _deadline(deadline)
        end = start + len(needle)
        offset = start + 1
        if whole_words and ((start and _word(normalized[start - 1])) or (end < len(normalized) and _word(normalized[end]))):
            continue
        safe = mapping is not None and (start == 0 or mapping[start - 1] != mapping[start]) and (end == len(normalized) or mapping[end - 1] != mapping[end])
        yield (mapping[start][0], mapping[end - 1][1]) if safe else (None, None)


def _word(char):
    return char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")


def _outside(text, start, end, *, dots=False):
    return not any(char and (char.isalnum() or char == "_" or dots and char == ".") for char in (text[start - 1] if start else "", text[end] if end < len(text) else ""))


def _luhn(digits):
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit
        total += digit
    return total % 10 == 0


def _pii(rule, text, deadline):
    from ..evaluators.pii import LocalPIIEvaluator
    from ..local_contract import CheckSpec, TextBatch, TextSegment

    selected = tuple("phone" if name == "international_phone" else name for name in rule.config.entities if name in {"email", "international_phone"})
    if selected:
        check = CheckSpec(check_id="pinned-pii", evaluator="local_pii", stages=("input",), pii_types=selected, requested_action="block")
        for found in LocalPIIEvaluator().evaluate(TextBatch(segments=(TextSegment(segment_id="0", text=text),)), check):
            _deadline(deadline)
            yield "international_phone" if found.reason_code.endswith(":phone") else "email", found.start, found.end
    if "payment_card" in rule.config.entities:
        for match in _CARD.finditer(text):
            _deadline(deadline)
            digits = match.group().replace(" ", "").replace("-", "")
            if _outside(text, *match.span()) and _luhn(digits):
                yield "payment_card", *match.span()
    if "ipv4" in rule.config.entities:
        for match in _IPV4.finditer(text):
            _deadline(deadline)
            if _outside(text, *match.span(), dots=True):
                try:
                    ipaddress.IPv4Address(match.group())
                except ipaddress.AddressValueError:
                    continue
                yield "ipv4", *match.span()


def _secrets(rule, text, deadline):
    for name, pattern in (("aws_access_key", _AWS), ("github_token", _GITHUB)):
        if name in rule.config.detectors:
            for match in pattern.finditer(text):
                _deadline(deadline)
                yield name, *match.span()
    if "private_key" in rule.config.detectors:
        for begin in _BEGIN.finditer(text):
            _deadline(deadline)
            closing = "-----END " + begin.group(1) + "-----"
            end = text.find(closing, begin.end())
            nested = _BEGIN.search(text, begin.end(), end if end >= 0 else len(text))
            if end < 0 or nested or len(text[begin.end():end].encode("utf-8")) > 65536:
                yield "private_key", None, None
            else:
                yield "private_key", begin.start(), end + len(closing)


def _equal(left, right):
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def schema_valid(value, schema, deadline_monotonic):
    """Validate the deliberately bounded dialect, with JSON numeric semantics."""
    _deadline(deadline_monotonic)
    try:
        validate_schema(schema)
        validate_json_value(value)
    except (ValueError, TypeError, RecursionError):
        _fail()

    def matches(item, node):
        _deadline(deadline_monotonic)
        numeric = type(item) in (int, float)
        types = {"null": item is None, "boolean": type(item) is bool, "object": isinstance(item, dict), "array": isinstance(item, (list, tuple)), "number": numeric, "integer": numeric and (type(item) is int or item.is_integer()), "string": type(item) is str}
        if "type" in node and not types[node["type"]]:
            return False
        if "const" in node and not _equal(item, node["const"]):
            return False
        if "enum" in node and not any(_equal(item, choice) for choice in node["enum"]):
            return False
        if "anyOf" in node and not any(matches(item, child) for child in node["anyOf"]):
            return False
        if numeric and ("minimum" in node and item < node["minimum"] or "maximum" in node and item > node["maximum"]):
            return False
        if type(item) is str and ("minLength" in node and len(item) < node["minLength"] or "maxLength" in node and len(item) > node["maxLength"]):
            return False
        if isinstance(item, (list, tuple)):
            if "minItems" in node and len(item) < node["minItems"] or "maxItems" in node and len(item) > node["maxItems"]:
                return False
            if "items" in node and not all(matches(child, node["items"]) for child in item):
                return False
        if isinstance(item, dict):
            properties = node.get("properties", {})
            if any(key not in item for key in node.get("required", ())):
                return False
            if node.get("additionalProperties") is False and set(item) - set(properties):
                return False
            if not all(matches(item[key], child) for key, child in properties.items() if key in item):
                return False
        return True

    return matches(value, schema)


def _entry_matches(text, entry, deadline, normalized_text=None):
    if entry.startswith("term:"):
        for start, end in _matches(text, entry[5:], deadline, normalized_text=normalized_text):
            yield "term", start, end
    elif entry.startswith("topic:") and entry[6:] in TOPICS:
        for term in TOPICS[entry[6:]]:
            for _ in _matches(text, term, deadline, whole_words=True, normalized_text=normalized_text):
                yield "topic", None, None
    else:
        _fail("policy_unresolved")


def evaluate_rule(binding: RulesBinding, rule: Rule, parts: tuple[ContentPart, ...], deadline_monotonic: float) -> tuple[Finding, ...]:
    """Worker-side entry point, with no scheduling or tenant-global state."""
    _deadline(deadline_monotonic)
    if not isinstance(binding, RulesBinding):
        _fail("policy_unresolved")
    policy = binding.policy()
    if not isinstance(rule, Rule) or not any(item == rule for item in policy.rules) or binding.stage not in rule.stages:
        _fail("policy_unresolved")
    validate_rule(rule, policy)
    leaves = string_leaves(parts)
    findings = []

    def emit(part, pointer, category, reason, start=None, end=None):
        if len(findings) >= MAX_FINDINGS:
            _fail("inspection_incomplete")
        findings.append(Finding(rule_id=rule.rule_id, category=category, part_id=part.part_id, reason_code=reason, start=start, end=end, json_pointer=pointer, score=1.0, evaluator_revision=rule.evaluator_revision))

    if rule.kind == "json_schema":
        for part in parts:
            _deadline(deadline_monotonic)
            try:
                value = strict_json_loads(part.text, max_bytes=65536) if part.kind == "text" else part.value
                validate_json_value(value)
            except (ValueError, TypeError, RecursionError):
                emit(part, None, "json_schema", "schema_invalid")
                continue
            if not schema_valid(value, rule.config.schema, deadline_monotonic):
                emit(part, None, "json_schema", "schema_invalid")
    elif rule.kind in {"pii", "secret"}:
        detector, reason = (_pii, "pii_detected") if rule.kind == "pii" else (_secrets, "secret_detected")
        for part, pointer, text in leaves:
            _deadline(deadline_monotonic)
            for category, start, end in detector(rule, text, deadline_monotonic):
                emit(part, pointer, category, reason, start, end)
    else:
        from .compiler import effective_lists

        ids = {*policy.residency.content_lists, rule.config.list_id}
        selected = tuple(item for item in policy.resolved_lists if item.list_id in ids)
        allow, deny = effective_lists(selected, "content")
        # JSON uses each actual string leaf; primitive values remain typed.
        for part, pointer, text in leaves:
            _deadline(deadline_monotonic)
            normalized_text = _normalized(text, deadline_monotonic)
            for entry in sorted(deny):
                for category, start, end in _entry_matches(text, entry, deadline_monotonic, normalized_text):
                    emit(part, pointer, category, "content_denied", start, end)
            if allow is not None and not any(next(_entry_matches(text, entry, deadline_monotonic, normalized_text), None) is not None for entry in sorted(allow)):
                emit(part, pointer, "content_allowlist", "content_not_allowed")
    _deadline(deadline_monotonic)
    return tuple(sorted(set(findings), key=lambda found: (found.part_id, found.json_pointer or "", found.start if found.start is not None else -1, found.end if found.end is not None else -1, found.category, found.reason_code)))


def _same_structure(before, after):
    if type(before) is str:
        return type(after) is str
    if isinstance(before, dict):
        return isinstance(after, dict) and before.keys() == after.keys() and all(_same_structure(before[key], after[key]) for key in before)
    if isinstance(before, (list, tuple)):
        return isinstance(after, (list, tuple)) and len(before) == len(after) and all(_same_structure(a, b) for a, b in zip(before, after))
    return type(before) is type(after) and before == after


def evaluate_content_lists(binding: RulesBinding, parts: tuple[ContentPart, ...], deadline_monotonic: float) -> tuple[Finding, ...]:
    """Independent policy constraints, attributed to exact immutable ListSet IDs.

    The compiler reserves ``content-list:`` against authored rules when these
    constraints are active. Runtime invokes this once per stage, even if there
    are no term/topic rules; the binding enforcement state still owns action.
    """
    from .compiler import effective_lists

    _deadline(deadline_monotonic)
    if not isinstance(binding, RulesBinding):
        _fail("policy_unresolved")
    policy = binding.policy()
    selected = tuple(item for item in policy.resolved_lists if item.list_id in policy.residency.content_lists)
    allow, _ = effective_lists(selected, "content")
    findings = []

    def emit(item, part, pointer, category, reason, start=None, end=None):
        if len(findings) >= MAX_FINDINGS:
            _fail("inspection_incomplete")
        findings.append(Finding(rule_id=CONTENT_LIST_RULE_PREFIX + item.list_id, category=category, part_id=part.part_id, reason_code=reason, start=start, end=end, json_pointer=pointer, score=1.0, evaluator_revision="lexical-v1"))

    for part, pointer, text in string_leaves(parts):
        _deadline(deadline_monotonic)
        normalized_text = _normalized(text, deadline_monotonic)
        for item in sorted(selected, key=lambda entry: entry.list_id):
            for entry in sorted(item.deny):
                for category, start, end in _entry_matches(text, entry, deadline_monotonic, normalized_text):
                    emit(item, part, pointer, category, "content_denied", start, end)
        if allow is not None and not any(next(_entry_matches(text, entry, deadline_monotonic, normalized_text), None) is not None for entry in sorted(allow)):
            for item in selected:
                if item.allow_enabled:
                    emit(item, part, pointer, "content_allowlist", "content_not_allowed")
    _deadline(deadline_monotonic)
    return tuple(sorted(set(findings), key=lambda found: (found.part_id, found.rule_id, found.json_pointer or "", found.start if found.start is not None else -1, found.end if found.end is not None else -1, found.category, found.reason_code)))


def transform_rule_findings(policy, stage, parts, findings, deadline_monotonic):
    """Runtime-owned, all-or-nothing category masking with schema revalidation.

    Findings are checked against the exact bound detector before any output is
    constructed. Unsupported/ambiguous spans never become replacement text.
    Text carrying a schema must remain JSON with identical keys and non-string
    values; structured parts replace only original string leaves.
    """
    _deadline(deadline_monotonic)
    binding = RulesBinding(policy, stage)
    leaves = {(part.part_id, pointer): text for part, pointer, text in string_leaves(parts)}
    if not isinstance(findings, tuple) or not 1 <= len(findings) <= MAX_FINDINGS or any(not isinstance(found, Finding) for found in findings):
        _fail("unsafe_transformation")
    rules = {rule.rule_id: rule for rule in binding.policy().rules}
    expected = set()
    if any(found.rule_id.startswith(CONTENT_LIST_RULE_PREFIX) for found in findings):
        expected.update(evaluate_content_lists(binding, parts, deadline_monotonic))
    for rule_id in {found.rule_id for found in findings}:
        if rule_id.startswith(CONTENT_LIST_RULE_PREFIX):
            continue
        rule = rules.get(rule_id)
        if rule is not None and rule.kind == "bedrock" and stage in rule.stages:
            # Only the internal managed adapter can supply these findings. It
            # has already reconstructed the exact vendor mask before emitting
            # spans; never repeat an external request during transformation.
            managed = tuple(found for found in findings if found.rule_id == rule_id)
            if (rule.evaluator_revision != "bedrock-v1"
                    or any(found.evaluator_revision != "bedrock-v1"
                           or found.category != "managed" or found.reason_code != "content_denied"
                           or found.score is not None or found.json_pointer is not None
                           for found in managed)):
                _fail("unsafe_transformation")
            expected.update(managed)
            continue
        if rule is None or rule.kind not in {"pii", "secret", "term"} or stage not in rule.stages:
            _fail("unsafe_transformation")
        expected.update(evaluate_rule(binding, rule, parts, deadline_monotonic))
    spans = {}
    for found in findings:
        key = (found.part_id, found.json_pointer)
        if found not in expected or key not in leaves or type(found.start) is not int or type(found.end) is not int or not 0 <= found.start < found.end <= len(leaves[key]):
            _fail("unsafe_transformation")
        spans.setdefault(key, []).append((found.start, found.end, found.category))
    replacements = {}
    for key, matches in spans.items():
        _deadline(deadline_monotonic)
        merged = []
        for start, end, category in sorted(set(matches)):
            if merged and start < merged[-1][1]:
                old_start, old_end, old_category = merged[-1]
                merged[-1] = (old_start, max(old_end, end), category if category == old_category else "sensitive")
            else:
                merged.append((start, end, category))
        pieces, offset = [], 0
        for start, end, category in merged:
            pieces.extend((leaves[key][offset:start], "[REDACTED:" + category + "]"))
            offset = end
        replacements[key] = "".join((*pieces, leaves[key][offset:]))

    def rewrite(part_id, value, pointer=""):
        _deadline(deadline_monotonic)
        if type(value) is str:
            return replacements.get((part_id, pointer), value)
        if isinstance(value, dict):
            return {key: rewrite(part_id, child, pointer + "/" + key.replace("~", "~0").replace("/", "~1")) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [rewrite(part_id, child, pointer + "/" + str(index)) for index, child in enumerate(value)]
        return value

    schemas = tuple(rule.config.schema for rule in policy.rules if rule.kind == "json_schema" and stage in rule.stages)
    output = []
    try:
        for part in parts:
            data = part.model_dump(mode="json")
            if part.kind == "text":
                data["text"] = replacements.get((part.part_id, None), part.text)
                if schemas:
                    before = strict_json_loads(part.text, max_bytes=65536)
                    after = strict_json_loads(data["text"], max_bytes=65536)
                    if not _same_structure(before, after):
                        _fail("unsafe_transformation")
            else:
                before, after = part.value, rewrite(part.part_id, part.value)
                data["value"] = after
            if schemas and any(not schema_valid(before, schema, deadline_monotonic) or not schema_valid(after, schema, deadline_monotonic) for schema in schemas):
                _fail("unsafe_transformation")
            output.append(ContentPart.model_validate(data))
        _parts(tuple(output))
    except (ValueError, TypeError, RecursionError):
        _fail("unsafe_transformation")
    _deadline(deadline_monotonic)
    return tuple(output)
