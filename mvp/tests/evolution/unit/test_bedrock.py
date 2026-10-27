"""No AWS calls: synthetic ApplyGuardrail, pinned transport and policy boundary."""

import asyncio
import hashlib
import json
import socket
import time
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpcore
import pytest
from guardrails.evolution import bedrock as subject
from guardrails.evolution.compiler import COMPILER_VERSION, policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    ContentPart,
    Destination,
    ListSet,
    PolicyVersion,
    ProviderProfile,
    Quota,
    ResidencyProfile,
    Rule,
    ToolCall,
    with_resource_digest,
)
from guardrails.evolution.transformations import transform_text

NOW = datetime(2026, 10, 12, tzinfo=UTC)
STAMP = "2026-10-12T00:00:00Z"
REGION = "us-east-1"
HOST = "bedrock-runtime.us-east-1.amazonaws.com"
PATH = "/guardrail/synthetic123/version/7/apply"
KEY = "AKIAABCDEFGHIJKLMNOP"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
CREDENTIALS = subject.AwsCredentials(KEY, SECRET)
FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures/bedrock_protocol.json").read_text()
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("unit test attempted a network connection")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def uid(number):
    return str(UUID(int=number))


def resource(cls, **values):
    return with_resource_digest(
        cls(tenant_id="bedrock-tests", created_at=STAMP, **values)
    )


def sealed(policy):
    return policy.model_copy(update={"compiled_digest": policy_digest(policy)})


def policy():
    provider = resource(
        ProviderProfile,
        provider_id=uid(1),
        kind="evaluator",
        vendor="bedrock",
        model_id="synthetic123",
        destination_id=uid(2),
        zone=REGION,
        credential_ref="bedrock-test-key",
    )
    destination = resource(
        Destination,
        destination_id=uid(2),
        name="managed-guardrail",
        host=HOST,
        path_prefix=PATH,
        cidrs=("54.0.0.0/8",),
        zone=REGION,
        purpose="evaluation",
        tls_ca_ref="aws-ca",
    )
    providers = resource(ListSet, list_id=uid(3), kind="provider", allow=(uid(1),))
    destinations = resource(
        ListSet, list_id=uid(4), kind="destination", allow=(uid(2),)
    )
    rule = Rule(
        rule_id="managed",
        kind="bedrock",
        stages=("input", "output"),
        action="redact",
        mandatory=False,
        minimum_state="observe",
        evaluator_revision=subject.REVISION,
        config={
            "provider_id": uid(1),
            "guardrail_id": "synthetic123",
            "guardrail_version": "7",
        },
    )
    return sealed(
        PolicyVersion(
            tenant_id="bedrock-tests",
            policy_id="managed-test",
            version=1,
            author_id=uid(5),
            created_at=STAMP,
            compiler_version=COMPILER_VERSION,
            status="approved",
            rules=(rule,),
            providers=(provider,),
            resolved_destinations=(destination,),
            resolved_lists=(providers, destinations),
            residency=resource(
                ResidencyProfile,
                residency_id=uid(6),
                allowed_zones=("local-shared", REGION),
                external_processing=True,
                provider_lists=(uid(3),),
                destination_lists=(uid(4),),
            ),
            quota=resource(Quota, quota_id=uid(7)),
        )
    )


def modify(base, section, **values):
    if section in {"residency", "quota"}:
        return sealed(
            base.model_copy(
                update={
                    section: with_resource_digest(
                        getattr(base, section).model_copy(update=values)
                    )
                }
            )
        )
    rows = list(getattr(base, section))
    rows[0] = with_resource_digest(rows[0].model_copy(update=values))
    return sealed(base.model_copy(update={section: tuple(rows)}))


def part(text="Synthetic clean text", identifier="text", **values):
    return ContentPart(
        part_id=identifier, kind="text", role="user", text=text, **values
    )


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class Transport:
    def __init__(self, response=None, *, status=200, error=None):
        self.response = response or {"action": "NONE", "assessments": [], "outputs": []}
        self.status, self.error, self.calls = status, error, []

    async def request(self, route, **options):
        self.calls.append((route, options))
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=self.status, content=raw(self.response))


