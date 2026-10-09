"""Validated immutable snapshots; evaluation never calls the control plane."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import datetime, timezone

import httpx

from .. import local_contract as d
from ..management.governance import applicable, validate_binding, validate_combination
from ..management.models import SnapshotModel
from ..transport import ContractProblem, parse_model


def unique(values, key):
    result = {key(value): value for value in values}
    if len(result) != len(values):
        raise d.DomainValidationError()
    return result


def validate_snapshot(snapshot):
    workloads = unique(snapshot.workloads, lambda w: (w.tenant_id, w.workload_id))
    policies = unique(snapshot.policies, lambda p: (p.tenant_id, p.policy_id, p.version))
    unique(snapshot.policies, lambda p: (p.tenant_id, p.policy_id, p.publication_ordinal))
    unique(snapshot.bindings, lambda b: (b.tenant_id, b.workload_id))
    unique(snapshot.floors, lambda f: (f.scope, f.tenant_id))
    unique(snapshot.credential_hashes, lambda c: c.credential_id)
    unique(snapshot.credential_hashes, lambda c: c.token_sha256)
    credentials = unique(snapshot.credential_hashes, lambda c: (c.tenant_id, c.workload_id))
    if set(credentials) != set(workloads):
        raise d.DomainValidationError()
    for key, credential in credentials.items():
        if (credential.revoked_at is not None or credential.capabilities
                or credential.revision != workloads[key].credential_revision):
            raise d.DomainValidationError()
    for tenant in {w.tenant_id for w in snapshot.workloads} | {f.tenant_id for f in snapshot.floors if f.tenant_id}:
        validate_combination(applicable(snapshot.floors, tenant))
    for binding in snapshot.bindings:
        policy = policies.get((binding.tenant_id, binding.policy_id, binding.policy_version))
        if ((binding.tenant_id, binding.workload_id) not in workloads or policy is None
                or binding.generation > snapshot.generation):
            raise d.DomainValidationError()
        validate_binding(binding, policy, applicable(snapshot.floors, binding.tenant_id))
    return snapshot


class SnapshotCache:
    def __init__(self, config=None, *, clock=time.monotonic):
        self.config = config or d.ProofConfig()
        self.clock = clock
        self.current = None
        self.last_contact = None
        self.refresh_failed = False
        self.acknowledged_generation = None

    @property
    def age(self):
        return None if self.last_contact is None else max(0.0, self.clock() - self.last_contact)

    @property
    def stale(self):
        return self.age is None or self.age > self.config.max_snapshot_age_s

    def health(self):
        return dict(ready=self.current is not None and not self.stale,
                    degraded=self.refresh_failed or self.stale,
                    applied_generation=self.current.generation if self.current else None, cache_age_s=self.age)

    def apply(self, raw):
        try:
            candidate = validate_snapshot(parse_model(raw, SnapshotModel).to_domain())
            if self.current and (candidate.generation < self.current.generation or
                                 (candidate.generation == self.current.generation and candidate.digest != self.current.digest)):
                raise d.DomainValidationError()
            if self.current:
                old_policies = {(p.tenant_id, p.policy_id, p.version): p for p in self.current.policies}
                for policy in candidate.policies:
                    previous = old_policies.get((policy.tenant_id, policy.policy_id, policy.version))
                    if previous is not None and policy != previous:
                        raise d.DomainValidationError()
        except (ContractProblem, d.DomainValidationError):
            self.refresh_failed = True
            raise ContractProblem('management_unavailable', 503) from None
        self.current = candidate  # single atomic reference swap after complete validation
        self.last_contact, self.refresh_failed = self.clock(), False
        return candidate

    def heartbeat(self, etag):
        if self.current is None or etag != '"' + self.current.digest + '"':
            self.refresh_failed = True
            raise ContractProblem('management_unavailable', 503)
        self.last_contact, self.refresh_failed = self.clock(), False

    def authenticate(self, authorization):
        if self.current is None:
            raise ContractProblem('management_unavailable', 503)
        if not authorization or not authorization.startswith('Bearer '):
            raise ContractProblem('authentication_failed', 401)
        token = authorization[7:]
        if not 32 <= len(token) <= 256 or any(c.isspace() for c in token):
            raise ContractProblem('authentication_failed', 401)
        try:
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
        except UnicodeError:
            raise ContractProblem('authentication_failed', 401) from None
        for credential in self.current.credential_hashes:
            if hmac.compare_digest(digest, credential.token_sha256):
                return credential
        raise ContractProblem('authentication_failed', 401)

    def resolve(self, identity):
        snapshot = self.current
        binding = next((b for b in snapshot.bindings if (b.tenant_id, b.workload_id) == identity), None)
        policy = next((p for p in snapshot.policies if binding and
                       (p.tenant_id, p.policy_id, p.version) == (binding.tenant_id, binding.policy_id, binding.policy_version)), None)
        required = any(f.require_binding for f in applicable(snapshot.floors, identity[0]))
        failure = policy.failure_behavior if policy else d.FailureBehavior(**{
            name: ('block' if required else self.config.unresolved_policy_behavior)
            for name in d.FailureBehavior.__dataclass_fields__})
        client = d.ClientConfig(schema_version=d.SCHEMA_VERSION, tenant_id=identity[0], workload_id=identity[1],
                                generation=snapshot.generation, input_timeout_ms=binding.input_timeout_ms if binding else 1500,
                                output_timeout_ms=binding.output_timeout_ms if binding else 1500,
                                failure_behavior=failure, retrieved_at=datetime.now(timezone.utc))
        return binding, policy, client


class SnapshotPoller:
    def __init__(self, cache, client):
        self.cache, self.client = cache, client

    async def refresh(self):
        try:
            headers = {'If-None-Match': '"' + self.cache.current.digest + '"'} if self.cache.current else {}
            async with asyncio.timeout(0.5):
                async with self.client.stream('GET', '/internal/v1/snapshot', headers=headers) as response:
                    if response.status_code == 304:
                        self.cache.heartbeat(response.headers.get('etag'))
                    else:
                        response.raise_for_status()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 8_388_608:
                                raise ContractProblem('management_unavailable', 503)
                            raw.extend(chunk)
                        self.cache.apply(bytes(raw))
            current = self.cache.current
            if self.cache.acknowledged_generation != current.generation:
                async with asyncio.timeout(0.5):
                    response = await self.client.post('/internal/v1/applied', json=dict(generation=current.generation, digest=current.digest))
                    response.raise_for_status()
                    if response.json() != {'applied_generation': current.generation}:
                        raise ContractProblem('management_unavailable', 503)
                    self.cache.acknowledged_generation = current.generation
        except (httpx.HTTPError, TimeoutError, ValueError, ContractProblem):
            self.cache.refresh_failed = True

    async def run(self):
        while True:
            await self.refresh()
            await asyncio.sleep(self.cache.config.poll_interval_ms / 1000)
