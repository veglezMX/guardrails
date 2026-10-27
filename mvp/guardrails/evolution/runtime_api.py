"""Authenticated, bounded text evaluation using exact approved policy bytes."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import uniform
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .audit import AuditStore
from .config import ServiceConfig, load_config
from .deadlines import current_deadline
from .errors import EvolutionError
from .execution_profiles import evaluation_deadlines, policy_profile
from .governance import PolicyGovernance, lock_admission, policy_model
from .identity import IdentityService
from .models import (
    Capability,
    Decision,
    Error,
    EvaluationContext,
    EvaluationRequest,
    canonical_json_bytes,
    parse_time,
    strict_json_loads,
)
from .plugins import EvaluatorRegistry
from .runtime_apply import RuntimeApplyClient, RuntimeSnapshotStore
from .snapshots import stamp

STATES = ("disabled", "observe", "warn", "redact", "block")
ACTIONS = ("allow", "warn", "redact", "block")
MAX_BODY_BYTES = 24 * 1024 * 1024


@dataclass(frozen=True)
class RuntimeSlot:
    store: RuntimeSnapshotStore
    client: RuntimeApplyClient | None = None


class EvaluationRuntime:
    def __init__(self, repository, *, config: ServiceConfig, runtime_slots,
                 registry=None, quotas=None, residency=None, identity=None,
                 governance=None, audit=None, clock=None):
        if not isinstance(config, ServiceConfig) or config.mode == "customer":
            # Portable/offline admission has a distinct owning phase.
            raise EvolutionError("capability_missing")
        if not isinstance(runtime_slots, dict) or not runtime_slots or len(runtime_slots) > 4:
            raise EvolutionError("validation_failed")
        if any(not isinstance(slot, RuntimeSlot) or tenant != slot.store.tenant_id or slot.client is not None and slot.client.store is not slot.store for tenant, slot in runtime_slots.items()):
            raise EvolutionError("validation_failed")
        self.repository, self.config = repository, config
        self.slots = dict(runtime_slots)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.identity = identity or IdentityService(repository, clock=self.clock)
        self.audit = audit or AuditStore(repository)
        self.governance = governance or PolicyGovernance(repository, self.audit)
        endpoints = {}
        if registry is None and config.evaluator_config_file is not None:
            from .evaluator_config import load_evaluator_endpoints
            endpoints = load_evaluator_endpoints(config.evaluator_config_file,
                runtime_zone=config.zone, external_processing=config.external_processing,
                tenant_ids=tuple(sorted(self.slots)))
        self.registry = registry or EvaluatorRegistry(
            tenant_ids=tuple(sorted(self.slots)), workers=config.worker_count,
            max_queued_jobs=config.queue_limit,
            timeout_ms=min(400, config.text_budget_ms - config.audit_reserve_ms),
            zone=config.zone,
            **endpoints,
        )
        if quotas is None:
            from .quotas import QuotaService
            quotas = QuotaService(repository, clock=clock)
        if residency is None:
            from .residency import ResidencyGuard
            residency = ResidencyGuard()
        self.quotas, self.residency = quotas, residency
        for slot in self.slots.values():
            slot.store.capability_check = self.registry.require
        # Every tenant owns bounded metadata workers too; blocked DB work for A
        # cannot occupy B's threads or create an unbounded executor queue.
        self._executors = {tenant: ThreadPoolExecutor(max_workers=2, thread_name_prefix="runtime-metadata") for tenant in self.slots}
        self._pending = {tenant: 0 for tenant in self.slots}
        self._polls, self._jobs, self._cleanup = set(), set(), set()
        self._started, self._closed = False, False

    async def metadata(self, tenant_id, operation, deadline):
        if tenant_id not in self._executors:
            raise EvolutionError("authentication_failed")
        if deadline <= time.monotonic():
            raise EvolutionError("evaluation_timeout")
        if self._pending[tenant_id] >= 2 + self.config.queue_limit // len(self.slots):
            raise EvolutionError("queue_full")
        self._pending[tenant_id] += 1
        loop = asyncio.get_running_loop()

        def run():
            token = current_deadline.set(deadline)
            try:
                if time.monotonic() >= deadline:
                    raise EvolutionError("evaluation_timeout")
                return operation()
            finally:
                current_deadline.reset(token)

        try:
            future = self._executors[tenant_id].submit(run)
        except RuntimeError:
            self._pending[tenant_id] -= 1
            raise EvolutionError("evaluator_unavailable") from None

        def done(_):
            def decrement():
                self._pending[tenant_id] -= 1
            if not loop.is_closed():
                loop.call_soon_threadsafe(decrement)

        future.add_done_callback(done)
        wrapped = asyncio.wrap_future(future)
        # Retrieve eventual failure even when a request no longer awaits it.
        def consume(value):
            if not value.cancelled():
                value.exception()
        wrapped.add_done_callback(consume)
        shielded = asyncio.shield(wrapped)
        shielded.add_done_callback(consume)
        try:
            async with asyncio.timeout_at(deadline):
                result = await shielded
            if time.monotonic() >= deadline:
                raise EvolutionError("evaluation_timeout")
            return result
        except TimeoutError:
            future.cancel()
            raise EvolutionError("evaluation_timeout") from None
        except asyncio.CancelledError:
            future.cancel()
            raise
        except SQLAlchemyError:
            raise EvolutionError("policy_unresolved") from None

    def token_scope(self, token):
        # This selects a fixed executor, never authorizes a tenant or file path.
        if not isinstance(token, str) or len(token) > 256:
            raise EvolutionError("authentication_failed")
        fields = token.split(".")
        if len(fields) != 4 or fields[0] != "gr2" or fields[1] not in self.slots:
            raise EvolutionError("authentication_failed")
        return fields[1]

    async def authenticate(self, token, deadline):
        tenant = self.token_scope(token)
        return await self.metadata(tenant, lambda: self.identity.authenticate(token), deadline)

    def _runtime_active(self, tx, slot, workload_id=None):
        row = tx.execute("""SELECT r.data AS install,e.data AS enrollment,p.data AS actor
            FROM evolution.records r JOIN evolution.records e
              ON e.tenant_id=r.tenant_id AND e.kind='runtime_enrollments' AND e.key=r.key
            JOIN evolution.records p ON p.tenant_id=r.tenant_id
              AND p.kind='principals' AND p.key=e.data->>'principal_id'
            JOIN evolution.records association ON association.tenant_id=r.tenant_id
              AND association.kind='runtime_principals' AND association.key=p.key
              AND association.data->>'runtime_id'=r.key
            WHERE r.tenant_id=:tenant AND r.kind='runtimes' AND r.key=:runtime
            FOR SHARE OF r,e,p,association""", {"tenant": tx.tenant_id, "runtime": slot.store.runtime_id}).first()
        if row is None:
            raise EvolutionError("resource_not_found")
        install, actor = row.install, row.actor
        if install["status"] == "revoked" or actor["status"] != "active" or "runtime" not in actor["roles"]:
            raise EvolutionError("forbidden")
        if install["zone"] != self.config.zone or install["mode"] != self.config.mode:
            raise EvolutionError("residency_denied")
        if workload_id is not None and workload_id not in row.enrollment["workload_ids"]:
            raise EvolutionError("resource_not_found")
        return install

    def _active_credential(self, tx, identity):
        rows = tx.execute("""SELECT kind,data FROM evolution.records WHERE tenant_id=:tenant AND
            ((kind='tenants' AND key=:tenant) OR (kind='principals' AND key=:principal)
             OR (kind='workloads' AND key=:workload) OR (kind='credentials' AND key=:credential))
            FOR SHARE""", {"tenant": tx.tenant_id, "principal": identity.principal_id,
                           "workload": identity.workload_id, "credential": identity.credential["credential_id"]})
        records = {row.kind: row.data for row in rows}
        if set(records) != {"tenants", "principals", "workloads", "credentials"} or any(records[kind]["status"] != "active" for kind in ("tenants", "principals", "workloads")):
            raise EvolutionError("authentication_failed")
        credential = records["credentials"]
        if credential["principal_id"] != identity.principal_id or credential["workload_id"] != identity.workload_id or "workload" not in records["principals"]["roles"]:
            raise EvolutionError("authentication_failed")
        if credential["revoked_at"] is not None or parse_time(credential["expires_at"]) <= self.clock() or credential["previous_valid_until"] is not None and parse_time(credential["previous_valid_until"]) <= self.clock():
            raise EvolutionError("authentication_failed")
        return records["workloads"]

    def _policy(self, tx, identity, request, *, context=None, workload=None, check_capabilities=True, baselines=None):
        slot = self.slots[identity.tenant_id]
        self._runtime_active(tx, slot, identity.workload_id)
        workload = workload or tx.get("workloads", identity.workload_id, required=True)
        if request.stage not in workload["allowed_stages"]:
            raise EvolutionError("unsupported_surface")
        if context is None:
            snapshot, binding, cached = slot.store.current(identity.workload_id)
            pointer = tx.get("binding_pointers", identity.workload_id, required=True)
            if pointer["binding_revision"] != binding.revision:
                raise EvolutionError("snapshot_stale")
            policy = policy_model(self.governance.eligible(tx, cached.reference(), baselines=baselines))
        else:
            snapshot = slot.store.lease()
            policy = policy_model(self.governance.eligible(tx, context.policy, baselines=baselines))
            if policy.compiled_digest in snapshot.revoked_digests or any(parent.digest in snapshot.revoked_digests for parent in policy.parents):
                raise EvolutionError("policy_revoked")
            from .models import Binding
            binding = Binding.model_validate_json(canonical_json_bytes(tx.get("bindings", f"{identity.workload_id}:{context.binding_revision}", required=True)))
            if binding.policy != context.policy:
                raise EvolutionError("context_invalid")
        minimum = max([STATES.index(workload["required_state"])] + [STATES.index(rule.minimum_state) for rule in policy.rules if rule.mandatory])
        if STATES.index(binding.state) < minimum:
            raise EvolutionError("floor_conflict")
        if parse_time(binding.effective_after) > self.clock():
            raise EvolutionError("snapshot_stale")
        if check_capabilities:
            self.registry.require(policy, stage=request.stage, parts=request.parts, zone=self.config.zone)
        # A compiled provider ID alone is not a pinned network Destination.
        # ResidencyGuard also checks lists, scope and the actual registered route.
        if check_capabilities:
            self.residency.check_request(policy, request, runtime_zone=self.config.zone, mode=self.config.mode, workload=workload)
        return snapshot, binding, policy

    def _context(self, tx, identity, request, *, expected_state):
        row = tx.get("evaluation_contexts", request.context_id, required=False, for_update=True)
        if row is None or row["principal_id"] != identity.principal_id or row["state"] != expected_state:
            raise EvolutionError("context_invalid")
        context = EvaluationContext.model_validate_json(canonical_json_bytes(row["context"]))
        if context.tenant_id != identity.tenant_id or context.workload_id != identity.workload_id or context.provider_id != request.provider_id or parse_time(context.expires_at) <= self.clock():
            raise EvolutionError("context_invalid")
        return row, context

    def _admit(self, identity, request, deadline, *, started):
        self.identity.authorize(identity, {"workload"}, tenant_id=request.tenant_id, workload_id=request.workload_id)
        admission_limit = current_deadline.get()
        if admission_limit is None:
            raise EvolutionError("validation_failed")
        with ExitStack() as budget, self.repository.transaction(identity.tenant_id) as tx:
            lock_admission(tx)
            workload = self._active_credential(tx, identity)
            previous, context = (None, None) if request.stage == "input" else self._context(tx, identity, request, expected_state="open")
            # The admission lock keeps this publication stable through quota
            # reservation. Finalization opens a new transaction and rereads it.
            baselines = self.governance.current_baselines(tx)
            snapshot, binding, policy = self._policy(tx, identity, request, context=context, workload=workload, baselines=baselines)
            if not baselines:
                raise EvolutionError("policy_unresolved")
            # Select only from the exact eligible policy under the admission
            # lock. Metadata and commit still share the initial text deadline;
            # the quota lease covers the admitted detector's full lifetime.
            profile = policy_profile(policy, request.stage, binding.state, self.config)
            deadline, detector_deadline = evaluation_deadlines(
                profile, started=started, request_ms=request.deadline_ms,
                outer_deadline=deadline, audit_reserve_ms=self.config.audit_reserve_ms,
                now=time.monotonic(),
            )
            token = current_deadline.set(min(admission_limit, deadline))
            budget.callback(current_deadline.reset, token)
            ceilings = {}
            for name in ("requests_per_minute", "burst", "max_inflight", "daily_tokens", "daily_cost_microunits"):
                values = [getattr(item.quota, name) for item in baselines if getattr(item.quota, name) is not None]
                ceilings[name] = min(values) if values else None
            tenant_quota = baselines[0].quota.model_copy(update={**ceilings, "workload_id": None})
            reservation = self.quotas.reserve(tx, principal=identity.principal, workload_id=identity.workload_id, request_id=request.request_id, policy_digest=policy.compiled_digest, stage=request.stage, tenant_quota=tenant_quota, workload_quota=policy.quota, deadline_monotonic=deadline)
            if context is None:
                context = EvaluationContext(context_id=str(uuid4()), tenant_id=identity.tenant_id, workload_id=identity.workload_id, policy=policy.reference(), binding_revision=binding.revision, snapshot_generation=snapshot.generation, provider_id=request.provider_id, expires_at=stamp(self.clock() + timedelta(seconds=120)), created_at=stamp(self.clock()))
                tx.insert("evaluation_contexts", context.context_id, {"context": context.model_dump(mode="json"), "principal_id": identity.principal_id, "state": "evaluating_input", "request_id": request.request_id})
            else:
                tx.put("evaluation_contexts", context.context_id, {**previous, "state": "evaluating_output", "request_id": request.request_id}, expected_revision=previous["revision"])
        return context, binding, policy, reservation, deadline, detector_deadline

    def _finalize(self, identity, request, context, decision):
        with self.repository.transaction(identity.tenant_id) as tx:
            lock_admission(tx)
            workload = self._active_credential(tx, identity)
            # Revalidate immutable old bytes against CURRENT restrictions. A new
            # binding alone preserves a turn; revocation/floor changes do not.
            # A completed block cannot release content. Its audit still requires
            # current credentials and the exact in-flight context, but repeated
            # policy resolution must not consume its reserved audit interval.
            if decision.action != "block":
                self._policy(tx, identity, request, context=context, workload=workload, check_capabilities=False)
            row = tx.get("evaluation_contexts", context.context_id, required=True, for_update=True)
            expected = "evaluating_" + request.stage
            if row["state"] != expected or row["request_id"] != request.request_id or row["principal_id"] != identity.principal_id or parse_time(context.expires_at) <= self.clock():
                raise EvolutionError("context_invalid")
            try:
                event = self.audit.decision_metadata(decision)
                event.pop("tenant_id")
                event["actor_id"] = identity.principal_id
                event["correlation_id"] = request.correlation_id
                self.audit.append(tx, **event)
            except (EvolutionError, SQLAlchemyError):
                raise EvolutionError("audit_failed") from None
            state = "blocked" if decision.action == "block" else "open" if request.stage == "input" else "completed"
            tx.put("evaluation_contexts", context.context_id, {**row, "state": state}, expected_revision=row["revision"])

    async def evaluate(self, identity, request, *, started, deadline, admission_budget=None):
        if not self._started or self._closed:
            raise EvolutionError("evaluator_unavailable")
        admission_deadline = min(deadline, started + request.deadline_ms / 1000,
                                 started + min(500, self.config.text_budget_ms) / 1000)
        if admission_deadline <= time.monotonic():
            raise EvolutionError("evaluation_timeout")
        if admission_budget is not None:
            # Only the HTTP handler owns this timeout. Untrusted request fields
            # never select the profile; _admit may only shorten this ceiling.
            if (not isinstance(admission_budget, asyncio.Timeout)
                    or admission_budget.expired()
                    or admission_budget.when() != deadline):
                raise EvolutionError("validation_failed")
            deadline = started + max(min(500, self.config.text_budget_ms),
                                     min(3000, self.config.classifier_budget_ms)) / 1000
        reservation = None
        job = asyncio.current_task()
        self._jobs.add(job)
        try:
            context, binding, policy, reservation, deadline, detector_deadline = await self.metadata(
                identity.tenant_id,
                lambda: self._admit(identity, request, deadline, started=started),
                admission_deadline,
            )
            if time.monotonic() >= admission_deadline:
                raise EvolutionError("evaluation_timeout")
            if admission_budget is not None:
                admission_budget.reschedule(deadline)
            if time.monotonic() >= detector_deadline:
                raise EvolutionError("evaluation_timeout")
            findings, reasons, revisions = [], set(), set()
            action, would_be, operational_block = "allow", "allow", False
            if binding.state == "disabled":
                reasons.add("disabled")
            else:
                content_lists = tuple(item for item in policy.resolved_lists
                                      if item.list_id in policy.residency.content_lists
                                      and (item.allow_enabled or item.deny))
                if content_lists:
                    revisions.add("lexical-v1")
                    try:
                        found = await self.registry.evaluate_content_lists(
                            policy, request.parts, detector_deadline,
                            stage=request.stage, tenant_id=identity.tenant_id,
                        )
                        findings.extend(found)
                        reasons.update(item.reason_code for item in found)
                        if found:
                            would_be = "block"
                    except EvolutionError as exc:
                        if exc.code not in {"evaluation_timeout", "evaluator_unavailable", "inspection_incomplete", "queue_full"}:
                            raise
                        # An approved policy-wide constraint must never disappear
                        # because no explicit term rule was authored.
                        reasons.add(exc.code)
                        operational_block = True
                for rule in policy.rules:
                    if request.stage not in rule.stages:
                        continue
                    revisions.add(rule.evaluator_revision)
                    try:
                        found = await self.registry.evaluate(rule, request.parts, detector_deadline,
                                                             tenant_id=identity.tenant_id, policy=policy,
                                                             stage=request.stage)
                        findings.extend(found)
                        reasons.update(item.reason_code for item in found)
                        if found and ACTIONS.index(rule.action) > ACTIONS.index(would_be):
                            would_be = rule.action
                    except EvolutionError as exc:
                        if exc.code not in {"evaluation_timeout", "evaluator_unavailable", "inspection_incomplete", "queue_full"}:
                            raise
                        reasons.add(exc.code)
                        if rule.mandatory or policy.failure_behavior != "observe":
                            operational_block = True
                        # No later detector may use time reserved for audit.
                        if time.monotonic() >= detector_deadline:
                            if any(other.mandatory and request.stage in other.stages for other in policy.rules):
                                operational_block = True
                            break
                if operational_block:
                    action = would_be = "block"
                elif would_be != "allow":
                    action = {"observe": "allow", "warn": "warn", "redact": "redact", "block": "block"}[binding.state]
            findings = tuple(sorted(set(findings), key=lambda item: (item.part_id, item.json_pointer or "", item.start if item.start is not None else -1, item.end if item.end is not None else -1, item.rule_id, item.category, item.reason_code)))
            transformed = None
            if action == "redact":
                try:
                    transformed = await self.registry.transform(
                        policy, request.parts, findings, detector_deadline,
                        stage=request.stage, tenant_id=identity.tenant_id,
                    )
                except EvolutionError:
                    action = "block"
                    reasons.add("unsafe_transformation")
            decision = Decision(decision_id=str(uuid4()), request_id=request.request_id, context_id=context.context_id, tenant_id=identity.tenant_id, workload_id=identity.workload_id, stage=request.stage, policy=policy.reference(), binding_revision=context.binding_revision, snapshot_generation=context.snapshot_generation, action=action, would_be_action=would_be, reason_codes=tuple(sorted(reasons)), findings=findings, transformed_parts=transformed, evaluation_ms=(time.monotonic() - started) * 1000, audit_status="persisted", evaluator_revisions=tuple(sorted(revisions)))
            await self.metadata(identity.tenant_id, lambda: self._finalize(identity, request, context, decision), deadline)
            return decision.model_copy(update={"evaluation_ms": (time.monotonic() - started) * 1000})
        finally:
            self._jobs.discard(job)
            if reservation is not None:
                async def cleanup():
                    try:
                        await self.metadata(identity.tenant_id, lambda: self.quotas.release(reservation), time.monotonic() + 0.5)
                    except (EvolutionError, SQLAlchemyError):
                        # The durable expiring lease remains the recovery path.
                        pass
                task = asyncio.create_task(cleanup())
                self._cleanup.add(task)
                task.add_done_callback(self._cleanup.discard)

    async def _poll(self, tenant, slot):
        # Independent startup phases keep replicas and tenant slots from issuing
        # every first poll together. Jitter never extends the signed lease.
        await asyncio.sleep(uniform(0, 1))
        while not self._closed:
            started = time.monotonic()
            try:
                await self.metadata(tenant, slot.client.sync_once, started + 3)
            except (EvolutionError, httpx.HTTPError, OSError, ValueError):
                # The cached lease keeps its original expiry. An outage must
                # neither extend it nor permanently stop future sync attempts.
                pass
            delay = uniform(0.9, 1.1) - (time.monotonic() - started)
            # A slow/outage attempt must not cause a burst of catch-up polls.
            await asyncio.sleep(delay if delay > 0 else uniform(0.05, 0.1))

    async def start(self):
        await self.registry.start()
        self._started = True
        for tenant, slot in self.slots.items():
            if slot.client is not None:
                task = asyncio.create_task(self._poll(tenant, slot))
                self._polls.add(task)
                task.add_done_callback(self._polls.discard)

    async def close(self):
        self._closed, self._started = True, False
        tasks = tuple(self._jobs | self._polls)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.close()
        if self._cleanup:
            await asyncio.gather(*tuple(self._cleanup), return_exceptions=True)
        for slot in self.slots.values():
            if slot.client is not None:
                slot.client.close()
        for executor in self._executors.values():
            executor.shutdown(wait=False, cancel_futures=True)
        self.audit.close()

    def capabilities_for(self, identity):
        self.identity.authorize(identity, {"workload"})
        with self.repository.transaction(identity.tenant_id) as tx:
            lock_admission(tx)
            self._active_credential(tx, identity)
            slot = self.slots[identity.tenant_id]
            self._runtime_active(tx, slot, identity.workload_id)
            _, _, policy = slot.store.current(identity.workload_id)
            self.governance.eligible(tx, policy.reference())
            workload = tx.get("workloads", identity.workload_id, required=True)
        allowed = set(workload["allowed_stages"])
        needed = {(stage, rule.evaluator_revision) for rule in policy.rules
                  for stage in rule.stages if stage in allowed}
        if any(item.list_id in policy.residency.content_lists and (item.allow_enabled or item.deny)
               for item in policy.resolved_lists):
            needed.update((stage, "lexical-v1") for stage in allowed & {"input", "output"})
        return tuple(value for value in self.registry.capabilities(identity.tenant_id)
                     if (value.stage, value.evaluator_revision) in needed)

    def ready_for(self, identity):
        self.identity.authorize(identity, {"runtime", "workload"})
        if not self.registry.health(identity.tenant_id)["ready"]:
            return False, "evaluator_unavailable"
        try:
            with self.repository.transaction(identity.tenant_id) as tx:
                lock_admission(tx)
                slot = self.slots[identity.tenant_id]
                self._runtime_active(tx, slot, identity.workload_id)
                if identity.workload_id is not None:
                    self._active_credential(tx, identity)
                snapshot = slot.store.lease()
                if not self.governance.current_baselines(tx):
                    raise EvolutionError("policy_unresolved")
                selected = [binding for binding in snapshot.bindings if identity.workload_id is None or binding.workload_id == identity.workload_id]
                if not selected:
                    raise EvolutionError("policy_unresolved")
                for binding in selected:
                    desired = tx.get("binding_pointers", binding.workload_id, required=True)
                    if desired["binding_revision"] != binding.revision:
                        raise EvolutionError("snapshot_stale")
                    self.registry.require(policy_model(self.governance.eligible(tx, binding.policy)))
            return True, None
        except EvolutionError as exc:
            return False, exc.code


def load_runtime_slots(path):
    """Private, explicit mounted deployment mapping; no request-derived paths."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    path = Path(path)
    if path.stat().st_size > 65536:
        raise EvolutionError("validation_failed")
    values = strict_json_loads(path.read_bytes())
    if not isinstance(values, list) or not 1 <= len(values) <= 4:
        raise EvolutionError("validation_failed")
    slots = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {"tenant_id", "runtime_id", "url", "ca_file", "credential_file", "state_file", "trust_key_files", "trust_key_expiries"}:
            raise EvolutionError("validation_failed")
        if value["tenant_id"] in slots or not isinstance(value["trust_key_files"], dict):
            raise EvolutionError("validation_failed")
        paths = [value[name] for name in ("ca_file", "credential_file", "state_file")] + list(value["trust_key_files"].values())
        if any(not isinstance(item, str) or not Path(item).is_absolute() for item in paths):
            raise EvolutionError("validation_failed")
        keys = {name: Ed25519PublicKey.from_public_bytes(Path(key_path).read_bytes()) for name, key_path in value["trust_key_files"].items()}
        store = RuntimeSnapshotStore(value["state_file"], value["tenant_id"], value["runtime_id"], keys, trust_key_expiries={name: parse_time(expiry) for name, expiry in value["trust_key_expiries"].items()})
        slots[value["tenant_id"]] = RuntimeSlot(store, RuntimeApplyClient(value["url"], value["ca_file"], value["credential_file"], store))
    return slots


