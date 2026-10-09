"""Configured failure behavior, durable replay and request-local pinning."""
import asyncio
from dataclasses import replace
import json
import ssl
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from connector.core import RuntimeClient, RuntimeClientError
from connector.gateway_state import GatewayTurn
from connector.spool import EventSpool
from proof.runtime_test_support import components, request_body


def client(tmp_path, failures=None):
    value = object.__new__(RuntimeClient)
    value._origin, value._url = 'https://runtime', 'https://runtime/v1/evaluate'
    value._tls, value._token = ssl.create_default_context(), 'synthetic-token'
    value.identity = ('local-proof', 'team-a')
    value.config = components(failures=failures)[2]
    value.spool = EventSpool(tmp_path / 'spool/events.jsonl')
    value.spool_failed, value.degraded, value._task = False, False, None
    return value


@pytest.mark.parametrize('code,field', [('evaluation_unavailable', 'evaluation_unavailable'), ('evaluation_timeout', 'evaluation_timeout'),
                                      ('unsafe_transformation', 'transformation_failure'), ('context_invalid', 'evaluation_unavailable'), ('audit_write_failed', 'audit_failure')])
@pytest.mark.parametrize('behavior', ['block', 'allow_and_log'])
def test_explicit_fallback_is_content_free_and_durable(tmp_path, code, field, behavior):
    value = client(tmp_path, {field: behavior})
    body = request_body('secret@example.test')
    result = asyncio.run(value.fallback(body, value.config, code))
    assert result['action'] == ('block' if behavior == 'block' else 'log_only')
    assert result['source'] == 'connector' and result['evaluation_id'] is None
    assert result['audit_status'] == 'spooled'
    assert result['audit_id'] is None and result['context_id'] is None
    assert 'secret@' not in value.spool.path.read_text()
    assert value.spool.path.stat().st_mode & 0o777 == 0o600
    assert EventSpool(value.spool.path).batch()[0]['metadata']['decision_id'] == result['decision_id']


def test_full_spool_reports_failed_and_never_weakens_audit_policy(tmp_path):
    value = client(tmp_path, {'evaluation_unavailable': 'allow_and_log'})
    value.spool.max_bytes = 1
    result = asyncio.run(value.fallback(request_body(), value.config, 'evaluation_unavailable'))
    assert result['action'] == 'block' and result['audit_status'] == 'failed'
    assert value.spool_failed and value.spool.path.read_bytes() == b''


def test_replay_keeps_unacknowledged_and_ids_across_retries(tmp_path):
    async def check():
        value = client(tmp_path)
        for _ in range(3):
            await value.fallback(request_body(), value.config, 'evaluation_unavailable')
        original = value.spool.batch()
        async def fail(*args, **kwargs):
            raise TimeoutError()
        value._request = fail
        await value.replay()
        assert value.spool.batch() == original
        async def accept(*args, body=None):
            return {'accepted_event_ids': [body[0]['event_id']]}
        value._request = accept
        await value.replay()
        assert value.spool.batch() == original[1:]
        # An event appended during network I/O is never removed by old receipts.
        value.spool.append(original[0])
        await value.replay()
        assert len(value.spool.batch()) == 2
    asyncio.run(check())


def test_spool_rejects_content_and_retains_corrupt_rows(tmp_path):
    value = client(tmp_path)
    asyncio.run(value.fallback(request_body(), value.config, 'evaluation_timeout'))
    event = value.spool.batch()[0]
    event['content'] = {'segments': [{'segment_id': '0', 'text': 'secret'}]}
    with pytest.raises(Exception):
        value.spool.append(event)
    with value.spool.path.open('ab') as stream:
        stream.write(b'{incomplete')
    before = value.spool.path.read_bytes()
    asyncio.run(value.replay())
    assert value.spool_failed and value.spool.path.read_bytes() == before


def test_refresh_never_replaces_good_config_with_foreign_or_older(tmp_path):
    async def check():
        value = client(tmp_path)
        original = value.config
        for changes in ({'generation': 1}, {'workload_id': 'foreign'}, {'extra': 'invalid'}):
            async def response(*args, **kwargs):
                return original.to_wire() | changes
            value._request = response
            await value.refresh()
            assert value.config is original and value.degraded
        async def newer(*args, **kwargs):
            return original.to_wire() | {'generation': 6}
        value._request = newer
        await value.refresh()
        assert value.config.generation == 6 and not value.degraded
    asyncio.run(check())


