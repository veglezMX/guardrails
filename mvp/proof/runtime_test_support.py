"""Authored synthetic runtime fixtures and real HTTPS service orchestration."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from guardrails import local_contract as d
from guardrails.transport import parse_policy
from proof.management_test_support import API, HTTPSProcess, PREFIX, envelope, policy


def batch(*texts):
    return d.TextBatch(segments=tuple(d.TextSegment(segment_id=str(i), text=t) for i, t in enumerate(texts)))


def components(state='block', *, requested='block', failures=None, retain=False):
    draft = policy(retain_content=retain)
    draft['checks'][0]['requested_action'] = requested
    draft['failure_behavior'].update(failures or {})
    model = parse_policy(d.canonical_bytes(draft)).to_domain()
    actor, now = uuid4(), datetime.now(timezone.utc)
    published = d.Policy(**{key: getattr(model, key) for key in model.__dataclass_fields__},
                         publication_ordinal=1, digest=model.content_digest(), published_at=now, published_by=actor)
    binding = d.PolicyBinding(tenant_id='local-proof', workload_id='team-a', policy_id='pii', policy_version='v1',
                              enforcement_state=state, revision=2, generation=5, input_timeout_ms=1500, output_timeout_ms=1500,
                              updated_at=now, updated_by=actor, reason='synthetic_promotion',
                              evidence_kind='synthetic_local_proof', fixture_run_id=uuid4())
    client = d.ClientConfig(schema_version=d.SCHEMA_VERSION, tenant_id='local-proof', workload_id='team-a', generation=5,
                            input_timeout_ms=1500, output_timeout_ms=1500, failure_behavior=published.failure_behavior, retrieved_at=now)
    return binding, published, client


def evaluation(*texts, stage='input', context=None, correlation=None, workload='team-a'):
    return d.Evaluation(evaluation_id=uuid4(), tenant_id='local-proof', workload_id=workload, stage=stage,
                        content=batch(*(texts or ('clean',))), correlation_id=correlation or uuid4(),
                        context_id=context, received_at=datetime.now(timezone.utc))


def request_body(text='clean', *, previous=None, workload='team-a'):
    return envelope(tenant_id='local-proof', workload_id=workload, stage='output' if previous else 'input',
                    content=batch(text).to_wire(), correlation_id=previous['correlation_id'] if previous else str(uuid4()),
                    context_id=previous['context_id'] if previous else None)


def change_state(api, state, workload='team-a'):
    path = PREFIX + f'/workloads/{workload}/binding'
    revision = api.request('GET', path).json()['desired']['revision']
    return api.request('POST', path + '/state', body=envelope(enforcement_state=state, reason='synthetic_promotion',
                       evidence_kind='synthetic_local_proof', fixture_run_id=str(uuid4())), revision=revision, status=202).json()


def publish_update(stack, draft):
    api = stack.management.api
    api.request('POST', PREFIX + '/policies', body=draft, status=201)
    binding = api.request('GET', PREFIX + '/workloads/team-a/binding').json()['desired']
    result = api.request('PUT', PREFIX + '/workloads/team-a/binding', revision=binding['revision'], status=202,
                         body=envelope(policy_id='pii', policy_version=draft['version'], reason='policy_update'))
    stack.wait_applied(result.json())
    return result.json()


class RuntimeStack:
    def __init__(self, root, *, config=None):
        self.root = Path(root)
        self.management = HTTPSProcess(self.root / 'management')
        self.pki = self.management.pki
        self.runtime_root = self.root / 'runtime'
        self.runtime_root.mkdir(mode=0o700)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'https://127.0.0.1:{self.port}'
        self.db = self.runtime_root / 'db/audit.sqlite3'
        self.config = self.runtime_root / 'runtime.json'
        self.process = None
        self.mock_process = None
        self.tokens = self.management.tokens
        self.tokens['proof'] = secrets.token_urlsafe(32)
        for name in ('runtime', 'management'):
            path = self.runtime_root / f'{name}.token'
            path.write_text(self.tokens[name])
            path.chmod(0o600)
        management_config = json.loads(self.management.config.read_text())
        management_config['runtime'] = dict(url=self.url, ca_file=str(self.pki / 'ca.crt'), token_file=str(self.runtime_root / 'management.token'))
        self.management.config.write_text(json.dumps(management_config))
        self.config.write_text(json.dumps(dict(
            management_url=self.management.url, ca_file=str(self.pki / 'ca.crt'), token_file=str(self.runtime_root / 'runtime.token'),
            credentials=[c for c in management_config['credentials'] if c['role'] == 'management_service'],
            proof_config=config or {}, proof_control_sha256=hashlib.sha256(self.tokens['proof'].encode()).hexdigest())))
        self.config.chmod(0o600)

    def start(self, *, management=True):
        if management:
            self.management.start()
        self.start_runtime()
        if management:
            deadline = time.monotonic() + 5
            while self.resources()['acknowledged_generation'] is None:
                if time.monotonic() >= deadline:
                    raise AssertionError('Runtime bootstrap acknowledgement timeout')
                time.sleep(.02)
        return self

    def start_runtime(self):
        import httpx
        env = os.environ | {'GUARDRAILS_RUNTIME_CONFIG': str(self.config), 'GUARDRAILS_AUDIT_DB': str(self.db), 'GUARDRAILS_PROOF_PROFILE': 'runtime'}
        env.pop('PYTHONPATH', None)
        self.log = (self.runtime_root / 'service.log').open('ab')
        (self.runtime_root / 'service.log').chmod(0o600)
        self.process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'guardrails.runtime.app:create_app', '--factory',
            '--host', '127.0.0.1', '--port', str(self.port), '--ssl-certfile', str(self.pki / 'runtime.crt'),
            '--ssl-keyfile', str(self.pki / 'runtime.key'), '--no-access-log', '--log-level', 'warning'],
            cwd=self.runtime_root, env=env, stdout=self.log, stderr=self.log)
        self.client = httpx.Client(base_url=self.url, verify=ssl.create_default_context(cafile=str(self.pki / 'ca.crt')), timeout=5, trust_env=False)
        self.api = API(self.client, self.tokens)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError('Runtime process exited during startup')
            try:
                if self.client.get('/health/live').status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.05)
        raise AssertionError('Runtime HTTPS startup timeout')

    def stop_runtime(self):
        if self.process is not None:
            self.client.close()
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
                self.log.close()
                self.process = None

    def stop(self):
        try:
            self.stop_runtime()
        finally:
            try:
                if self.mock_process:
                    self.mock_client.close()
                    self.mock_process.terminate()
                    try:
                        self.mock_process.wait(timeout=10)
                    finally:
                        if self.mock_process.poll() is None:
                            self.mock_process.kill()
                            self.mock_process.wait(timeout=5)
                        self.mock_log.close()
                        self.mock_process = None
            finally:
                self.management.stop()

    def start_mock(self):
        import httpx
        root = self.root / 'mock'
        root.mkdir(mode=0o700)
        for name in ('proof', 'model'):
            self.tokens.setdefault(name, secrets.token_urlsafe(32))
            path = root / f'{name}.token'
            path.write_text(self.tokens[name])
            path.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = os.environ | {'GUARDRAILS_PROOF_PROFILE': 'smoke', 'GUARDRAILS_PROOF_CONTROL_TOKEN_FILE': str(root / 'proof.token'),
                            'GUARDRAILS_MOCK_TOKEN_FILE': str(root / 'model.token')}
        env.pop('PYTHONPATH', None)
        self.mock_log = (root / 'service.log').open('ab')
        (root / 'service.log').chmod(0o600)
        self.mock_process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'proof.mock_model:create_app', '--factory',
            '--host', '127.0.0.1', '--port', str(port), '--ssl-certfile', str(self.pki / 'mock-model.crt'),
            '--ssl-keyfile', str(self.pki / 'mock-model.key'), '--no-access-log', '--log-level', 'warning'],
            cwd=root, env=env, stdout=self.mock_log, stderr=self.mock_log)
        self.mock_client = httpx.Client(base_url=f'https://127.0.0.1:{port}',
                                       verify=ssl.create_default_context(cafile=str(self.pki / 'ca.crt')), timeout=5, trust_env=False)
        self.mock_api = API(self.mock_client, self.tokens)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.mock_process.poll() is not None:
                raise AssertionError('Mock exited at startup')
            try:
                if self.mock_client.get('/health/live').status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.05)
        raise AssertionError('Mock startup timeout')

    def infer(self, text='clean'):
        return self.mock_api.request('POST', '/v1/chat/completions', actor='model',
                                     body={'model': 'proof', 'messages': [{'role': 'user', 'content': text}]}).json()['choices'][0]['message']['content']

    def wait_applied(self, mutation):
        operation = mutation['operation']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.management.api.client.get('/v1/operations/' + operation['operation_id'],
                headers={'Authorization':'Bearer '+self.tokens['admin']})
            assert response.status_code in (200, 503), 'operation read rejected'
            if response.status_code == 200:
                result = response.json()
                if result['status'] == 'applied':
                    assert result['applied_generation'] >= operation['desired_generation']
                    return result
            time.sleep(.05)
        raise AssertionError('Runtime did not acknowledge generation within five seconds')

    def seed(self, state='block'):
        api = self.management.api
        # Serialize setup by real application receipts, just as the default CLI
        # does. Do not race subsequent mutations against snapshot acknowledgement
        # writers or retry a mutation that may already have committed.
        issued = api.request('POST', PREFIX + '/workloads', body=envelope(workload_id='team-a', display_name='Synthetic team'), status=201).json()
        api.tokens['team-a'] = issued['token']
        self.wait_applied(issued)
        self.wait_applied(api.request('POST', PREFIX + '/policies', body=policy(), status=201).json())
        initial = api.request('PUT', PREFIX + '/workloads/team-a/binding', body=envelope(policy_id='pii', policy_version='v1', reason='initial_binding'), create=True, status=202).json()
        self.wait_applied(initial)
        if state != 'observe':
            self.wait_applied(change_state(self.management.api, state))
        return initial

    def evaluate(self, text='clean', *, previous=None, workload='team-a', status=200):
        return self.api.request('POST', '/v1/evaluate', actor=workload,
                                body=request_body(text, previous=previous, workload=workload), status=status).json()

    def fault(self, worker=None):
        return self.api.request('PUT', '/proof/fault', actor='proof', body={'worker': worker}).json()

    def resources(self):
        return self.api.request('GET', '/proof/resources', actor='proof').json()

    def wait_recovered(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.resources()
            if value['admitted'] == 0 and value['available_workers'] == value['workers'] and value['workers'] > 0:
                return value
            time.sleep(.02)
        raise AssertionError('Worker capacity did not recover')
