"""Actual bounded request port with fake HTTPX/TLS; no sockets or native calls."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from types import SimpleNamespace as N

import httpx
import pytest
from evolution.classifier_budget_evidence import BudgetObservationError
from guardrails.evolution import models
from pydantic import ValidationError

from mvp.tests.evolution.e2e import classifier_admission_support as s
from mvp.tests.evolution.unit import test_classifier_admission as existing

no_actual_operations = existing.no_actual_operations


@pytest.fixture
def transport(tmp_path, monkeypatch):
    sub = existing.subjects(); state = tmp_path / 'state'; ca = state / 'credentials/ca.crt'
    inventory = {'tenants': {t: {'workload': {'workload_id': sub[t]['workload_id']},
        'roles': {'workload': {'principal_id': sub[t]['principal_id'],
            'token_file': str(state / ('assembly/' + t + '.token'))}}} for t in s.evidence.TENANTS}}
    access = {'profile': 'classifier', 'runtime_url': 'https://localhost:4443', 'ca_file': str(ca),
        'tenants': {t: {'workload_token_file': inventory['tenants'][t]['roles']['workload']['token_file']}
                    for t in s.evidence.TENANTS}}
    cluster = N(state=state, profile='classifier', access=access, inventory=inventory)
    events, reads, settings = [], [], []
    data = {str(ca): b'synthetic CA', **{item['roles']['workload']['token_file']: (t * 10).encode() + b'\n'
                                     for t, item in inventory['tenants'].items()}}
    def read(path, *, deadline, maximum):
        reads.append((str(path), deadline, maximum)); return data[str(path)]
    monkeypatch.setattr(s, 'private_bytes', read)
    class TLS:
        def __init__(self, protocol): assert protocol == s.ssl.PROTOCOL_TLS_CLIENT; self.minimum_version = None
        def load_verify_locations(self, *, cadata):
            assert cadata == 'synthetic CA'; events.append(('tls', self.minimum_version))
    monkeypatch.setattr(s.ssl, 'SSLContext', TLS)
    now = time.monotonic(); port = s.RuntimePort(cluster, deadline=now + 10)
    fault, status = [None], [200]
    class Client:
        def __init__(self, **kwargs): settings.append(kwargs)
        async def __aenter__(self): events.append('client_enter'); return self
        async def __aexit__(self, *args): events.append('client_close')
        def stream(self, method, url, *, headers, content):
            events.append(('request', method, url, copy.deepcopy(headers), content))
            sent = models.strict_json_loads(content); subject = sub[sent['tenant_id']]
            if fault[0] == 'connect': raise httpx.ConnectError('private connect detail')
            decision = models.Decision(decision_id=existing.uid(701), request_id=sent['request_id'],
                context_id=sent.get('context_id') or existing.uid(702), tenant_id=subject['tenant_id'],
                workload_id=subject['workload_id'], stage=sent['stage'], policy=subject['policy'],
                binding_revision=subject['binding_revision'], snapshot_generation=1, action='allow',
                would_be_action='allow', reason_codes=(), findings=(), evaluation_ms=1,
                audit_status='persisted', evaluator_revisions=(subject['evaluator_revision'],))
            value = decision.model_dump(mode='json') if status[0] == 200 else {
                'code': 'quota_exceeded', 'message': 'Quota exceeded', 'correlation_id': sent['correlation_id'], 'retryable': True}
            if fault[0] == 'decision_scope': value['tenant_id'] = 'foreign'
            if fault[0] == 'error_code': value['code'] = 'queue_full'
            if fault[0] == 'error_correlation': value['correlation_id'] = existing.uid(999)
            raw = models.canonical_json_bytes(value if status[0] == 200 else {'error': value})
            if fault[0] == 'json': raw = b'{}'
            if fault[0] == 'oversize': raw = b'x' * (2 * 1024 * 1024 + 1)
            head = [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                (b'x-request-id', sent['correlation_id'].encode()), (b'content-length', str(len(raw)).encode())]
            if status[0] == 429: head.append((b'retry-after', b'2'))
            if fault[0] == 'retry': head[-1] = (b'retry-after', b'100')
            if fault[0] == 'duplicate_header': head.append(head[0])
            if fault[0] == 'wrong_length': head[-1] = (b'content-length', b'0')
            if fault[0] == 'wrong_correlation': head[2] = (b'x-request-id', b'foreign')
            if fault[0] == 'gzip': head.append((b'content-encoding', b'gzip'))
            tls = N(version=lambda: 'TLSv1.1' if fault[0] == 'old_tls' else 'TLSv1.3')
            network = N(get_extra_info=lambda name: None if fault[0] == 'no_tls' else tls)
            class Response:
                status_code = status[0]
                headers = httpx.Headers(head)
                def __init__(self): self.extensions = {'network_stream': network}
                async def __aenter__(self): events.append('response_enter'); return self
                async def __aexit__(self, *args): events.append('response_close')
                async def aiter_raw(self, *, chunk_size):
                    assert chunk_size == 65536
                    if fault[0] == 'body_failure': raise OSError('private body detail')
                    if fault[0] == 'timeout': await asyncio.sleep(.03)
                    for i in range(0, len(raw), chunk_size): yield raw[i:i+chunk_size]
            return Response()
    monkeypatch.setattr(s.httpx, 'AsyncClient', Client)
    return N(**locals())


def execute(f, tenant='team-a', *, deadline=None):
    payload = s.sent(f.sub[tenant], request_id=existing.uid(800), correlation_id=existing.uid(801))
    return asyncio.run(f.port(f.sub[tenant], payload, label='request', deadline=deadline or time.monotonic()+5))


@pytest.mark.parametrize('tenant,seconds', [('team-a', 4), ('team-b', 1.5)])
def test_genuine_port_uses_exact_tls_route_profile_and_scoped_token(transport, tenant, seconds):
    f = transport; row = execute(f, tenant)
    assert row['status_code'] == 200 and row['budget_ms'] == (3000 if tenant == 'team-a' else 500)
    assert f.settings[0]['trust_env'] is False and f.settings[0]['follow_redirects'] is False
    timeout = f.settings[0]['timeout']; assert timeout.connect == .25 and timeout.read == seconds
    sent = next(event for event in f.events if isinstance(event, tuple) and event[0] == 'request')
    assert sent[1:3] == ('POST', 'https://127.0.0.1:4443/v2/evaluations')
    assert sent[3]['Host'] == 'localhost:4443' and sent[3]['Authorization'] == 'Bearer ' + tenant * 10
    assert sent[3]['X-Request-ID'] == existing.uid(801)
    assert f.events[-2:] == ['response_close', 'client_close']
    assert f.reads[0][2] == 65536 and [row[2] for row in f.reads[1:]] == [4097, 4097]
    assert f.events[0] == ('tls', s.ssl.TLSVersion.TLSv1_2)
    assert 'Bearer' not in json.dumps(row)


def test_genuine_port_accepts_only_exact_quota_denial_envelope(transport):
    transport.status[0] = 429
    row = execute(transport)
    assert row['status_code'] == 429 and row['decision'] is None and row['retry_after'] == 2


@pytest.mark.parametrize(('fault', 'error'), [
    ('decision_scope', ValidationError), ('json', ValidationError),
    ('oversize', BudgetObservationError), ('duplicate_header', BudgetObservationError),
    ('wrong_length', s.evidence.AdmissionError), ('wrong_correlation', BudgetObservationError),
    ('gzip', BudgetObservationError), ('no_tls', s.evidence.AdmissionError),
    ('old_tls', s.evidence.AdmissionError), ('body_failure', OSError), ('connect', httpx.ConnectError),
])
def test_actual_transport_refusal_closes_owned_clients_without_retry(transport, fault, error):
    f = transport; f.fault[0] = fault
    with pytest.raises(error): execute(f)
    assert f.events[-1] == 'client_close' and len(f.settings) == 1
    assert sum(isinstance(row, tuple) and row[0] == 'request' for row in f.events) == 1


@pytest.mark.parametrize('fault', ['retry', 'error_code', 'error_correlation'])
def test_unrelated_429_is_not_admission_evidence(transport, fault):
    transport.fault[0], transport.status[0] = fault, 429
    with pytest.raises(s.evidence.AdmissionError): execute(transport)


def test_original_outer_deadline_cancels_body_and_still_closes_both(transport):
    transport.fault[0] = 'timeout'
    with pytest.raises(TimeoutError): execute(transport, deadline=time.monotonic()+.01)
    assert transport.events[-2:] == ['response_close', 'client_close']


@pytest.mark.parametrize('fault', ['profile', 'origin', 'ca_path', 'token_path', 'token_text', 'principal'])
def test_scoped_material_or_subject_refused_before_request(transport, fault):
    f = transport
    if fault == 'principal':
        f.sub['team-a']['principal_id'] = existing.uid(999)
        with pytest.raises(s.evidence.AdmissionError): execute(f)
    else:
        if fault == 'profile': f.cluster.profile = 'mock'
        elif fault == 'origin': f.access['runtime_url'] = 'https://foreign:4443'
        elif fault == 'ca_path': f.access['ca_file'] = str(f.state / 'assembly/ca.crt')
        elif fault == 'token_path': f.access['tenants']['team-a']['workload_token_file'] = '/foreign/token'
        else: f.data[f.inventory['tenants']['team-a']['roles']['workload']['token_file']] = b'short'
        with pytest.raises(s.evidence.AdmissionError): s.RuntimePort(f.cluster, deadline=time.monotonic()+5)
    assert not f.settings
