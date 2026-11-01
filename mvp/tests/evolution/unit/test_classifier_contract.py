"""Pure coverage, immutable route, parser and attribution tests; no ML imports."""
import asyncio
import hashlib
import time
from uuid import UUID

import pytest
from guardrails.evolution import classifier_contract as contract
from guardrails.evolution.classifier import (
    ClassifierEndpoint,
    ClassifierEndpointConfig,
    ClassifierHealthSnapshot,
    _segments,
)
from guardrails.evolution.compiler import policy_digest
from guardrails.evolution.egress import PinnedResponse
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
    with_resource_digest,
)

from services.classifier import worker


def uid(number):
    return str(UUID(int=number))


def resource(cls, **values):
    return with_resource_digest(cls(created_at="2026-10-12T00:00:00Z", tenant_id="team-a", **values))


def policy():
    rule = Rule(rule_id="injection", kind="injection", stages=contract.STAGES, action="block", config={"threshold": 0.9, "max_tokens": 512}, evaluator_revision=contract.EVALUATOR_REVISION)
    provider = resource(ProviderProfile, provider_id=uid(2), kind="evaluator", vendor="local_classifier", model_id=contract.MODEL_ID, artifact_digest=contract.WEIGHTS_SHA256, destination_id=uid(3), zone="local-shared", credential_ref="classifier-auth")
    destination = resource(Destination, destination_id=uid(3), name="classifier", host="classifier.internal", path_prefix="/v1/classifier", cidrs=("10.42.0.5/32",), zone="local-shared", purpose="evaluation", tls_ca_ref="classifier-ca")
    lists = (resource(ListSet, list_id=uid(4), kind="provider", allow=(uid(2),)), resource(ListSet, list_id=uid(5), kind="destination", allow=(uid(3),)))
    value = PolicyVersion(tenant_id="team-a", policy_id="classifier", version=1, author_id=uid(1), created_at="2026-10-12T00:00:00Z", rules=(rule,), providers=(provider,), resolved_destinations=(destination,), resolved_lists=lists, residency=resource(ResidencyProfile, residency_id=uid(6), provider_lists=(uid(4),), destination_lists=(uid(5),)), quota=resource(Quota, quota_id=uid(7)), status="approved", compiler_version="compiler-v2.2")
    return value.model_copy(update={"compiled_digest": policy_digest(value)})


def part(text="hello", **changes):
    return ContentPart.model_validate({"part_id": "p", "kind": "text", "role": "user", "text": text, **changes})


def request(segments=None):
    return {"api_major": 1, "identity": contract.identity(), "request_id": "a" * 32, "budget_ms": 2900, "segments": segments if segments is not None else _segments((part(),))}


class Tokenizer:
    def num_special_tokens_to_add(self, pair=False):
        assert pair is False
        return 2

    def encode(self, value, **options):
        assert options == {"add_special_tokens": False, "truncation": False}
        return list(range(int(value))) if value.isdigit() else list(range(len(value)))

    def build_inputs_with_special_tokens(self, tokens):
        return [128000] + tokens + [128001]


class Transport:
    def __init__(self, mutate=None, score=0.95):
        self.calls, self.mutate, self.score = [], mutate, score

    async def request(self, route, **kwargs):
        route.validate()
        self.calls.append((route, kwargs))
        if kwargs["method"] == "GET":
            result = {"api_major": 1, "ready": True, "identity": contract.identity()}
        else:
            value = contract.parse(kwargs["body"])
            result = {"api_major": 1, "identity": contract.identity(), "request_id": value["request_id"], "complete": True, "windows": len(value["segments"]), "scores": [{"part_id": row["part_id"], "json_pointer": row["json_pointer"], "score": self.score, "windows": 1} for row in value["segments"]]}
        if self.mutate:
            self.mutate(result)
        return PinnedResponse(200, (), contract.canonical(result))


