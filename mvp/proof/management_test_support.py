"""Synthetic management fixtures; imported only by proof tests."""
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from guardrails.management.identity import credential

PREFIX = '/v1/tenants/local-proof'
SCHEMA = 'local-proof-v1'


def envelope(**values):
    return dict(schema_version=SCHEMA, **values)


def policy(version='v1', tenant='local-proof', **changes):
    value = envelope(tenant_id=tenant, policy_id='pii', version=version,
                     checks=[dict(check_id='pii', evaluator='local_pii', stages=['input', 'output'],
                                  pii_types=['email', 'phone'], requested_action='block')],
                     failure_behavior=dict.fromkeys(('evaluation_timeout', 'evaluation_unavailable', 'policy_unresolved',
                                                      'management_unavailable', 'audit_failure', 'transformation_failure'), 'block'),
                     retain_content=False, detector_format_version='pii-ascii-v1')
    return value | changes


def floor(**changes):
    return envelope(require_binding=True, required_stages=['input', 'output'], required_pii_types=['email', 'phone'],
                    minimum_action='log_only', minimum_state='observe', required_policy_id='pii',
                    minimum_policy_ordinals={}, require_fail_closed=['transformation_failure'],
                    forbid_content_retention=True) | changes


def actors():
    tokens = {name: secrets.token_urlsafe(32) for name in ('admin', 'operator', 'runtime', 'management')}
    identities = (
        credential(role='security_admin', token=tokens['admin']),
        credential(role='tenant_operator', token=tokens['operator'], tenant_id='local-proof'),
        credential(role='runtime_service', token=tokens['runtime'], service_id='runtime'),
        credential(role='management_service', token=tokens['management'], service_id='management'),
    )
    return tokens, identities


class API:
    def __init__(self, client, tokens):
        self.client, self.tokens = client, tokens

    def request(self, method, path, *, body=None, status=200, actor='admin', revision=None, create=False, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        if actor is not None:
            headers['Authorization'] = 'Bearer ' + self.tokens.get(actor, actor)
        if revision is not None:
            headers['If-Match'] = str(revision)
        if create:
            headers['If-None-Match'] = '*'
        if body is not None:
            kwargs['json'] = body
        result = self.client.request(method, path, headers=headers, **kwargs)
        assert result.status_code == status, (method, path, result.status_code, status)
        if status >= 400:
            assert result.headers['content-type'] == 'application/problem+json'
        return result

    def seed(self, workload='team-a'):
        registered = self.request('POST', PREFIX + '/workloads', body=envelope(workload_id=workload, display_name='Synthetic team'), status=201).json()
        self.tokens[workload] = registered['token']
        self.request('POST', PREFIX + '/policies', body=policy(), status=201)
        return self.request('PUT', PREFIX + f'/workloads/{workload}/binding', body=envelope(policy_id='pii', policy_version='v1', reason='initial_binding'), create=True, status=202).json()


class HTTPSProcess:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True)
        self.tokens, credentials = actors()
        self.config = self.root / 'management.json'
        self.config.write_text(json.dumps({'credentials': [item.to_wire() for item in credentials]}))
        self.config.chmod(0o600)
        self.db = self.root / 'db/control.sqlite3'
        self.pki = Path(os.environ['PROOF_PKI'])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'https://127.0.0.1:{self.port}'
        self.process = None

    def start(self):
        import httpx
        env = os.environ | {'GUARDRAILS_MANAGEMENT_CONFIG': str(self.config), 'GUARDRAILS_CONTROL_DB': str(self.db)}
        # The service must run the installed image artifact, not the mounted test sources.
        env.pop('PYTHONPATH', None)
        self.log = (self.root / 'service.log').open('ab')
        (self.root / 'service.log').chmod(0o600)
        self.process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'guardrails.management.app:create_app', '--factory',
                                         '--host', '127.0.0.1', '--port', str(self.port),
                                         '--ssl-certfile', str(self.pki / 'management.crt'), '--ssl-keyfile', str(self.pki / 'management.key'),
                                         '--no-access-log', '--log-level', 'warning'],
                                        cwd=self.root, env=env, stdout=self.log, stderr=self.log)
        self.client = httpx.Client(base_url=self.url, verify=ssl.create_default_context(cafile=str(self.pki / 'ca.crt')), timeout=5, trust_env=False)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError('Management process exited during startup')
            try:
                if self.client.get('/health/live').status_code == 200:
                    self.api = API(self.client, self.tokens)
                    return self.api
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        self.stop()
        raise AssertionError('Management HTTPS readiness timeout')

    def stop(self, *, kill=False):
        if self.process is not None:
            self.client.close()
            if kill:
                self.process.kill()
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=10)
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
                self.log.close()
                self.process = None


def integration_root():
    return Path(os.environ['PROOF_MANAGEMENT_TEST_ROOT']) / str(uuid4())