def test_unconfigured_never_calls_evaluate(tmp_path):
    value = client(tmp_path)
    value.config = None
    with pytest.raises(RuntimeClientError, match='client_unconfigured'):
        asyncio.run(value.evaluate_turn(request_body(), GatewayTurn(str(uuid4()))))


def test_unpinned_output_uses_input_fallback_configuration_after_refresh(tmp_path):
    async def check():
        value = client(tmp_path, {'evaluation_unavailable': 'allow_and_log'})
        turn = GatewayTurn(str(uuid4()))
        body = request_body()
        calls = []
        async def fail(body, timeout):
            calls.append(body)
            raise RuntimeClientError('evaluation_unavailable')
        value.evaluate = fail
        before = await value.evaluate_turn(body, turn)
        turn.input_decision = before
        value.config = replace(value.config, failure_behavior=components()[2].failure_behavior, generation=6)
        after = await value.evaluate_turn(body | {'stage': 'output'}, turn)
        assert before['action'] == after['action'] == 'log_only'
        assert len(calls) == 1 and after['source'] == 'connector'
        assert after['operational_codes'] == ['context_invalid']
    asyncio.run(check())


@pytest.mark.parametrize('status,code', [(401, 'authentication_failed'), (403, 'identity_mismatch'), (400, 'invalid_request'), (413, 'invalid_request'), (422, 'invalid_request')])
def test_authentication_and_caller_rejections_cannot_fail_open(tmp_path, status, code):
    async def check():
        value = client(tmp_path, {'evaluation_unavailable': 'allow_and_log'})
        original = httpx.AsyncClient
        def transport(**kwargs):
            return original(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={'error':'rejected'})), **kwargs)
        with patch('httpx.AsyncClient', transport):
            with pytest.raises(RuntimeClientError, match=code):
                await value.evaluate_turn(request_body(), GatewayTurn(str(uuid4())))
        assert not value.spool.batch()
    asyncio.run(check())


def test_spool_batches_at_most_one_hundred(tmp_path):
    value = client(tmp_path)
    asyncio.run(value.fallback(request_body(), value.config, 'evaluation_unavailable'))
    event = value.spool.batch()[0]
    for _ in range(101):
        value.spool.append(event | {'event_id': str(uuid4())})
    assert len(value.spool.batch()) == 100


@pytest.mark.parametrize('action', ['block','redact'])
def test_audit_spool_records_the_retained_restrictive_action(tmp_path, action):
    from guardrails.engine import BatchDecisionEngine
    from proof.runtime_test_support import evaluation
    value = client(tmp_path, {'audit_failure':'allow_and_log'})
    binding, policy_value, config = components(state=action, failures={'audit_failure':'allow_and_log'})
    # A prior validated restriction is supplied; the spool strips content/findings.
    previous = dict(policy_id='pii',policy_version='v1',policy_digest=policy_value.digest,binding_revision=2,
                    snapshot_generation=5,enforcement_state=action,action=action,
                    transformed_content={'segments':[{'segment_id':'0','text':'[REDACTED]'}]} if action == 'redact' else None)
    result = asyncio.run(value.fallback(request_body(), config, 'audit_write_failed', prior=previous))
    assert result['action'] == action
    event = value.spool.batch()[0]
    assert event['metadata']['action'] == action and event['content'] is None
    assert event['metadata']['findings'] == []


