"""CLI routes the full public lifecycle through the same strict APIs."""
import json
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest
from guardrails.cli import main, parser, route
from proof.management_test_support import PREFIX, policy


@pytest.fixture
def cli(management, tmp_path, capsys):
    token = tmp_path / 'admin.token'
    token.write_text(management.tokens['admin'])
    token.chmod(0o600)
    original = httpx.Client
    def handle(request):
        response = management.client.request(request.method, request.url.raw_path.decode(), content=request.content, headers=request.headers)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)
    def factory(**kwargs):
        kwargs['verify'] = True
        return original(transport=httpx.MockTransport(handle), **kwargs)
    def invoke(*args, status=0):
        with patch('httpx.Client', factory), patch('ssl.create_default_context', return_value=True):
            result = main(['--management-url', 'https://management', '--token-file', str(token), '--ca-file', 'fixture', '--no-wait', *map(str,args)])
        output = capsys.readouterr()
        assert result == status
        assert management.tokens['admin'] not in output.out + output.err
        return json.loads(output.out if status == 0 else output.err)
    return invoke


def test_cli_public_lifecycle_credentials_and_conflicts(cli, tmp_path, management):
    target = tmp_path / 'workload.token'
    issued = cli('workloads', 'register', '--tenant', 'local-proof', '--id', 'cli-team', '--name', 'CLI team', '--credential-out', target)
    assert 'token' not in issued and issued['operation']['status'] == 'pending'
    assert target.stat().st_mode & 0o777 == 0o600
    token = target.read_text()
    cli('workloads', 'register', '--tenant', 'local-proof', '--id', 'unused', '--name', 'unused', '--credential-out', target, status=4)
    assert target.read_text() == token
    assert len(cli('workloads', 'list', '--tenant', 'local-proof')['items']) == 1
    draft = tmp_path / 'policy.json'
    draft.write_text(json.dumps(policy()))
    assert cli('policies', 'validate', '--tenant', 'local-proof', '--file', draft)['valid']
    first = cli('policies', 'publish', '--tenant', 'local-proof', '--file', draft)
    assert cli('policies', 'get', '--tenant', 'local-proof', '--id', 'pii', '--version', 'v1')['digest'] == first['policy']['digest']
    assert cli('policies', 'publish', '--tenant', 'local-proof', '--file', draft)['policy']['digest'] == first['policy']['digest']
    draft.write_text(json.dumps(policy(retain_content=True)))
    cli('policies', 'publish', '--tenant', 'local-proof', '--file', draft, status=4)
    binding = cli('bindings', 'set', '--tenant', 'local-proof', '--workload', 'cli-team', '--policy', 'pii', '--version', 'v1', '--reason', 'initial_binding')
    assert binding['resource']['enforcement_state'] == 'observe'
    assert cli('bindings', 'get', '--tenant', 'local-proof', '--workload', 'cli-team')['desired']['revision'] == 1


@pytest.mark.parametrize('change', [{'retain_content': 'false'}, {'unknown': True}, {'checks': []}])
def test_cli_matches_api_schema_rejection(cli, management, tmp_path, change):
    value = policy() | change
    path = tmp_path / 'invalid.json'
    path.write_text(json.dumps(value))
    direct = management.request('POST', PREFIX + '/policies/validate', body=value, status=422).json()
    result = cli('policies', 'validate', '--tenant', 'local-proof', '--file', path, status=2)
    assert result['code'] == direct['code']


def test_help_and_parser_never_require_network_or_echo_invalid_values(capsys):
    with pytest.raises(SystemExit) as result:
        main(['bindings', 'state', '--help'])
    assert result.value.code == 0
    assert main(['--token', 'secret-never-echo']) == 2
    assert 'secret-never-echo' not in capsys.readouterr().err


@pytest.mark.parametrize('args,method,path', [
    (['workloads','rotate-credential','--tenant','t','--id','w','--revision','2','--reason','credential_rotation','--credential-out','new'], 'POST','/v1/tenants/t/workloads/w/credentials/rotate'),
    (['bindings','rollback','--tenant','t','--workload','w','--revision','2','--to-revision','1','--reason','rollback'],'POST','/v1/tenants/t/workloads/w/binding/rollback'),
    (['bindings','remove','--tenant','t','--workload','w','--revision','2','--reason','binding_removal'],'DELETE','/v1/tenants/t/workloads/w/binding'),
    (['floors','get','--scope','tenant','--scope-id','t'],'GET','/v1/governance/floors/tenant/t'),
    (['audit','list','--tenant','t','--include-content','--allow-partial'],'GET','/v1/audit'),
    (['capabilities'],'GET','/v1/capabilities'),
])
def test_cli_route_and_revision_contract(args, method, path):
    actual = route(parser().parse_args(args))
    assert actual[:2] == (method, path)
    if '--revision' in args:
        assert actual[3]['If-Match'] == '2'
