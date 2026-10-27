"""Role-separated public governance for the new bounded admission family.

Only an additional immutable quota version and two approved policies/releases
are created. Current baseline bytes are never written. The original acknowledged
release handles remain restoration authority even if readiness polling fails.
"""
from __future__ import annotations

import copy
import hashlib
import time
from pathlib import Path

from evolution.evaluator_attempt import GovernanceAttempt
from evolution.evaluator_cases import control
from evolution.evaluator_governance import (
    payload_from_approved,
    policy_reference,
    reference,
)
from guardrails.evolution import models
from guardrails.evolution.storage import current_deadline

from mvp.evolution import classifier_admission_measurements as evidence


class AdmissionAttempt(GovernanceAttempt):
    """The sole added mutation is the exact precomputed stricter quota revision."""
    def __init__(self, *args, quota_path=None, quota_body=None, **kwargs):
        self.quota_path, self.quota_body = quota_path, copy.deepcopy(quota_body)
        super().__init__(*args, **kwargs)

    def before_request(self, *, method, path, role, body, key, expected):
        if path != self.quota_path:
            return super().before_request(method=method, path=path, role=role, body=body, key=key, expected=expected)
        evidence.require(self.quota_path is not None and method == 'POST' and role == 'tenant_admin'
            and expected == 201 and body == self.quota_body and self.pending is None and self.counter < 16)
        evidence.uuid(key)
        self.counter += 1
        self.write(f'request-{self.counter:02d}-intent.json', {'operation': self.operation, 'method': method,
            'path': path, 'role': role, 'actor_id': self.actors[role], 'idempotency_key': key,
            'body_sha256': evidence.digest(body), 'expected_status': expected, 'status': 'request_intended_outcome_unknown'})
        self.pending = self.counter


