"""Closed, pinned evaluator registry with isolated deterministic worker pools.

Policies select declared revisions only; code, artifacts, limits and reserved
tenant pools are deployment owned. The minimal PII profile remains explicit.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import sys
import time
from pathlib import Path
from types import MappingProxyType

from .errors import EvolutionError
from .models import (
    Capability,
    ContentPart,
    Finding,
    PolicyVersion,
    Rule,
    canonical_json_bytes,
    strict_json_loads,
)

PLUGIN_API_MAJOR = 1
PII_REVISION = "pii-v2"
PINNED_ARTIFACTS = {
    "evaluators/pii.py": "ae5ac4a0aac37d0b2d4833e00c0bb5d661d46b4e2b82fd97fe5bf8810a11c297",
    "local_contract.py": "69fc3df12bfb63376ebbaf34e6b1f38fad41b542aff93723eaec5c2147f470f5",
}
MAX_WIRE_BYTES = 512 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_FINDINGS = 4096
SUPPORTED_ENTITIES = frozenset({"email", "international_phone"})


def _verify_artifacts():
    root = Path(__file__).resolve().parents[1]
    for name, expected in PINNED_ARTIFACTS.items():
        try:
            valid = hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
        except OSError:
            valid = False
        if not valid:
            raise EvolutionError("evaluator_unavailable")


def _validate_rule(rule):
    if not isinstance(rule, Rule) or rule.kind != "pii" or rule.evaluator_revision != PII_REVISION:
        raise EvolutionError("capability_missing")
    if not set(rule.config.entities) <= SUPPORTED_ENTITIES or not set(rule.stages) <= {"input", "output"}:
        raise EvolutionError("capability_missing")


def _validate(rule, parts):
    _validate_rule(rule)
    if not isinstance(parts, tuple) or not 1 <= len(parts) <= 64 or any(not isinstance(part, ContentPart) or part.kind != "text" or part.tool is not None for part in parts):
        raise EvolutionError("unsupported_surface")
    if len({part.part_id for part in parts}) != len(parts):
        raise EvolutionError("validation_failed")
    if sum(len(part.text.encode("utf-8")) for part in parts) > 65536:
        raise EvolutionError("payload_too_large")


class PinnedPiiEvaluator:
    """Explicitly started, bounded process pool implementing EvaluatorPort."""

    _max_wire_bytes = MAX_WIRE_BYTES
    _max_result_bytes = MAX_RESULT_BYTES

    @staticmethod
    def _verify():
        _verify_artifacts()

    @staticmethod
    def _worker_command():
        return (sys.executable, "-m", "guardrails.evolution.plugins", "--worker")

    def __init__(self, *, workers=2, max_queued_jobs=8, timeout_ms=400, zone="local-shared", api_major=1):
        if api_major != PLUGIN_API_MAJOR or type(api_major) is not int:
            raise EvolutionError("capability_missing")
        if any(type(value) is not int for value in (workers, max_queued_jobs, timeout_ms)) or not 1 <= workers <= 4 or not 0 <= max_queued_jobs <= 32 or not 1 <= timeout_ms <= 500:
            raise EvolutionError("validation_failed")
        # Validate deployment metadata without starting a process.
        Capability(capability_id="pii-email-phone", stage="input", part_kinds=("text",), actions=("allow", "warn", "redact", "block"), evaluator_revision=PII_REVISION, max_input_bytes=65536, timeout_ms=timeout_ms, zone=zone, status="unavailable")
        self.worker_count, self.max_queued_jobs, self.timeout_ms, self.zone = workers, max_queued_jobs, timeout_ms, zone
        self._available = asyncio.Queue(maxsize=workers)
        self._workers, self._tasks, self._cleanup = set(), set(), set()
        self._prepared, self._busy = set(), set()
        self._admitted = 0
        self._closed = False
        self._ready = False
        self.replacements = 0

    @property
    def worker_pids(self):
        return tuple(sorted(worker.pid for worker in tuple(self._workers) if worker.returncode is None))

    def capabilities(self):
        status = "enabled" if self.health()["ready"] else "unavailable"
        return tuple(Capability(capability_id="pii-email-phone", stage=stage, part_kinds=("text",), actions=("allow", "warn", "redact", "block"), evaluator_revision=PII_REVISION, max_input_bytes=65536, timeout_ms=self.timeout_ms, zone=self.zone, status=status) for stage in ("input", "output"))

    def health(self):
        prepared = {process for process in tuple(self._prepared) if process.returncode is None}
        return {"ready": bool(self._ready and not self._closed and prepared), "workers": len(self.worker_pids), "admitted": self._admitted, "available_workers": len(prepared - self._busy.copy()), "replacements": self.replacements}

    async def _spawn(self):
        environment = {"PYTHONDONTWRITEBYTECODE": "1"}
        if "PYTHONPATH" in os.environ:
            environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
        process = await asyncio.create_subprocess_exec(*self._worker_command(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=environment, limit=self._max_result_bytes)
        self._workers.add(process)
        try:
            async with asyncio.timeout(10):
                if await process.stdout.readline() != b'{"api_major":1,"ready":true}\n':
                    raise EvolutionError("evaluator_unavailable")
        except BaseException:
            await self._reap(process)
            raise
        self._prepared.add(process)
        return process

    async def start(self):
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        if self._ready:
            return
        self._verify()
        try:
            for _ in range(self.worker_count):
                self._available.put_nowait(await self._spawn())
        except BaseException:  # noqa: BLE001 - all startup failures reclaim partial pools
            await self.close()
            raise EvolutionError("evaluator_unavailable") from None
        self._ready = True

    async def _reap(self, process):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()
        self._workers.discard(process)
        self._prepared.discard(process)
        self._busy.discard(process)

    async def _replace(self, process):
        await self._reap(process)
        if not self._closed:
            try:
                self._available.put_nowait(await self._spawn())
                self.replacements += 1
            except Exception:  # noqa: BLE001 - replacement failure cannot leave readiness true
                self._ready = False

    async def evaluate(self, rule: Rule, parts: tuple[ContentPart, ...], deadline_monotonic: float) -> tuple[Finding, ...]:
        _validate(rule, parts)
        payload = {"rule": rule.model_dump(mode="json"), "parts": [part.model_dump(mode="json") for part in parts]}
        return await self._submit(payload, deadline_monotonic, lambda result: self._decode(result, rule, parts))

    @staticmethod
    def _decode(result, rule, parts):
        if set(result) == {"error"} and result["error"] == "inspection_incomplete":
            raise EvolutionError("inspection_incomplete")
        if set(result) != {"findings"} or not isinstance(result["findings"], list) or len(result["findings"]) > MAX_FINDINGS:
            raise EvolutionError("evaluator_unavailable")
        findings = tuple(Finding.model_validate(item) for item in result["findings"])
        by_id = {part.part_id: part for part in parts}
        for finding in findings:
            if finding.rule_id != rule.rule_id or finding.evaluator_revision != PII_REVISION or finding.category not in rule.config.entities or finding.reason_code != "pii_detected" or finding.part_id not in by_id or finding.start is None or finding.end > len(by_id[finding.part_id].text) or finding.json_pointer is not None:
                raise EvolutionError("inspection_incomplete")
        return tuple(sorted(findings, key=lambda finding: (finding.part_id, finding.rule_id, finding.start, finding.end, finding.category)))

    async def _submit(self, payload, deadline_monotonic, decode):
        """Shared bounded transport; concrete installed adapters own both codecs."""
        loop = asyncio.get_running_loop()
        if type(deadline_monotonic) not in (float, int) or not math.isfinite(deadline_monotonic):
            raise EvolutionError("validation_failed")
        cutoff = min(deadline_monotonic, loop.time() + self.timeout_ms / 1000)
        if cutoff <= loop.time():
            raise EvolutionError("evaluation_timeout")
        if not self._ready or self._closed:
            raise EvolutionError("evaluator_unavailable")
        if self._admitted >= self.worker_count + self.max_queued_jobs:
            raise EvolutionError("queue_full")
        self._admitted += 1
        task, process = asyncio.current_task(), None
        self._tasks.add(task)
        try:
            async with asyncio.timeout_at(cutoff):
                process = await self._available.get()
                self._busy.add(process)
                raw_payload = canonical_json_bytes(payload)
                if len(raw_payload) > self._max_wire_bytes:
                    raise EvolutionError("payload_too_large")
                process.stdin.write(raw_payload + b"\n")
                await process.stdin.drain()
                raw = await process.stdout.readline()
                if not raw or len(raw) > self._max_result_bytes:
                    raise EvolutionError("evaluator_unavailable")
                result = strict_json_loads(raw)
                value = decode(result)
                self._available.put_nowait(process)
                self._busy.discard(process)
                process = None
                return value
        except TimeoutError:
            raise EvolutionError("evaluation_timeout") from None
        except (EvolutionError, asyncio.CancelledError):
            raise
        except Exception:  # noqa: BLE001 - worker boundary never exposes payload/driver errors
            raise EvolutionError("evaluator_unavailable") from None
        finally:
            self._admitted -= 1
            self._tasks.discard(task)
            if process is not None:
                self._busy.discard(process)
                self._prepared.discard(process)
                # Reclaim the actual process when the request loses its budget.
                # Replacement is asynchronous, so startup cannot extend the SLA.
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                cleanup = asyncio.create_task(self._replace(process))
                self._cleanup.add(cleanup)
                cleanup.add_done_callback(self._cleanup.discard)

    async def close(self):
        self._closed, self._ready = True, False
        pending = tuple(task for task in self._tasks if task is not asyncio.current_task())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._cleanup:
            await asyncio.gather(*tuple(self._cleanup), return_exceptions=True)
        for process in tuple(self._workers):
            await self._reap(process)
        while not self._available.empty():
            self._available.get_nowait()


class PiiOnlyRegistry:
    """Built-ins only, with fixed reserved workers for explicitly scoped tenants.

    There is no registration/import entry point accepting submitted code. Empty
    tenant_ids creates an unscoped deployment pool, useful for a single service
    or activation validator. Shared runtime assembly supplies its tenant slots;
    a request for another tenant can never borrow a scoped pool.
    """

    registered_revisions = MappingProxyType({"pii": frozenset({PII_REVISION})})

    @staticmethod
    def _make_pool(**options):
        return PinnedPiiEvaluator(**options)

    def __init__(self, *, tenant_ids=(), workers=4, max_queued_jobs=32,
                 timeout_ms=400, zone="local-shared", api_major=1):
        if not isinstance(tenant_ids, tuple) or len(set(tenant_ids)) != len(tenant_ids) or any(not isinstance(value, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", value) for value in tenant_ids):
            raise EvolutionError("validation_failed")
        if type(workers) is not int or not 1 <= workers <= 4 or len(tenant_ids) > workers or type(max_queued_jobs) is not int or not 0 <= max_queued_jobs <= 32:
            raise EvolutionError("validation_failed")
        self.zone = zone
        names = tenant_ids or (None,)
        self._pools = {
            name: self._make_pool(
                workers=workers // len(names) + int(index < workers % len(names)),
                max_queued_jobs=max_queued_jobs // len(names) + int(index < max_queued_jobs % len(names)),
                timeout_ms=timeout_ms, zone=zone, api_major=api_major,
            ) for index, name in enumerate(names)
        }
        self._monitor_task = None

    def _pool(self, tenant_id):
        if None in self._pools:
            return self._pools[None]
        if tenant_id not in self._pools:
            raise EvolutionError("resource_not_found")
        return self._pools[tenant_id]

    async def start(self):
        try:
            for pool in self._pools.values():
                await pool.start()
            if self._monitor_task is None:
                self._monitor_task = asyncio.create_task(self._monitor())
        except BaseException:
            await self.close()
            raise

    async def _monitor(self):
        # Repair idle workers too: capability rejection must not prevent recovery
        # by making the failed process reachable only through another evaluation.
        while True:
            await asyncio.sleep(0.05)
            for pool in self._pools.values():
                idle = []
                while not pool._available.empty():
                    idle.append(pool._available.get_nowait())
                for process in idle:
                    if process.returncode is None:
                        pool._available.put_nowait(process)
                    else:
                        pool._prepared.discard(process)
                        cleanup = asyncio.create_task(pool._replace(process))
                        pool._cleanup.add(cleanup)
                        cleanup.add_done_callback(pool._cleanup.discard)

    def capabilities(self, tenant_id=None):
        if tenant_id is not None or None in self._pools:
            return self._pool(tenant_id).capabilities()
        # Unscoped health is enabled only when every reserved tenant can run it.
        available = all(pool.health()["ready"] for pool in self._pools.values())
        sample = next(iter(self._pools.values())).capabilities()
        return tuple(value.model_copy(update={"status": "enabled" if available else "unavailable"}) for value in sample)

    def declared_capabilities(self):
        """Installed control-plane catalog; runtime endpoints report live health."""
        try:
            _verify_artifacts()
            status = "enabled"
        except EvolutionError:
            status = "unavailable"
        sample = next(iter(self._pools.values())).capabilities()
        return tuple(value.model_copy(update={"status": status}) for value in sample)

    def health(self, tenant_id=None):
        selected = (self._pool(tenant_id),) if tenant_id is not None or None in self._pools else tuple(self._pools.values())
        health = tuple(pool.health() for pool in selected)
        return {"ready": all(item["ready"] for item in health), "workers": sum(item["workers"] for item in health), "admitted": sum(item["admitted"] for item in health)}

    def validate_declared(self, policy: PolicyVersion, *, stage=None, parts=None, zone=None):
        """Check installed built-in support without claiming remote worker health."""
        if not isinstance(policy, PolicyVersion):
            raise EvolutionError("validation_failed")
        if zone is not None and zone != self.zone or self.zone not in policy.residency.allowed_zones:
            raise EvolutionError("residency_denied")
        pool = self._pool(policy.tenant_id)
        _verify_artifacts()
        # These restrictions are executable policy, not inert configuration.
        # Term/topic evaluators arrive in their owning phase; accepting their
        # active lists now would falsely claim to enforce approved controls.
        if any(item.kind == "content" and (item.allow_enabled or item.deny) for item in policy.resolved_lists):
            raise EvolutionError("capability_missing")
        if stage is not None and stage not in {"input", "output"}:
            raise EvolutionError("unsupported_surface")
        if parts is not None:
            # Validate text shape even when a policy intentionally has no rules.
            if not isinstance(parts, tuple) or not 1 <= len(parts) <= 64 or any(not isinstance(part, ContentPart) or part.kind != "text" or part.tool is not None for part in parts):
                raise EvolutionError("unsupported_surface")
            if len({part.part_id for part in parts}) != len(parts):
                raise EvolutionError("validation_failed")
            if sum(len(part.text.encode("utf-8")) for part in parts) > 65536:
                raise EvolutionError("payload_too_large")
        capabilities = {value.stage: value for value in pool.capabilities()}
        for rule in policy.rules:
            _validate_rule(rule)
            for required_stage in rule.stages:
                capability = capabilities.get(required_stage)
                if capability is None or rule.action not in capability.actions:
                    raise EvolutionError("capability_missing")

    def require(self, policy: PolicyVersion, *, stage=None, parts=None, zone=None):
        self.validate_declared(policy, stage=stage, parts=parts, zone=zone)
        capabilities = {value.stage: value for value in self._pool(policy.tenant_id).capabilities()}
        if any(capabilities[required_stage].status != "enabled" for rule in policy.rules for required_stage in rule.stages):
            raise EvolutionError("evaluator_unavailable")

    def activation_check(self, compiled, workload):
        policy = PolicyVersion.model_validate_json(canonical_json_bytes(compiled))
        if workload.get("tenant_id") != policy.tenant_id or any(not set(rule.stages) <= set(workload["allowed_stages"]) for rule in policy.rules):
            raise EvolutionError("capability_missing")
        self.validate_declared(policy)

    async def evaluate(self, rule, parts, deadline_monotonic, *, tenant_id=None,
                       policy=None, stage=None):
        if policy is not None and (
                not isinstance(policy, PolicyVersion)
                or policy.tenant_id != tenant_id or rule not in policy.rules
                or stage not in rule.stages):
            raise EvolutionError("policy_unresolved")
        return await self._pool(tenant_id).evaluate(rule, parts, deadline_monotonic)

    async def evaluate_content_lists(self, policy, parts, deadline_monotonic, *,
                                     stage, tenant_id):
        # The explicit PII-only installed catalog has no lexical evaluator.
        raise EvolutionError("capability_missing")

    async def transform(self, policy, parts, findings, deadline_monotonic, *,
                        stage, tenant_id):
        from .transformations import transform_text

        if policy.tenant_id != tenant_id or stage not in {"input", "output"}:
            raise EvolutionError("policy_unresolved")
        if time.monotonic() >= deadline_monotonic:
            raise EvolutionError("evaluation_timeout")
        return transform_text(parts, findings)

    async def close(self):
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        await asyncio.gather(*(pool.close() for pool in self._pools.values()))


class EvaluatorRegistry(PiiOnlyRegistry):
    """Installed engines with fixed pools and operator-owned remote endpoints.

    Every job carries an immutable compiled policy and stage. There is no
    request-created pool, detector import path or policy cache.
    """

    from .rules import REVISIONS as _revisions

    registered_revisions = MappingProxyType({
        **{kind: frozenset({revision}) for kind, revision in _revisions.items()},
        "injection": frozenset({"deberta-v2-90c9989b1a34"}),
        "bedrock": frozenset({"bedrock-v1"}),
    })

    def __init__(self, *, classifier_endpoints=None, bedrock_endpoints=None, **options):
        from .classifier import ClassifierEndpoint
        from .managed_endpoint import BedrockEndpoint

        super().__init__(**options)
        endpoints = {} if classifier_endpoints is None else classifier_endpoints
        if type(endpoints) is not dict or len(endpoints) > 4:
            raise EvolutionError("validation_failed")
        for tenant, endpoint in endpoints.items():
            if (type(tenant) is not str or not re.fullmatch(r"[a-z0-9-]{1,64}", tenant)
                    or not isinstance(endpoint, ClassifierEndpoint)
                    or endpoint.config.runtime_zone != self.zone
                    or endpoint.config.route().provider.tenant_id not in {tenant, "_platform"}
                    or endpoint.config.route().destination.tenant_id not in {tenant, "_platform"}
                    or None not in self._pools and tenant not in self._pools):
                raise EvolutionError("validation_failed")
        self._classifier_endpoints = MappingProxyType(dict(endpoints))
        managed = {} if bedrock_endpoints is None else bedrock_endpoints
        if type(managed) is not dict or len(managed) > 4:
            raise EvolutionError("validation_failed")
        for tenant, endpoint in managed.items():
            if (type(tenant) is not str or not re.fullmatch(r"[a-z0-9-]{1,64}", tenant)
                    or not isinstance(endpoint, BedrockEndpoint)
                    or endpoint.runtime_zone != self.zone
                    or any(resource.tenant_id not in {tenant, "_platform"}
                           for resource in endpoint.resources())
                    or None not in self._pools and tenant not in self._pools):
                raise EvolutionError("validation_failed")
        self._bedrock_endpoints = MappingProxyType(dict(managed))
        self._bedrock_jobs = {}
        self._classifier_monitor_task = None
        self._classifier_jobs = set()
        self._closed = False

    def _endpoints(self):
        return tuple(dict.fromkeys(self._classifier_endpoints.values()))

    async def _refresh_classifiers(self):
        async def refresh(endpoint):
            try:
                async with asyncio.timeout(0.4):
                    await endpoint.refresh_readiness(time.monotonic() + 0.4)
            except Exception:  # noqa: BLE001 - endpoint retains finite unavailable health
                return False
            return True
        await asyncio.gather(*(refresh(endpoint) for endpoint in self._endpoints()))

    async def _monitor_classifiers(self):
        while True:
            await asyncio.sleep(0.05)
            await self._refresh_classifiers()

    async def start(self):
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        try:
            await super().start()
            if self._classifier_endpoints and self._classifier_monitor_task is None:
                await self._refresh_classifiers()
                self._classifier_monitor_task = asyncio.create_task(self._monitor_classifiers())
        except BaseException:
            await self.close()
            raise

    def _classifier_catalog(self, tenant_id=None, *, declared=False):
        endpoints = (self._classifier_endpoints[tenant_id],) if tenant_id in self._classifier_endpoints else ()
        if tenant_id is None:
            endpoints = self._endpoints()
        by_stage = {}
        for endpoint in endpoints:
            for value in endpoint.capabilities():
                status = "enabled" if declared else value.status
                previous = by_stage.get(value.stage)
                if previous is not None and previous.status != "enabled":
                    status = "unavailable"
                by_stage[value.stage] = value.model_copy(update={"status": status})
        return tuple(by_stage.values())

    def capabilities(self, tenant_id=None):
        return (super().capabilities(tenant_id) + self._classifier_catalog(tenant_id)
                + self._bedrock_catalog(tenant_id))

    def _bedrock_catalog(self, tenant_id=None):
        endpoints = (self._bedrock_endpoints[tenant_id],) if tenant_id in self._bedrock_endpoints else ()
        if tenant_id is None:
            endpoints = tuple(self._bedrock_endpoints.values())
        by_stage = {}
        for endpoint in endpoints:
            for value in endpoint.capabilities():
                previous = by_stage.get(value.stage)
                if self._closed or previous is not None and previous.status != "enabled":
                    value = value.model_copy(update={"status": "unavailable"})
                by_stage[value.stage] = value
        return tuple(by_stage.values())

    def _bedrock(self, policy, stage, tenant_id):
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        if not isinstance(policy, PolicyVersion) or policy.tenant_id != tenant_id:
            raise EvolutionError("policy_unresolved")
        self._pool(tenant_id)
        endpoint = self._bedrock_endpoints.get(tenant_id)
        if endpoint is None:
            raise EvolutionError("capability_missing")
        return endpoint.bind(policy, stage)

    def _classifier(self, policy, stage, tenant_id):
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        if not isinstance(policy, PolicyVersion) or policy.tenant_id != tenant_id:
            raise EvolutionError("policy_unresolved")
        self._pool(tenant_id)
        endpoint = self._classifier_endpoints.get(tenant_id)
        if endpoint is None:
            raise EvolutionError("capability_missing")
        selected = [item for item in policy.providers
                    if item.vendor == "local_classifier" and item.kind == "evaluator"]
        if len(selected) != 1 or canonical_json_bytes(selected[0]) != endpoint.config.provider_bytes:
            raise EvolutionError("provider_denied")
        if stage not in {"input", "output"}:
            raise EvolutionError("unsupported_surface")
        return endpoint.bind(policy, stage)

    @staticmethod
    def _make_pool(**options):
        from .rules_process import RulesProcessPool

        return RulesProcessPool(**options)

    def declared_capabilities(self):
        pool = next(iter(self._pools.values()))
        try:
            pool._verify()
            status = "enabled"
        except EvolutionError:
            status = "unavailable"
        return tuple(value.model_copy(update={"status": status})
                       for value in pool.capabilities()) + self._classifier_catalog(declared=True) + self._bedrock_catalog()

    def validate_declared(self, policy: PolicyVersion, *, stage=None, parts=None, zone=None):
        from .rules import RulesBinding, _parts, validate_rule
        from .rules_process import CAPABILITY_IDS

        if not isinstance(policy, PolicyVersion):
            raise EvolutionError("validation_failed")
        if zone is not None and zone != self.zone or self.zone not in policy.residency.allowed_zones:
            raise EvolutionError("residency_denied")
        pool = self._pool(policy.tenant_id)
        pool._verify()
        if stage is not None and stage not in {"input", "output"}:
            raise EvolutionError("unsupported_surface")
        # Authenticating the complete immutable binding also validates every
        # referenced list and compiled/resource digest before CPU dispatch.
        RulesBinding(policy, stage or "input")
        if parts is not None:
            _parts(parts)
            if any(part.tool is not None for part in parts):
                raise EvolutionError("unsupported_surface")
        capabilities = {(value.capability_id, value.stage): value
                        for value in pool.capabilities()}
        classifier_caps = {}
        for required_stage in {stage for rule in policy.rules if rule.kind == "injection" for stage in rule.stages}:
            adapter = self._classifier(policy, required_stage, policy.tenant_id)
            classifier_caps.update({value.stage: value for value in adapter.capabilities()})
        for rule in policy.rules:
            if rule.kind == "bedrock":
                if rule.evaluator_revision not in self.registered_revisions["bedrock"]:
                    raise EvolutionError("capability_missing")
                for required_stage in rule.stages:
                    adapter = self._bedrock(policy, required_stage, policy.tenant_id)
                    capability = adapter.capabilities()[0]
                    if rule.action not in capability.actions:
                        raise EvolutionError("capability_missing")
                if (parts is not None and (stage is None or stage in rule.stages)
                        and any(part.kind != "text" for part in parts)):
                    raise EvolutionError("unsupported_surface")
                continue
            if rule.kind == "injection":
                if rule.evaluator_revision not in self.registered_revisions["injection"]:
                    raise EvolutionError("capability_missing")
                for required_stage in rule.stages:
                    capability = classifier_caps.get(required_stage)
                    if capability is None or rule.action not in capability.actions:
                        raise EvolutionError("capability_missing")
                continue
            validate_rule(rule, policy)
            for required_stage in rule.stages:
                capability = capabilities.get((CAPABILITY_IDS[rule.kind], required_stage))
                if capability is None or rule.action not in capability.actions:
                    raise EvolutionError("capability_missing")

    def require(self, policy: PolicyVersion, *, stage=None, parts=None, zone=None):
        self.validate_declared(policy, stage=stage, parts=parts, zone=zone)
        active_lists = any(
            item.list_id in policy.residency.content_lists
            and (item.allow_enabled or item.deny)
            for item in policy.resolved_lists
        )
        # Managed safe masks use this same pool even when the policy has no
        # deterministic rule. Binding enforcement can demand that transform.
        applicable = any(rule.kind in {*self._revisions, "bedrock"} and (stage is None or stage in rule.stages)
                         for rule in policy.rules)
        if (applicable or active_lists) and not self._pool(policy.tenant_id).health()["ready"]:
            raise EvolutionError("evaluator_unavailable")
        if (any(rule.kind == "injection" and (stage is None or stage in rule.stages) for rule in policy.rules)
                and not self._classifier_endpoints[policy.tenant_id].health_snapshot().ready):
            raise EvolutionError("evaluator_unavailable")

    def _bound(self, policy, stage, tenant_id):
        from .rules import RulesBinding

        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        if not isinstance(policy, PolicyVersion) or policy.tenant_id != tenant_id:
            raise EvolutionError("policy_unresolved")
        if stage not in {"input", "output"}:
            raise EvolutionError("unsupported_surface")
        return self._pool(tenant_id).bound(RulesBinding(policy, stage))

    async def evaluate(self, rule, parts, deadline_monotonic, *, tenant_id=None,
                       policy=None, stage=None):
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        if not isinstance(policy, PolicyVersion) or rule not in policy.rules or stage not in rule.stages:
            raise EvolutionError("policy_unresolved")
        if rule.kind == "bedrock":
            adapter = self._bedrock(policy, stage, tenant_id)
            if (len(self._bedrock_jobs) >= 16
                    or sum(value == tenant_id for value in self._bedrock_jobs.values()) >= 4):
                raise EvolutionError("queue_full")
            task = asyncio.current_task()
            self._bedrock_jobs[task] = tenant_id
            try:
                return await adapter.evaluate(rule, parts, deadline_monotonic)
            finally:
                await adapter.close()
                self._bedrock_jobs.pop(task, None)
        if rule.kind == "injection":
            adapter = self._classifier(policy, stage, tenant_id)
            task = asyncio.current_task()
            self._classifier_jobs.add(task)
            try:
                return await adapter.evaluate(rule, parts, deadline_monotonic)
            finally:
                self._classifier_jobs.discard(task)
        return await self._bound(policy, stage, tenant_id).evaluate(rule, parts, deadline_monotonic)

    async def evaluate_content_lists(self, policy, parts, deadline_monotonic, *,
                                     stage, tenant_id):
        return await self._bound(policy, stage, tenant_id).content_lists(parts, deadline_monotonic)

    async def transform(self, policy, parts, findings, deadline_monotonic, *,
                        stage, tenant_id):
        return await self._bound(policy, stage, tenant_id).transform(parts, findings, deadline_monotonic)

    async def close(self):
        self._closed = True
        if self._classifier_monitor_task is not None:
            self._classifier_monitor_task.cancel()
            await asyncio.gather(self._classifier_monitor_task, return_exceptions=True)
            self._classifier_monitor_task = None
        jobs = tuple(job for job in self._classifier_jobs | self._bedrock_jobs.keys()
                     if job is not asyncio.current_task())
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        await super().close()
        await asyncio.gather(*(endpoint.close() for endpoint in self._endpoints()))


def _detect(rule, parts):
    from ..evaluators.pii import LocalPIIEvaluator
    from ..local_contract import CheckSpec, TextBatch, TextSegment

    _validate(rule, parts)
    check = CheckSpec(check_id="pinned-pii", evaluator="local_pii", stages=tuple(rule.stages), pii_types=tuple("phone" if entity == "international_phone" else entity for entity in rule.config.entities), requested_action="block")
    findings = []
    for offset in range(0, len(parts), 32):
        batch = parts[offset:offset + 32]
        text = TextBatch(segments=tuple(TextSegment(segment_id=str(index), text=part.text) for index, part in enumerate(batch)))
        for found in LocalPIIEvaluator().evaluate(text, check):
            category = {"GR-003:local_pii:email": "email", "GR-003:local_pii:phone": "international_phone"}[found.reason_code]
            findings.append(Finding(rule_id=rule.rule_id, category=category, part_id=batch[int(found.segment_id)].part_id, reason_code="pii_detected", start=found.start, end=found.end, score=1.0, evaluator_revision=PII_REVISION).model_dump(mode="json"))
            if len(findings) > MAX_FINDINGS:
                raise EvolutionError("inspection_incomplete")
    return findings


def _worker():
    # Worker limits supplement per-job monotonic deadlines and process killing.
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 31))
    _verify_artifacts()
    warm_rule = Rule(rule_id="warmup", kind="pii", stages=("input",), action="block", config={"entities": ("email", "international_phone")}, evaluator_revision=PII_REVISION)
    warm = _detect(warm_rule, (ContentPart(part_id="warmup", kind="text", role="user", text="probe@example.test +525512345678"),))
    if {item["category"] for item in warm} != SUPPORTED_ENTITIES or len(warm) != 2:
        return
    print('{"api_major":1,"ready":true}', flush=True)
    while raw := sys.stdin.buffer.readline(MAX_WIRE_BYTES + 2):
        if len(raw) > MAX_WIRE_BYTES + 1 or not raw.endswith(b"\n"):
            return
        try:
            payload = strict_json_loads(raw)
            if set(payload) != {"rule", "parts"}:
                return
            rule = Rule.model_validate(payload["rule"])
            parts = tuple(ContentPart.model_validate(part) for part in payload["parts"])
            result = {"findings": _detect(rule, parts)}
        except EvolutionError as error:
            result = {"error": error.code}
        except Exception:  # noqa: BLE001 - only finite errors cross the isolated worker boundary
            result = {"error": "evaluator_unavailable"}
        sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
        sys.stdout.buffer.flush()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _worker()