def adapter(base=None, transport=None, **options):
    base = base or policy()
    values = {
        "policy": base,
        "stage": "input",
        "runtime_zone": "local-shared",
        "region": REGION,
        "credentials_by_ref": {"bedrock-test-key": CREDENTIALS},
        "enabled": True,
        "transport": transport or Transport(),
        "wall_clock": lambda: NOW,
    }
    return subject.BedrockAdapter(**{**values, **options})


async def evaluate(instance, base=None, parts=None, deadline=None):
    return await instance.evaluate(
        (base or policy()).rules[0],
        parts or (part(),),
        deadline or time.monotonic() + 3,
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_synthetic_protocol_preserves_only_attributable_original_spans(case):
    base, content = policy(), part(case["text"])
    findings = subject.response_findings(raw(case["response"]), base.rules[0], content)
    assert [
        None if row.start is None else [row.start, row.end] for row in findings
    ] == case["spans"]
    for row in findings:
        assert (
            row.rule_id == "managed"
            and row.part_id == "text"
            and row.reason_code == "content_denied"
        )
        assert row.category == "managed" and row.evaluator_revision == subject.REVISION
        assert (
            "Vendor" not in row.model_dump_json()
            and "alice@example" not in row.model_dump_json()
        )
    if any(span is None for span in case["spans"]):
        with pytest.raises(EvolutionError, match="Unsafe transformation"):
            transform_text((content,), findings)
    elif findings:
        transformed = transform_text((content,), findings)
        assert (
            "[REDACTED]" in transformed[0].text
            and "alice@example.test" not in transformed[0].text
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,source", [("input", "INPUT"), ("output", "OUTPUT")])
async def test_exact_pinned_request_stage_signing_and_shared_deadline(stage, source):
    base, transport = policy(), Transport()
    instance = adapter(base, transport, stage=stage)
    deadline = time.monotonic() + 0.2
    assert await evaluate(instance, base, (part("🙂 exact source"),), deadline) == ()
    assert len(transport.calls) == 1
    route, options = transport.calls[0]
    assert (
        route.provider == base.providers[0]
        and route.destination == base.resolved_destinations[0]
    )
    assert set(options) == {
        "method",
        "path",
        "body",
        "headers",
        "deadline_monotonic",
        "max_response_bytes",
    }
    assert options["method"] == "POST" and options["path"] == PATH
    assert options["body"] == raw(
        {
            "source": source,
            "outputScope": "INTERVENTIONS",
            "content": [{"text": {"text": "🙂 exact source"}}],
        }
    )
    assert (
        options["deadline_monotonic"] == deadline
        and options["max_response_bytes"] == 1048576
    )
    headers = options["headers"]
    assert headers["x-amz-date"] == "20261012T000000Z"
    assert (
        headers["x-amz-content-sha256"] == hashlib.sha256(options["body"]).hexdigest()
    )
    assert "20261012/us-east-1/bedrock/aws4_request" in headers["authorization"]
    assert "host" not in headers and SECRET not in repr(headers)
    # Existing HTTP core produces exactly the authority signed by this adapter.
    request = httpcore.Request(
        "POST", f"https://{HOST}:443{PATH}", headers=headers, content=options["body"]
    )
    actual = httpcore._models.include_request_headers(
        request.headers, url=request.url, content=options["body"]
    )
    assert [value for name, value in actual if name.lower() == b"host"] == [
        HOST.encode()
    ]
    await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        ("disabled", "evaluator_unavailable"),
        ("local-only", "residency_denied"),
        ("cross-region", "residency_denied"),
        ("foreign-host", "destination_denied"),
        ("wrong-port", "destination_denied"),
        ("wrong-path", "destination_denied"),
        ("provider-denied", "provider_denied"),
        ("destination-denied", "destination_denied"),
        ("provider-disabled", "provider_denied"),
        ("missing-credentials", "evaluator_unavailable"),
        ("expired-credentials", "evaluator_unavailable"),
        ("foreign-tenant", "forbidden"),
        ("stale-resource", "policy_unresolved"),
        ("stale-policy", "policy_unresolved"),
    ],
)
async def test_authorization_refuses_before_any_dispatch(change, code):
    base, transport, options = policy(), Transport(), {}
    if change == "disabled":
        options["enabled"] = False
    elif change == "local-only":
        base = modify(base, "residency", external_processing=False)
    elif change == "cross-region":
        options["region"] = "us-west-2"
    elif change == "foreign-host":
        base = modify(base, "resolved_destinations", host="example.com")
    elif change == "wrong-port":
        base = modify(base, "resolved_destinations", port=8443)
    elif change == "wrong-path":
        base = modify(base, "resolved_destinations", path_prefix="/unapproved")
    elif change == "provider-denied":
        base = modify(base, "resolved_lists", deny=(uid(1),))
    elif change == "destination-denied":
        rows = list(base.resolved_lists)
        rows[1] = with_resource_digest(rows[1].model_copy(update={"deny": (uid(2),)}))
        base = sealed(base.model_copy(update={"resolved_lists": tuple(rows)}))
    elif change == "provider-disabled":
        base = modify(base, "providers", enabled=False)
    elif change == "missing-credentials":
        options["credentials_by_ref"] = {}
    elif change == "expired-credentials":
        options["credentials_by_ref"] = {
            "bedrock-test-key": subject.AwsCredentials(KEY, SECRET, expires_at=NOW)
        }
    elif change == "foreign-tenant":
        base = modify(base, "providers", tenant_id="other-tenant")
    elif change == "stale-resource":
        base = sealed(
            base.model_copy(
                update={
                    "providers": (
                        base.providers[0].model_copy(update={"model_id": "tampered"}),
                    )
                }
            )
        )
    elif change == "stale-policy":
        base = base.model_copy(update={"policy_id": "tampered"})
    instance = adapter(base, transport, **options)
    with pytest.raises(EvolutionError) as caught:
        await evaluate(instance, base)
    assert caught.value.code == code and transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["draft", "oversized-version", "path-id", "different-rule", "revision"]
)
async def test_mutable_or_unregistered_guardrail_configuration_never_dispatches(change):
    base, transport = policy(), Transport()
    instance = adapter(base, transport)
    rule = base.rules[0]
    if change == "draft":
        rule = rule.model_copy(
            update={
                "config": rule.config.model_copy(update={"guardrail_version": "DRAFT"})
            }
        )
    elif change == "oversized-version":
        rule = rule.model_copy(
            update={
                "config": rule.config.model_copy(
                    update={"guardrail_version": "100000000"}
                )
            }
        )
    elif change == "path-id":
        rule = rule.model_copy(
            update={"config": rule.config.model_copy(update={"guardrail_id": "../bad"})}
        )
    elif change == "different-rule":
        rule = rule.model_copy(update={"rule_id": "other"})
    elif change == "revision":
        rule = rule.model_copy(update={"evaluator_revision": "unknown"})
    with pytest.raises(EvolutionError):
        await instance.evaluate(rule, (part(),), time.monotonic() + 2)
    assert transport.calls == []


