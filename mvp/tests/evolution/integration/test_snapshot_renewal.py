"""Early signed renewal preserves strict expiry and pinned in-flight contexts.

Real PostgreSQL/TLS/detector ports; the UTC admission/lease clock is controlled.
The monotonic evaluation clock and its original 500 ms budget are not changed.
"""

import multiprocessing
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import httpx
import pytest
from connector.evolution import TextConnector
from guardrails.evolution import models
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import STATUS, EvolutionError
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.runtime_apply import RuntimeSnapshotStore
from guardrails.evolution.snapshots import SnapshotDistributor
from guardrails.evolution.storage import TenantRepository
from guardrails.evolution.sync_api import mount_sync_routes
from https_support import serve_https
from test_evaluation_runtime import (
    fixture_system,
    http,
    refresh,
    request,
    runtime,
)


class LeaseClock:
    def __init__(self, value):
        self.value = multiprocessing.get_context("fork").Value("d", value.timestamp())

    def __call__(self):
        return datetime.fromtimestamp(self.value.value, UTC)

    def set(self, value):
        self.value.value = value.timestamp()


@pytest.fixture
def system(tmp_path):
    value = fixture_system(tmp_path / "system")
    try:
        yield value
    finally:
        value["service"].audit.close()
        value["repo"].close()


def issuer(system, clock, *, ttl_s=60, repository=None):
    repo = repository or system["repo"]
    service = system["service"]
    return SnapshotDistributor(
        repo,
        service.governance,
        service.bindings,
        system["key"],
        "test-key",
        clock=clock,
        ttl_s=ttl_s,
    )


def issue(system, distributor):
    with distributor.repository.transaction(system["tenant"]) as tx:
        return distributor.issue(tx, system["actors"]["runtime-a"])


def require_decision(response, system):
    """On the original failed call, retain finite stages without any payload/ID."""
    if response.status_code != 200:
        code = None
        try:
            candidate = models.strict_json_loads(response.content)["error"]["code"]
            if type(candidate) is str and candidate in STATUS:
                code = candidate
        except ValueError, KeyError, TypeError:
            pass
        states = stages = "metadata_unavailable"
        token = current_deadline.set(time.monotonic() + 2)
        try:
            with system["repo"].transaction(system["tenant"]) as tx:
                contexts = tx.list("evaluation_contexts")
                audits = [
                    row for row in tx.list("audit") if row["event_type"] == "evaluation"
                ]
            states = {
                name: sum(row["state"] == name for row in contexts)
                for name in (
                    "evaluating_input",
                    "open",
                    "evaluating_output",
                    "completed",
                    "blocked",
                )
            }
            stages = {
                name: sum(row["metadata"].get("stage") == name for row in audits)
                for name in ("input", "output")
            }
        except Exception:  # noqa: BLE001 - observation cannot replace the original HTTP failure
            states = stages = "metadata_unavailable"
        finally:
            current_deadline.reset(token)
        pytest.fail(
            f"runtime_status={response.status_code} code={code} context_counts={states} audit_stage_counts={stages}"
        )
    return models.Decision.model_validate_json(response.content)


