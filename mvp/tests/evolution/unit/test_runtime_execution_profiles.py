"""Exercise real HTTP timeout ownership and locked admission with inert DB ports."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from guardrails.evolution.deadlines import current_deadline, remaining_ms
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import EvaluationRequest
from guardrails.evolution.runtime_api import EvaluationRuntime, create_app
from test_compiler import policy, rule, uid
from test_execution_profiles import config, injection


def payload(**changes):
    return {"request_id": str(uuid4()), "correlation_id": str(uuid4()),
            "tenant_id": "team-a", "workload_id": "chat", "provider_id": uid(910),
            "stage": "input", "deadline_ms": 180000,
            "parts": [{"part_id": "body", "kind": "text", "role": "user", "text": "synthetic"}]} | changes


def fixture_runtime(*, rules=None, state="block", detector_delay=0, policy_delay=0,
                    authentication_delay=0, refusal=None, settings=None):
    candidate = policy(rules=rules if rules is not None else [injection()])
    identity = SimpleNamespace(tenant_id="team-a", workload_id="chat", principal_id=uid(12),
                               principal={"tenant_id": "team-a"})
    records = SimpleNamespace(order=[], leases=[], writes=[], detectors=[], finalized=[], released=[])

    class Transaction:
        def lock_governance(self):
            records.order.append("organization-lock")

        def lock(self, name):
            assert name == "governance-admission"
            records.order.append("lock")

        def insert(self, kind, key, value):
            records.writes.append((kind, key, value))

    @contextmanager
    def transaction(tenant):
        assert tenant == identity.tenant_id
        try:
            yield Transaction()
            remaining_ms()
            records.order.append("commit")
        except BaseException:
            records.writes.clear()
            records.order.append("rollback")
            raise

    def eligible(*args, **kwargs):
        records.order.append("eligible")
        if policy_delay:
            time.sleep(policy_delay)
        if refusal:
            raise EvolutionError(refusal)
        return SimpleNamespace(generation=1), SimpleNamespace(revision=1, state=state), candidate

    def reserve(*args, **kwargs):
        lease = kwargs["deadline_monotonic"]
        admission = current_deadline.get()
        assert admission <= lease
        remaining_ms()
        records.leases.append((admission, lease))
        records.order.append("reserve")
        return object()

    async def detector(rule, parts, deadline, **kwargs):
        records.detectors.append((time.monotonic(), deadline))
        await asyncio.sleep(detector_delay)
        return ()

    service = EvaluationRuntime.__new__(EvaluationRuntime)
    service.config = settings or config()
    service.repository = SimpleNamespace(transaction=transaction)
    service.identity = SimpleNamespace(authorize=lambda *a, **kw: None)
    service.governance = SimpleNamespace(current_baselines=lambda tx: [candidate])
    service.registry = SimpleNamespace(evaluate=detector)
    service.quotas = SimpleNamespace(reserve=reserve, release=lambda value: records.released.append(value))
    service._active_credential = lambda *args: {"status": "active"}
    service._policy = eligible
    service._finalize = lambda *args: records.finalized.append(current_deadline.get())
    service.clock = lambda: datetime.now(UTC)
    service._executors = {"team-a": ThreadPoolExecutor(max_workers=1)}
    service._pending = {"team-a": 0}
    service.slots = {"team-a": None}
    service._jobs, service._cleanup = set(), set()
    service._started, service._closed = True, False

    async def authenticate(token, deadline):
        assert token == "synthetic"
        await asyncio.sleep(authentication_delay)
        return identity

    service.authenticate = authenticate
    return service, identity, records


def exercise(service, value=None):
    async def send():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=service)),
                                     base_url="https://runtime.example") as client:
            response = await client.post("/v2/evaluations", json=value or payload(),
                                         headers={"Authorization": "Bearer synthetic"})
        await asyncio.gather(*tuple(service._cleanup))
        return response
    try:
        return asyncio.run(send())
    finally:
        service._executors["team-a"].shutdown(wait=True, cancel_futures=True)


def test_classifier_policy_reschedules_http_budget_only_after_locked_admission():
    service, _, records = fixture_runtime(detector_delay=.55)
    before = time.monotonic()
    response = exercise(service)
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "allow"
    assert response.json()["evaluation_ms"] >= 550
    assert time.monotonic() - before < 2
    assert records.order == ["organization-lock", "lock", "eligible", "reserve", "commit"]
    admission, lease = records.leases[0]
    assert lease - admission == pytest.approx(2.5, abs=.01)
    assert records.detectors[0][1] == pytest.approx(lease - .1)
    assert records.finalized == [lease] and len(records.released) == 1


@pytest.mark.parametrize("rules", [[rule()], [injection(stages=["output"])], [rule(), injection(stages=["output"])]])
def test_text_or_other_stage_cannot_request_classifier_time(rules):
    service, _, records = fixture_runtime(rules=rules, detector_delay=.55)
    response = exercise(service)
    if any("input" in rule.stages for rule in rules):
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "evaluation_timeout"
        assert records.finalized == []
    else:
        assert response.status_code == 200
        assert records.detectors == []
    assert records.leases[0][0] == records.leases[0][1]
    assert len(records.released) == 1


def test_disabled_classifier_has_text_budget_and_no_detector():
    service, _, records = fixture_runtime(state="disabled", detector_delay=.55)
    response = exercise(service)
    assert response.status_code == 200
    assert response.json()["reason_codes"] == ["disabled"]
    assert records.detectors == []
    assert records.leases[0][0] == records.leases[0][1]


@pytest.mark.parametrize("code", ["policy_revoked", "floor_conflict", "destination_denied", "capability_missing"])
def test_ineligible_policy_cannot_reserve_or_extend_admission(code):
    service, _, records = fixture_runtime(refusal=code)
    response = exercise(service)
    assert response.status_code != 200
    assert response.json()["error"]["code"] == code
    assert records.order == ["organization-lock", "lock", "eligible", "rollback"]
    assert records.leases == records.detectors == records.writes == []


def test_late_authentication_cannot_acquire_larger_classifier_budget():
    service, _, records = fixture_runtime(authentication_delay=.55)
    response = exercise(service)
    assert response.status_code == 504
    assert records.order == records.leases == records.detectors == []


def test_slow_request_body_keeps_initial_budget_before_trusted_policy_selection():
    service, _, records = fixture_runtime()

    async def send():
        async def body():
            raw = json.dumps(payload()).encode()
            yield raw[:1]
            await asyncio.sleep(.55)
            yield raw[1:]

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=service)),
                                     base_url="https://runtime.example") as client:
            return await client.post("/v2/evaluations", content=body(),
                                     headers={"Authorization": "Bearer synthetic", "Content-Type": "application/json"})

    try:
        response = asyncio.run(send())
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "evaluation_timeout"
        assert records.order == records.leases == records.detectors == []
    finally:
        service._executors["team-a"].shutdown(wait=True, cancel_futures=True)


def test_admission_past_initial_deadline_rolls_back_without_dispatch():
    service, _, records = fixture_runtime(policy_delay=.55)
    response = exercise(service)
    assert response.status_code == 504
    assert records.writes == records.detectors == records.finalized == []
    assert records.order[-1] == "rollback"


def test_admission_time_remains_spent_inside_classifier_profile():
    service, _, records = fixture_runtime(policy_delay=.15)
    response = exercise(service)
    assert response.status_code == 200
    admission, lease = records.leases[0]
    begun, detector_end = records.detectors[0]
    assert lease - admission == pytest.approx(2.5, abs=.01)
    assert 2.5 < detector_end - begun < 2.79


@pytest.mark.parametrize("request_ms,configured", [(220, 3000), (180000, 220)])
def test_request_and_configuration_can_shorten_classifier_and_database_budget(request_ms, configured):
    service, _, records = fixture_runtime(settings=config(classifier_budget_ms=configured))
    response = exercise(service, payload(deadline_ms=request_ms))
    assert response.status_code == 200
    admission, lease = records.leases[0]
    assert admission == lease
    begun, detector_end = records.detectors[0]
    assert 0 < detector_end - begun <= .12


def test_direct_runtime_caller_outer_deadline_is_never_extended():
    service, identity, records = fixture_runtime()
    async def run():
        started = time.monotonic()
        end = started + .3
        result = await service.evaluate(identity, EvaluationRequest.model_validate(payload()),
                                        started=started, deadline=end)
        await asyncio.gather(*tuple(service._cleanup))
        assert result.action == "allow"
        assert records.leases[0] == (end, end)
        assert records.detectors[0][1] == pytest.approx(end - .1)
    try:
        asyncio.run(run())
    finally:
        service._executors["team-a"].shutdown(wait=True, cancel_futures=True)