@pytest.mark.parametrize("stage", ["tool_arguments", "tool_result", "other", None])
def test_unsupported_constructor_stage_has_no_capability(stage):
    with pytest.raises(EvolutionError) as caught:
        adapter(stage=stage)
    assert caught.value.code == "unsupported_surface"


@pytest.mark.parametrize("status", ["candidate", "rejected", "revoked"])
def test_unapproved_policy_cannot_construct_enabled_adapter(status):
    with pytest.raises(EvolutionError) as caught:
        adapter(policy().model_copy(update={"status": status}))
    assert caught.value.code == "approval_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 403, 404, 429, 500, 503])
async def test_provider_errors_have_no_retry_or_vendor_error_body(status):
    transport = Transport({"secret": "vendor-private-body"}, status=status)
    with pytest.raises(EvolutionError) as caught:
        await evaluate(adapter(transport=transport))
    assert caught.value.code == "evaluator_unavailable" and len(transport.calls) == 1
    assert "private" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (EvolutionError("upstream_timeout"), "upstream_timeout"),
        (EvolutionError("destination_denied"), "destination_denied"),
        (RuntimeError("private vendor secret"), "evaluator_unavailable"),
    ],
)
async def test_transport_failure_is_finite_and_no_retry(error, code):
    transport = Transport(error=error)
    with pytest.raises(EvolutionError) as caught:
        await evaluate(adapter(transport=transport))
    assert (
        caught.value.code == code
        and len(transport.calls) == 1
        and "private" not in str(caught.value)
    )


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"[]",
        b"{",
        b'{"action":"NONE","action":"NONE"}',
        b'{"value":NaN}',
        b'{"value":1e309}',
        b"x" * 1048577,
    ],
    ids=["empty", "array", "syntax", "duplicate", "nan", "overflow", "oversized"],
)
def test_malformed_wire_response_is_typed_failure(value):
    with pytest.raises(EvolutionError) as caught:
        subject.response_findings(value, policy().rules[0], part())
    assert caught.value.code == "evaluator_unavailable"


