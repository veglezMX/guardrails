"""Synthetic composition only: actual serializers/scope-reader, fake HTTP/PG."""
from __future__ import annotations

import asyncio
import copy
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from mvp.evolution import classifier_admission_measurements as m
from mvp.tests.evolution.e2e import classifier_admission_support as s


@pytest.fixture(autouse=True)
def no_actual_operations(monkeypatch):
    def fail(*a, **kw): pytest.fail('unexpected actual operation')
    original_socket = socket.socket
    def local_socket(family=socket.AF_INET, *args, **kwargs):
        if family != socket.AF_UNIX: fail()
        return original_socket(family, *args, **kwargs)
    monkeypatch.setattr(socket, 'socket', local_socket)
    monkeypatch.setattr(subprocess, 'Popen', fail)
    monkeypatch.setattr(os, 'kill', fail)
    monkeypatch.setattr(os, 'killpg', fail)
    monkeypatch.setattr(s.httpx, 'AsyncClient', fail)


def uid(n): return str(UUID(int=n))


def subjects():
    return {t: {'tenant_id': t, 'principal_id': uid(10 + i), 'workload_id': 'work-' + t, 'provider_id': uid(20 + i),
        'policy': {'tenant_id': t, 'policy_id': 'observed-' + t, 'version': 1, 'digest': str(i + 1) * 64},
        'binding_revision': 2, 'quota_sha256': '3' * 64, 'max_inflight': 1, 'rule_id': 'test-' + t,
        'evaluator_revision': 'classifier-v1' if i == 0 else 'secret-v1', 'budget_ms': 3000 if i == 0 else 500}
        for i, t in enumerate(m.TENANTS)}


class Repo:
    def __init__(self): self.rows, self.deadlines = {}, []

    @contextmanager
    def transaction(self, tenant):
        assert s.current_deadline.get() is not None
        self.deadlines.append(s.current_deadline.get())
        owner = self
        class Tx:
            def get(self, kind, key, required=False):
                value = owner.rows.get((tenant, kind, key))
                assert value is not None or not required
                return copy.deepcopy(value)

            def execute(self, query, params=None):
                if query == 'SELECT extract(epoch FROM clock_timestamp())':
                    return SimpleNamespace(scalar_one=lambda: time.time())
                assert params['tenant'] == tenant and 'LIMIT 2' in query
                field, kind = ('request_id', 'evaluation_contexts') if "kind='evaluation_contexts'" in query else ('correlation_id', 'audit')
                needle = params['request'] if field == 'request_id' else params['correlation']
                return [SimpleNamespace(key=k) for (t, family, k), row in owner.rows.items()
                        if t == tenant and family == kind and row[field] == needle][:2]
        yield Tx()


