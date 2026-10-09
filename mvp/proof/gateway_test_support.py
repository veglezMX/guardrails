"""Host supervision of an isolated, prepared, real two-gateway proof stack."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from .management_test_support import API, PREFIX, envelope, policy

ROOT = Path(__file__).resolve().parents[2]
PREPARED = ROOT / '.proof'


def port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


class GatewayStack:
    def __init__(self, *, live=False, resume=None, acceptance=False):
        self.run_id = Path(resume["root"]).name if resume else str(uuid4())
        self.resuming = resume is not None
        self.root = PREPARED / ('state/acceptance' if acceptance else 'state/live-stacks' if live else 'state/gateway-tests') / self.run_id
        self.root.mkdir(parents=True, mode=0o700, exist_ok=self.resuming)
        self.project = resume['project'] if resume else ('guardrails-p5-' if acceptance else 'guardrails-p4-') + self.run_id[:8]
        self.config = self.root / 'compose.json'
        self.tokens = {}
        self.fixture_content = set()
        self.ports = {service: port() for service in ('management', 'runtime', 'gateway-a', 'gateway-b')}
        self.client = httpx.Client(verify=ssl.create_default_context(cafile=str(PREPARED / 'pki/ca.crt')), trust_env=False, timeout=20)
        if live:
            self.ports = dict(management=8443, runtime=8444, **{'gateway-a':8401,'gateway-b':8402})
        self.live = live
        self.control = None
        self.forwarders = {}

    def compose(self, *args, check=True):
        result = subprocess.run(['docker', 'compose', '-p', self.project, '-f', str(self.config), *args],
                                cwd=ROOT, text=True, capture_output=True, check=check)
        if result.returncode == 0 and args[0] in ('start', 'restart', 'up'):
            for service in tuple(self.forwarders):
                if service in args:
                    self.forwarders.pop(service).close()
                    self.forward(service)
        return result

    def prepare(self):
        env = os.environ | {'PROOF_UID': str(os.getuid()), 'PROOF_GID': str(os.getgid())}
        raw = subprocess.check_output(['docker', 'compose', '-f', str(ROOT / 'mvp/proof/compose.yaml'),
              '-f', str(ROOT / 'mvp/proof/compose.runtime.yaml'), '--profile', 'management', 'config', '--format', 'json'], env=env)
        config = json.loads(raw)
        config['name'] = self.project
        config['services'].pop('runner', None)
        config['networks']['proof']['name'] = self.project + '_proof'
        for name, service in config['services'].items():
            service.pop('profiles', None)
            if name in self.ports:
                service['ports'] = [dict(target=8443 if name == 'management' else 8444 if name == 'runtime' else 4000,
                                         published=str(self.ports[name]), host_ip='127.0.0.1', protocol='tcp')]
            for volume in service['volumes']:
                source = Path(volume['source'])
                if source.is_relative_to(PREPARED) and not source.is_relative_to(PREPARED / 'pki'):
                    destination = self.root / source.relative_to(PREPARED)
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if source.is_file() and 'workload.token' not in source.name:
                        shutil.copyfile(source, destination)
                        destination.chmod(0o600)
                    elif source.suffix not in ('.json', '.token'):
                        destination.mkdir(exist_ok=True, mode=0o700)
                    volume['source'] = str(destination)
            if name == 'runtime':
                service['environment']['GUARDRAILS_PROOF_PROFILE'] = 'live' if self.live else 'runtime'
        self.config.write_text(json.dumps(config, indent=2))
        for source in (PREPARED / 'secrets').glob('*.token'):
            if 'workload.token' not in source.name:
                destination = self.root / 'secrets' / source.name
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
        self.load_credentials()
        runtime = self.root / 'live-runtime.json'
        values = json.loads(runtime.read_text())
        values['proof_control_sha256'] = hashlib.sha256(self.tokens['control'].encode()).hexdigest()
        runtime.write_text(json.dumps(values))
    def load_credentials(self):
        for path in (self.root / 'secrets').glob('*.token'):
            self.tokens[path.stem] = path.read_text().strip()
            if os.environ.get('PROOF_ACCEPTANCE_STATE'):
                from proof.privacy import INPUT_FILES, SECRETS
                INPUT_FILES.add(path.resolve())
                SECRETS.add(self.tokens[path.stem])
        self.tokens.update(admin=self.tokens['management-admin'], operator=self.tokens['tenant-operator'])
        for letter in ('a','b'):
            if 'gateway-' + letter + '.workload' in self.tokens:
                self.tokens['gateway-' + letter] = self.tokens['gateway-' + letter + '.workload']
        self.api = API(httpx.Client(base_url=self.url('management'), verify=ssl.create_default_context(cafile=str(PREPARED / 'pki/ca.crt')), trust_env=False, timeout=10), self.tokens)

    def forward(self, service):
        from .ports import LoopbackForwarder
        container = self.compose('ps', '-q', service).stdout.strip()
        inspected = json.loads(subprocess.check_output(['docker','inspect',container]))[0]
        internal_port = 8443 if service == 'management' else 8444 if service == 'runtime' else 4000
        bindings = inspected['NetworkSettings']['Ports'].get(str(internal_port) + '/tcp')
        if not bindings:
            address = inspected['NetworkSettings']['Networks'][self.project + '_proof']['IPAddress']
            self.forwarders[service] = LoopbackForwarder(self.ports[service], (address, internal_port))

    def url(self, service):
        return 'https://localhost:' + str(self.ports[service])

    def headers(self, actor):
        return {'Authorization': 'Bearer ' + self.tokens[actor]}

    def ready(self, service, timeout=90):
        actor = 'admin' if service == 'management' else 'management-service' if service == 'runtime' else service + '.client'
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                response = self.client.get(self.url(service) + '/health/ready', headers=self.headers(actor))
                if response.status_code == 200:
                    return response.json()
            except httpx.TransportError:
                pass
            time.sleep(.2)
        raise AssertionError(service + ' readiness timeout')

    def start(self):
        if self.resuming:
            self.load_credentials()
        else:
            self.prepare()
        self.compose('up', '-d', '--pull', 'never', '--no-build', 'management', 'runtime', 'mock-model')
        self.forward('management')
        self.forward('runtime')
        self.ready('management')
        self.ready('runtime')
        if self.live:
            time.sleep(1.2)
        else:
            self.wait_initial_receipt()
        if not self.resuming:
            self.wait(self.api.request('POST', PREFIX + '/policies', body=policy(), status=201).json())
            for letter in ('a', 'b'):
                name = 'gateway-' + letter
                # Exercise credential issuance through the public CLI, not config seeding.
                self.cli('workloads', 'register', '--tenant', 'local-proof', '--id', name, '--name', name,
                         '--credential-out', str(self.root / 'secrets' / (name + '.workload.token')))
                self.tokens[name] = (self.root / 'secrets' / (name + '.workload.token')).read_text().strip()
                value = self.api.request('PUT', PREFIX + f'/workloads/{name}/binding', body=envelope(policy_id='pii', policy_version='v1', reason='initial_binding'), status=202, create=True).json()
                self.wait(value)
        self.compose('up', '-d', '--pull', 'never', '--no-build', 'gateway-a', 'gateway-b')
        self.forward('gateway-a')
        self.forward('gateway-b')
        self.ready('gateway-a')
        self.ready('gateway-b')
        return self

    def wait_initial_receipt(self):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            value = self.client.get(self.url('runtime') + '/proof/resources', headers=self.headers('control')).json()
            if value.get('acknowledged_generation'):
                return
            time.sleep(.05)
        raise AssertionError('initial runtime receipt missing')

    def cli(self, *args, actor='admin', status=0, extra=()):
        if os.environ.get('PROOF_ACCEPTANCE_STATE'):
            from proof.privacy import INPUT_FILES
            if '--file' in args:
                INPUT_FILES.add(Path(args[args.index('--file')+1]).resolve())
        token = self.root / 'secrets' / ('management-admin.token' if actor == 'admin' else 'tenant-operator.token' if actor == 'operator' else actor + '.token')
        result = subprocess.run([sys.executable, '-m', 'guardrails', '--management-url', self.url('management'),
            '--runtime-url', self.url('runtime'), '--ca-file', str(PREPARED / 'pki/ca.crt'), '--token-file', str(token), *extra, *map(str, args)],
            cwd=ROOT, env=os.environ | {'PYTHONPATH': str(ROOT / 'mvp'), 'PYTHONDONTWRITEBYTECODE': '1'}, text=True, capture_output=True)
        assert result.returncode == status, ('CLI exit', result.returncode, status)
        if os.environ.get('PROOF_ACCEPTANCE_STATE') and result.returncode==0 and '--credential-out' in args:
            from proof.privacy import SECRETS
            issued=Path(args[args.index('--credential-out')+1])
            INPUT_FILES.add(issued.resolve())
            SECRETS.add(issued.read_text().strip())
        assert not any(value in result.stdout + result.stderr for value in self.tokens.values()), 'credential exposed by CLI'
        return json.loads(result.stdout if status == 0 else result.stderr)

    def wait(self, value):
        operation = value['operation']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(self.url('management') + '/v1/operations/' + operation['operation_id'], headers=self.headers('admin'))
            # Applied-state polling is a bounded, retryable read; brief database
            # contention is explicitly a 503. Never retry the committed mutation.
            assert response.status_code in (200, 503), 'operation read rejected'
            if response.status_code == 200:
                result = response.json()
                if result['status'] == 'applied':
                    return result
            time.sleep(.1)
        raise AssertionError('applied generation timeout')

    def binding(self, gateway):
        return self.api.request('GET', PREFIX + f'/workloads/gateway-{gateway}/binding').json()['desired']

    def state(self, state, gateway=None):
        for letter in ((gateway,) if gateway else ('a', 'b')):
            current = self.binding(letter)
            if current['enforcement_state'] == state:
                continue
            value = self.api.request('POST', PREFIX + f'/workloads/gateway-{letter}/binding/state', status=202, revision=current['revision'],
                body=envelope(enforcement_state=state, reason='synthetic_demotion' if ['disabled','observe','warn','redact','block'].index(state) < ['disabled','observe','warn','redact','block'].index(current['enforcement_state']) else 'synthetic_promotion', evidence_kind='synthetic_local_proof', fixture_run_id=self.run_id)).json()
            self.wait(value)

    def bind_policy(self, draft):
        self.wait(self.api.request('POST', PREFIX + '/policies', body=draft, status=201).json())
        for letter in ('a', 'b'):
            value = self.api.request('PUT', PREFIX + f'/workloads/gateway-{letter}/binding', status=202, revision=self.binding(letter)['revision'],
                body=envelope(policy_id='pii', policy_version=draft['version'], reason='policy_update')).json()
            operation = self.wait(value)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if all(self.ready('gateway-' + letter)['client_generation'] >= operation['desired_generation'] for letter in ('a', 'b')):
                return
            time.sleep(.1)
        raise AssertionError('gateway configuration refresh timeout')

    def chat(self, gateway='a', texts=('Synthetic clean gateway input.',), **overrides):
        self.fixture_content.update(texts)
        roles = ('system', 'developer', 'assistant', 'user') if len(texts) == 4 else ('user',) * len(texts)
        body = dict(model='proof-' + gateway, messages=[dict(role=role, content=text) for role, text in zip(roles, texts)]) | overrides
        return self.client.post(self.url('gateway-' + gateway) + '/v1/chat/completions', headers=self.headers('gateway-' + gateway + '.client'), json=body)

    def mock(self, method='GET', path='/proof/calls', body=None):
        # Mock inspection is deliberately unpublished. TLS, authentication and
        # plaintext capture stay inside this short-lived supervised process.
        script = "import sys,json,ssl,httpx;from pathlib import Path;v=json.load(sys.stdin);c=httpx.Client(verify=ssl.create_default_context(cafile='/pki/ca.crt'),trust_env=False);r=c.request(v['method'],'https://mock-model:8445'+v['path'],headers={'Authorization':'Bearer '+Path('/secrets/control.token').read_text().strip()},json=v['body']);assert r.status_code==200;print(r.text)"
        result = subprocess.run(['docker', 'compose', '-p', self.project, '-f', str(self.config), 'exec', '-T', 'mock-model', 'python', '-c', script],
            input=json.dumps(dict(method=method, path=path, body=body)), text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def scenario(self, scenario='clean'):
        self.mock('PUT', '/proof/scenario', {'scenario': scenario})
        self.mock('DELETE')

    def fault(self, worker=None):
        result = self.client.put(self.url('runtime') + '/proof/fault', headers=self.headers('control'), json={'worker': worker})
        assert result.status_code == 200

    def stop(self):
        if self.config.exists():
            for service in ('management', 'runtime', 'mock-model', 'gateway-a', 'gateway-b'):
                log = self.compose('logs', '--no-color', service, check=False)
                (self.root / (service + '.log')).write_text(log.stdout + log.stderr)
            self.compose('down', '--remove-orphans', check=False)
        for forwarder in self.forwarders.values():
            forwarder.close()
        self.client.close()
        if hasattr(self, 'api'):
            self.api.client.close()
        print('Phase 4 isolated state retained:', self.root)