@pytest.mark.parametrize("ttl_s", [60, 20, 2])
def test_signed_renewal_exact_boundary_preserves_old_wire_and_bounded_ttl(
    system, tmp_path, ttl_s
):
    # Move past the fixture's original generation before selecting a short TTL.
    old_expiry = models.parse_time(system["stores"][0].snapshot.expires_at)
    clock = LeaseClock(old_expiry + timedelta(seconds=1))
    distributor = issuer(system, clock, ttl_s=ttl_s)
    original = issue(system, distributor)
    original_bytes = models.canonical_json_bytes(original)
    boundary = models.parse_time(original["expires_at"]) - timedelta(
        seconds=min(10, ttl_s / 2)
    )
    clock.set(boundary - timedelta(microseconds=1))
    assert models.canonical_json_bytes(issue(system, distributor)) == original_bytes
    clock.set(boundary)
    renewed = issue(system, distributor)
    assert renewed["generation"] == original["generation"] + 1
    assert models.parse_time(renewed["issued_at"]) == clock()
    assert models.parse_time(renewed["expires_at"]) == clock() + timedelta(
        seconds=ttl_s
    )
    assert models.parse_time(renewed["issued_at"]) < models.parse_time(
        original["expires_at"]
    )
    assert (
        renewed["digest"] != original["digest"]
        and renewed["signature"] != original["signature"]
    )
    for name in ("bindings", "policies", "revoked_digests"):
        assert renewed[name] == original[name]
    store = RuntimeSnapshotStore(
        tmp_path / "verified",
        system["tenant"],
        system["runtime_ids"][0],
        {"test-key": system["key"].public_key()},
        clock=clock,
    )
    assert store.verify(renewed).generation == renewed["generation"]
    assert issue(system, distributor) == renewed  # No per-poll generation churn.
    with system["repo"].transaction(system["tenant"]) as tx:
        retained = tx.get(
            "issued_snapshots",
            f"{system['runtime_ids'][0]}:{original['generation']}",
            required=True,
        )
    assert models.canonical_json_bytes(retained) == original_bytes
    clock.set(models.parse_time(original["expires_at"]))
    with pytest.raises(EvolutionError) as caught:
        store.verify(original)
    assert caught.value.code == "snapshot_stale"
    assert store.verify(renewed).generation == renewed["generation"]


def test_concurrent_issuers_commit_one_early_generation(system):
    original = system["stores"][0].snapshot.model_dump(mode="json")
    clock = LeaseClock(
        models.parse_time(original["expires_at"]) - timedelta(seconds=10)
    )
    barrier = Barrier(2)

    def competing_issue():
        import time

        repo = TenantRepository(system["dsn"])
        token = current_deadline.set(time.monotonic() + 5)
        try:
            barrier.wait(timeout=3)
            return issue(system, issuer(system, clock, repository=repo))
        finally:
            current_deadline.reset(token)
            repo.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(competing_issue) for _ in range(2)]
        results = [future.result(timeout=6) for future in futures]
    assert results[0] == results[1]
    assert results[0]["generation"] == original["generation"] + 1
    with system["repo"].transaction(system["tenant"]) as tx:
        generations = [
            row["generation"]
            for row in tx.list("issued_snapshots")
            if row["runtime_id"] == system["runtime_ids"][0]
        ]
    assert generations.count(results[0]["generation"]) == 1