@pytest.mark.parametrize(
    "change",
    [
        "unknown-action",
        "unknown-root",
        "wrong-assessments",
        "wrong-outputs",
        "altered-clean-output",
        "foreign-guardrail",
        "foreign-version",
        "partial-coverage",
        "boolean-coverage",
        "surrogate-output",
        "invalid-detected",
        "action-conflict",
    ],
)
def test_inconsistent_response_cannot_become_clean(change):
    value = {"action": "NONE", "assessments": [], "outputs": []}
    if change == "unknown-action":
        value["action"] = []
    elif change == "unknown-root":
        value["unrecognized"] = True
    elif change == "wrong-assessments":
        value["assessments"] = {}
    elif change == "wrong-outputs":
        value["outputs"] = [{}]
    elif change == "altered-clean-output":
        value["outputs"] = [{"text": "changed"}]
    elif change.startswith("foreign-"):
        value["assessments"] = [
            {
                "appliedGuardrailDetails": {
                    "guardrailId": "other"
                    if change == "foreign-guardrail"
                    else "synthetic123",
                    "guardrailVersion": "8" if change == "foreign-version" else "7",
                }
            }
        ]
    elif change.endswith("coverage"):
        value["guardrailCoverage"] = {
            "textCharacters": {
                "total": len(part().text),
                "guarded": True if change == "boolean-coverage" else 1,
            }
        }
    elif change == "surrogate-output":
        with pytest.raises(EvolutionError):
            subject.response_findings(
                b'{"action":"NONE","assessments":[],"outputs":[{"text":"\\ud800"}]}',
                policy().rules[0],
                part(),
            )
        return
    else:
        value["assessments"] = [
            {
                "contentPolicy": {
                    "filters": [
                        {
                            "action": "BLOCKED"
                            if change == "action-conflict"
                            else "NONE",
                            "detected": "yes" if change == "invalid-detected" else True,
                        }
                    ]
                }
            }
        ]
    with pytest.raises(EvolutionError):
        subject.response_findings(raw(value), policy().rules[0], part())


@pytest.mark.parametrize(
    "change",
    [
        "changed-context",
        "wrong-marker",
        "overlap",
        "duplicate",
        "unknown-pii",
        "missing-output",
        "blocked-pii",
        "undetected-pii",
    ],
)
def test_unsafe_vendor_mask_cannot_authorize_redaction(change):
    case = deepcopy(FIXTURE["cases"][4])
    value = case["response"]
    entities = value["assessments"][0]["sensitiveInformationPolicy"]["piiEntities"]
    if change == "changed-context":
        value["outputs"][0]["text"] = "Changed {EMAIL}"
    elif change == "wrong-marker":
        value["outputs"][0]["text"] = "🙂 mail REDACTED!"
    elif change == "overlap":
        entities.append({"action": "ANONYMIZED", "match": "alice", "type": "NAME"})
    elif change == "duplicate":
        entities.append(deepcopy(entities[0]))
    elif change == "unknown-pii":
        entities[0]["type"] = "FUTURE_PII"
    elif change == "missing-output":
        value["outputs"] = []
    elif change == "blocked-pii":
        entities[0]["action"] = "BLOCKED"
    elif change == "undetected-pii":
        entities[0]["detected"] = False
    findings = subject.response_findings(
        raw(value), policy().rules[0], part(case["text"])
    )
    assert len(findings) == 1 and findings[0].start is None and findings[0].end is None


