"""Real tenant governance, signed stores and HTTPS for Rules composition tests.

All database/network/process work happens only when a parent invokes a test.
No model or managed detector is mocked or dispatched. These fixtures compose
tenant-owned ancestors; they do not fabricate a published _platform resolver.
"""
from __future__ import annotations

import copy
import hashlib
import json
from contextlib import ExitStack, contextmanager
from uuid import uuid4

from guardrails.evolution import models
from guardrails.evolution.runtime_api import EvaluationRuntime, RuntimeSlot, create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from test_evaluation_runtime import config, fixture_system, http, ref, refresh, request

SLOTS = ("first", "second")
STAGES = ("input", "output")
MAIL = "composition-private@example.test"
KEY = "AKIA0123456789ABCDEF"


def fingerprint(value):
    return hashlib.sha256(models.canonical_json_bytes(value)).hexdigest()


def close_system(system):
    try:
        system["service"].audit.close()
    finally:
        system["repo"].close()


@contextmanager
def fresh_pair(directory):
    with ExitStack() as stack:
        pair = {}
        for slot in SLOTS:
            pair[slot] = fixture_system(directory / slot, baseline=False)
            stack.callback(close_system, pair[slot])
        assert pair["first"]["tenant"] != pair["second"]["tenant"]
        yield pair


def listing(system, *, allow=(), deny=(), enabled=False):
    return models.with_resource_digest(models.ListSet(
        list_id=str(uuid4()), tenant_id=system["tenant"], kind="content",
        allow=tuple(allow), deny=tuple(deny), allow_enabled=enabled,
    ))


def rule(identity, kind, action, stage, config_value):
    revision = {"pii": "pii-v2", "secret": "secrets-v1", "term": "lexical-v1",
                "topic": "lexical-v1", "json_schema": "schema-v1"}[kind]
    return {"rule_id": identity, "kind": kind, "stages": [stage], "action": action,
            "mandatory": False, "minimum_state": "disabled", "config": config_value,
            "evaluator_revision": revision}


def approve(system, identity, *, rules=(), lists=(), content_ids=(), parents=()):
    """Use actual immutable resource insertion, compilation and distinct review."""
    service, actors = system["service"], system["actors"]
    assert actors["author"].principal_id != actors["approver"].principal_id
    payload = copy.deepcopy(system["payload"])
    payload.update(rules=list(rules), parents=list(parents))
    with system["repo"].transaction(system["tenant"]) as tx:
        old = tx.get("config:residency", payload["residency_ref"]["id"] + ":1", required=True)
        residency = models.with_resource_digest(models.ResidencyProfile.model_validate({
            **old, "residency_id": str(uuid4()), "version": 1, "content_lists": list(content_ids),
            "digest": "0" * 64,
        }))
        tx.insert("config:residency", residency.residency_id + ":1", residency)
        for entry in lists:
            tx.insert("config:lists", entry.list_id + ":1", entry)
        payload["residency_ref"] = ref(residency, "residency_id")
        # Keep the original, exact inference provider allow resource. Parents
        # merge their other resources through the actual production compiler.
        payload["list_refs"] = [*payload["list_refs"], *(ref(entry, "list_id") for entry in lists)]
        value = service.governance.submit(tx, actors["author"], identity, payload)
        service.governance.review(tx, actors["approver"], identity, value["version"],
            outcome="approve", compiled_digest=value["compiled_digest"], reason="Synthetic composition reviewed")
        reference = {"tenant_id": system["tenant"], "policy_id": identity,
                     "version": value["version"], "digest": value["compiled_digest"]}
        actual = service.governance.get(tx, identity, value["version"])
        assert actual["status"] == "approved"
        reviews = [row for row in tx.list("approvals") if row["compiled_digest"] == reference["digest"]]
        assert len(reviews) == 1
        assert reviews[0]["actor_id"] == actors["approver"].principal_id
    return value, reference


def activate(system, selected, *, floors=None, state="block"):
    value, reference = selected
    with system["repo"].transaction(system["tenant"]) as tx:
        result = system["service"].governance.publish_baseline(
            tx, system["actors"]["tenant_admin"], floors or [reference], 0)
        assert result["revision"] == 1
    system.update(policy=value, policy_ref=reference)
    refresh(system, state="observe", policy_ref=reference)
    if state != "observe":
        refresh(system, state=state)
    with system["repo"].transaction(system["tenant"]) as tx:
        receipts = [row for row in tx.list("receipts")
                    if row["binding_revision"] == system["revision"] and row["workload_id"] == "chat"]
        assert {row["runtime_id"] for row in receipts} == set(system["runtime_ids"])
        assert len(receipts) == 2
        assert all(row["digest"] == reference["digest"] and row["health"] == "ready" for row in receipts)
    return system


