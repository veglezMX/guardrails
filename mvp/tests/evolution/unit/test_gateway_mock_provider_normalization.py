"""Portable mock safe-view normalization with the actual gateway observer.

Synthetic public provider records replace the retained private reproduction.
Only the HTTP boundary is fake; the original ProviderProfile and digest checks
run inside the source's complete observed_sample function before any POST.
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from guardrails.evolution import models


class RequestBoundaryReached(Exception):
    pass


def fixture(tenant):
    provider = models.with_resource_digest(models.ProviderProfile(
        tenant_id=tenant, created_at='2026-10-12T00:00:00Z',
        provider_id="00000000-0000-0000-0000-000000000001", kind='model', vendor='ollama',
        model_id='synthetic-mock', destination_id="00000000-0000-0000-0000-000000000002", zone='local-shared'))
    approved = provider.model_dump(mode='json')
    safe_view = {key: value for key, value in approved.items() if key != 'credential_ref'}
    return {'profiles': {'mock': {'provider': safe_view}}}, {'providers': [approved]}


def observed():
    path = Path(__file__).resolve().parents[1] / 'e2e/test_cluster_evaluator_gateways.py'
    tree = ast.parse(path.read_bytes(), filename=str(path))
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'observed_sample']
    assert len(body) == 1
    namespace = {'models': models, 'uuid4': uuid4,
        'sample': lambda *_args, **_kwargs: ('synthetic prompt', 'clean', 'synthetic output')}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['observed_sample']


def invoke(tenant, item, policy):
    calls = []
    def post(*args, **kwargs):
        calls.append((args, kwargs))
        raise RequestBoundaryReached
    cluster = SimpleNamespace(inventory={'tenants': {tenant: item}},
        access={'tenants': {tenant: {'gateway_url': 'https://localhost:8443',
            'gateway_token_file': '/never/read', 'model_alias': 'synthetic-alias'}}},
        scenario=lambda _: None, calls=lambda: {'count': 0, 'calls': []},
        http=SimpleNamespace(post=post), headers=lambda *_args, **_kwargs: {})
    try:
        observed()(cluster, tenant, policy, 1, 'pii', 'input', 'observe', clean=True)
    except RequestBoundaryReached:
        return calls
    return calls


@pytest.mark.parametrize('tenant', ('team-a', 'team-b'))
def test_safe_view_null_omission_differs_only_in_representation(tenant):
    item, policy = fixture(tenant)
    provider = item['profiles']['mock']['provider']
    assert provider not in policy['providers']
    normalized = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(provider)).model_dump(mode='json')
    assert normalized['credential_ref'] is None and normalized in policy['providers']
    assert {key: value for key, value in normalized.items() if key != 'credential_ref'} == provider


@pytest.mark.parametrize('tenant', ('team-a', 'team-b'))
@pytest.mark.parametrize('representation', ('omitted', 'explicit_null'))
def test_normalized_exact_provider_reaches_only_fake_request_boundary(tenant, representation):
    item, policy = fixture(tenant)
    if representation == 'explicit_null': item['profiles']['mock']['provider']['credential_ref'] = None
    calls = invoke(tenant, item, policy)
    assert len(calls) == 1 and calls[0][1]['json']['model'] == 'synthetic-alias'


def rehash(provider):
    value = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(provider))
    provider['digest'] = models.resource_digest(value)


@pytest.mark.parametrize('tenant', ('team-a', 'team-b'))
def test_nonnull_credential_cannot_be_admitted_even_if_approved_and_rehashed(tenant):
    item, policy = fixture(tenant)
    provider = item['profiles']['mock']['provider']
    provider['credential_ref'] = 'synthetic-private-reference'; rehash(provider)
    policy['providers'] = [copy.deepcopy(provider)]
    with pytest.raises(AssertionError): invoke(tenant, item, policy)


@pytest.mark.parametrize('field,value', (
    ('destination_id', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    ('provider_id', 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
    ('tenant_id', 'other-tenant'), ('version', 99), ('model_id', 'different-model'),
    ('artifact_digest', 'a' * 64), ('enabled', False), ('vendor', 'bedrock'), ('zone', 'different-zone'),
), ids=('destination', 'provider', 'tenant', 'version', 'model', 'artifact', 'enabled', 'vendor', 'zone'))
def test_changed_valid_resource_cannot_replace_exact_approved_identity(field, value):
    item, policy = fixture('team-a')
    provider = item['profiles']['mock']['provider']; provider[field] = value; rehash(provider)
    with pytest.raises(AssertionError): invoke('team-a', item, policy)


def test_forged_digest_refuses_even_when_raw_policy_membership_would_match():
    item, policy = fixture('team-a')
    provider = item['profiles']['mock']['provider']; provider['digest'] = 'f' * 64
    policy['providers'] = [{**provider, 'credential_ref': None}]
    with pytest.raises(AssertionError): invoke('team-a', item, policy)


def test_unknown_field_is_refused_by_the_original_model():
    item, policy = fixture('team-a')
    item['profiles']['mock']['provider']['unapproved_extra'] = True
    with pytest.raises(ValueError): invoke('team-a', item, policy)