class WaitingTransport(Transport):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = False

    async def request(self, route, **options):
        self.calls.append((route, options))
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_one_deadline_cancels_transport_and_releases_capacity():
    transport = WaitingTransport()
    instance = adapter(transport=transport, timeout_ms=20)
    with pytest.raises(EvolutionError) as caught:
        await evaluate(instance)
    assert (
        caught.value.code == "evaluation_timeout"
        and transport.cancelled
        and instance._tasks == set()
    )
    assert len(transport.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["caller-cancel", "close"])
async def test_cancellation_reaps_inflight_request_without_new_dispatch(operation):
    transport = WaitingTransport()
    instance = adapter(transport=transport)
    task = asyncio.create_task(evaluate(instance))
    await transport.entered.wait()
    if operation == "close":
        await instance.close()
    else:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.cancelled and len(transport.calls) == 1 and not instance._tasks
    await instance.close()
    assert instance.capabilities()[0].status == "disabled"


@pytest.mark.asyncio
async def test_full_capacity_rejects_without_queueing_or_dispatch():
    transport = WaitingTransport()
    instance = adapter(transport=transport, max_inflight=1)
    first = asyncio.create_task(evaluate(instance))
    await transport.entered.wait()
    with pytest.raises(EvolutionError) as caught:
        await evaluate(instance)
    assert caught.value.code == "queue_full" and len(transport.calls) == 1
    await instance.close()
    with pytest.raises(asyncio.CancelledError):
        await first


@pytest.mark.asyncio
async def test_all_parts_share_deadline_and_later_failure_discards_partial_findings():
    class SecondFailure(Transport):
        async def request(self, route, **options):
            self.calls.append((route, options))
            if len(self.calls) == 2:
                raise EvolutionError("upstream_timeout")
            return SimpleNamespace(
                status_code=200, content=raw(FIXTURE["cases"][3]["response"])
            )

    transport = SecondFailure()
    instance = adapter(transport=transport)
    deadline = time.monotonic() + 0.2
    with pytest.raises(EvolutionError) as caught:
        await evaluate(
            instance,
            parts=(part("First", "one"), part("Second", "two")),
            deadline=deadline,
        )
    assert caught.value.code == "upstream_timeout" and len(transport.calls) == 2
    assert all(
        options["deadline_monotonic"] == deadline for _, options in transport.calls
    )


@pytest.mark.asyncio
async def test_credential_snapshot_cannot_mutate_after_binding_and_expires_before_next_part():
    current = [NOW]
    expiry = NOW + timedelta(seconds=1)
    original = {
        "bedrock-test-key": subject.AwsCredentials(KEY, SECRET, expires_at=expiry)
    }

    class AdvanceClock(Transport):
        async def request(self, route, **options):
            result = await super().request(route, **options)
            current[0] = expiry
            return result

    transport = AdvanceClock()
    instance = adapter(
        transport=transport, credentials_by_ref=original, wall_clock=lambda: current[0]
    )
    original.clear()
    with pytest.raises(FrozenInstanceError):
        CREDENTIALS.secret_access_key = "changed"
    with pytest.raises(EvolutionError):
        await evaluate(instance, parts=(part("one", "one"), part("two", "two")))
    assert len(transport.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "later", -1])
async def test_invalid_or_expired_deadline_never_dispatches(value):
    transport = Transport()
    instance = adapter(transport=transport)
    with pytest.raises(EvolutionError):
        await instance.evaluate(policy().rules[0], (part(),), value)
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["json", "empty", "duplicate", "combined-size", "tool"]
)
async def test_unsupported_parts_never_dispatch(kind):
    transport = Transport()
    instance = adapter(transport=transport)
    parts = (part(),)
    if kind == "json":
        parts = (ContentPart(part_id="json", kind="json", role="user", value={}),)
    elif kind == "empty":
        parts = ()
    elif kind == "duplicate":
        parts = (part(), part())
    elif kind == "combined-size":
        parts = (part("x" * 40000, "one"), part("y" * 40000, "two"))
    elif kind == "tool":
        parts = (
            part(
                tool=ToolCall(
                    call_id="call", name="tool", tool_digest="0" * 64, arguments={}
                )
            ),
        )
    with pytest.raises(EvolutionError):
        await instance.evaluate(policy().rules[0], parts, time.monotonic() + 3)
    assert transport.calls == []


