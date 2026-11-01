"""Policy-bound, one-shot ApplyGuardrail over the existing pinned HTTPS port.

No ambient credentials, SDK retry loop, endpoint discovery or content logging.
Managed execution requires explicit operator enablement and approved residency.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from pydantic import ValidationError

from .egress import PinnedHttpsTransport
from .errors import EvolutionError
from .models import (
    Capability,
    ContentPart,
    Finding,
    PolicyVersion,
    Rule,
    canonical_json_bytes,
)
from .residency import ResidencyGuard

REVISION = "bedrock-v1"
MAX_INPUT_BYTES = 65536
MAX_RESPONSE_BYTES = 1048576
PII_TYPES = frozenset(
    [
        "ADDRESS",
        "AGE",
        "AWS_ACCESS_KEY",
        "AWS_SECRET_KEY",
        "CA_HEALTH_NUMBER",
        "CA_SOCIAL_INSURANCE_NUMBER",
        "CREDIT_DEBIT_CARD_CVV",
        "CREDIT_DEBIT_CARD_EXPIRY",
        "CREDIT_DEBIT_CARD_NUMBER",
        "DRIVER_ID",
        "EMAIL",
        "INTERNATIONAL_BANK_ACCOUNT_NUMBER",
        "IP_ADDRESS",
        "LICENSE_PLATE",
        "MAC_ADDRESS",
        "NAME",
        "PASSWORD",
        "PHONE",
        "PIN",
        "SWIFT_CODE",
        "UK_NATIONAL_HEALTH_SERVICE_NUMBER",
        "UK_NATIONAL_INSURANCE_NUMBER",
        "UK_UNIQUE_TAXPAYER_REFERENCE_NUMBER",
        "URL",
        "USERNAME",
        "US_BANK_ACCOUNT_NUMBER",
        "US_BANK_ROUTING_NUMBER",
        "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER",
        "US_PASSPORT_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER",
        "VEHICLE_IDENTIFICATION_NUMBER",
    ]
)


def _require(condition, code="evaluator_unavailable"):
    if not condition:
        raise EvolutionError(code)


def _now():
    return datetime.now(UTC)


@dataclass(frozen=True, repr=False)
class AwsCredentials:
    """Operator-provided snapshot; secrets are never discovered or refreshed."""

    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None

    def validate(self, now):
        _require(
            type(now) is datetime
            and now.tzinfo is not None
            and now.utcoffset().total_seconds() == 0
        )
        _require(
            type(self.access_key_id) is str
            and re.fullmatch(r"(?:AKIA|ASIA)[A-Z0-9]{16}", self.access_key_id)
        )
        _require(
            type(self.secret_access_key) is str
            and re.fullmatch(r"[A-Za-z0-9/+=]{40}", self.secret_access_key)
        )
        _require(
            self.session_token is None
            or type(self.session_token) is str
            and 1 <= len(self.session_token) <= 4096
            and all(32 < ord(char) < 127 for char in self.session_token)
        )
        if self.access_key_id.startswith("ASIA"):
            _require(self.session_token is not None and self.expires_at is not None)
        if self.expires_at is not None:
            _require(
                type(self.expires_at) is datetime
                and self.expires_at.tzinfo is not None
                and self.expires_at.utcoffset().total_seconds() == 0
                and now < self.expires_at
            )


def sign_request(*, host, path, region, body, credentials, now):
    """SigV4 headers for one exact POST. Transport supplies the signed Host."""
    _require(type(credentials) is AwsCredentials)
    credentials.validate(now)
    _require(
        type(body) is bytes
        and type(path) is str
        and re.fullmatch(
            r"/guardrail/[a-z0-9]{1,128}/version/[1-9][0-9]{0,7}/apply", path
        )
    )
    _require(
        type(region) is str and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", region)
    )
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    _require(host == f"bedrock-runtime.{region}.{suffix}", "destination_denied")
    stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    headers = {
        "content-type": "application/json",
        "host": host,
        "x-amz-date": stamp,
        "x-amz-content-sha256": hashlib.sha256(body).hexdigest(),
    }
    if credentials.session_token is not None:
        headers["x-amz-security-token"] = credentials.session_token
    names = ";".join(sorted(headers))
    canonical = "\n".join(
        (
            "POST",
            path,
            "",
            "".join(name + ":" + headers[name] + "\n" for name in sorted(headers)),
            names,
            headers["x-amz-content-sha256"],
        )
    )
    scope = f"{date}/{region}/bedrock/aws4_request"
    to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            stamp,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
    )
    key = ("AWS4" + credentials.secret_access_key).encode()
    for value in (date, region, "bedrock", "aws4_request"):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{scope}, SignedHeaders={names}, Signature={signature}"
    )
    del headers["host"]  # PinnedHttpsTransport forbids callers overriding Host.
    return headers


def _document(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_RESPONSE_BYTES)

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def constant(_):
        raise EvolutionError("evaluator_unavailable")

    def number(value):
        result = float(value)
        _require(math.isfinite(result))
        return result

    try:
        result = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=number
        )
    except ValueError, UnicodeError, RecursionError:
        raise EvolutionError("evaluator_unavailable") from None
    _require(type(result) is dict)
    return result


def _finding(rule, part, span=None):
    return Finding(
        rule_id=rule.rule_id,
        category="managed",
        part_id=part.part_id,
        reason_code="content_denied",
        evaluator_revision=REVISION,
        start=None if span is None else span[0],
        end=None if span is None else span[1],
    )


def _text(value):
    _require(type(value) is str and len(value) <= MAX_INPUT_BYTES)
    try:
        _require(len(value.encode("utf-8")) <= MAX_INPUT_BYTES)
    except UnicodeError:
        raise EvolutionError("evaluator_unavailable") from None
    return value


def _other_assessments(assessment):
    """Known non-masking families can report clean rows or a spanless finding."""
    fields = {
        "contentPolicy": ("filters",),
        "topicPolicy": ("topics",),
        "wordPolicy": ("customWords", "managedWordLists"),
        "contextualGroundingPolicy": ("filters",),
    }
    unsafe = bool(
        set(assessment)
        - {
            *fields,
            "sensitiveInformationPolicy",
            "appliedGuardrailDetails",
            "invocationMetrics",
        }
    )
    intervention = False
    for family, lists in fields.items():
        if family not in assessment:
            continue
        policy = assessment[family]
        _require(type(policy) is dict and set(policy) == set(lists))
        for name in lists:
            rows = policy[name]
            _require(type(rows) is list and len(rows) <= 4096)
            for row in rows:
                _require(
                    type(row) is dict
                    and type(row.get("action")) is str
                    and row["action"] in {"NONE", "BLOCKED"}
                )
                _require("detected" not in row or type(row["detected"]) is bool)
                if family == "contentPolicy":
                    _require(
                        {"action", "confidence", "type"} <= set(row)
                        and set(row)
                        <= {
                            "action",
                            "confidence",
                            "type",
                            "detected",
                            "filterStrength",
                        }
                    )
                    _require(
                        row["confidence"] in ("NONE", "LOW", "MEDIUM", "HIGH")
                        and row["type"]
                        in (
                            "INSULTS",
                            "HATE",
                            "SEXUAL",
                            "VIOLENCE",
                            "MISCONDUCT",
                            "PROMPT_ATTACK",
                        )
                    )
                    _require(
                        "filterStrength" not in row
                        or row["filterStrength"] in ("NONE", "LOW", "MEDIUM", "HIGH")
                    )
                elif family == "topicPolicy":
                    _require(
                        {"action", "name", "type"} <= set(row)
                        and set(row) <= {"action", "name", "type", "detected"}
                        and row["type"] == "DENY"
                    )
                    _text(row["name"])
                elif family == "wordPolicy":
                    required = {"action", "match"} | (
                        {"type"} if name == "managedWordLists" else set()
                    )
                    _require(
                        required <= set(row) and set(row) <= required | {"detected"}
                    )
                    _text(row["match"])
                    _require(name != "managedWordLists" or row["type"] == "PROFANITY")
                else:
                    _require(
                        {"action", "score", "threshold", "type"} <= set(row)
                        and set(row)
                        <= {"action", "score", "threshold", "type", "detected"}
                        and row["type"] in ("GROUNDING", "RELEVANCE")
                    )
                    _require(
                        all(
                            type(row[key]) in (float, int)
                            and math.isfinite(row[key])
                            and 0 <= row[key] <= 1
                            for key in ("score", "threshold")
                        )
                    )
                intervention |= row["action"] == "BLOCKED"
                unsafe |= row["action"] == "BLOCKED" or row.get("detected", False)
    return unsafe, intervention


def response_findings(raw, rule, part):
    """Accept only exactly attributable masking; unsafe intervention has no span."""
    value = _document(raw)
    _require(
        {"action", "assessments", "outputs"} <= set(value)
        and set(value)
        <= {
            "action",
            "actionReason",
            "assessments",
            "outputs",
            "usage",
            "guardrailCoverage",
        }
    )
    action, assessments, outputs = (
        value["action"],
        value["assessments"],
        value["outputs"],
    )
    _require(type(action) is str and action in {"NONE", "GUARDRAIL_INTERVENED"})
    _require(
        type(assessments) is list
        and len(assessments) <= 64
        and all(type(row) is dict for row in assessments)
    )
    _require(
        type(outputs) is list
        and len(outputs) <= 1
        and all(type(row) is dict and set(row) == {"text"} for row in outputs)
    )
    for row in outputs:
        _text(row["text"])
    if "guardrailCoverage" in value:
        coverage = value["guardrailCoverage"]
        _require(
            type(coverage) is dict and type(coverage.get("textCharacters")) is dict
        )
        text = coverage["textCharacters"]
        _require(
            set(text) == {"guarded", "total"}
            and all(type(v) is int and v >= 0 for v in text.values())
            and text["guarded"] == text["total"] == len(part.text)
        )
    spans, intervention, unsafe = [], False, False
    for assessment in assessments:
        details = assessment.get("appliedGuardrailDetails")
        if details is not None:
            _require(
                type(details) is dict
                and details.get("guardrailId") == rule.config.guardrail_id
                and details.get("guardrailVersion") == rule.config.guardrail_version
            )
        family_unsafe, family_intervention = _other_assessments(assessment)
        unsafe |= family_unsafe
        intervention |= family_intervention
        sensitive = assessment.get("sensitiveInformationPolicy", {})
        _require(
            type(sensitive) is dict and set(sensitive) <= {"piiEntities", "regexes"}
        )
        regexes, entities = (
            sensitive.get("regexes", []),
            sensitive.get("piiEntities", []),
        )
        _require(
            type(regexes) is list
            and type(entities) is list
            and len(regexes) + len(entities) <= 4096
        )
        for regex in regexes:
            _require(
                type(regex) is dict
                and set(regex) <= {"action", "detected", "match", "name", "regex"}
                and type(regex.get("action")) is str
                and regex["action"] in {"NONE", "BLOCKED", "ANONYMIZED"}
            )
            _require("detected" not in regex or type(regex["detected"]) is bool)
            for field_name in ("match", "name", "regex"):
                if field_name in regex:
                    _text(regex[field_name])
            # Custom regex masking has no stable marker contract.
            unsafe |= (
                regex["action"] != "NONE"
                or regex.get("detected", False)
                or bool(regex.get("match"))
            )
            intervention |= regex["action"] != "NONE"
        for entity in entities:
            _require(
                type(entity) is dict
                and {"action", "match", "type"} <= set(entity)
                and set(entity) <= {"action", "match", "type", "detected"}
            )
            entity_action, match, kind = (
                entity["action"],
                entity["match"],
                entity["type"],
            )
            _require(
                type(entity_action) is str
                and entity_action in {"NONE", "ANONYMIZED", "BLOCKED"}
                and type(match) is str
                and type(kind) is str
            )
            _text(match)
            _require("detected" not in entity or type(entity["detected"]) is bool)
            if entity_action == "NONE":
                unsafe |= entity.get("detected", False) or bool(match)
                continue
            intervention = True
            if (
                entity_action != "ANONYMIZED"
                or kind not in PII_TYPES
                or not match
                or entity.get("detected") is False
            ):
                unsafe = True
                continue
            start = part.text.find(match)
            if start < 0 or part.text.find(match, start + 1) >= 0:
                unsafe = True
                continue
            spans.append((start, start + len(match), "{" + kind + "}"))
    if action == "NONE":
        _require(not intervention and (not outputs or outputs[0]["text"] == part.text))
        # A detect-only/unknown assessment cannot silently clean a managed rule.
        return (_finding(rule, part),) if unsafe else ()
    if unsafe or not spans or not outputs:
        return (_finding(rule, part),)
    spans.sort()
    offset, pieces = 0, []
    for start, end, marker in spans:
        if start < offset:
            return (_finding(rule, part),)
        pieces.extend((part.text[offset:start], marker))
        offset = end
    pieces.append(part.text[offset:])
    if "".join(pieces) != outputs[0]["text"]:
        return (_finding(rule, part),)
    return tuple(_finding(rule, part, (start, end)) for start, end, _ in spans)


@dataclass(frozen=True)
class _Binding:
    policy_raw: bytes
    stage: str
    runtime_zone: str
    region: str
    credentials: Mapping = field(repr=False)
    enabled: bool
    timeout_ms: int
    max_inflight: int


class BedrockAdapter:
    def __init__(
        self,
        *,
        policy,
        stage,
        runtime_zone,
        region,
        credentials_by_ref=None,
        transport=None,
        enabled=False,
        timeout_ms=500,
        max_inflight=4,
        wall_clock=_now,
    ):
        _require(
            type(enabled) is bool
            and type(timeout_ms) is int
            and 1 <= timeout_ms <= 500
            and type(max_inflight) is int
            and 1 <= max_inflight <= 32,
            "validation_failed",
        )
        _require(
            type(stage) is str and stage in {"input", "output"}, "unsupported_surface"
        )
        _require(
            type(region) is str
            and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", region)
            and type(runtime_zone) is str
            and runtime_zone,
            "residency_denied",
        )
        try:
            snapshot = PolicyVersion.model_validate_json(canonical_json_bytes(policy))
        except ValidationError, ValueError, TypeError:
            raise EvolutionError("policy_unresolved") from None
        _require(snapshot.status == "approved", "approval_required")
        _require(
            credentials_by_ref is None or isinstance(credentials_by_ref, Mapping),
            "validation_failed",
        )
        credentials = dict(credentials_by_ref or {})
        _require(
            all(
                type(ref) is str and ref and type(value) is AwsCredentials
                for ref, value in credentials.items()
            ),
            "validation_failed",
        )
        self._binding = _Binding(
            canonical_json_bytes(snapshot),
            stage,
            runtime_zone,
            region,
            MappingProxyType(credentials),
            enabled,
            timeout_ms,
            max_inflight,
        )
        self._transport, self._clock = (
            transport or PinnedHttpsTransport(ca_files={}),
            wall_clock,
        )
        self._tasks, self._closed = set(), False

    def capabilities(self):
        bound = self._binding
        return (
            Capability(
                capability_id="managed-bedrock",
                stage=bound.stage,
                part_kinds=("text",),
                actions=("allow", "warn", "redact", "block"),
                evaluator_revision=REVISION,
                max_input_bytes=MAX_INPUT_BYTES,
                timeout_ms=bound.timeout_ms,
                zone=bound.runtime_zone,
                status="enabled" if bound.enabled and not self._closed else "disabled",
            ),
        )

    def _authorize(self, rule):
        bound = self._binding
        _require(bound.enabled and not self._closed, "evaluator_unavailable")
        try:
            policy = PolicyVersion.model_validate_json(bound.policy_raw)
            parsed = Rule.model_validate_json(canonical_json_bytes(rule))
        except ValidationError, ValueError, TypeError:
            raise EvolutionError("validation_failed") from None
        _require(
            parsed.kind == "bedrock"
            and parsed.evaluator_revision == REVISION
            and bound.stage in parsed.stages,
            "capability_missing",
        )
        _require(
            any(
                canonical_json_bytes(item) == canonical_json_bytes(parsed)
                for item in policy.rules
            ),
            "policy_unresolved",
        )
        _require(
            re.fullmatch(r"[a-z0-9]{1,128}", parsed.config.guardrail_id)
            and re.fullmatch(r"[1-9][0-9]{0,7}", parsed.config.guardrail_version),
            "validation_failed",
        )
        route = ResidencyGuard().authorize_provider(
            policy,
            parsed.config.provider_id,
            runtime_zone=bound.runtime_zone,
            purpose="evaluation",
        )
        suffix = (
            "amazonaws.com.cn" if bound.region.startswith("cn-") else "amazonaws.com"
        )
        _require(
            route.provider.vendor == "bedrock"
            and route.provider.zone == bound.region
            and route.destination.zone == bound.region,
            "residency_denied",
        )
        _require(
            route.destination.host == f"bedrock-runtime.{bound.region}.{suffix}"
            and route.destination.port == 443,
            "destination_denied",
        )
        path = f"/guardrail/{parsed.config.guardrail_id}/version/{parsed.config.guardrail_version}/apply"
        PinnedHttpsTransport.validate_url(
            route, "https://" + route.destination.host + path
        )
        credential = bound.credentials.get(route.provider.credential_ref)
        _require(type(credential) is AwsCredentials, "evaluator_unavailable")
        credential.validate(self._clock())
        return parsed, route, credential

    async def evaluate(self, rule, parts, deadline_monotonic):
        _require(
            type(deadline_monotonic) in (int, float)
            and math.isfinite(deadline_monotonic),
            "validation_failed",
        )
        deadline = min(
            deadline_monotonic, time.monotonic() + self._binding.timeout_ms / 1000
        )
        _require(deadline > time.monotonic(), "evaluation_timeout")
        self._authorize(rule)  # Residency/credentials before content or network.
        _require(type(parts) is tuple and 1 <= len(parts) <= 64, "unsupported_surface")
        try:
            parsed = tuple(
                ContentPart.model_validate_json(canonical_json_bytes(part))
                for part in parts
            )
        except ValidationError, ValueError, TypeError:
            raise EvolutionError("unsupported_surface") from None
        _require(
            all(
                part.kind == "text" and part.tool is None and part.text
                for part in parsed
            ),
            "unsupported_surface",
        )
        _require(
            len({part.part_id for part in parsed}) == len(parsed), "validation_failed"
        )
        _require(
            sum(len(part.text.encode()) for part in parsed) <= MAX_INPUT_BYTES,
            "payload_too_large",
        )
        _require(len(self._tasks) < self._binding.max_inflight, "queue_full")
        task = asyncio.create_task(self._evaluate(rule, parsed, deadline))
        self._tasks.add(task)
        try:
            return await task
        finally:
            self._tasks.discard(task)

    async def _evaluate(self, rule, parts, deadline):
        findings = []
        try:
            async with asyncio.timeout_at(deadline):
                for part in parts:
                    _require(time.monotonic() < deadline, "evaluation_timeout")
                    rule, route, credentials = self._authorize(rule)
                    path = f"/guardrail/{rule.config.guardrail_id}/version/{rule.config.guardrail_version}/apply"
                    body = json.dumps(
                        {
                            "source": self._binding.stage.upper(),
                            "outputScope": "INTERVENTIONS",
                            "content": [{"text": {"text": part.text}}],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                    headers = sign_request(
                        host=route.destination.host,
                        path=path,
                        region=self._binding.region,
                        body=body,
                        credentials=credentials,
                        now=self._clock(),
                    )
                    _require(time.monotonic() < deadline, "evaluation_timeout")
                    response = await self._transport.request(
                        route,
                        method="POST",
                        path=path,
                        body=body,
                        headers=headers,
                        deadline_monotonic=deadline,
                        max_response_bytes=MAX_RESPONSE_BYTES,
                    )
                    _require(response.status_code == 200)
                    findings.extend(response_findings(response.content, rule, part))
                    _require(len(findings) <= 4096)
                    _require(time.monotonic() < deadline, "evaluation_timeout")
            return tuple(findings)
        except TimeoutError:
            raise EvolutionError("evaluation_timeout") from None
        except EvolutionError:
            raise
        except OSError, RuntimeError, ValueError, TypeError, AttributeError:
            # Provider/transport exceptions must never expose response or credentials.
            raise EvolutionError("evaluator_unavailable") from None

    async def close(self):
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


def _read_private_json(path):
    """Explicit live-command inputs only; never read ambient credential files."""
    import os
    import stat
    from pathlib import Path

    path = Path(path)
    _require(path.is_absolute() and path.resolve() == path, "validation_failed")
    before = path.lstat()
    _require(
        stat.S_ISREG(before.st_mode)
        and stat.S_IMODE(before.st_mode) == 0o600
        and before.st_uid == os.getuid()
        and before.st_nlink == 1
        and before.st_size <= 1048576,
        "validation_failed",
    )
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mode,
        item.st_uid,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _require(
            identity(os.fstat(stream.fileno())) == identity(before), "validation_failed"
        )
        raw = stream.read(1048577)
        _require(
            identity(os.fstat(stream.fileno())) == identity(before), "validation_failed"
        )
    _require(
        path.resolve() == path
        and identity(path.lstat()) == identity(before)
        and len(raw) == before.st_size,
        "validation_failed",
    )
    return _document(raw)


def _private_json(path):
    try:
        return _read_private_json(path)
    except OSError, RuntimeError, ValueError, TypeError:
        raise EvolutionError("validation_failed") from None


async def live_check(config, credential, ca_file):
    """Separate optional verification using one fixed synthetic text only."""
    _require(
        type(config) is dict
        and set(config) == {"policy", "stage", "runtime_zone", "region", "rule_id"},
        "validation_failed",
    )
    try:
        policy = PolicyVersion.model_validate(config["policy"])
    except ValidationError, ValueError, TypeError:
        raise EvolutionError("validation_failed") from None
    _require(policy.residency.external_processing, "residency_denied")
    rule = next((row for row in policy.rules if row.rule_id == config["rule_id"]), None)
    _require(rule is not None and rule.kind == "bedrock", "capability_missing")
    route = ResidencyGuard().authorize_provider(
        policy,
        rule.config.provider_id,
        runtime_zone=config["runtime_zone"],
        purpose="evaluation",
    )
    _require(
        type(credential) is dict
        and set(credential)
        == {
            "credential_ref",
            "access_key_id",
            "secret_access_key",
            "session_token",
            "expires_at",
        }
        and credential["credential_ref"] == route.provider.credential_ref,
        "validation_failed",
    )
    try:
        expiry = (
            None
            if credential["expires_at"] is None
            else datetime.fromisoformat(credential["expires_at"])
        )
    except ValueError, TypeError, AttributeError:
        raise EvolutionError("validation_failed") from None
    credentials = AwsCredentials(
        credential["access_key_id"],
        credential["secret_access_key"],
        credential["session_token"],
        expiry,
    )
    transport = PinnedHttpsTransport(ca_files={route.destination.tls_ca_ref: ca_file})
    adapter = BedrockAdapter(
        policy=policy,
        stage=config["stage"],
        runtime_zone=config["runtime_zone"],
        region=config["region"],
        credentials_by_ref={credential["credential_ref"]: credentials},
        transport=transport,
        enabled=True,
    )
    try:
        result = await adapter.evaluate(
            rule,
            (
                ContentPart(
                    part_id="synthetic-live-check",
                    kind="text",
                    role="user",
                    text="This is a synthetic guardrail connectivity check.",
                ),
            ),
            time.monotonic() + 0.5,
        )
        return {
            "profile": "bedrock_live_optional",
            "status": "completed",
            "findings": len(result),
            "local_gate_evidence": False,
        }
    finally:
        await adapter.close()


def main(argv=None):
    """Explicit external opt-in; default local-only invocation never dispatches."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Separate optional managed-engine verification; never local acceptance evidence"
    )
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--config", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--ca-file", required=True)
    args = parser.parse_args(argv)
    _require(args.allow_external, "residency_denied")
    config = _private_json(args.config)
    _require(
        type(config.get("policy")) is dict
        and type(config["policy"].get("residency")) is dict
        and config["policy"]["residency"].get("external_processing") is True,
        "residency_denied",
    )
    result = asyncio.run(
        live_check(config, _private_json(args.credentials), args.ca_file)
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvolutionError as error:
        print(
            json.dumps(
                {
                    "profile": "bedrock_live_optional",
                    "status": "failed",
                    "code": error.code,
                }
            )
        )
        raise SystemExit(1)