class FakeHTTP:
    def __init__(self, sub, repo):
        self.subjects, self.repo, self.inflight = sub, repo, set()
        self.mode = 'success'; self.calls = []; self.counter = 0

    async def __call__(self, subject, sent, *, label, deadline):
        from guardrails.evolution import models
        models.EvaluationRequest.model_validate_json(models.canonical_json_bytes(sent))
        started = time.monotonic(); self.calls.append(copy.deepcopy(sent)); self.counter += 1
        tenant = subject['tenant_id']
        overflow = tenant in self.inflight
        if not overflow: self.inflight.add(tenant)
        rid = sent['request_id']; context_id = sent.get('context_id', str(uuid4()))
        lease_key = s.ScopeReader.key(subject, rid)
        lease = {k: subject[k] for k in ('tenant_id', 'principal_id', 'workload_id')}
        lease.update(request_id=rid, policy_digest=subject['policy']['digest'], stage=sent['stage'],
            lease_id=str(uuid4()), state='active', expires_epoch=time.time() + 3)
        if not overflow:
            self.repo.rows[(tenant, 'quota_leases', lease_key)] = lease
        await asyncio.sleep(0.06 if label == 'a-original' else 0.015)
        row = {'label': label, 'tenant_id': tenant, 'request_id': rid, 'correlation_id': sent['correlation_id'],
            'stage': sent['stage'], 'context_id': sent.get('context_id'), 'body_sha256': m.digest(sent),
            'status_code': 429 if overflow else 200, 'decision': None, 'error_code': 'quota_exceeded' if overflow else None,
            'retry_after': 1 if overflow else None, 'started': started, 'finished': time.monotonic(), 'budget_ms': subject['budget_ms']}
        if overflow: return row
        self.inflight.remove(tenant)
        d = {k: subject[k] for k in ('tenant_id', 'workload_id', 'policy', 'binding_revision')}
        d.update(decision_id=str(uuid4()), request_id=rid, context_id=context_id, stage=sent['stage'], action='allow',
            reason_codes=[], evaluator_revisions=[subject['evaluator_revision']],
            finding_rules=[subject['rule_id']] if tenant == 'team-b' else [], audit_status='persisted')
        if self.mode == 'operational' and tenant == 'team-a': d['reason_codes'] = ['evaluator_unavailable']
        if self.mode == 'timeout' and tenant == 'team-a': d.update(action='block', reason_codes=['evaluation_timeout'])
        row['decision'] = d
        lease['state'] = 'released'
        self.repo.rows[(tenant, 'evaluation_contexts', context_id)] = {'context': {k: d[k] for k in ('context_id', 'tenant_id', 'workload_id', 'policy')},
            'principal_id': subject['principal_id'], 'request_id': rid,
            'state': 'blocked' if d['action'] == 'block' else 'open' if sent['stage'] == 'input' else 'completed'}
        self.repo.rows[(tenant, 'audit', d['decision_id'])] = {'event_id': d['decision_id'], 'event_type': 'evaluation',
            'decision_id': d['decision_id'], 'tenant_id': tenant, 'actor_id': subject['principal_id'], 'workload_id': subject['workload_id'],
            'correlation_id': sent['correlation_id'], 'policy_digest': subject['policy']['digest'], 'binding_revision': subject['binding_revision'],
            'action': d['action'], 'event_hash': 'a' * 64, 'raw_content_retained': False, 'metadata': {'stage': sent['stage']}}
        return row


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('native child'))
    monkeypatch.setattr(s.httpx, 'AsyncClient', lambda *a, **kw: pytest.fail('HTTP'))
    sub = subjects(); repo = Repo(); records = []
    def record(name, value):
        raw = s.models.canonical_json_bytes(value)
        with (tmp_path / name).open('xb') as stream: stream.write(raw)
        (tmp_path / name).chmod(0o600); records.append(copy.deepcopy(value))
    f = SimpleNamespace(sub=sub, repo=repo, records=records)
    f.http = FakeHTTP(sub, repo); f.reader = s.ScopeReader(SimpleNamespace(repo=repo))
    def make(case=m.CASES[0], **kw):
        return s.AdmissionObservation(case_id=case, operation=str(uuid4()), sources={'mvp/pinned.py': 'a' * 64},
            subjects=sub, authorization_sha256='b' * 64, http=f.http, reader=f.reader, record=record,
            verify=kw.pop('verify', lambda value: True), **kw)
    f.make = make
    return f


def run(f, case=m.CASES[0], **kwargs):
    observed = f.make(case, **kwargs)
    asyncio.run(observed.run(deadline=time.monotonic() + 5))
    return observed, observed.complete(restoration='restored')


def test_real_scope_projection_concurrent_same_identifiers_and_two_stages(fixture):
    f = fixture; observed, result = run(f)
    assert len(result['requests']) == 4 and len(f.http.calls) == 4
    assert len({r['correlation_id'] for r in f.http.calls}) == 1
    assert len({r['request_id'] for r in f.http.calls}) == 2
    assert f.records[0]['phase'] == 'intent' and not f.records[0]['requests']
    assert f.repo.deadlines and observed.value['cleanup']['requests'] == 'joined'
    assert b'AKIAABCDEFGHIJKLMNOP' not in s.models.canonical_json_bytes(result)
    assert b'owner_hash' not in s.models.canonical_json_bytes(result)


def test_real_scope_reader_requires_live_lease_before_concurrent_quota_and_peer(fixture):
    f = fixture; _, result = run(f, m.CASES[1])
    assert [r['status_code'] for r in result['requests']] == [200, 429, 200]
    assert result['observations']['active']['lease']['state'] == 'active'
    assert result['observations']['overflow_absent'] == {'context_count': 0, 'lease': False, 'audit_count': 0}
    assert len(f.http.calls) == 3


@pytest.mark.parametrize('fault', ['operational', 'foreign_context', 'wrong_audit_actor', 'wrong_lease_principal', 'wrong_policy', 'no_b_finding'])
def test_actual_progress_retained_when_semantic_projection_refuses(fixture, monkeypatch, fault):
    f = fixture
    if fault == 'operational': f.http.mode = fault
    else:
        original = f.reader.committed
        def changed(*a, **kw):
            row = original(*a, **kw)
            if fault == 'foreign_context': row['foreign']['context'] = True
            elif fault == 'wrong_audit_actor': row['audit']['actor_id'] = uid(999)
            elif fault == 'wrong_lease_principal': row['lease']['principal_id'] = uid(999)
            elif fault == 'wrong_policy': row['context']['policy']['digest'] = 'f' * 64
            return row
        if fault != 'no_b_finding': monkeypatch.setattr(f.reader, 'committed', changed)
        else:
            original_http = f.http
            async def http(*a, **kw):
                row = await original_http(*a, **kw)
                if row['tenant_id'] == 'team-b': row['decision']['finding_rules'] = []
                return row
            f.http = http
    observed = f.make()
    with pytest.raises(m.AdmissionError): asyncio.run(observed.run(deadline=time.monotonic() + 5))
    assert f.records[-1]['status'] == 'failed' and f.records[-1]['requests']
    assert observed.value['cleanup']['requests'] == 'joined'