def test_real_https_etag_reuses_before_lead_and_returns_new_signed_lease_at_boundary(
    system, tmp_path
):
    original = system["stores"][0].snapshot.model_dump(mode="json")
    expiry = models.parse_time(original["expires_at"])
    clock = LeaseClock(expiry - timedelta(seconds=11))

    def factory():
        repo = TenantRepository(system["dsn"])
        service = ManagementApi(repo)
        distributor = SnapshotDistributor(
            repo,
            service.governance,
            service.bindings,
            system["key"],
            "test-key",
            clock=clock,
        )
        return mount_sync_routes(create_app(service=service), service, distributor)

    with (
        serve_https(factory, tmp_path / "sync-tls") as endpoint,
        httpx.Client(
            base_url=endpoint["url"],
            verify=ssl.create_default_context(cafile=endpoint["ca_file"]),
            trust_env=False,
            timeout=3,
            headers={"Authorization": "Bearer " + system["tokens"]["runtime-a"]},
        ) as client,
    ):
        etag = '"' + original["digest"] + '"'
        unchanged = client.get("/v2/runtime/snapshot", headers={"If-None-Match": etag})
        assert unchanged.status_code == 304 and not unchanged.content
        clock.set(expiry - timedelta(seconds=10))
        response = client.get("/v2/runtime/snapshot", headers={"If-None-Match": etag})
        assert response.status_code == 200
        renewed = models.Snapshot.model_validate_json(response.content)
        assert renewed.generation == original["generation"] + 1
        assert models.parse_time(renewed.expires_at) - models.parse_time(
            renewed.issued_at
        ) == timedelta(seconds=60)
        store = RuntimeSnapshotStore(
            tmp_path / "sync-cache",
            system["tenant"],
            system["runtime_ids"][0],
            {"test-key": system["key"].public_key()},
            clock=clock,
        )
        receipt = store.apply(response.content)[0]
        from uuid import uuid4

        acknowledged = client.post(
            "/v2/runtime/receipts",
            json=receipt,
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert acknowledged.status_code == 200 and acknowledged.json() == receipt
        current = client.get(
            "/v2/runtime/snapshot",
            headers={"If-None-Match": '"' + renewed.digest + '"'},
        )
        assert current.status_code == 304 and not current.content


@pytest.mark.parametrize("change", ["unchanged", "new_binding", "revoke", "floor"])
def test_real_https_output_crosses_old_lease_expiry_with_pinned_context_and_current_restrictions(
    system, tmp_path, change
):
    original = system["stores"][0].snapshot
    expiry = models.parse_time(original.expires_at)
    clock = LeaseClock(expiry - timedelta(seconds=11))
    system["distributor"].clock = clock
    for store in system["stores"]:
        store.clock = clock

    def factory():
        from guardrails.evolution.runtime_api import create_app as runtime_app

        return runtime_app(service=runtime(system, clock=clock))

    with (
        serve_https(factory, tmp_path / "runtime-tls") as endpoint,
        http(endpoint, system["tokens"]["workload"]) as client,
    ):
        first_request = request(system, text="A clear blue sky.")
        first = client.post("/v2/evaluations", json=first_request)
        before = require_decision(first, system)
        assert (
            before.action == "allow"
            and before.audit_status == "persisted"
            and before.evaluation_ms < 500
        )
        clock.set(expiry - timedelta(seconds=10))
        refresh(system)
        assert system["stores"][0].snapshot.generation == original.generation + 1
        renewed = system["stores"][0].snapshot
        if change == "new_binding":
            with system["repo"].transaction(system["tenant"]) as tx:
                policy = system["service"].governance.submit(
                    tx, system["actors"]["author"], "policy", system["payload"]
                )
                system["service"].governance.review(
                    tx,
                    system["actors"]["approver"],
                    "policy",
                    2,
                    outcome="approve",
                    compiled_digest=policy["compiled_digest"],
                    reason="Approved next policy",
                )
            next_ref = {
                **system["policy_ref"],
                "version": 2,
                "digest": policy["compiled_digest"],
            }
            refresh(system, state="observe", policy_ref=next_ref)
        elif change == "revoke":
            with system["repo"].transaction(system["tenant"]) as tx:
                system["service"].governance.review(
                    tx,
                    system["actors"]["approver"],
                    "policy",
                    1,
                    outcome="revoke",
                    compiled_digest=system["policy_ref"]["digest"],
                    reason="Revoked before output",
                )
        elif change == "floor":
            with system["repo"].transaction(system["tenant"]) as tx:
                workload = tx.get("workloads", "chat", required=True)
                tx.put(
                    "workloads",
                    "chat",
                    {**workload, "required_state": "block"},
                    expected_revision=workload["revision"],
                )
        clock.set(expiry + timedelta(seconds=1))
        assert models.parse_time(renewed.expires_at) > clock()
        value = request(
            system,
            text="The sky remains blue.",
            stage="output",
            context_id=before.context_id,
        )
        response = client.post("/v2/evaluations", json=value)
        if change in {"revoke", "floor"}:
            assert response.status_code == 409
            assert (
                response.json()["error"]["code"]
                == {"revoke": "policy_revoked", "floor": "floor_conflict"}[change]
            )
        else:
            after = require_decision(response, system)
            TextConnector.verify(
                after, models.EvaluationRequest.model_validate(value), before
            )
            assert (
                after.policy == before.policy
                and after.binding_revision == before.binding_revision
            )
            assert (
                after.context_id == before.context_id
                and after.snapshot_generation == before.snapshot_generation
            )
            assert (
                after.action == "allow"
                and after.audit_status == "persisted"
                and after.evaluation_ms < 500
            )
        with system["repo"].transaction(system["tenant"]) as tx:
            context = tx.get("evaluation_contexts", before.context_id, required=True)
            events = [
                row for row in tx.list("audit") if row["event_type"] == "evaluation"
            ]
        assert context["state"] == (
            "open" if change in {"revoke", "floor"} else "completed"
        )
        assert len(events) == (1 if change in {"revoke", "floor"} else 2)
        assert all(row["raw_content_retained"] is False for row in events)