class GovernedAdmission:
    def __init__(self, cluster, *, operation_root, source_root, sources, case_id):
        evidence.require(case_id in evidence.CASES and cluster.tenants == evidence.TENANTS)
        self.cluster, self.root, self.source_root = cluster, Path(operation_root), Path(source_root)
        self.sources, self.case = copy.deepcopy(sources), case_id
        self.attempts, self.journeys, self.original, self.baselines = {}, {}, {}, {}
        self.subjects, self.restoration = {}, {}
        self.quota_audits = {}
        self.receipts = {}
        self.started = self.finished = False

    def quota_audit(self, tenant, quota, *, deadline):
        """Read the actual one scoped configuration-creation event; no audit writes."""
        token = current_deadline.set(min(deadline, time.monotonic() + 0.5))
        try:
            with self.cluster.repo.transaction(tenant) as tx:
                rows = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant "
                    "AND kind='audit' AND data->>'event_type'='configuration_created' "
                    "AND data->'metadata'->>'resource_type'='quotas' "
                    "AND data->'metadata'->>'resource_id'=:resource "
                    "AND data->'metadata'->>'revision'=:version LIMIT 2",
                    {'tenant': tenant, 'resource': quota['quota_id'], 'version': str(quota['version'])}))
            evidence.require(len(rows) == 1)
            event = rows[0].data
            evidence.require(event['tenant_id'] == tenant and event['actor_id'] == self.attempts[tenant].actors['tenant_admin']
                and event['event_type'] == 'configuration_created' and event['raw_content_retained'] is False
                and event['metadata'] == {'resource_type': 'quotas', 'resource_id': quota['quota_id'], 'revision': quota['version']}
                and evidence.sha(event['event_hash']))
            evidence.uuid(event['event_id'])
            other = next(t for t in evidence.TENANTS if t != tenant)
            with self.cluster.repo.transaction(other) as tx:
                evidence.require(tx.get('audit', event['event_id']) is None)
            evidence.require(time.monotonic() < deadline)
            return {k: copy.deepcopy(event[k]) for k in ('event_id', 'tenant_id', 'actor_id', 'event_type', 'event_hash', 'metadata')}
        finally:
            current_deadline.reset(token)

    def prepare(self, *, deadline):
        from mvp.tests.evolution.e2e.classifier_admission_support import check

        evidence.require(not self.started and not self.finished, 'admission_governance_replay')
        self.started = True
        c = self.cluster
        for tenant in evidence.TENANTS:
            check(deadline)
            original, original_policy = c.binding(tenant)
            baseline, base = c.baseline(tenant)
            self.original[tenant] = (copy.deepcopy(original), copy.deepcopy(original_policy))
            self.baselines[tenant] = copy.deepcopy(baseline)
            item = c.inventory['tenants'][tenant]
            actors = {role: item['roles'][role]['principal_id'] for role in
                      ('tenant_admin', 'author', 'approver', 'operator', 'auditor')}
            engine = 'injection' if tenant == 'team-a' else 'secret'
            quota = copy.deepcopy(base['quota'])
            stricter = tenant == 'team-a' and self.case != evidence.CASES[0]
            body = {key: value for key, value in quota.items()
                    if key not in {'quota_id', 'tenant_id', 'version', 'digest', 'created_at', 'revision'}}
            body['max_inflight'] = 1
            path = '/quotas/' + quota['quota_id'] + '/versions' if stricter else None
            journal_sources = {name: self.source_root / name for name in self.sources if name in {
                'mvp/evolution/evaluator_attempt.py', 'mvp/evolution/evaluator_governance.py', 'mvp/evolution/evaluator_cases.py',
                'mvp/evolution/classifier_admission_measurements.py',
                'mvp/tests/evolution/e2e/classifier_admission_governance.py',
                'mvp/tests/evolution/e2e/classifier_admission_support.py'}}
            attempt = AdmissionAttempt(self.root, tenant=tenant, original_binding=original,
                original_policy=original_policy, actors=actors, engine=engine, stage='input', state='observe',
                sources=journal_sources, quota_path=path, quota_body=body if stricter else None)
            self.attempts[tenant] = attempt
            evidence.require(attempt.source_sha256 == {name: self.sources[name] for name in journal_sources})
            journey = c.journey(tenant, attempt.operation, journal=attempt)
            self.journeys[tenant] = journey
            rule = control(engine, 'input'); rule['stages'] = ['input', 'output']
            if self.case == evidence.CASES[2] and tenant == 'team-a':
                # An operational classifier failure must be an audited block.
                rule['mandatory'], rule['minimum_state'] = True, 'observe'
            replacements = ()
            if tenant == 'team-a':
                endpoint = c.endpoints[tenant]
                replacements = (('providers', endpoint['provider']), ('resolved_destinations', endpoint['destination']))
            payload = payload_from_approved(base, rule, replacements=replacements)
            if stricter:
                returned = journey._request('POST', path, role='tenant_admin', body=body, label='admission-quota', expected=201)
                parsed = models.Quota.model_validate_json(models.canonical_json_bytes(returned))
                quota = parsed.model_dump(mode='json')
                evidence.require(parsed.digest == models.resource_digest(parsed) and quota['tenant_id'] == tenant
                    and quota['quota_id'] == base['quota']['quota_id'] and quota['version'] > base['quota']['version']
                    and {k: quota[k] for k in body} == body)
                payload['quota_ref'] = reference(quota, 'quota_id')
                self.quota_audits[tenant] = self.quota_audit(tenant, quota, deadline=deadline)
            approved = journey.approve(payload)
            evidence.require(all(row in approved['rules'] for row in base['rules'] if row['mandatory']))
            runtime_ids = tuple(row['runtime_id'] for row in item['runtimes'].values())
            check(deadline)
            released = journey.release(policy=approved, workload_id=original['workload_id'], state='observe',
                expected_revision=original['revision'], runtime_ids=runtime_ids, label='matrix-release',
                seconds=min(120, max(1, deadline - time.monotonic())))
            evidence.require(len(released['receipts']) == 2)
            c.assert_governance_audit(tenant, approved, applied=True)
            actual, actual_policy = c.binding(tenant)
            evidence.require(actual['policy'] == policy_reference(approved) and actual['state'] == 'observe'
                             and actual_policy['compiled_digest'] == approved['compiled_digest'])
            self.subjects[tenant] = {'tenant_id': tenant, 'principal_id': actors.get('workload', item['roles']['workload']['principal_id']),
                'workload_id': original['workload_id'], 'provider_id': item['profiles']['mock']['provider']['provider_id'],
                'policy': policy_reference(approved), 'binding_revision': actual['revision'],
                'quota_sha256': evidence.digest(approved['quota']), 'max_inflight': approved['quota']['max_inflight'],
                'rule_id': rule['rule_id'], 'evaluator_revision': rule['evaluator_revision'], 'budget_ms': 3000 if tenant == 'team-a' else 500}
            evidence.require(c.baseline(tenant)[0] == self.baselines[tenant])
        evidence.subjects(self.subjects)
        return copy.deepcopy(self.subjects)

    def restore(self, *, passed, deadline):
        """Attempt both independent owned restorations; never adopt unknown release."""
        evidence.require(self.started and not self.finished and type(passed) is bool, 'admission_governance_replay')
        self.finished = True
        errors = []
        for tenant in reversed(evidence.TENANTS):
            attempt, journey = self.attempts.get(tenant), self.journeys.get(tenant)
            if attempt is None: continue
            restoration = 'not_needed'
            try:
                handle = None if journey is None else journey.last_release
                original, policy = self.original[tenant]
                if handle is not None:
                    revision = self.cluster.owned_binding_revision(tenant, handle)
                    evidence.require(time.monotonic() < deadline)
                    ids = tuple(row['runtime_id'] for row in self.cluster.inventory['tenants'][tenant]['runtimes'].values())
                    journey.release(policy=policy, workload_id=original['workload_id'], state=original['state'],
                        expected_revision=revision, runtime_ids=ids, label='restore-original-binding',
                        seconds=min(120, max(1, deadline - time.monotonic())))
                    final, _ = self.cluster.binding(tenant)
                    evidence.require(final['policy'] == original['policy'] and final['state'] == original['state'])
                    restoration = 'applied'
                else:
                    current, current_policy = self.cluster.binding(tenant)
                    evidence.require(current == original and policy_reference(current_policy) == policy_reference(policy))
                evidence.require(not passed or attempt.pending is None, 'admission_unknown_outcome')
                evidence.require(self.cluster.baseline(tenant)[0] == self.baselines[tenant])
            except BaseException as error:  # noqa: BLE001 - attempt both owned tenant restorations even after interruption
                errors.append(type(error).__name__); restoration = 'failed'
            finally:
                self.restoration[tenant] = restoration
        # Complete only after both restoration attempts, so a later peer failure
        # cannot leave an earlier tenant's journal claiming the whole case passed.
        for tenant in reversed(evidence.TENANTS):
            attempt = self.attempts.get(tenant)
            if attempt is None: continue
            try:
                attempt.finish(passed=passed and not errors, restoration=self.restoration[tenant])
                # Capture the actual original journal while its held directory
                # is still authenticated. These are hashes, never token/policy bytes.
                from mvp.tests.evolution.e2e.classifier_network_support import (
                    private_bytes,
                )

                attempt.check()
                names = ('intent.json', 'release-matrix-release.json', 'release-restore-original-binding.json', 'completion.json')
                refs = {}
                for name in names:
                    path = attempt.path / name
                    if path.exists():
                        raw = private_bytes(path, deadline=deadline, maximum=16384)
                        refs[name] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
                if passed and not errors: evidence.require(set(refs) == set(names))
                attempt.check()
                self.receipts[tenant] = {'operation': attempt.operation, 'references': refs,
                    'source_sha256': copy.deepcopy(attempt.source_sha256),
                    'restoration': self.restoration[tenant], 'quota_audit': copy.deepcopy(self.quota_audits.get(tenant))}
            except BaseException as error: errors.append(type(error).__name__)  # noqa: BLE001 - retain each journal failure
            try: attempt.close()
            except BaseException as error: errors.append(type(error).__name__)  # noqa: BLE001 - attempt all owned closes
        evidence.require(not errors, 'admission_restoration_failed')
        return 'restored'