def evaluator(approved=None, transport=None, **changes):
    approved = approved or policy()
    config = ClassifierEndpointConfig(provider=approved.providers[0], destination=approved.resolved_destinations[0], runtime_zone=changes.pop("runtime_zone", "local-shared"), language_profile=changes.pop("language_profile", "en"), bearer_token="x" * 40)
    assert not changes
    return ClassifierEndpoint(config, transport or Transport()).bind(approved, "input")


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e309}', b'{"x":[-1e309]}', b'[]', b'null', b'\xff', b'{'])
def test_closed_json_rejects_ambiguous_nonfinite_and_wrong_roots(raw):
    with pytest.raises(contract.ClassifierError):
        contract.parse(raw)


def test_wire_and_score_numeric_bounds():
    with pytest.raises(contract.ClassifierError):
        contract.parse(b" " * (contract.MAX_WIRE_BYTES + 1))
    assert contract.parse(b'{"x":1}') == {"x": 1}
    assert all(not contract.score_checked(value) for value in (True, False, -0.1, 1.1, float("nan"), float("inf"), "0.9"))
    assert all(contract.score_checked(value) for value in (0, 0.9, 1))


@pytest.mark.parametrize("field,wrong", [("api_major", True), ("api_major", 2), ("identity", {}), ("request_id", "foreign"), ("budget_ms", True), ("budget_ms", 0), ("budget_ms", 2901)])
def test_request_exact_identity_and_budget(field, wrong):
    value = request()
    value[field] = wrong
    with pytest.raises(contract.ClassifierError):
        contract.request_checked(value)


@pytest.mark.parametrize("role", [[], {}, None, True, 9])
def test_wrong_role_type_is_a_finite_protocol_refusal(role):
    value = request()
    value["segments"][0]["role"] = role
    with pytest.raises(contract.ClassifierError, match="validation_failed"):
        contract.request_checked(value)


@pytest.mark.parametrize("deadline", [True, None, float("nan"), float("inf")])
def test_invalid_worker_deadline_refuses_before_tokenization(deadline):
    class NoTokenizer:
        def num_special_tokens_to_add(self, **_):
            pytest.fail("invalid deadline must precede tokenization")

    with pytest.raises(contract.ClassifierError, match="validation_failed"):
        worker.classify(request(), NoTokenizer(), lambda _: pytest.fail("prediction"), deadline)


@pytest.mark.parametrize("count,windows", [(0, 1), (1, 1), (510, 1), (511, 2), (512, 2), (766, 2), (767, 3), (4350, 16)])
def test_windows_cover_every_token_and_tail_without_truncation(count, windows):
    segments = _segments((part(str(count)),))
    jobs = worker.window_jobs(segments, Tokenizer(), time.monotonic() + 1)
    assert len(jobs) == windows
    assert {token for _, tokens in jobs for token in tokens[1:-1]} == set(range(count))
    assert all(len(tokens) <= 512 for _, tokens in jobs)


@pytest.mark.parametrize("segments", [[{"part_id": "p", "role": "user", "json_pointer": None, "text": "4351"}], [{"part_id": f"p{i}", "role": "system", "json_pointer": None, "text": "1"} for i in range(17)]])
def test_overbound_full_job_refuses_before_any_prediction(segments):
    predictions = []
    with pytest.raises(contract.ClassifierError, match="inspection_incomplete"):
        worker.classify(request(segments), Tokenizer(), lambda tokens: predictions.append(tokens) or 0.0, time.monotonic() + 1)
    assert predictions == []


def test_long_tail_positive_aggregates_all_windows():
    seen = []
    def predict(tokens):
        seen.append(tokens)
        return 0.99 if 766 in tokens else 0.01
    result = worker.classify(request(_segments((part("767"),))), Tokenizer(), predict, time.monotonic() + 1)
    assert result["complete"] is True and result["windows"] == 3 and len(seen) == 3
    assert result["scores"] == [{"part_id": "p", "json_pointer": None, "score": 0.99, "windows": 3}]


@pytest.mark.parametrize("role", ["system", "user", "assistant", "retrieval", "tool"])
def test_every_role_and_json_string_leaf_is_preserved(role):
    values = (part("text", role=role), ContentPart(part_id="j", kind="json", role=role, value={"a/b": ["first", {"~": "last"}], "number": 3, "flag": False}))
    rows = _segments(values)
    assert [(r["part_id"], r["json_pointer"], r["text"], r["role"]) for r in rows] == [("p", None, "text", role), ("j", "/a~1b/0", "first", role), ("j", "/a~1b/1/~0", "last", role)]


def test_empty_json_and_empty_text_have_distinct_coverage():
    structured = ContentPart(part_id="j", kind="json", role="tool", value={"n": 3})
    result = worker.classify(request(_segments((structured,))), Tokenizer(), lambda _: pytest.fail("no string content"), time.monotonic() + 1)
    assert result["complete"] and result["windows"] == 0 and result["scores"] == []
    result = worker.classify(request(_segments((part(""),))), Tokenizer(), lambda _: 0.0, time.monotonic() + 1)
    assert result["windows"] == 1


def test_deadline_and_nonfinite_prediction_never_return_partial_clean():
    with pytest.raises(contract.ClassifierError, match="evaluation_timeout"):
        worker.classify(request(), Tokenizer(), lambda _: pytest.fail("expired"), time.monotonic() - 1)
    with pytest.raises(contract.ClassifierError, match="evaluator_unavailable"):
        worker.classify(request(), Tokenizer(), lambda _: float("nan"), time.monotonic() + 1)


def test_artifact_hashes_have_complete_fixed_shape():
    assert len(contract.FILES) == 9
    assert all(len(sha) == 64 and int(sha, 16) >= 0 and type(size) is int and size > 0 for sha, size in contract.FILES.values())
    assert contract.BUNDLE_SHA256 == hashlib.sha256(contract.canonical(dict(contract.FILES))).hexdigest()


@pytest.mark.parametrize("language", ["es", "auto", "all", "EN", None])
def test_english_only_language_profile_refuses_without_guessing(language):
    with pytest.raises(EvolutionError) as caught:
        evaluator(language_profile=language)
    assert caught.value.code == "capability_missing"


@pytest.mark.parametrize("field,wrong", [("model_id", "different-model"), ("artifact_digest", "0" * 64), ("vendor", "bedrock"), ("zone", "foreign-zone"), ("credential_ref", None)])
def test_provider_binding_refuses_approved_but_wrong_engine_identity(field, wrong):
    value = policy()
    changed = with_resource_digest(value.providers[0].model_copy(update={field: wrong}))
    value = value.model_copy(update={"providers": (changed,)})
    value = value.model_copy(update={"compiled_digest": policy_digest(value)})
    with pytest.raises(EvolutionError):
        evaluator(approved=value)


def test_route_policy_tampering_denies_before_transport():
    value = policy()
    object.__setattr__(value.resolved_destinations[0], "host", "unapproved.invalid")
    transport = Transport()
    with pytest.raises(EvolutionError):
        evaluator(approved=value, transport=transport)
    assert transport.calls == []


def test_successful_dispatch_binds_all_results_without_spans_or_raw_retention():
    async def run():
        approved, transport = policy(), Transport()
        subject = evaluator(approved=approved, transport=transport)
        object.__setattr__(approved.providers[0], "model_id", "later mutation does not alter bound bytes")
        value = ContentPart(part_id="j", kind="json", role="system", value={"message": "synthetic sensitive prompt"})
        findings = await subject.evaluate(policy().rules[0], (value,), time.monotonic() + 3)
        assert len(transport.calls) == 1
        route, call = transport.calls[0]
        assert route.provider.model_id == contract.MODEL_ID and call["method"] == "POST"
        assert call["path"] == "/v1/classifier/evaluate"
        assert findings[0].json_pointer == "/message" and findings[0].start is None and findings[0].end is None
        assert findings[0].reason_code == "injection_detected" and findings[0].score == 0.95
        assert "synthetic sensitive prompt" not in findings[0].model_dump_json()
        await subject.close()
    asyncio.run(run())


@pytest.mark.parametrize("field,wrong", [("complete", False), ("api_major", True), ("identity", {}), ("request_id", "f" * 32), ("windows", 2), ("scores", [])])
def test_incomplete_or_foreign_response_is_not_clean(field, wrong):
    async def run():
        subject = evaluator(transport=Transport(mutate=lambda result: result.update({field: wrong})))
        with pytest.raises(EvolutionError):
            await subject.evaluate(policy().rules[0], (part(),), time.monotonic() + 3)
        assert subject.capabilities()[0].status == "unavailable"
    asyncio.run(run())


def test_threshold_equality_is_a_finding_and_lower_score_is_clean():
    async def run():
        for score, count in ((0.9, 1), (0.89999, 0)):
            subject = evaluator(transport=Transport(score=score))
            assert len(await subject.evaluate(policy().rules[0], (part(),), time.monotonic() + 3)) == count
    asyncio.run(run())


def test_readiness_is_exact_and_expires_without_monitor_refresh(monkeypatch):
    async def run():
        subject = evaluator()
        assert subject.capabilities()[0].status == "unavailable"
        await subject.refresh_readiness(time.monotonic() + 1)
        assert subject.capabilities()[0].status == "enabled"
        with subject._endpoint._lock:
            subject._endpoint._health = ClassifierHealthSnapshot(True, time.monotonic() - 2, time.monotonic() - 1, 0)
        assert subject.capabilities()[0].status == "unavailable"
    asyncio.run(run())


def test_rule_not_in_bound_policy_never_dispatches():
    async def run():
        transport = Transport()
        subject = evaluator(transport=transport)
        changed = policy().rules[0].model_copy(update={"config": policy().rules[0].config.model_copy(update={"threshold": 0.1})})
        with pytest.raises(EvolutionError):
            await subject.evaluate(changed, (part(),), time.monotonic() + 3)
        assert transport.calls == []
    asyncio.run(run())


def test_config_repr_never_contains_token_and_private_key_changes_on_rotation():
    approved = policy()
    arguments = {"provider": approved.providers[0], "destination": approved.resolved_destinations[0], "runtime_zone": "local-shared", "language_profile": "en"}
    first = ClassifierEndpointConfig(**arguments, bearer_token="private-synthetic-token-" + "a" * 40)
    rotated = ClassifierEndpointConfig(**arguments, bearer_token="private-synthetic-token-" + "b" * 40)
    assert first.binding_key != rotated.binding_key
    assert "private-synthetic" not in repr(first) and "private-synthetic" not in repr(rotated)
    with pytest.raises(AttributeError):
        first.bearer_token = "replacement"


def test_shared_health_is_used_by_lightweight_bindings_and_adapter_close_is_local():
    async def run():
        one = evaluator()
        endpoint = one._endpoint
        two = endpoint.bind(policy(), "output")
        await endpoint.refresh_readiness(time.monotonic() + 1)
        assert one.capabilities()[0].status == two.capabilities()[0].status == "enabled"
        await one.close()
        assert one.capabilities()[0].status == "unavailable" and two.capabilities()[0].status == "enabled"
        assert {row.stage for row in endpoint.capabilities()} == {"input", "output"}
        assert all(row.actions == ("allow", "warn", "block") for row in endpoint.capabilities())
        assert endpoint.bind(policy(), "retrieval").capabilities() == ()
        await endpoint.close()
        assert two.capabilities()[0].status == "unavailable"
        with pytest.raises(EvolutionError):
            await two.evaluate(policy().rules[0], (part(),), time.monotonic() + 1)
    asyncio.run(run())


def test_operator_and_policy_exact_resource_bytes_must_match_even_when_both_valid():
    first = evaluator()
    approved = policy()
    changed = with_resource_digest(approved.providers[0].model_copy(update={"version": 2}))
    approved = approved.model_copy(update={"providers": (changed,)})
    approved = approved.model_copy(update={"compiled_digest": policy_digest(approved)})
    with pytest.raises(EvolutionError) as caught:
        first._endpoint.bind(approved, "input")
    assert caught.value.code == "provider_denied"


def test_old_inflight_health_success_cannot_erase_newer_failure():
    endpoint = evaluator()._endpoint
    first_observation = endpoint._begin_observation()
    endpoint._unavailable()
    endpoint._observed_ready(first_observation)
    assert not endpoint.health_snapshot().ready
    endpoint._observed_ready(endpoint._begin_observation())
    assert endpoint.health_snapshot().ready


def test_health_metadata_snapshot_is_safe_in_worker_threads():
    from concurrent.futures import ThreadPoolExecutor
    endpoint = evaluator()._endpoint
    endpoint._observed_ready(endpoint._begin_observation())
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: endpoint.health_snapshot(), range(64)))
    assert all(row.ready and row.expires_at > row.observed_at for row in snapshots)
    with pytest.raises(AttributeError):
        snapshots[0].ready = False


def test_independent_external_permission_never_enables_public_classifier_route():
    approved = policy()
    approved = approved.model_copy(update={"residency": with_resource_digest(approved.residency.model_copy(update={"external_processing": True}))})
    approved = approved.model_copy(update={"compiled_digest": policy_digest(approved)})
    subject = evaluator(approved=approved)
    assert subject._route().external_processing is False


@pytest.mark.parametrize("close_endpoint", [False, True])
def test_close_during_inflight_dispatch_cannot_return_a_clean_result(close_endpoint):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class DelayedTransport(Transport):
            async def request(self, route, **kwargs):
                entered.set()
                await release.wait()
                return await super().request(route, **kwargs)

        transport = DelayedTransport(score=0.01)
        subject = evaluator(transport=transport)
        task = asyncio.create_task(subject.evaluate(policy().rules[0], (part(),), time.monotonic() + 1))
        await entered.wait()
        await (subject._endpoint.close() if close_endpoint else subject.close())
        release.set()
        with pytest.raises(EvolutionError) as caught:
            await task
        assert caught.value.code == "evaluator_unavailable"
        assert len(transport.calls) == 1
        assert not subject._endpoint.health_snapshot().ready

    asyncio.run(run())
