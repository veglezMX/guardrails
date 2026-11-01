"""Real compiler/governance/journals with synthetic management and scoped SQL."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace as N
from uuid import uuid4

import pytest
from evolution.evaluator_governance import (
    GovernanceJourney,
    owned_revision,
    policy_reference,
)
from guardrails.evolution import models
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import safe_view

from mvp.tests.evolution.e2e import classifier_admission_governance as s
from mvp.tests.evolution.unit import test_classifier_admission as existing
from mvp.tests.evolution.unit import test_evaluator_governance as original

no_actual_operations = existing.no_actual_operations
base = original.base
uid = existing.uid
STAMP = original.STAMP


def base_for(base, tenant, actor):
    value=copy.deepcopy(base);value['tenant_id']=tenant;value['author_id']=actor
    value['status']='candidate';value['compiled_digest']='0'*64
    for field,cls in (('residency',models.ResidencyProfile),('quota',models.Quota)):
        value[field]=models.with_resource_digest(cls.model_validate_json(models.canonical_json_bytes(
            {**value[field],'tenant_id':tenant}))).model_dump(mode='json')
    for field,cls in (('providers',models.ProviderProfile),('resolved_destinations',models.Destination),('resolved_lists',models.ListSet)):
        value[field]=[models.with_resource_digest(cls.model_validate_json(models.canonical_json_bytes(
            {**item,'tenant_id':tenant}))).model_dump(mode='json') for item in value[field]]
    compiled=PolicyCompiler().compile(models.PolicyVersion.model_validate_json(models.canonical_json_bytes(value)))
    return compiled.model_copy(update={'status':'approved'}).model_dump(mode='json')


class Management:
    """No product service fake: real models/compiler, isolated HTTP boundary only."""
    def __init__(self, cluster, tenant, journal):
        self.cluster,self.tenant,self.journal=cluster,tenant,journal
        self.roles=cluster.inventory['tenants'][tenant]['roles'];self.calls=[];self.approval=None

    def __call__(self,method,path,*,role,body,key,expected):
        c,t=self.cluster,self.tenant
        self.calls.append((method,path,copy.deepcopy(body),role))
        if method=='POST':
            raw=json.loads((self.journal.path/f'request-{self.journal.counter:02d}-intent.json').read_bytes())
            assert raw['body_sha256']==s.evidence.digest(body) and raw['actor_id']==self.roles[role]['principal_id']
            assert raw['idempotency_key']==key and raw['status']=='request_intended_outcome_unknown'
        if path.startswith('/quotas/'):
            assert role=='tenant_admin' and method=='POST' and expected==201
            old=c.base[t]['quota'];value=models.Quota(**body,tenant_id=t,quota_id=old['quota_id'],version=2,created_at=STAMP)
            value=models.with_resource_digest(value).model_dump(mode='json')
            c.quota[t]=value
            event={'event_id':str(uuid4()),'tenant_id':t,'event_type':'configuration_created',
                'actor_id':self.roles['tenant_admin']['principal_id'],'raw_content_retained':False,'event_hash':'b'*64,
                'metadata':{'resource_type':'quotas','resource_id':value['quota_id'],'revision':2}}
            c.audits[t]=event
            if c.fault=='quota_unknown':raise TimeoutError('private response lost')
            if c.fault=='quota_digest':value['digest']='0'*64
            if c.fault=='quota_scope':value['tenant_id']='foreign'
            if c.fault=='quota_version':value['version']=1
            if c.fault=='quota_broaden':
                value=models.with_resource_digest(models.Quota.model_validate_json(models.canonical_json_bytes(
                    {**value,'burst':value['burst']+1}))).model_dump(mode='json')
            return safe_view(value)
        if path.startswith('/policies/'):
            policy_id=path.split('/')[2]
            if method=='POST' and path.endswith('/versions'):
                assert role=='author'
                if c.fault=='b_create' and t=='team-b':raise EvolutionError('policy_unresolved')
                value=copy.deepcopy(c.base[t]);value.update(policy_id=policy_id,author_id=self.roles['author']['principal_id'],
                    rules=body['rules'],status='candidate',quota=c.quota.get(t,c.base[t]['quota']))
                if t=='team-a':
                    value['providers']=[value['providers'][0],c.endpoints[t]['provider']]
                    value['resolved_destinations']=[value['resolved_destinations'][0],c.endpoints[t]['destination']]
                parsed=PolicyCompiler().compile(models.PolicyVersion.model_validate_json(models.canonical_json_bytes(value)))
                PolicyCompiler().validate_current(parsed,models.PolicyVersion.model_validate_json(models.canonical_json_bytes(c.base[t])))
                c.policies[t][policy_id]=parsed.model_dump(mode='json')
                return safe_view(c.policies[t][policy_id])
            if method=='POST':
                assert role=='approver' and body['outcome']=='approve'
                value=c.policies[t][policy_id];assert body['compiled_digest']==value['compiled_digest']
                self.approval=models.Approval(approval_id=str(uuid4()),tenant_id=t,policy=policy_reference(value),
                    compiled_digest=value['compiled_digest'],
                    actor_id=self.roles[role]['principal_id'],outcome='approve',reason='synthetic review',created_at=STAMP).model_dump(mode='json')
                value['status']='approved';return self.approval
            return {'policy':safe_view(c.policies[t][policy_id]),'approvals':[self.approval]}
        if path.startswith('/workloads/'):
            binding=c.bindings[t];return {'tenant_id':t,'workload_id':'chat',
                'desired_binding':{'binding_revision':binding['revision']},
                'applied_binding':{'binding_revision':binding['revision'],'policy':binding['policy']}}
        if path=='/releases':
            assert method=='POST' and role=='operator' and expected==202
            old=c.bindings[t];assert body['expected_revisions']=={'chat':old['revision']}
            self.release=models.Release(**body,tenant_id=t,release_id=str(uuid4()),
                actor_id=self.roles[role]['principal_id'],created_at=STAMP).model_dump(mode='json')
            c.bindings[t]=models.Binding(tenant_id=t,workload_id='chat',revision=old['revision']+1,
                policy=body['policy'],state=body['state'],release_id=self.release['release_id'],
                activated_by=self.roles[role]['principal_id'],previous_revision=old['revision'],
                effective_after=STAMP,created_at=STAMP,reason='synthetic',status='applied').model_dump(mode='json')
            return self.release
        assert path.startswith('/releases/') and method=='GET'
        handle=self.journal.path/('release-restore-original-binding.json' if self.release['policy']['policy_id']=='baseline'
            else 'release-matrix-release.json')
        assert json.loads(handle.read_bytes())['handle']['release_id']==self.release['release_id']
        if c.fault=='poll' and self.release['policy']['policy_id']!='baseline':raise EvolutionError('snapshot_stale')
        receipts=[models.RuntimeReceipt(runtime_id=item['runtime_id'],tenant_id=t,workload_id='chat',
            binding_revision=c.bindings[t]['revision'],snapshot_generation=c.bindings[t]['revision'],
            digest=self.release['policy']['digest'],applied_at=STAMP,created_at=STAMP,health='ready').model_dump(mode='json')
            for item in c.inventory['tenants'][t]['runtimes'].values()]
        return {'release':{**self.release,'status':'applied'},'receipts':receipts}


@pytest.fixture
def cluster(tmp_path, base):
    class Cluster:
        tenants=s.evidence.TENANTS
        def __init__(self):
            self.base={};self.inventory={'tenants':{}};self.endpoints={};self.bindings={};self.policies={}
            self.ports={};self.quota={};self.audits={};self.fault=None;self.events=[];self.repo=self
            for index,t in enumerate(self.tenants):
                roles={name:{'principal_id':uid(100*index+n)} for n,name in enumerate(
                    ('tenant_admin','author','approver','operator','auditor','workload'),10)}
                value=base_for(base,t,roles['author']['principal_id']);self.base[t]=value
                self.policies[t]={'baseline':value}
                self.inventory['tenants'][t]={'roles':roles,'workload':{'workload_id':'chat'},
                    'runtimes':{str(n):{'runtime_id':uid(100*index+n)} for n in (51,52)},
                    'profiles':{'mock':{'provider':value['providers'][0]}}}
                route=models.Destination.model_validate_json(models.canonical_json_bytes(value['resolved_destinations'][1]))
                route=models.with_resource_digest(route.model_copy(update={'version':2,'purpose':'evaluation',
                    'host':'classifier.gr-platform.svc.cluster.local','path_prefix':'/v1/classifier'}))
                provider=models.ProviderProfile.model_validate_json(models.canonical_json_bytes(value['providers'][1]))
                provider=models.with_resource_digest(provider.model_copy(update={'version':2,'kind':'evaluator',
                    'vendor':'local_classifier','model_id':original.MODEL_ID,'artifact_digest':original.WEIGHTS_SHA256,
                    'credential_ref':'synthetic-operator'}))
                self.endpoints[t]={'provider':provider.model_dump(mode='json'),'destination':route.model_dump(mode='json')}
                self.bindings[t]=models.Binding(tenant_id=t,workload_id='chat',revision=7,policy=policy_reference(value),
                    state='observe',release_id=uid(100*index+60),activated_by=roles['operator']['principal_id'],
                    previous_revision=6,effective_after=STAMP,created_at=STAMP,reason='private reason',status='applied').model_dump(mode='json')
        def baseline(self,t):return {'policies':[policy_reference(self.base[t])]},copy.deepcopy(self.base[t])
        def binding(self,t):
            row=self.bindings[t];return copy.deepcopy(row),copy.deepcopy(self.policies[t][row['policy']['policy_id']])
        def journey(self,t,operation,*,journal):
            port=Management(self,t,journal);self.ports[t]=port
            return GovernanceJourney(tenant=t,operation=operation,actors=journal.actors,call=port,journal=journal,
                operator_providers=(self.endpoints[t]['provider'],))
        def assert_governance_audit(self,t,policy,*,applied):
            assert applied;calls=self.ports[t].calls
            assert any(m=='POST' and r=='author' for m,p,b,r in calls)
            assert any(m=='POST' and r=='approver' for m,p,b,r in calls)
            assert any(m=='POST' and r=='operator' for m,p,b,r in calls)
            self.events.append(('governance_audit',t,policy['compiled_digest']))
        def owned_binding_revision(self,t,handle):
            self.events.append(('owned',t))
            if self.fault=='drift' and t=='team-b':self.bindings[t]['revision']+=1
            return owned_revision(handle,{'binding_revision':self.bindings[t]['revision']},self.bindings[t])
        @contextmanager
        def transaction(self,t):
            owner=self
            class Tx:
                def execute(self,query,params):
                    assert params['tenant']==t and 'LIMIT 2' in query and s.current_deadline.get()>time.monotonic()
                    event=copy.deepcopy(owner.audits[t])
                    if owner.fault=='audit_actor':event['actor_id']=uid(999)
                    if owner.fault=='audit_version':event['metadata']['revision']=3
                    if owner.fault=='audit_missing':return []
                    return [N(data=event)]
                def get(self,kind,key):
                    assert kind=='audit'
                    return {'foreign':True} if owner.fault=='audit_foreign' else None
            yield Tx()
    c=Cluster();root=tmp_path/'operations';root.mkdir(mode=0o700);source=tmp_path/'sources';source.mkdir()
    name='mvp/tests/evolution/e2e/classifier_admission_governance.py'
    path=source/name;path.parent.mkdir(parents=True);path.write_bytes(b'# synthetic governed source\n')
    pins={name:hashlib.sha256(path.read_bytes()).hexdigest()}
    def make(case=s.evidence.CASES[1]):return s.GovernedAdmission(c,operation_root=root,source_root=source,sources=pins,case_id=case)
    return N(c=c,root=root,source=source,pins=pins,make=make)


@pytest.mark.parametrize('case',s.evidence.CASES[:2])
def test_real_public_journey_stricter_quota_approval_receipts_and_owned_restore(cluster,case):
    f=cluster;initial=copy.deepcopy(f.c.base);g=f.make(case)
    sub=g.prepare(deadline=time.monotonic()+30)
    assert sub['team-a']['max_inflight']==(1 if case==s.evidence.CASES[1] else 4)
    assert sub['team-b']['budget_ms']==500 and sub['team-a']['budget_ms']==3000
    assert f.c.base==initial
    assert g.restore(passed=True,deadline=time.monotonic()+30)=='restored'
    assert [x for x in f.c.events if x[0]=='owned']==[('owned','team-b'),('owned','team-a')]
    for t,attempt in g.attempts.items():
        assert attempt.fd is None and f.c.bindings[t]['policy']==policy_reference(initial[t])
        assert json.loads((attempt.path/'completion.json').read_bytes())['status']=='case_passed'
        assert (attempt.path/'release-matrix-release.json').is_file() and (attempt.path/'release-restore-original-binding.json').is_file()
        raw=b''.join(p.read_bytes() for p in attempt.path.iterdir())
        assert b'synthetic-operator' not in raw and b'private reason' not in raw
    if case==s.evidence.CASES[1]:
        assert set(g.quota_audits)=={'team-a'} and g.quota_audits['team-a']['metadata']['revision']==2


@pytest.mark.parametrize('fault',['quota_digest','quota_scope','quota_version','quota_broaden','audit_actor','audit_version','audit_missing','audit_foreign'])
def test_invalid_created_quota_or_actual_creation_audit_refuses_before_release(cluster,fault):
    f=cluster;f.c.fault=fault;g=f.make()
    with pytest.raises(s.evidence.AdmissionError):g.prepare(deadline=time.monotonic()+30)
    assert not any(path=='/releases' for port in f.c.ports.values() for _,path,_,_ in port.calls)
    assert g.restore(passed=False,deadline=time.monotonic()+30)=='restored'
    assert all(json.loads((a.path/'completion.json').read_bytes())['status']=='case_failed' for a in g.attempts.values())


def test_lost_mutation_response_keeps_unknown_intent_without_replay(cluster):
    f=cluster;f.c.fault='quota_unknown';g=f.make()
    with pytest.raises(TimeoutError):g.prepare(deadline=time.monotonic()+30)
    assert g.restore(passed=False,deadline=time.monotonic()+30)=='restored'
    attempt=g.attempts['team-a'];completion=json.loads((attempt.path/'completion.json').read_bytes())
    assert completion['last_request_outcome_unknown'] and len(f.c.ports['team-a'].calls)==1
    assert not (attempt.path/'request-01-returned.json').exists()
    with pytest.raises(s.evidence.AdmissionError):g.prepare(deadline=time.monotonic()+30)


@pytest.mark.parametrize('fault',['poll','b_create'])
def test_acknowledged_binding_is_restored_even_when_later_prepare_fails(cluster,fault):
    f=cluster;f.c.fault=fault;g=f.make()
    with pytest.raises(EvolutionError):g.prepare(deadline=time.monotonic()+30)
    assert g.restore(passed=False,deadline=time.monotonic()+30)=='restored'
    assert f.c.bindings['team-a']['policy']==policy_reference(f.c.base['team-a'])
    assert (g.attempts['team-a'].path/'release-restore-original-binding.json').is_file()
    assert all(a.fd is None for a in g.attempts.values())


def test_foreign_revision_is_never_overwritten_and_peer_cleanup_still_runs(cluster):
    f=cluster;g=f.make();g.prepare(deadline=time.monotonic()+30);f.c.fault='drift'
    with pytest.raises(s.evidence.AdmissionError,match='admission_restoration_failed'):g.restore(passed=True,deadline=time.monotonic()+30)
    assert f.c.bindings['team-a']['policy']==policy_reference(f.c.base['team-a'])
    assert f.c.bindings['team-b']['policy']['policy_id']!='baseline'
    assert not (g.attempts['team-b'].path/'release-restore-original-binding.json').exists()
    assert all(a.fd is None and json.loads((a.path/'completion.json').read_bytes())['status']=='case_failed' for a in g.attempts.values())
