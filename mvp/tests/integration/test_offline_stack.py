"""Prepared start, TLS, closed routes, egress denial and generated-data privacy."""
import json
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from proof.gateway_test_support import port


@pytest.mark.parametrize('service', ['gateway-a','gateway-b','management','runtime'])
def test_untrusted_tls_rejects_for_every_public_service(gateways, service):
    with httpx.Client(verify=True, trust_env=False, timeout=2) as client:
        with pytest.raises(httpx.ConnectError) as rejected:
            client.get(gateways.url(service) + '/health/live')
        assert isinstance(rejected.value, httpx.ConnectError)


@pytest.mark.parametrize('gateway', ['a','b'])
def test_alternate_routes_overrides_and_wrong_keys_never_invoke(gateways, gateway):
    s = gateways
    s.scenario()
    url = s.url('gateway-' + gateway)
    headers = s.headers('gateway-' + gateway + '.client')
    for path in ('/chat/completions','/v1/completions','/v1/embeddings','/responses','/v1/responses','/key/generate','/ui','/docs'):
        assert s.client.post(url + path, headers=headers, json={}).status_code == 400
    for field,value,status in [('guardrails',[],403),('metadata',{},403),('tools',[],400),('stream',True,400),('n',2,400),('model','openai/direct',400)]:
        assert s.chat(gateway, **{field:value}).status_code == status
    other = 'b' if gateway == 'a' else 'a'
    assert s.client.post(url + '/v1/chat/completions', headers=s.headers('gateway-' + other + '.client'), json={}).status_code == 401
    assert s.mock()['count'] == 0


def test_prepared_network_blocks_reachable_external_sentinel(gateways):
    s = gateways
    for service in ('gateway-a','gateway-b'):
        assert s.ready(service)['external_connect_attempts'] == 0, 'gateway attempted an external dependency'
    network, container = s.project + '-outside', s.project + '-sentinel'
    external_port = port()
    subprocess.run(['docker','network','create',network],check=True,capture_output=True)
    try:
        subprocess.run(['docker','run','-d','--name',container,'--network',network,'--pull','never','-p',f'127.0.0.1:{external_port}:8787',
                        'local/guardrails-proof:phase1','python','-m','http.server','8787'],check=True,capture_output=True)
        deadline = time.monotonic() + 5
        with httpx.Client(trust_env=False) as host:
            while True:
                try:
                    assert host.get(f'http://127.0.0.1:{external_port}').status_code == 200
                    break
                except httpx.TransportError:
                    assert time.monotonic() < deadline
                    time.sleep(.1)
        address = json.loads(subprocess.check_output(['docker','inspect',container]))[0]['NetworkSettings']['Networks'][network]['IPAddress']
        network_state = json.loads(subprocess.check_output(['docker','network','inspect',s.project + '_proof']))[0]
        assert network_state['Internal'] is True
        for service in ('gateway-a','gateway-b','runtime','management','mock-model'):
            script = "import socket,sys\ntry:\n s=socket.create_connection((sys.argv[1],8787),.5)\nexcept OSError:\n print('egress_denied')\nelse:\n s.close();raise SystemExit(1)"
            result = s.compose('exec','-T',service,'python','-c',script,address)
            assert result.stdout.strip() == 'egress_denied'
        config = json.loads(s.config.read_text())
        assert all(service['pull_policy'] == 'never' for service in config['services'].values())
        assert 'ports' not in config['services']['mock-model']
        for service in config['services'].values():
            assert all(binding['host_ip'] == '127.0.0.1' for binding in service.get('ports', []))
        for name in ('gateway-a','gateway-b'):
            targets = [volume['target'] for volume in config['services'][name]['volumes']]
            assert '/state' not in targets and '/live-state' not in targets
        (s.root / 'offline-evidence.json').write_text(json.dumps({'internal_network':True,'reachable_sentinel':True,'denied_services':list(config['services']),'prepared_pull_never':True}))
    finally:
        subprocess.run(['docker','rm','-f',container],check=False,capture_output=True)
        subprocess.run(['docker','network','rm',network],check=False,capture_output=True)


def test_default_persistence_and_service_logs_contain_no_traffic_or_tokens(gateways):
    from proof.mock_model import SCENARIOS
    s = gateways
    prohibited = [b'alice@example.test', b'proof@example.test', b'+15551234567']
    prohibited += [path.read_bytes().strip() for path in (s.root / 'secrets').glob('*.token')]
    # Scan actual submitted text and deterministic output, including clean text.
    # Versioned corpus source and credential input files are not observations.
    prohibited += [value.encode() for value in s.fixture_content | set(SCENARIOS.values()) if value]
    for path in (s.root / 'state').rglob('*'):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        assert not any(value in raw for value in prohibited), 'raw traffic or credential in generated persistence'
    for service in ('management','runtime','gateway-a','gateway-b','mock-model'):
        result = s.compose('logs','--no-color',service)
        raw = result.stdout.encode() + result.stderr.encode()
        assert not any(value in raw for value in prohibited), 'raw traffic or credential in service log'
        (s.root / (service + '.log')).write_bytes(raw)
    for path in (s.root / 'state').rglob('*.sqlite3'):
        with sqlite3.connect(path) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert s.mock('DELETE')['cleared']
    assert s.mock()['count'] == 0 and s.mock()['calls'] == []


def test_hostname_verification_is_required(gateways):
    import ssl
    from proof.gateway_test_support import PREPARED
    context = ssl.create_default_context(cafile=str(PREPARED / 'pki/ca.crt'))
    with socket.create_connection(('127.0.0.1', gateways.ports['gateway-a'])) as raw:
        with pytest.raises(ssl.SSLCertVerificationError):
            context.wrap_socket(raw, server_hostname='not-a-proof-service')


def test_documented_launcher_preserves_restart_and_resets_only_selected_run():
    import ssl
    from proof.gateway_test_support import PREPARED, ROOT
    pointer = PREPARED / 'live.json'
    # Never repurpose an operator's active run for a test.
    if pointer.exists() and json.loads(pointer.read_text()).get('status') in ('ready','starting'):
        pytest.fail('active operator stack must be stopped before lifecycle gate')
    def command(name):
        result = subprocess.run(['just',name],cwd=ROOT,text=True,capture_output=True)
        assert result.returncode == 0, ('lifecycle command failed',name,result.returncode)
    try:
        command('proof-up')
        original = json.loads(pointer.read_text())
        root = Path(original['root'])
        secret = (root / 'secrets/gateway-a.workload.token').read_bytes()
        with httpx.Client(verify=ssl.create_default_context(cafile=str(PREPARED / 'pki/ca.crt')),trust_env=False) as client:
            headers={'Authorization':'Bearer '+(root/'secrets/management-admin.token').read_text().strip()}
            response=client.get('https://localhost:8443/v1/tenants/local-proof/workloads',headers=headers)
            assert response.status_code == 200 and len(response.json()['items']) == 2
            # No fault control is enabled by ordinary live startup.
            assert client.put('https://localhost:8444/proof/fault',json={'worker':'stall'}).status_code == 404
        command('proof-down')
        assert root.is_dir() and (root/'state/management/control.sqlite3').is_file()
        command('proof-up')
        assert json.loads(pointer.read_text())['root'] == str(root)
        assert (root / 'secrets/gateway-a.workload.token').read_bytes() == secret
        command('proof-reset')
        assert not pointer.exists() and root.is_dir()
        command('proof-up')
        replacement = Path(json.loads(pointer.read_text())['root'])
        assert replacement != root
        assert (replacement / 'secrets/gateway-a.workload.token').read_bytes() != secret
    finally:
        command('proof-down')