@pytest.mark.parametrize('field', ['principal_id', 'provider_id', 'binding_revision', 'max_inflight', 'budget_ms', 'quota_sha256', 'policy'])
def test_closed_subject_refusals(field):
    value = subjects(); value['team-a'][field] = None
    with pytest.raises(m.AdmissionError): m.subjects(value)


@pytest.mark.parametrize('fault', ['duplicate_context', 'different_request', 'different_correlation', 'serial', 'foreign_audit',
    'wrong_revision', 'operational', 'missing_attribution', 'missing_lease', 'unrestored', 'extra_field'])
def test_consumer_rejects_tampered_same_id_evidence(fixture, fault):
    _, value = run(fixture); rows = value['requests']
    if fault == 'duplicate_context':
        for r in rows: r['decision']['context_id'] = uid(500)
    elif fault == 'different_request': rows[1]['request_id'] = uid(501)
    elif fault == 'different_correlation': rows[1]['correlation_id'] = uid(502)
    elif fault == 'serial': rows[1]['started'] = rows[0]['finished'] + 0.001; rows[1]['finished'] = rows[1]['started'] + 0.001
    elif fault == 'foreign_audit': next(iter(value['observations'].values()))['foreign']['audit'] = True
    elif fault == 'wrong_revision': rows[0]['decision']['binding_revision'] += 1
    elif fault == 'operational': rows[0]['decision']['reason_codes'] = ['inspection_incomplete']
    elif fault == 'missing_attribution': next(r for r in rows if r['tenant_id'] == 'team-b')['decision']['finding_rules'] = []
    elif fault == 'missing_lease': next(iter(value['observations'].values()))['lease']['state'] = 'active'
    elif fault == 'unrestored': value['cleanup']['governance'] = 'failed'
    else: value['extra'] = 'untrusted'
    with pytest.raises((m.AdmissionError, KeyError)): m.validate(value, verify_provenance=lambda v: True)


@pytest.mark.parametrize('fault', ['stale_lease', 'wrong_principal', 'ended_before_b', 'wrong_denial', 'denied_was_written'])
def test_consumer_rejects_false_saturation(fixture, fault):
    _, value = run(fixture, m.CASES[1]); active = value['observations']['active']
    if fault == 'stale_lease': active['database_epoch'] = active['lease']['expires_epoch']
    elif fault == 'wrong_principal': active['lease']['principal_id'] = uid(600)
    elif fault == 'ended_before_b': value['requests'][0]['finished'] = value['requests'][2]['started'] - 0.001
    elif fault == 'wrong_denial': value['requests'][1]['error_code'] = 'queue_full'
    else: value['observations']['overflow_absent']['lease'] = True
    with pytest.raises(m.AdmissionError): m.validate(value, verify_provenance=lambda v: True)


def test_parent_verifier_mandatory_detached_and_finite_failure(fixture):
    _, value = run(fixture)
    with pytest.raises(m.AdmissionError): m.validate(value, verify_provenance=None)
    with pytest.raises(m.AdmissionError): m.validate(value, verify_provenance=lambda v: False)
    before = copy.deepcopy(value)
    def mutate(detached): detached['status'] = 'changed'; return True
    assert m.validate(value, verify_provenance=mutate) == before
    assert value == before


def test_no_retry_when_original_already_finished(fixture, monkeypatch):
    f = fixture; monkeypatch.setattr(f.reader, 'active', lambda *a, **kw: None)
    observed = f.make(m.CASES[1])
    with pytest.raises(m.AdmissionError, match='admission_no_active_lease'):
        asyncio.run(observed.run(deadline=time.monotonic() + 3))
    assert len(f.http.calls) == 1 and f.records[-1]['status'] == 'failed'


def test_retention_failure_prevents_success_without_replaying_requests(fixture):
    f = fixture; observed = f.make()
    def fail(*a): raise OSError('synthetic')
    observed.record = fail
    with pytest.raises(OSError): asyncio.run(observed.run(deadline=time.monotonic() + 5))
    assert f.http.calls == []