def layered(system, *, state="block", rules=()):
    """Three genuine same-tenant ancestors; global publication is a separate seam."""
    entries = (
        listing(system, allow=("term:common", "term:parent-only", "term:overlap"),
                deny=("term:rootdeny",), enabled=True),
        listing(system, allow=("term:common", "term:tenant-only", "term:overlap"),
                deny=("term:tenantdeny",), enabled=True),
        listing(system, allow=("term:common", "term:workload-only", "term:overlap"),
                deny=("term:workloaddeny", "term:overlap"), enabled=True),
    )
    first = approve(system, "composition-parent", lists=(entries[0],), content_ids=(entries[0].list_id,))
    second = approve(system, "composition-tenant", lists=(entries[1],),
                     content_ids=tuple(x.list_id for x in entries[:2]), parents=(first[1],))
    last = approve(system, "composition-workload", lists=(entries[2],), rules=rules,
                   content_ids=tuple(x.list_id for x in entries), parents=(second[1],))
    compiled = last[0]
    assert {x["list_id"] for x in compiled["resolved_lists"] if x["kind"] == "content"} == {x.list_id for x in entries}
    assert set(compiled["residency"]["content_lists"]) == {x.list_id for x in entries}
    assert compiled["parents"] == [second[1]]
    activate(system, last, floors=[first[1], second[1]], state=state)
    return entries


def text_parts(value, stage, *, second=None):
    role = "assistant" if stage == "output" else "user"
    result = [{"part_id": "content", "kind": "text", "role": role, "text": value}]
    if second is not None:
        result.append({"part_id": "second", "kind": "text", "role": role, "text": second})
    return result


@contextmanager
def clients(pair, directory):
    """One actual HTTPS runtime with both fixed tenant slots and real Rules pools."""
    first = pair["first"]

    def factory():
        service = EvaluationRuntime(TenantRepository(first["dsn"]), config=config(first),
            runtime_slots={item["tenant"]: RuntimeSlot(item["stores"][0]) for item in pair.values()})
        return create_app(service=service)

    with ExitStack() as stack:
        server = stack.enter_context(serve_https(factory, directory))
        result = {slot: stack.enter_context(http(server, system["tokens"]["workload"]))
                  for slot, system in pair.items()}
        for client in result.values():
            ready = client.get("/health/ready")
            assert ready.status_code == 200 and ready.json()["ready"] is True
        yield result


def evaluate(client, system, stage, parts):
    context = None
    opened = None
    if stage == "output":
        value = request(system, text="common ordinary", stage="input")
        response = client.post("/v2/evaluations", json=value)
        assert response.status_code == 200
        opened = models.Decision.model_validate_json(response.content)
        assert opened.action == opened.would_be_action == "allow"
        assert opened.findings == ()
        context = opened.context_id
    payload = request(system, stage=stage, parts=parts, context_id=context)
    response = client.post("/v2/evaluations", json=payload)
    assert response.status_code == 200
    result = models.Decision.model_validate_json(response.content)
    assert result.request_id == payload["request_id"] and result.stage == stage
    assert result.tenant_id == system["tenant"] and result.workload_id == "chat"
    assert result.policy.model_dump(mode="json") == system["policy_ref"]
    assert result.binding_revision == system["revision"]
    assert result.audit_status == "persisted" and result.evaluation_ms < 500
    if opened is not None:
        assert result.context_id == opened.context_id
        assert result.policy == opened.policy and result.snapshot_generation == opened.snapshot_generation
    return result


def audited_isolation(pair, slot, client, decision, *, forbidden=()):
    system = pair[slot]
    other = pair[SLOTS[1 - SLOTS.index(slot)]]
    # Authenticate the current tenant but submit the other tenant's body. A
    # scope refusal must create no peer evaluation, context or quota lease.
    denied = client.post("/v2/evaluations", json=request(other, text="common ordinary"))
    assert denied.status_code == 404
    with system["repo"].transaction(system["tenant"]) as tx:
        events = [row for row in tx.list("audit") if row["event_type"] == "evaluation"]
        assert len(events) == (2 if decision.stage == "output" else 1)
        selected = [row for row in events if row["decision_id"] == decision.decision_id]
        assert len(selected) == 1
        event = selected[0]
        assert event["tenant_id"] == system["tenant"] and event["workload_id"] == "chat"
        assert event["actor_id"] == system["actors"]["workload"].principal_id
        assert event["metadata"] == {"stage": decision.stage}
        assert event["action"] == decision.action and event["reason_codes"] == list(decision.reason_codes)
        assert event["policy_digest"] == decision.policy.digest and event["binding_revision"] == decision.binding_revision
        foreign = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant",
                                  {"tenant": other["tenant"]}))
        assert len(foreign) == 0
        stored = [row.data for row in tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant",
                                                {"tenant": system["tenant"]})]
        has_raw = any(value in json.dumps(stored) for value in forbidden)
        assert has_raw is False
    with other["repo"].transaction(other["tenant"]) as tx:
        assert len([row for row in tx.list("audit") if row["event_type"] == "evaluation"]) == 0
        assert len(tx.list("evaluation_contexts")) == len(tx.list("quota_leases")) == 0

