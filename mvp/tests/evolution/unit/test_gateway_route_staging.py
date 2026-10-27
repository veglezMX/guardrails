"""Governed route staging with closed synthetic Kubernetes/process/TLS ports.

No test here opens a socket or starts kubectl, Docker, an application or model.
The actual route builder/recovery state machine runs against explicit fake ports.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from guardrails.evolution import models
from guardrails.evolution.compiler import PolicyCompiler
from test_evaluator_governance import base as base_fixture
from test_evaluator_governance import uid

ROOT = Path(__file__).resolve().parents[4]
base = base_fixture


def load(name):
    spec = importlib.util.spec_from_file_location('route_staging_fake_' + name,
                                                ROOT / 'deploy/evolution' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def subject():
    return load('gateway_routes')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = value if isinstance(value, bytes) else models.canonical_json_bytes(value) + b'\n'
    path.write_bytes(raw)
    path.chmod(0o600)


@pytest.fixture
def policies(base):
    old = models.PolicyVersion.model_validate_json(models.canonical_json_bytes(base))
    candidate = old.model_copy(update={'policy_id': 'matrix', 'status': 'candidate', 'compiled_digest': '0' * 64})
    new = PolicyCompiler().compile(candidate).model_copy(update={'status': 'approved'}).model_dump(mode='json')
    route = {'policy': {'tenant_id': 'team-a', 'policy_id': base['policy_id'], 'version': base['version'],
                       'digest': base['compiled_digest']}, 'provider': base['providers'][0],
             'destination': base['resolved_destinations'][0], 'external_processing': False}
    manifest = {'tenant_id': 'team-a', 'workload_id': 'chat', 'routes': [route],
                'ca_files': {'local-ca': '/config/local-ca.crt'}, 'local_services': {}}
    return base, new, manifest


def build(subject, policies):
    old, new, manifest = policies
    return subject.build_manifest(models.canonical_json_bytes(manifest), tenant='team-a', workload='chat',
        policy=new, original_policy=old, provider=old['providers'][0], destination=old['resolved_destinations'][0])


def test_manifest_preserves_old_route_and_exact_resources(subject, policies):
    old, new, manifest = policies
    before = copy.deepcopy(manifest)
    result = json.loads(build(subject, policies))
    assert result['routes'][0] == before['routes'][0]
    assert result['routes'][1] == {**before['routes'][0], 'policy': subject.policy_reference(new)}
    assert {key: result[key] for key in result if key != 'routes'} == {key: before[key] for key in before if key != 'routes'}
    assert manifest == before


@pytest.mark.parametrize('manifest_omits_default', [False, True])
@pytest.mark.parametrize('inventory_omits_default', [False, True])
def test_manifest_accepts_serialized_seed_defaults_without_changing_route_bytes(
        subject, policies, manifest_omits_default, inventory_omits_default):
    old, new, manifest = copy.deepcopy(policies)
    provider = copy.deepcopy(old['providers'][0])
    # The retained deployed seed and inventory omit credential_ref, while
    # management policy responses/model dumps include its None default.
    assert provider['credential_ref'] is None
    if manifest_omits_default:
        del manifest['routes'][0]['provider']['credential_ref']
    if inventory_omits_default:
        del provider['credential_ref']
    before = copy.deepcopy((old, new, manifest, provider))
    result = json.loads(subject.build_manifest(models.canonical_json_bytes(manifest),
        tenant='team-a', workload='chat', policy=new, original_policy=old,
        provider=provider, destination=old['resolved_destinations'][0]))
    assert result['routes'] == [manifest['routes'][0],
        {**manifest['routes'][0], 'policy': subject.policy_reference(new)}]
    assert {key: result[key] for key in result if key != 'routes'} == {
        key: manifest[key] for key in manifest if key != 'routes'}
    assert (old, new, manifest, provider) == before


@pytest.mark.parametrize('fault', ['provider_model', 'provider_version', 'provider_digest',
    'provider_credential', 'provider_extra', 'destination_host', 'destination_digest',
    'destination_cidrs', 'destination_extra'])
def test_serialized_seed_defaults_do_not_weaken_exact_selected_resources(subject, policies, fault):
    old, new, manifest = copy.deepcopy(policies)
    provider = copy.deepcopy(old['providers'][0])
    destination = copy.deepcopy(old['resolved_destinations'][0])
    del manifest['routes'][0]['provider']['credential_ref']
    del provider['credential_ref']
    if fault == 'provider_model': provider['model_id'] = 'different'
    if fault == 'provider_version': provider['version'] += 1
    if fault == 'provider_digest': provider['digest'] = '0' * 64
    if fault == 'provider_credential': provider['credential_ref'] = 'different-credential'
    if fault == 'provider_extra': provider['unknown_field'] = 'not-allowed'
    if fault == 'destination_host': destination['host'] = 'different.gr-model.svc.cluster.local'
    if fault == 'destination_digest': destination['digest'] = '0' * 64
    if fault == 'destination_cidrs': destination['cidrs'] = ['10.96.0.99/32']
    if fault == 'destination_extra': destination['unknown_field'] = 'not-allowed'
    with pytest.raises(Exception):
        subject.build_manifest(models.canonical_json_bytes(manifest), tenant='team-a', workload='chat',
            policy=new, original_policy=old, provider=provider, destination=destination)


@pytest.mark.parametrize('field', ['provider', 'destination'])
def test_serialized_matching_route_still_requires_both_approved_policies(subject, policies, field):
    old, new, manifest = copy.deepcopy(policies)
    original = manifest['routes'][0]
    cls = models.ProviderProfile if field == 'provider' else models.Destination
    resource = copy.deepcopy(original[field])
    resource['version'] += 1
    original[field] = models.with_resource_digest(cls.model_validate(resource)).model_dump(mode='json')
    del original['provider']['credential_ref']
    with pytest.raises(subject.RouteStageError, match='route_approved_resources'):
        subject.build_manifest(models.canonical_json_bytes(manifest), tenant='team-a', workload='chat',
            policy=new, original_policy=old, provider=original['provider'], destination=original['destination'])


@pytest.mark.parametrize('fault', ['unapproved', 'digest', 'foreign', 'old_route_missing', 'provider_changed', 'duplicate'])
def test_manifest_refuses_unauthorized_or_ambiguous_route(subject, policies, fault):
    old, new, manifest = copy.deepcopy(policies)
    if fault == 'unapproved': new['status'] = 'candidate'
    if fault == 'digest': new['compiled_digest'] = '0' * 64
    if fault == 'foreign': manifest['tenant_id'] = 'team-b'
    if fault == 'old_route_missing': manifest['routes'][0]['policy'] = subject.policy_reference(new)
    if fault == 'provider_changed': manifest['routes'][0]['provider']['model_id'] = 'different'
    if fault == 'duplicate': manifest['routes'].append({**manifest['routes'][0], 'policy': subject.policy_reference(new)})
    with pytest.raises(Exception):
        build(subject, (old, new, manifest))


def original_spec():
    return {'replicas': 1, 'strategy': {'type': 'Recreate'},
        'selector': {'matchLabels': {'guardrails.evolution/instance': 'gateway-team-a'}},
        'template': {'metadata': {'labels': {'guardrails.evolution/instance': 'gateway-team-a'},
                                 'annotations': {'guardrails.evolution/config-revision': 'exact-config'}},
            'spec': {'containers': [{'name': 'gateway', 'image': 'image@sha256:' + 'a' * 64,
                    'volumeMounts': [{'name': 'private', 'mountPath': '/config', 'subPath': 'data', 'readOnly': True}],
                    'resources': {'limits': {'memory': '1Gi', 'cpu': '750m'}}}],
                'volumes': [{'name': 'private', 'emptyDir': {}}]}}}


def test_staged_file_mount_retains_all_original_spec_fields(subject):
    original = original_spec()
    new = subject.staged_spec(original, 'gateway-route-owned', uid(99))
    assert original == original_spec()
    assert new['template']['spec']['containers'][0]['volumeMounts'][-1] == {
        'name': 'governed-route', 'mountPath': '/config/routes.json', 'subPath': 'routes.json', 'readOnly': True}
    assert new['template']['spec']['volumes'][-1]['secret']['defaultMode'] == 0o444
    new['template']['spec']['volumes'].pop()
    new['template']['spec']['containers'][0]['volumeMounts'].pop()
    del new['template']['metadata']['annotations'][subject.LABEL]
    assert new == original


@pytest.fixture
def rig(subject, policies, tmp_path, monkeypatch):
    old, new, manifest = policies
    assembly = load('assembly')
    root = tmp_path / 'state'; root.mkdir(mode=0o700)
    journal = tmp_path / 'journal'; journal.mkdir(mode=0o700)
    owner = {'profile': 'guardrails-evolution', 'uid': os.getuid(), 'state': str(root),
             'owner_id': 'b' * 32, 'container_id': 'c' * 64}
    write(root / 'owner.json', owner)
    write(root / 'assembly/owner.json', {key: owner[key] for key in ('owner_id', 'container_id', 'state', 'uid')})
    ports = {'team-a': 18501, 'team-b': 18502}; write(root / 'assembly/ports.json', ports)
    deployment = SimpleNamespace(root=ROOT, state=root, bin=root / 'bin', kubeconfig=root / 'kubeconfig', env={},
        support=lambda name: assembly, lock=nullcontext, read_json=lambda path: json.loads(path.read_bytes()),
        write_json=write, owner=lambda **_: pytest.fail('unjournaled bootstrap owner call'))
    processes = {}
    for index, tenant in enumerate(ports, 100):
        row = {'pid': index, 'start': str(index), 'command': assembly._forward_digest(deployment, tenant, ports[tenant])}
        processes[index] = row
        write(root / 'assembly' / ('forward-' + tenant + '.json'), row)
    trace, forwards = [], []
    monkeypatch.setattr(assembly, '_process_identity', lambda pid: copy.deepcopy(processes.get(pid)))
    monkeypatch.setattr(assembly, '_forward_listening', lambda row, port: processes.get(row['pid']) == row)
    descriptors = {}
    def pidfd(pid):
        descriptor = os.open('/dev/null', os.O_RDONLY)
        descriptors[descriptor] = pid
        return descriptor
    monkeypatch.setattr(subject.os, 'pidfd_open', pidfd)
    monkeypatch.setattr(subject.signal, 'pidfd_send_signal', lambda fd, signal: processes.pop(descriptors[fd], None))
    def forward(deployment, directory, tenant, port, **kwargs):
        forwards.append(tenant)
        number = 200 + len(forwards)
        row = {'pid': number, 'start': str(number), 'command': assembly._forward_digest(deployment, tenant, port)}
        processes[number] = row
        write(directory / ('forward-' + tenant + '.json'), row)
    monkeypatch.setattr(assembly, '_forward', forward)
    monkeypatch.setattr(assembly.subprocess, 'Popen', lambda *a, **kw: pytest.fail('unexpected native process'))

    class Port:
        def __init__(self):
            self.rows = {}; self.secrets = {}; self.pod_number = 1; self.fail_delete = False
            self.mount_fault = False
            for tenant in ports:
                spec = original_spec()
                spec['selector']['matchLabels']['guardrails.evolution/instance'] = 'gateway-' + tenant
                spec['template']['metadata']['labels']['guardrails.evolution/instance'] = 'gateway-' + tenant
                self.rows[tenant] = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                    'metadata': {'name': 'gateway-' + tenant, 'namespace': 'gr-' + tenant, 'uid': 'deployment-' + tenant,
                        'labels': {'guardrails.evolution/owner': owner['owner_id']}, 'resourceVersion': '1', 'generation': 1},
                    'spec': spec, 'status': {'observedGeneration': 1, 'replicas': 1, 'updatedReplicas': 1,
                                           'readyReplicas': 1, 'availableReplicas': 1}}
        def run(self, argv, **kwargs):
            trace.append(('node',))
            assert argv == ['docker', 'container', 'inspect', 'guardrails-evolution']
            return 0, json.dumps([{'Id': owner['container_id'], 'State': {'Running': True}}])
        def readiness(self, **kwargs):
            trace.append(('ready',)); return 200
        def forward(self, argv, **kwargs):
            pytest.fail('synthetic assembly forward must not invoke a process-spawn port')
        def current_pod(self, tenant):
            row = self.rows[tenant]
            return {'metadata': {'name': 'pod-' + str(self.pod_number), 'namespace': 'gr-' + tenant,
                'uid': 'pod-uid-' + str(self.pod_number), 'ownerReferences': [{'kind': 'ReplicaSet',
                    'name': 'replica', 'uid': 'replica-' + str(self.pod_number), 'controller': True}]},
                'spec': copy.deepcopy(row['spec']['template']['spec']),
                'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                    'containerStatuses': [{'name': 'gateway', 'ready': True,
                        'image': 'sha256:d54d3831cad06978c579458ef50d5c6f57dd45a5a5dc88860af32b9bbd19e225',
                        'imageID': row['spec']['template']['spec']['containers'][0]['image'],
                        'containerID': 'containerd://' + str(self.pod_number),
                        'restartCount': 0, 'state': {'running': {'startedAt': '2026-10-13T00:00:00Z'}}}]}}
        def kubectl(self, *args, namespace, input_text=None, **kwargs):
            trace.append(tuple(args))
            tenant = namespace.removeprefix('gr-')
            if args[0] == 'get':
                kind, name = args[1:3]
                if kind == 'deployment': result = self.rows[tenant]
                elif kind == 'secret' and name.endswith('-config'):
                    return 0, base64.b64encode(models.canonical_json_bytes(manifest)).decode()
                elif kind == 'secret':
                    result = self.secrets.get(name)
                    if result is None: return 0, ''
                elif kind == 'pods': result = {'kind': 'List', 'items': [self.current_pod(tenant)]}
                elif kind == 'pod': result = self.current_pod(tenant)
                elif kind == 'replicaset':
                    template = copy.deepcopy(self.rows[tenant]['spec']['template'])
                    template['metadata']['labels']['pod-template-hash'] = 'hash'
                    result = {'metadata': {'uid': 'replica-' + str(self.pod_number), 'ownerReferences': [
                        {'kind': 'Deployment', 'name': 'gateway-' + tenant, 'uid': 'deployment-' + tenant, 'controller': True}]},
                        'spec': {'template': template}}
                else: raise AssertionError(args)
                return 0, json.dumps(result)
            if args[0] == 'create':
                value = json.loads(input_text); value['metadata'].update(uid='created-secret', resourceVersion='7')
                self.secrets[value['metadata']['name']] = value
            elif args[0] == 'patch':
                value = json.loads(input_text); row = self.rows[tenant]
                assert value[0]['value'] == row['metadata']['uid']
                assert value[1]['value'] == row['metadata']['resourceVersion']
                assert value[2]['value'] == row['spec']
                row['spec'] = value[3]['value']; row['metadata']['resourceVersion'] = str(int(row['metadata']['resourceVersion']) + 1)
                self.pod_number += 1
            elif args[0] == 'delete':
                if self.fail_delete:
                    self.fail_delete = False
                    raise RuntimeError('synthetic interrupted delete before dispatch')
                value = json.loads(input_text); name = args[1].split('/')[-1]
                assert value['preconditions']['uid'] == self.secrets[name]['metadata']['uid']
                assert value['preconditions']['resourceVersion'] == self.secrets[name]['metadata']['resourceVersion']
                del self.secrets[name]
            elif args[0] == 'exec':
                volumes = self.rows[tenant]['spec']['template']['spec']['volumes']
                mounted = next((row for row in volumes if row['name'] == 'governed-route'), None)
                raw = (base64.b64decode(self.secrets[mounted['secret']['secretName']]['data']['routes.json'])
                       if mounted else models.canonical_json_bytes(manifest))
                return 0, json.dumps({'sha256': '0' * 64 if self.mount_fault else subject.digest(raw),
                    'size_bytes': len(raw), 'uid': 0 if mounted else 10001, 'mode': 0o444 if mounted else 0o600})
            elif args[0] != 'rollout': raise AssertionError(args)
            return 0, '{}'

    port = Port()
    assert all(callable(getattr(port, name, None)) for name in ('run', 'kubectl', 'readiness', 'forward'))
    op = subject.GatewayRouteStage(deployment, journal_root=journal, deadline=time.monotonic() + 75, native=port)
    original_binding = {'tenant_id': 'team-a', 'workload_id': 'chat', 'policy': subject.policy_reference(old),
                        'state': 'observe', 'revision': 1, 'release_id': uid(70), 'activated_by': uid(14),
                        'previous_revision': None, 'status': 'applied', 'reason': 'synthetic original',
                        'created_at': '2026-10-13T00:00:00Z', 'effective_after': '2026-10-13T00:00:00Z'}
    args = {'operation': uid(99), 'tenant': 'team-a', 'policy': new, 'original_policy': old,
        'original_binding': original_binding,
        'inventory': {'workload': {'workload_id': 'chat'}, 'roles': {'operator': {'principal_id': uid(14)}},
            'runtimes': {'runtime-1': {'runtime_id': uid(81)}, 'runtime-2': {'runtime_id': uid(82)}},
            'profiles': {'mock': {'provider': old['providers'][0], 'destination': old['resolved_destinations'][0]}}},
        'access': {'ca_file': '/synthetic/ca', 'tenants': {'team-a': {'gateway_url': 'https://localhost:18501',
            'gateway_token_file': '/synthetic/token'}}}, 'operator_id': uid(14)}
    def binding(intent):
        trace.append(('binding',))
        return {'verified_original_binding': True, **original_binding}
    def recover():
        return subject.restore(deployment, journal_root=journal, deadline=time.monotonic() + 75,
                               worker_closed=True, verify_binding=binding, native=port)
    return SimpleNamespace(op=op, args=args, port=port, trace=trace, forwards=forwards, journal=journal,
        deployment=deployment, recover=recover, binding=binding, processes=processes, assembly=assembly)


def test_full_stage_and_restore_preserve_other_team_and_governance_order(rig):
    other = copy.deepcopy(rig.port.rows['team-b'])
    rig.op.stage(**rig.args)
    rig.op.assert_active()
    result = rig.recover()
    assert result['original_spec_restored'] and result['created_secret_absent']
    assert result['mount']['sha256'] == rig.op.intent['original_routes_sha256']
    assert rig.port.rows['team-b'] == other and rig.forwards == ['team-a', 'team-a']
    assert rig.trace.index(('binding',)) < max(index for index, row in enumerate(rig.trace) if row[0] == 'patch')
    assert not rig.port.secrets


def test_full_stage_accepts_serialized_seed_provider_without_optional_default(rig, policies):
    manifest = policies[2]
    manifest['routes'][0]['provider'] = copy.deepcopy(manifest['routes'][0]['provider'])
    del manifest['routes'][0]['provider']['credential_ref']
    rig.args['inventory']['profiles']['mock']['provider'] = copy.deepcopy(manifest['routes'][0]['provider'])
    other = copy.deepcopy(rig.port.rows['team-b'])
    rig.op.stage(**rig.args)
    secret = next(iter(rig.port.secrets.values()))
    staged = json.loads(base64.b64decode(secret['data']['routes.json']))
    assert staged['routes'][0] == manifest['routes'][0]
    assert staged['routes'][1]['provider'] == manifest['routes'][0]['provider']
    assert 'credential_ref' not in staged['routes'][1]['provider']
    rig.op.assert_active()
    result = rig.recover()
    assert result['original_spec_restored'] and result['created_secret_absent']
    assert rig.port.rows['team-b'] == other


def test_restore_resumes_after_forward_checkpoint_without_rotating_again(rig):
    rig.op.stage(**rig.args)
    rig.port.fail_delete = True
    with pytest.raises(RuntimeError, match='synthetic interrupted'):
        rig.recover()
    assert len(rig.forwards) == 2
    assert rig.recover()['status'] == 'restored'
    assert len(rig.forwards) == 2
    assert rig.recover()['status'] == 'restored'
    assert len(rig.forwards) == 2


def test_wrong_binding_forbids_every_cleanup_mutation(rig, subject):
    rig.op.stage(**rig.args)
    before = copy.deepcopy(rig.port.rows)
    with pytest.raises(subject.RouteStageError, match='binding_recovery_required'):
        subject.restore(rig.deployment, journal_root=rig.journal, deadline=time.monotonic() + 75,
            worker_closed=True, verify_binding=lambda intent: {'verified_original_binding': False}, native=rig.port)
    assert rig.port.rows == before and len(rig.port.secrets) == 1 and len(rig.forwards) == 1


@pytest.mark.parametrize('fault', ['deployment_uid', 'competing_spec', 'secret_uid', 'other_team'])
def test_recovery_refuses_competing_owned_resources(rig, subject, fault):
    rig.op.stage(**rig.args)
    if fault == 'deployment_uid': rig.port.rows['team-a']['metadata']['uid'] = 'replacement'
    if fault == 'competing_spec': rig.port.rows['team-a']['spec']['replicas'] = 2
    if fault == 'secret_uid': next(iter(rig.port.secrets.values()))['metadata']['uid'] = 'replacement'
    if fault == 'other_team': rig.port.rows['team-b']['spec']['replicas'] = 2
    with pytest.raises(subject.RouteStageError): rig.recover()
    assert rig.port.secrets


def test_mounted_hash_refusal_occurs_before_any_staging_mutation(rig, subject):
    rig.port.mount_fault = True
    with pytest.raises(subject.RouteStageError, match='mounted_bytes'):
        rig.op.stage(**rig.args)
    assert not rig.port.secrets and not rig.forwards


def test_observed_cri_config_display_with_exact_manifest_identity_stages_and_restores(rig):
    # Public fields from the exact historical20b089 Pod response, captured by
    # the bounded052e diagnostic: status.image is a config digest, imageID is
    # the immutable manifest reference. Neither field is an interchangeable tag.
    image = 'docker.io/guardrails-evolution/gateway@sha256:09c3493e6f553f3610f2835ca717ba5b98055bacaba9f3bd9aae33752c1866d3'
    rig.port.rows['team-a']['spec']['template']['spec']['containers'][0]['image'] = image
    before = rig.port.current_pod('team-a')
    assert before['status']['containerStatuses'][0]['image'] != image
    assert before['status']['containerStatuses'][0]['imageID'] == image
    rig.op.stage(**rig.args)
    assert rig.recover()['status'] == 'restored'
    assert rig.port.rows['team-a']['spec']['template']['spec']['containers'][0]['image'] == image


@pytest.mark.parametrize('fault', ['wrong_manifest', 'missing_image_id', 'config_digest', 'display_tag',
                                  'wrong_declared_image', 'wrong_declared_name', 'extra_declared_container'])
def test_pod_image_identity_refusal_precedes_route_mutation(rig, subject, monkeypatch, fault):
    original = rig.port.current_pod
    def observe(tenant):
        value = original(tenant)
        status = value['status']['containerStatuses'][0]
        declared = value['spec']['containers']
        if fault == 'wrong_manifest': status['imageID'] = 'image@sha256:' + 'b' * 64
        elif fault == 'missing_image_id': status.pop('imageID')
        elif fault == 'config_digest': status['imageID'] = status['image']
        elif fault == 'display_tag': status['imageID'] = 'guardrails-evolution/gateway:latest'
        elif fault == 'wrong_declared_image': declared[0]['image'] = 'image@sha256:' + 'b' * 64
        elif fault == 'wrong_declared_name': declared[0]['name'] = 'different'
        else: declared.append(copy.deepcopy(declared[0]))
        return value
    monkeypatch.setattr(rig.port, 'current_pod', observe)
    with pytest.raises(subject.RouteStageError, match='route_pod_image_identity'):
        rig.op.stage(**rig.args)
    assert not rig.port.secrets and not rig.forwards


@pytest.mark.parametrize('phase', ['Running', 'Pending', 'Succeeded', 'Failed'])
def test_pod_inventory_requires_old_nonterminal_pod_closure(rig, subject, monkeypatch, phase):
    original = rig.port.kubectl
    def observe(*args, **kwargs):
        code, raw = original(*args, **kwargs)
        if args[:2] == ('get', 'pods'):
            value = json.loads(raw)
            sibling = copy.deepcopy(value['items'][0])
            sibling['metadata'].update(name='old-pod', uid='old-pod-uid', deletionTimestamp='2026-10-13T00:00:01Z')
            sibling['status']['phase'] = phase
            value['items'].append(sibling)
            raw = json.dumps(value)
        return code, raw
    monkeypatch.setattr(rig.port, 'kubectl', observe)
    if phase in {'Running', 'Pending'}:
        with pytest.raises(subject.RouteStageError, match='route_pod_count'):
            rig.op.stage(**rig.args)
        assert not rig.port.secrets and not rig.forwards
    else:
        rig.op.stage(**rig.args)
        assert rig.recover()['status'] == 'restored'


def test_no_mutation_recovery_preserves_original_forward(rig, subject):
    rig.port.mount_fault = True
    with pytest.raises(subject.RouteStageError): rig.op.stage(**rig.args)
    rig.port.mount_fault = False
    result = rig.recover()
    assert result['status'] == 'restored' and result['staging_command_issued'] is False
    assert result['forward'] == rig.op.intent['original_forward'] and not rig.forwards


def unresolved(rig, subject, verb, *, current_rv=None, acknowledged=False):
    # Explicit durable dispatch fixture: a closed local child does not prove the
    # Kubernetes operation was rejected or cannot commit after a read.
    j = rig.op.journal
    number = 900
    j.write('command-900-intent.json', {'args': [verb], 'namespace': 'gr-team-a', 'input_sha256': '0' * 64,
                                      'status': 'outcome_unknown'})
    j.write('native-900-started.json', {'pid': 900, 'start': '900', 'command': 'a' * 64,
        'gate_command': 'b' * 64, 'group': 123, 'forward': False, 'argv': ['kubectl'],
        'command_id': number, 'status': 'gate_held'})
    j.write('native-900-dispatch.json', {'status': 'may_have_dispatched'})
    j.write('native-900-closed.json', {'pid': 900, 'exit_code': 0 if acknowledged else -9,
                                     'reaped': True, 'interrupted': not acknowledged})
    if verb == 'patch':
        j.write('patch-900-intent.json', {'uid': 'deployment-team-a', 'resource_version': current_rv,
            'from_spec': rig.op.intent['original']['spec'], 'to_spec': rig.op.intent['staged']['spec'], 'command_id': number})


def test_unknown_create_cannot_be_settled_by_absence(rig, subject):
    rig.op.stage(**rig.args)
    rig.port.secrets.clear()
    unresolved(rig, subject, 'create')
    with pytest.raises(subject.RouteStageError, match='create_outcome_unresolved'): rig.recover()


def test_closed_zero_native_settles_missing_high_level_create_result_without_retry(rig, subject):
    rig.op.stage(**rig.args)
    unresolved(rig, subject, 'create', acknowledged=True)
    assert rig.recover()['status'] == 'restored'
    assert sum(row[0] == 'create' for row in rig.trace) == 1


@pytest.mark.parametrize('advanced', [False, True])
def test_unknown_patch_needs_observed_effect_or_changed_tested_rv(rig, subject, advanced):
    rig.op.stage(**rig.args)
    row = rig.port.rows['team-a']; row['spec'] = copy.deepcopy(rig.op.intent['original']['spec'])
    row['metadata']['resourceVersion'] = '9' if advanced else '8'
    unresolved(rig, subject, 'patch', current_rv='8')
    if advanced:
        assert rig.recover()['status'] == 'restored'
    else:
        with pytest.raises(subject.RouteStageError, match='patch_outcome_unresolved'): rig.recover()


def test_unpublished_forward_is_closed_before_renewal(rig, subject):
    rig.op.stage(**rig.args)
    before = json.loads((rig.deployment.state / 'assembly/forward-team-a.json').read_bytes())
    orphan = {**before, 'pid': 700, 'start': '700'}
    rig.processes[700] = orphan
    rig.op.journal.write('native-700-started.json', {**orphan, 'gate_command': 'b' * 64,
        'group': 700, 'forward': True, 'argv': ['kubectl'], 'command_id': None, 'status': 'gate_held'})
    assert rig.recover()['status'] == 'restored'
    assert 700 not in rig.processes


def test_live_synchronous_native_child_refuses_recovery(rig, subject):
    rig.op.stage(**rig.args)
    unresolved(rig, subject, 'create')
    rig.processes[900] = {'pid': 900, 'start': '900', 'command': 'a' * 64}
    with pytest.raises(subject.RouteStageError, match='native_still_active'): rig.recover()


def test_forward_reused_pid_is_never_signaled(rig):
    rig.op.stage(**rig.args)
    before = json.loads((rig.deployment.state / 'assembly/forward-team-a.json').read_bytes())
    row = {**before, 'pid': 700, 'start': '700'}
    rig.op.journal.write('native-700-started.json', {**row, 'gate_command': 'b' * 64,
        'group': 700, 'forward': True, 'argv': ['kubectl'], 'command_id': None, 'status': 'gate_held'})
    unrelated = {**row, 'start': '701', 'command': 'f' * 64}
    rig.processes[700] = unrelated
    assert rig.recover()['status'] == 'restored'
    assert rig.processes[700] == unrelated


def test_gate_ledger_failure_never_releases_and_reaps_child(subject, tmp_path, monkeypatch):
    directory = tmp_path / 'journal'; directory.mkdir(mode=0o700)
    journal = subject.Journal(directory)
    events = []
    class Process:
        pid = 900
        returncode = None
        def kill(self): events.append('kill'); self.returncode = -9
        def wait(self, timeout): events.append('wait'); return self.returncode
    process = Process()
    def spawn(argv, **kwargs):
        assert kwargs['start_new_session'] is False and argv[5] == str(4 * subject.MAXIMUM_BYTES)
        events.append('spawn'); return process
    monkeypatch.setattr(subject.subprocess, 'Popen', spawn)
    monkeypatch.setattr(subject.os, 'getpgid', lambda pid: 123)
    monkeypatch.setattr(subject.os, 'write', lambda fd, raw: events.append('gate_release'))
    monkeypatch.setattr(journal, 'write', lambda *args: (_ for _ in ()).throw(OSError('synthetic durability failure')))
    assembly = SimpleNamespace(_process_identity=lambda pid: {'pid': pid, 'start': '9', 'command': 'a' * 64})
    port = subject.NativePort(SimpleNamespace(), journal, assembly)
    with pytest.raises(OSError, match='synthetic durability failure'):
        port.launch(['/synthetic/kubectl'])
    assert events == ['spawn', 'kill', 'wait']


def test_native_gate_hard_bounds_output_before_exec_without_starting_process(subject):
    events = []
    fake_os = SimpleNamespace(read=lambda fd, n: b'G', close=lambda fd: events.append('close'), environ={},
        execvpe=lambda command, argv, env: events.append(('exec', command, argv)))
    fake_sys = SimpleNamespace(argv=['gate', '9', '4194304', '/synthetic/kubectl', 'get'],
                               exit=lambda code: pytest.fail('unexpected closed gate'))
    fake_resource = SimpleNamespace(RLIMIT_FSIZE=1, setrlimit=lambda kind, limit: events.append(('limit', kind, limit)))
    modules = {'os': fake_os, 'sys': fake_sys, 'resource': fake_resource}
    namespace = {'__builtins__': {'__import__': lambda name, *args: modules[name], 'int': int}}
    exec(subject.GATE, namespace)
    assert events == ['close', ('limit', 1, (4194304, 4194304)), ('exec', '/synthetic/kubectl', ['/synthetic/kubectl', 'get'])]
