"""Full governed family composition with fake HTTP/SQL/native boundaries."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace as N
from uuid import uuid4

import pytest
from evolution.registry import EvidenceError

from mvp.tests.evolution.e2e import classifier_admission_case as s
from mvp.tests.evolution.unit import test_classifier_admission as observations
from mvp.tests.evolution.unit import test_classifier_admission_fault as faults
from mvp.tests.evolution.unit import test_classifier_admission_governance as governance

no_actual_operations = observations.no_actual_operations
base = governance.base
cluster = governance.cluster
native_port = faults.native_port


@pytest.fixture
def whole(cluster,tmp_path,monkeypatch):
    f=cluster;f.c.state=tmp_path/'state';f.c.state.mkdir(mode=0o700)
    for name in s.SOURCES:
        p=f.source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'# synthetic exact source\n')
        f.pins[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    metadata=observations.Repo();original_repo=f.c.repo
    class Combined:
        @contextmanager
        def transaction(self,tenant):
            with original_repo.transaction(tenant) as gtx,metadata.transaction(tenant) as mtx:
                class Tx:
                    def get(self,kind,key,required=False):
                        value=mtx.get(kind,key,required=required)
                        return gtx.get(kind,key) if value is None and kind=='audit' else value
                    def execute(self,query,params=None):
                        return gtx.execute(query,params) if 'configuration_created' in query else mtx.execute(query,params)
                yield Tx()
    f.c.repo=Combined();events=[];holder={};records=[];fault={'phase':None,'drift':False,'http_mode':'success'}
    original_governor=s.GovernedAdmission
    def governed(*a,**kw):
        events.append('governance');holder['g']=original_governor(*a,**kw);return holder['g']
    monkeypatch.setattr(s,'GovernedAdmission',governed)
    def runtime(c,*,deadline):
        assert c is f.c and deadline<=stop;events.append('client')
        http=observations.FakeHTTP(holder['g'].subjects,metadata);http.mode=fault['http_mode'];holder['http']=http;return http
    monkeypatch.setattr(s,'RuntimePort',runtime)
    monkeypatch.setattr(s,'held_lock',lambda state,*,deadline:events.append('held_lock'))
    out=tmp_path/'case';out.mkdir(mode=0o700)
    def record(name,value):
        raw=s.evidence.canonical(value);(out/name).write_bytes(raw);(out/name).chmod(0o600)
        records.append((name,copy.deepcopy(value)));events.append('record-'+name)
    def verify(**kw):
        assert kw['deadline']<=stop and kw['operation']==operation and kw['authorization_sha256']=='e'*64
        assert kw['sources']==f.pins
        phase=kw['phase'];events.append('verify-'+phase)
        if phase in {'restored','consume'}:
            assert set(kw['governance'])==set(s.evidence.TENANTS)
            for item in kw['governance'].values():
                for ref in item['references'].values():
                    from pathlib import Path
                    assert hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()==ref['sha256']
        if fault['drift']:
            (f.source/next(iter(s.SOURCES))).write_bytes(b'# changed\n')
        return phase!=fault['phase']
    stop=time.monotonic()+30;operation=str(uuid4())
    def run(case=s.evidence.CASES[0],*,owned_fault=None):
        return s.run_case_locked(case_id=case,operation=operation,cluster=f.c,source_root=f.source,sources=f.pins,
            operation_root=f.root,authorization_sha256='e'*64,record=record,verify_provenance=verify,deadline=stop,fault=owned_fault)
    return N(f=f,run=run,verify=verify,stop=stop,operation=operation,events=events,records=records,holder=holder,fault=fault,out=out)


@pytest.mark.parametrize('case',s.evidence.CASES[:2])
def test_actual_outer_journey_ports_and_final_receipts_are_composed(whole,case):
    w=whole;value=w.run(case)
    assert value['status']=='observed' and value['phase']=='complete'
    assert w.events.index('record-admission-case-01.json')<w.events.index('governance')<w.events.index('client')
    assert w.events.index('verify-before')<w.events.index('governance') and w.events.index('verify-authorized')<w.events.index('client')
    assert s.validate(value,verify_provenance=w.verify,deadline=w.stop)==value
    assert len(w.holder['http'].calls)==(4 if case==s.evidence.CASES[0] else 3)
    assert all(a.fd is None for a in w.holder['g'].attempts.values())
    for tenant in s.evidence.TENANTS:
        assert w.f.c.bindings[tenant]['policy']==governance.policy_reference(w.f.c.base[tenant])
    raw=b''.join(p.read_bytes() for p in w.out.iterdir())
    assert b'AKIAABCDEFGHIJKLMNOP' not in raw and b'synthetic-operator' not in raw


@pytest.mark.parametrize('phase',['before','authorized','after','restored'])
def test_parent_refusal_retains_original_prefix_and_restores_any_owned_bindings(whole,phase):
    w=whole;w.fault['phase']=phase
    with pytest.raises(s.evidence.AdmissionError):w.run()
    final=w.records[-1][1];assert final['status']=='failed' and final['failure_type']=='AdmissionError'
    if phase=='before':assert 'governance' not in w.events and 'client' not in w.events
    else:
        assert all(a.fd is None for a in w.holder['g'].attempts.values())
        assert all(w.f.c.bindings[t]['policy']==governance.policy_reference(w.f.c.base[t]) for t in s.evidence.TENANTS)
    if phase in {'before','authorized'}:assert 'client' not in w.events
    if phase in {'after','restored'}:assert final['measurement']['requests']


def test_callback_source_drift_refuses_before_management_or_clients(whole):
    w=whole;w.fault['drift']=True
    with pytest.raises(EvidenceError, match='diagnostic_source_changed'):w.run()
    assert 'governance' not in w.events and 'client' not in w.events and w.records[-1][1]['status']=='failed'


def test_mutation_unknown_does_not_replay_and_retains_both_case_and_original_journal(whole):
    w=whole;w.f.c.fault='quota_unknown'
    with pytest.raises(TimeoutError):w.run(s.evidence.CASES[1])
    assert 'client' not in w.events and len(w.f.c.ports['team-a'].calls)==1
    final=w.records[-1][1];assert final['status']=='failed' and final['failure_type']=='TimeoutError'
    attempt=w.holder['g'].attempts['team-a']
    assert json.loads((attempt.path/'completion.json').read_bytes())['last_request_outcome_unknown'] is True
    assert not (attempt.path/'request-01-returned.json').exists()


def test_final_collector_requires_exact_original_journal_references(whole):
    w=whole;value=w.run();changed=copy.deepcopy(value)
    changed['governance']['team-a']['references'].pop('completion.json')
    with pytest.raises(s.evidence.AdmissionError):s.validate(changed,verify_provenance=w.verify,deadline=w.stop)
    with pytest.raises(s.evidence.AdmissionError):s.validate(value,verify_provenance=None,deadline=w.stop)
    with pytest.raises(s.evidence.AdmissionError):s.validate(value,verify_provenance=lambda **kw:False,deadline=w.stop)


def test_actual_outer_governance_fault_and_final_consumer_composition(whole,native_port,monkeypatch):
    w=whole;p=native_port;w.fault['http_mode']='timeout'
    p.auth['operation']=w.operation;p.f.auth['operation']=w.operation
    p.f.value['authorization']['operation']=w.operation
    recovered=p.f.recovered
    def replacement(*,deadline):
        p.model['replacement']=True
        return recovered(deadline=deadline)
    monkeypatch.setattr(p.f,'recovered',replacement)
    value=w.run(s.evidence.CASES[2],owned_fault=p.f)
    assert value['status']=='observed' and value['measurement']['cleanup']=={
        'requests':'joined','governance':'restored','fault':'clear'}
    assert value['measurement']['requests'][0]['decision']['reason_codes']==['evaluation_timeout']
    assert value['measurement']['requests'][1]['status_code']==429
    assert value['measurement']['requests'][2]['tenant_id']=='team-b'
    assert p.events.count('stop')==p.events.count('resume')==1
    assert len(w.holder['http'].calls)==3
    assert s.validate(value,verify_provenance=w.verify,deadline=w.stop)==value
    assert all(a.fd is None for a in w.holder['g'].attempts.values())
    assert all(w.f.c.bindings[t]['policy']==governance.policy_reference(w.f.c.base[t]) for t in s.evidence.TENANTS)