@pytest.mark.parametrize(
    "fields",
    [
        {"access_key_id": "bad"},
        {"secret_access_key": "bad"},
        {"session_token": "line\nbreak"},
        {"access_key_id": "ASIAABCDEFGHIJKLMNOP"},
        {"expires_at": NOW},
        {"expires_at": datetime(2027, 1, 1)},
    ],
    ids=[
        "key",
        "secret",
        "header-injection",
        "incomplete-sts",
        "expired",
        "naive-expiry",
    ],
)
def test_credentials_refuse_invalid_authority(fields):
    values = {"access_key_id": KEY, "secret_access_key": SECRET, **fields}
    with pytest.raises(EvolutionError):
        subject.AwsCredentials(**values).validate(NOW)
    assert SECRET not in repr(subject.AwsCredentials(**values))


def test_temporary_credentials_sign_the_session_token_and_never_secret():
    credentials = subject.AwsCredentials(
        "ASIAABCDEFGHIJKLMNOP", SECRET, "synthetic-token", NOW + timedelta(minutes=5)
    )
    headers = subject.sign_request(
        host=HOST,
        path=PATH,
        region=REGION,
        body=b"{}",
        credentials=credentials,
        now=NOW,
    )
    assert headers["x-amz-security-token"] == "synthetic-token"
    assert ";x-amz-security-token" in headers["authorization"] and SECRET not in repr(
        headers
    )


def test_sigv4_matches_independent_manual_canonical_openssl_vector():
    # Synthetic vector retained with manual canonical bytes and OpenSSL HMAC provenance.
    headers = subject.sign_request(
        host=HOST,
        path=PATH,
        region=REGION,
        body=b"{}",
        credentials=CREDENTIALS,
        now=NOW,
    )
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAABCDEFGHIJKLMNOP/20261012/us-east-1/bedrock/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, "
        "Signature=7c5afd37dbd533b53004e3317b9852f120443d8997d69753c6598535d66f6e28"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["sign", "decode"])
async def test_synchronous_work_cannot_dispatch_or_return_after_deadline(
    monkeypatch, phase
):
    name = "sign_request" if phase == "sign" else "response_findings"
    original = getattr(subject, name)

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(0.03)
        return result

    monkeypatch.setattr(subject, name, delayed)
    transport = Transport()
    instance = adapter(transport=transport, timeout_ms=20)
    with pytest.raises(EvolutionError) as caught:
        await evaluate(instance)
    assert caught.value.code == "evaluation_timeout" and len(transport.calls) == (
        0 if phase == "sign" else 1
    )


@pytest.mark.parametrize(
    "family,rows",
    [
        ("contentPolicy", {"filters": [{"action": "NONE"}]}),
        ("topicPolicy", {"topics": [{"action": "NONE", "type": "DENY"}]}),
        ("wordPolicy", {"customWords": [{"action": "NONE"}], "managedWordLists": []}),
        (
            "contextualGroundingPolicy",
            {
                "filters": [
                    {
                        "action": "NONE",
                        "type": "GROUNDING",
                        "score": True,
                        "threshold": 0.5,
                    }
                ]
            },
        ),
    ],
)
def test_missing_or_malformed_known_assessment_fields_fail_closed(family, rows):
    value = {"action": "NONE", "assessments": [{family: rows}], "outputs": []}
    with pytest.raises(EvolutionError):
        subject.response_findings(raw(value), policy().rules[0], part())


@pytest.mark.parametrize("name", ["match", "name", "regex"])
@pytest.mark.parametrize(
    "value", [0, [], None, "x" * 65537], ids=["integer", "array", "null", "oversized"]
)
def test_malformed_optional_regex_strings_cannot_become_clean(name, value):
    response = {
        "action": "NONE",
        "assessments": [
            {
                "sensitiveInformationPolicy": {
                    "piiEntities": [],
                    "regexes": [{"action": "NONE", name: value}],
                }
            }
        ],
        "outputs": [],
    }
    with pytest.raises(EvolutionError) as caught:
        subject.response_findings(raw(response), policy().rules[0], part())
    assert caught.value.code == "evaluator_unavailable"