def create_app(repository=None, *, service=None, config=None, runtime_slots=None):
    owned_repository = repository is None and service is None
    if service is None:
        config = config or load_config("service.json")
        if repository is None:
            from .storage import TenantRepository
            repository = TenantRepository(Path(config.database_url_file).read_text().strip())
        service = EvaluationRuntime(repository, config=config, runtime_slots=runtime_slots or load_runtime_slots("runtime-slots.json"))

    @asynccontextmanager
    async def lifespan(app):
        try:
            await service.start()
            yield
        finally:
            await service.close()
            if owned_repository:
                service.repository.close()

    app = FastAPI(title="Guardrails evolution evaluation", version="2.0.0", lifespan=lifespan)
    app.state.service = service

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    async def evaluate_connected(request, identity, value, started, deadline, budget):
        # ASGI servers do not cancel ordinary handlers when a peer disappears.
        # After the complete body has been consumed, this is its sole reader.
        async def disconnected():
            while True:
                event = await request.receive()
                if event["type"] == "http.disconnect":
                    return

        evaluation = asyncio.create_task(service.evaluate(
            identity, value, started=started, deadline=deadline, admission_budget=budget))
        connection = asyncio.create_task(disconnected())
        try:
            completed, _ = await asyncio.wait((evaluation, connection), return_when=asyncio.FIRST_COMPLETED)
            if connection in completed:
                await connection
                raise EvolutionError("evaluator_unavailable")
            return await evaluation
        finally:
            for task in (evaluation, connection):
                if not task.done():
                    task.cancel()
            await asyncio.gather(evaluation, connection, return_exceptions=True)

    async def handle(request: Request):
        started = time.monotonic()
        deadline = started + min(500, service.config.text_budget_ms) / 1000
        correlation = str(uuid4())
        headers = {"Cache-Control": "no-store", "X-Request-ID": correlation}
        try:
            async with asyncio.timeout_at(deadline) as budget:
                if request.url.scheme != "https":
                    raise EvolutionError("forbidden")
                if request.query_params:
                    raise EvolutionError("validation_failed")
                supplied = request.headers.get("x-request-id")
                if supplied is not None:
                    if str(UUID(supplied)) != supplied:
                        raise EvolutionError("validation_failed")
                    correlation = supplied
                    headers["X-Request-ID"] = correlation
                authorization = request.headers.get("authorization", "")
                if not authorization.startswith("Bearer "):
                    raise EvolutionError("authentication_failed")
                identity = await service.authenticate(authorization[7:], deadline)
                if request.url.path == "/health/ready":
                    ready, dependency = await service.metadata(identity.tenant_id, lambda: service.ready_for(identity), deadline)
                    return JSONResponse({"ready": ready, "dependency_codes": [] if ready else [dependency]}, status_code=200 if ready else 503, headers=headers)
                service.identity.authorize(identity, {"workload"})
                if request.method == "GET":
                    values = await service.metadata(identity.tenant_id, lambda: service.capabilities_for(identity), deadline)
                    return JSONResponse([value.model_dump(mode="json") for value in values], headers=headers)
                if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                    raise EvolutionError("invalid_json")
                if "content-length" in request.headers:
                    length = request.headers["content-length"]
                    if not length.isdecimal() or int(length) > MAX_BODY_BYTES:
                        raise EvolutionError("payload_too_large")
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY_BYTES:
                        raise EvolutionError("payload_too_large")
                try:
                    payload = strict_json_loads(bytes(body), max_bytes=MAX_BODY_BYTES)
                except ValueError:
                    raise EvolutionError("invalid_json") from None
                if isinstance(payload, dict) and isinstance(payload.get("parts"), list):
                    parts = payload["parts"]
                    if len(parts) > 64 or sum(len(part["text"].encode("utf-8")) for part in parts if isinstance(part, dict) and part.get("kind") == "text" and isinstance(part.get("text"), str)) > 65536:
                        raise EvolutionError("payload_too_large")
                value = EvaluationRequest.model_validate_json(canonical_json_bytes(payload))
                body.clear()
                decision = await evaluate_connected(request, identity, value, started, deadline, budget)
                return JSONResponse(decision.model_dump(mode="json"), headers=headers)
        except TimeoutError:
            error = EvolutionError("evaluation_timeout")
        except EvolutionError as exc:
            error = exc
        except (ValidationError, ValueError, TypeError, KeyError):
            error = EvolutionError("validation_failed")
        except SQLAlchemyError:
            error = EvolutionError("policy_unresolved")
        if error.status == 429:
            headers["Retry-After"] = str(max(1, min(3600, getattr(error, "retry_after_s", 1))))
        return JSONResponse(error.envelope(correlation), status_code=error.status, headers=headers)

    app.add_api_route("/v2/evaluations", handle, methods=["POST"], response_model=Decision, openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EvaluationRequest"}}}}})
    app.add_api_route("/v2/capabilities", handle, methods=["GET"], response_model=list[Capability])
    app.add_api_route("/health/ready", handle, methods=["GET"])

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            request_schema = EvaluationRequest.model_json_schema(ref_template="#/components/schemas/{model}")
            schema.setdefault("components", {}).setdefault("schemas", {}).update(request_schema.pop("$defs", {}))
            schema["components"]["schemas"]["EvaluationRequest"] = request_schema
            error_schema = Error.model_json_schema(ref_template="#/components/schemas/{model}")
            schema["components"]["schemas"].update(error_schema.pop("$defs", {}))
            schema["components"]["schemas"]["Error"] = error_schema
            schema["components"]["schemas"]["ErrorEnvelope"] = {"type": "object", "additionalProperties": False, "required": ["error"], "properties": {"error": {"$ref": "#/components/schemas/Error"}}}
            schema["components"]["securitySchemes"] = {"BearerAuth": {"type": "http", "scheme": "bearer"}}
            for path, operations in schema["paths"].items():
                if path != "/health/live":
                    for operation in operations.values():
                        operation["security"] = [{"BearerAuth": []}]
                        for status in (400, 401, 403, 404, 409, 413, 422, 429, 503, 504):
                            operation["responses"][str(status)] = {"description": "Safe bounded failure", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}}
            readiness = {"type": "object", "additionalProperties": False, "required": ["ready", "dependency_codes"], "properties": {"ready": {"type": "boolean"}, "dependency_codes": {"type": "array", "maxItems": 1, "items": error_schema["properties"]["code"]}}}
            schema["components"]["schemas"]["Readiness"] = readiness
            ready_responses = schema["paths"]["/health/ready"]["get"]["responses"]
            ready_responses["200"] = {"description": "Scoped runtime is ready", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Readiness"}}}}
            ready_responses["503"]["content"]["application/json"]["schema"] = {"oneOf": [{"$ref": "#/components/schemas/Readiness"}, {"$ref": "#/components/schemas/ErrorEnvelope"}]}
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app
