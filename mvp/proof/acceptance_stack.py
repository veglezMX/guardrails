"""Offline runner's real-service adapter and narrowly scoped host fault control.

The runner has neither a Docker socket nor an external network. The supervisor
owns Docker; a local Unix socket exposes fixed operations on this run only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import subprocess
import threading

import httpx

from .gateway_test_support import GatewayStack, PREPARED, ROOT
from .management_test_support import PREFIX, envelope, policy

SERVICES = ('management', 'runtime', 'mock-model', 'gateway-a', 'gateway-b')


def rpc(method, **values):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(120)
        client.connect(os.environ['PROOF_CONTROL_SOCKET'])
        stream = client.makefile('rwb')
        stream.write(json.dumps(dict(method=method, **values)).encode() + b'\n')
        stream.flush()
        raw = stream.readline(16 * 1024 * 1024)
        result = json.loads(raw)
        if result.get('error'):
            raise RuntimeError('supervisor_operation_failed')
        return result


class Supervisor:
    def __init__(self, stack, sentinel_address):
        self.stack = stack
        self.sentinel_address = sentinel_address
        self.path = '.proof/control-' + stack.run_id[:8] + '.sock'
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    value = json.loads(self.rfile.readline(8192))
                    result = owner.handle(value)
                except Exception:
                    result = {'error': 'supervisor_operation_failed'}
                self.wfile.write(json.dumps(result).encode() + b'\n')

        self.server = socketserver.ThreadingUnixStreamServer(self.path, Handler)
        Path(self.path).chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def handle(self, value):
        method = value['method']
        if method == 'compose':
            args = value['args']
            # No exec, build, pull, config replacement, arbitrary project or host command.
            valid = (len(args) == 2 and args[0] in ('stop', 'start', 'restart', 'pause', 'unpause') and args[1] in SERVICES)
            valid |= len(args) == 4 and args[:3] == ['stop', '-t', '0'] and args[3] in SERVICES
            valid |= len(args) == 3 and args[:2] == ['logs', '--no-color'] and args[2] in SERVICES
            valid |= args == ['up', '-d', '--pull', 'never', '--no-build', 'gateway-a', 'gateway-b']
            if not valid:
                raise ValueError('unsupported_supervisor_command')
            result = self.stack.compose(*args, check=False)
            return dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        if method == 'network':
            network = json.loads(subprocess.check_output(['docker', 'network', 'inspect', self.stack.project + '_proof']))[0]
            identities = {}
            for service in SERVICES:
                container = self.stack.compose('ps', '-q', service).stdout.strip()
                item = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
                identities[service] = dict(container_id=item['Id'], image_id=item['Image'],
                                           networks=sorted(item['NetworkSettings']['Networks']), running=item['State']['Running'])
            runner=json.loads(subprocess.check_output(['docker','inspect',self.stack.project+'-runner']))[0]
            runner_identity=dict(container_id=runner['Id'],image_id=runner['Image'],
                networks=sorted(runner['NetworkSettings']['Networks']),running=runner['State']['Running'])
            return dict(internal=network['Internal'], services=identities, runner=runner_identity, sentinel_address=self.sentinel_address)
        if method == 'probe':
            service = value['service']
            if service not in SERVICES:
                raise ValueError('invalid_service')
            script = "import socket,sys\ntry:\n s=socket.create_connection((sys.argv[1],8787),.5)\nexcept OSError:\n print('egress_denied')\nelse:\n s.close();raise SystemExit(1)"
            result = self.stack.compose('exec', '-T', service, 'python', '-c', script, self.sentinel_address, check=False)
            return dict(denied=result.returncode == 0 and result.stdout.strip() == 'egress_denied')
        raise ValueError('unsupported_supervisor_operation')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        Path(self.path).unlink(missing_ok=True)


class AcceptanceStack(GatewayStack):
    def __init__(self):
        self.root = Path(os.environ['PROOF_ACCEPTANCE_STATE'])
        self.run_id = self.root.name
        self.project = 'guardrails-p5-' + self.run_id[:8]
        self.config = self.root / 'compose.json'
        self.tokens, self.fixture_content = {}, set()
        self.forwarders = {}
        self.client = httpx.Client(verify=ssl.create_default_context(cafile=str(PREPARED / 'pki/ca.crt')), trust_env=False, timeout=20)

    def url(self, service):
        return 'https://' + service + ':' + str(8443 if service == 'management' else 8444 if service == 'runtime' else 8445 if service == 'mock-model' else 4000)

    def compose(self, *args, check=True):
        value = rpc('compose', args=list(args))
        result = subprocess.CompletedProcess(args, value['returncode'], value['stdout'], value['stderr'])
        if check:
            result.check_returncode()
        return result

    def start(self):
        self.load_credentials()
        self.ready('management')
        self.ready('runtime')
        self.wait_initial_receipt()
        self.wait(self.api.request('POST', PREFIX + '/policies', body=policy(), status=201).json())
        for letter in ('a', 'b'):
            name = 'gateway-' + letter
            token = self.root / 'secrets' / (name + '.workload.token')
            self.cli('workloads', 'register', '--tenant', 'local-proof', '--id', name, '--name', name, '--credential-out', token)
            self.tokens[name] = token.read_text().strip()
            self.wait(self.api.request('PUT', PREFIX + f'/workloads/{name}/binding',
                body=envelope(policy_id='pii', policy_version='v1', reason='initial_binding'), status=202, create=True).json())
        self.compose('up', '-d', '--pull', 'never', '--no-build', 'gateway-a', 'gateway-b')
        self.ready('gateway-a')
        self.ready('gateway-b')
        return self

    def mock(self, method='GET', path='/proof/calls', body=None):
        response = self.client.request(method, self.url('mock-model') + path, headers=self.headers('control'), json=body)
        assert response.status_code == 200, 'mock_control_failed'
        return response.json()

    def stop(self):
        self.client.close()
        if hasattr(self, 'api'):
            self.api.client.close()