def test_unknown_regex_fields_are_not_silently_ignored():
    response = {
        "action": "NONE",
        "assessments": [
            {
                "sensitiveInformationPolicy": {
                    "piiEntities": [],
                    "regexes": [{"action": "NONE", "unknown": "value"}],
                }
            }
        ],
        "outputs": [],
    }
    with pytest.raises(EvolutionError):
        subject.response_findings(raw(response), policy().rules[0], part())


@pytest.mark.parametrize(
    "regex",
    [
        {"action": "NONE"},
        {
            "action": "NONE",
            "match": "",
            "name": "synthetic",
            "regex": "example",
            "detected": False,
        },
    ],
    ids=["absent-optional", "present-optional"],
)
def test_optional_regex_fields_preserve_legitimate_clean_shape(regex):
    response = {
        "action": "NONE",
        "assessments": [
            {
                "sensitiveInformationPolicy": {
                    "piiEntities": [],
                    "regexes": [regex],
                }
            }
        ],
        "outputs": [],
    }
    assert subject.response_findings(raw(response), policy().rules[0], part()) == ()


@pytest.mark.parametrize("timeout", [0, True, 501, 5000])
def test_runtime_budget_cannot_exceed_frozen_text_profile(timeout):
    with pytest.raises(EvolutionError) as caught:
        adapter(timeout_ms=timeout)
    assert caught.value.code == "validation_failed"


@pytest.mark.parametrize("allow", [False, True])
def test_optional_live_command_refuses_local_only_before_credential_read(
    monkeypatch, allow
):
    reads = []
    local = modify(policy(), "residency", external_processing=False)

    def read(path):
        reads.append(path)
        assert path == "/config"
        return {"policy": local.model_dump(mode="json")}

    monkeypatch.setattr(subject, "_private_json", read)
    options = ["--config", "/config", "--credentials", "/secrets", "--ca-file", "/ca"]
    if allow:
        options.append("--allow-external")
    with pytest.raises(EvolutionError) as caught:
        subject.main(options)
    assert caught.value.code == "residency_denied" and reads == (
        ["/config"] if allow else []
    )


def test_default_operator_configuration_is_disabled():
    instance = subject.BedrockAdapter(
        policy=policy(), stage="input", runtime_zone="local-shared", region=REGION
    )
    capability = instance.capabilities()[0]
    assert capability.status == "disabled" and capability.part_kinds == ("text",)
    assert (
        capability.evaluator_revision == subject.REVISION
        and capability.timeout_ms == 500
    )


@pytest.mark.parametrize("mode", ["valid", "public", "symlink", "missing", "hardlink"])
def test_live_command_private_input_boundary(tmp_path, mode):
    import os

    path = tmp_path / "input.json"
    path.write_bytes(b'{"synthetic":true}')
    path.chmod(0o600)
    if mode == "valid":
        assert subject._private_json(str(path)) == {"synthetic": True}
        return
    if mode == "public":
        path.chmod(0o644)
    elif mode == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    elif mode == "missing":
        path.unlink()
    else:
        os.link(path, tmp_path / "other.json")
    with pytest.raises(EvolutionError) as caught:
        subject._private_json(str(path))
    assert caught.value.code == "validation_failed"


@pytest.mark.asyncio
async def test_optional_live_command_uses_only_fixed_synthetic_content(monkeypatch):
    transport = Transport()
    monkeypatch.setattr(
        subject,
        "PinnedHttpsTransport",
        type(
            "FakePort",
            (),
            {
                "__new__": lambda cls, **kwargs: transport,
                "validate_url": staticmethod(lambda route, url: PATH),
            },
        ),
    )
    base = policy()
    config = {
        "policy": base.model_dump(mode="json"),
        "stage": "input",
        "runtime_zone": "local-shared",
        "region": REGION,
        "rule_id": "managed",
    }
    credentials = {
        "credential_ref": "bedrock-test-key",
        "access_key_id": KEY,
        "secret_access_key": SECRET,
        "session_token": None,
        "expires_at": None,
    }
    result = await subject.live_check(config, credentials, "/explicit-ca")
    assert result == {
        "profile": "bedrock_live_optional",
        "status": "completed",
        "findings": 0,
        "local_gate_evidence": False,
    }
    assert json.loads(transport.calls[0][1]["body"])["content"] == [
        {"text": {"text": "This is a synthetic guardrail connectivity check."}}
    ]
    assert len(transport.calls) == 1