@pytest.mark.parametrize('message,choices,accepted', [
    ({'role':'assistant','content':'clean'},1,True),
    ({'role':'assistant','content':'','refusal':'synthetic refusal'},1,False),
    ({'role':'assistant','content':'','tool_calls':[]},1,False),
    ({'role':'assistant','content':[]},1,False),
    ({'role':'user','content':'text'},1,False),
    ({'role':'assistant','content':'clean'},2,False),
])
def test_raw_model_response_is_checked_before_sdk_normalization(message, choices, accepted):
    from connector.model_response import validate_model_response
    from connector.gateway_state import current_turn
    async def check():
        turn = GatewayTurn(str(uuid4()))
        scope = current_turn.set(turn)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'choices':[{'message':message}]*choices})),
                event_hooks={'response':[validate_model_response]}) as transport:
                if accepted:
                    response = await transport.get('https://mock/v1/chat/completions')
                    assert response.json()['choices'][0]['message'] == message
                    assert turn.error is None
                else:
                    with pytest.raises(ValueError,match='unsupported_model_response'):
                        await transport.get('https://mock/v1/chat/completions')
                    assert turn.error['code'] == 'unsupported_surface'
                    assert 'synthetic refusal' not in json.dumps(turn.error)
        finally:
            current_turn.reset(scope)
    asyncio.run(check())


@pytest.mark.parametrize('transformed', [None, {'segments':[{'segment_id':'foreign','text':'x'}]}, {'segments':[]}])
def test_invalid_redaction_uses_transformation_action_not_general_unavailability(tmp_path, transformed):
    async def check():
        value = client(tmp_path, {'evaluation_unavailable':'allow_and_log','transformation_failure':'block'})
        body = request_body()
        wire = await value.fallback(body, value.config, 'evaluation_unavailable')
        wire.update(source='runtime', evaluation_id=str(uuid4()), action='redact', allowed=True,
                    transformed_content=transformed, context_id='pinned-context', audit_status='persisted', audit_id=str(uuid4()))
        original = httpx.AsyncClient
        def transport(**kwargs):
            return original(transport=httpx.MockTransport(lambda request: httpx.Response(200,json=wire)), **kwargs)
        with patch('httpx.AsyncClient', transport):
            result = await value.evaluate_turn(body, GatewayTurn(body['correlation_id']))
        assert result['source'] == 'connector' and result['action'] == 'block'
        assert result['operational_codes'] == ['unsafe_transformation']
    asyncio.run(check())


def test_connection_accounting_survives_peer_ip_changes_without_dns(monkeypatch):
    from connector import network_audit
    hooks = []
    monkeypatch.setattr(network_audit, '_installed', False)
    monkeypatch.setattr(network_audit, 'external_connect_attempts', 0)
    monkeypatch.setattr(network_audit.sys, 'addaudithook', hooks.append)
    monkeypatch.setattr(network_audit.socket, 'getaddrinfo', lambda *args: pytest.fail('startup must not depend on peer DNS'))
    routes = 'Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\neth0 000014AC 00000000 0001 0 0 0 0000FFFF 0 0 0\neth0 00000000 010014AC 0003 0 0 0 00000000 0 0 0\n'
    monkeypatch.setattr(network_audit.Path, 'read_text', lambda self: routes)
    network_audit.install()
    for peer in ('172.20.0.3','172.20.0.8','127.0.0.1','::1'):
        hooks[0]('socket.connect', (None, (peer, 8444)))
    assert network_audit.external_connect_attempts == 0
    for peer in ('172.21.0.3','198.51.100.1'):
        hooks[0]('socket.connect', (None, (peer, 443)))
    assert network_audit.external_connect_attempts == 2


@pytest.mark.parametrize('code', ['context_invalid','context_expired'])
def test_runtime_context_problem_uses_pinned_output_fallback(tmp_path, code):
    from guardrails.transport import ContractProblem, problem_document
    async def check():
        value = client(tmp_path, {'evaluation_unavailable':'allow_and_log'})
        body = request_body() | {'stage':'output','context_id':'previous-runtime-context'}
        turn = GatewayTurn(body['correlation_id'])
        turn.context_id, turn.client_config = body['context_id'], value.config
        original = httpx.AsyncClient
        def transport(**kwargs):
            return original(transport=httpx.MockTransport(lambda request: httpx.Response(422,
                json=problem_document(ContractProblem(code,422),uuid4()), headers={'content-type':'application/problem+json'})), **kwargs)
        with patch('httpx.AsyncClient', transport):
            result = await value.evaluate_turn(body, turn)
        assert result['source'] == 'connector' and result['action'] == 'log_only'
        assert result['operational_codes'] == ['context_invalid']
        assert result['policy_id'] is None and result['evaluation_id'] is None
    asyncio.run(check())
