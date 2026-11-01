"""Dedicated transition proof and fixed native port; no processes are signalled."""
from __future__ import annotations

import copy
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace as N
from uuid import uuid4

import pytest

from mvp.evolution import classifier_admission_measurements as m
from mvp.tests.evolution.e2e import classifier_admission_fault as s
from mvp.tests.evolution.unit import test_classifier_admission as existing
from mvp.tests.evolution.unit.test_classifier_worker_transition import process

no_actual_operations = existing.no_actual_operations


def authority(program=b'# exact synthetic fixed program\n'):
    return {'schema_version':'evolution-classifier-worker-transition-v1','operation':str(uuid4()),
        'parent_invocation_sha256':'1'*64,'preflight_sha256':'2'*64,'owner_id':'owner',
        'node_id':'3'*64,'pod_uid':str(uuid4()),'container_id':'4'*64,'selection_sha256':'5'*64,
        'images_sha256':'6'*64,'credentials_sha256':'7'*64,'old_epoch':'8'*32,
        'original':{'supervisor':process(1),'child':process()},
        'supervisor_command_sha256':'a'*64,'worker_command_sha256':'a'*64,
        'worker_files':{'/app/worker.py':'a'*64},'program_sha256':hashlib.sha256(program).hexdigest(),
        'maximum_seconds':180}


def ready(auth, epoch=None):
    return {'status':'ready','epoch':epoch or auth['old_epoch'],
        **{k:auth[k] for k in ('selection_sha256','images_sha256','credentials_sha256')}}


def proof():
    auth=authority();original=copy.deepcopy(auth['original']);stopped=copy.deepcopy(original);stopped['child']['state']='T'
    held={'status':'observed','old_terminated':False,'old':copy.deepcopy(stopped['child']),**stopped}
    replacement={'status':'observed','old_terminated':True,'old':None,
        'supervisor':copy.deepcopy(original['supervisor']),'child':process(12)}
    rows=[('intent',{'original':original,'ready':ready(auth)}),('stopped',{'status':'stopped',**stopped}),
        ('held',held),('replacement',{'process':replacement,'ready':ready(auth,'9'*32)}),
        ('recovered',{}),('cleanup',{'status':'gone','signalled':False})]
    times=[100.,101.,102.,104.,105.,106.]
    value={'status':'recovered','authorization':auth,'events':[{'kind':kind,'observed':stamp,'value':value}
        for (kind,value),stamp in zip(rows,times,strict=True)],'cleanup':'clear'}
    return value,{'started':101.5,'finished':103.5},{'finished':101.9}


def test_closed_old_to_new_proof_has_rules_peer_while_original_still_stopped():
    value,first,peer=proof();m.transition(value,operation=value['authorization']['operation'],first=first,peer=peer)
    # Later PID reuse is independently observed and cleanup never signals it.
    value['events'][3]['value']['process']['old']=process();value['events'][3]['value']['process']['old']['start_ticks']+=1
    value['events'][5]['value']={'status':'pid_reused_not_signalled','signalled':False}
    m.transition(value,operation=value['authorization']['operation'],first=first,peer=peer)


@pytest.mark.parametrize('fault',['foreign_operation','supervisor_changed','worker_uid','worker_command','worker_cgroup',
    'worker_namespace','pid_reused_before_stop','not_stopped','old_alive','same_child','same_epoch','selection_drift',
    'credential_drift','peer_after_hold','first_before_stop','replacement_before_failure','late_cleanup',
    'cleanup_signalled','extra_event','missing_event','bool_pid','extra_authority'])
def test_consumer_refuses_unowned_or_false_recovery(fault):
    value,first,peer=proof();events=value['events'];a=value['authorization'];old=a['original']['child']
    if fault=='foreign_operation':a['operation']=str(uuid4())
    elif fault=='supervisor_changed':events[3]['value']['process']['supervisor']['start_ticks']+=1
    elif fault.startswith('worker_'):
        key={'worker_uid':'uid','worker_command':'command_sha256','worker_cgroup':'cgroup_sha256','worker_namespace':'pid_namespace'}[fault]
        events[3]['value']['process']['child'][key]=0 if key=='uid' else 'pid:[999]' if key=='pid_namespace' else '0'*64
    elif fault=='pid_reused_before_stop':events[1]['value']['child']['start_ticks']+=1
    elif fault=='not_stopped':events[2]['value']['child']['state']='S'
    elif fault=='old_alive':events[3]['value']['process']['old']=copy.deepcopy(old)
    elif fault=='same_child':events[3]['value']['process']['child']=copy.deepcopy(old)
    elif fault=='same_epoch':events[3]['value']['ready']['epoch']=a['old_epoch']
    elif fault=='selection_drift':events[3]['value']['ready']['selection_sha256']='0'*64
    elif fault=='credential_drift':events[3]['value']['ready']['credentials_sha256']='0'*64
    elif fault=='peer_after_hold':peer['finished']=102.1
    elif fault=='first_before_stop':first['started']=100.5
    elif fault=='replacement_before_failure':first['finished']=104.1
    elif fault=='late_cleanup':events[-1]['observed']=281.
    elif fault=='cleanup_signalled':events[-1]['value']={'status':'signal_sent','signalled':True}
    elif fault=='extra_event':events.append(copy.deepcopy(events[-1]))
    elif fault=='missing_event':events.pop(2)
    elif fault=='bool_pid':a['original']['child']['pid']=True
    else:a['unknown']=False
    operation=str(uuid4()) if fault=='foreign_operation' else a['operation']
    with pytest.raises((m.AdmissionError,KeyError)):
        m.transition(value,operation=operation,first=first,peer=peer)


@pytest.fixture
def native_port(monkeypatch):
    program=b'# exact synthetic fixed program\n';auth=authority(program)
    executor=object.__new__(s.native.NativeReadOnlyExecutor);executor.preflight={'synthetic':True}
    executor.manifest={'synthetic_manifest':True};executor.root=Path('/synthetic/root')
    executor._attempts=[]
    events=[];records=[];bound=(('/owned/kubectl','--kubeconfig','/dev/stdin'),'synthetic static config','classifier-original')
    model={'replacement':False}
    def invoke(args,family,deadline,maximum,*,input_text):
        assert family=='kubectl' and args[:3]==bound[0] and maximum==32768 and input_text==bound[1]
        assert deadline<=max(stop,time.monotonic()+s.CLEANUP_SECONDS) and '--cache-dir=/proc/self/fd' in args
        assert args[-2]==program.decode()
        value=s.native.contract.parse(args[-1].encode());op=value['operation'];events.append(op)
        if op=='capture':return s.native.contract.canonical({'status':'captured',**auth['original']}).decode()
        if op=='stop':
            assert records[-1]['events'][-1]['kind']=='intent'
            row=copy.deepcopy(auth['original']);row['child']['state']='T'
            return s.native.contract.canonical({'status':'stopped',**row}).decode()
        if op=='resume':return '{"status":"gone","signalled":false}'
        row=copy.deepcopy(auth['original']);row['child']['state']='T'
        result={'status':'observed','old_terminated':False,'old':copy.deepcopy(row['child']),**row}
        if model['replacement']:result.update(old_terminated=True,old=None,child=process(12))
        return s.native.contract.canonical(result).decode()
    monkeypatch.setattr(s.native.NativeReadOnlyExecutor,'_invoke',lambda self,*a,**kw:invoke(*a,**kw))
    stop=time.monotonic()+30
    def authorize(a,p,*,deadline):assert a==auth and p==executor.preflight and deadline<=stop;events.append('authorize');return True
    def record(name,value):records.append(copy.deepcopy(value));events.append('record-'+name)
    def readiness(*,deadline):assert deadline<=stop;return ready(auth,'9'*32 if model['replacement'] else None)
    def verify(value,*,deadline):assert value['events'][-1]['kind']=='replacement' and deadline<=stop;events.append('verify');return True
    f=s.OwnedClassifierFault(executor,auth,program,record=record,readiness=readiness,authorize=authorize,verify=verify,deadline=stop)
    def objects(deadline,*,executor=None):
        assert deadline<=max(stop,time.monotonic()+s.CLEANUP_SECONDS)
        if executor is not None:executor.budget.check()
        events.append('objects');return bound
    monkeypatch.setattr(f,'_objects',objects)
    return N(f=f,auth=auth,events=events,records=records,model=model,deadline=stop)


def test_actual_fixed_port_keeps_intent_before_stop_and_fast_status_readonly(native_port):
    p=native_port;p.f.suspend(deadline=p.deadline);objects=p.events.count('objects')
    p.f.stopped(deadline=p.deadline)
    assert p.events.count('objects')==objects and p.events.count('stop')==1
    p.model['replacement']=True;p.f.recovered(deadline=p.deadline);assert p.f.cleanup(deadline=p.deadline)=='clear'
    assert p.f.value['status']=='recovered' and p.f.value['cleanup']=='clear'
    assert [v['kind'] for v in p.f.value['events']]==['intent','stopped','held','replacement','recovered','cleanup']
    assert p.events.index('authorize')<p.events.index('capture')<p.events.index('stop')
    assert p.events.count('verify')==1


@pytest.mark.parametrize('operation',['capture','stop','resume'])
def test_fast_path_cannot_be_used_to_signal_or_capture(operation,native_port):
    p=native_port;p.f.suspend(deadline=p.deadline);before=list(p.events)
    with pytest.raises(m.AdmissionError):p.f._call(operation,deadline=p.deadline,during_request=True)
    assert p.events==before


def test_refused_authority_never_reads_or_signals(native_port):
    p=native_port;p.f.authorize=lambda *a,**k:False
    with pytest.raises(m.AdmissionError):p.f.suspend(deadline=p.deadline)
    assert p.events==[]


def test_intent_retention_failure_prevents_signal_and_keeps_original(native_port):
    p=native_port
    def fail(*a):raise OSError('synthetic private storage error')
    p.f.record=fail
    with pytest.raises(OSError):p.f.suspend(deadline=p.deadline)
    assert 'capture' in p.events and 'stop' not in p.events and p.f.expected==p.auth['original']


def test_deadline_failure_has_no_native_retry(native_port):
    p=native_port
    with pytest.raises(m.AdmissionError):p.f.suspend(deadline=time.monotonic()-1)
    assert p.events==[]


def test_expired_observation_still_resumes_only_original_child_once(native_port):
    p=native_port;p.f.suspend(deadline=p.deadline)
    original=p.f.executor;expired=time.monotonic()-1;p.f.deadline=expired
    original.budget=N(check=lambda:(_ for _ in ()).throw(AssertionError('expired acceptance budget')))
    assert p.f.cleanup(deadline=expired)=='clear'
    assert p.f.executor is original and p.f.deadline==expired
    assert p.events.count('resume')==1 and p.events.count('stop')==1
    with pytest.raises(m.AdmissionError):p.f.cleanup(deadline=expired)
    assert p.events.count('resume')==1
    with pytest.raises(m.AdmissionError):p.f.stopped(deadline=expired)


@pytest.mark.parametrize('change',['manifest','preflight','root','program','authority','original','route'])
def test_cleanup_authority_drift_refuses_before_resume(native_port,change):
    p=native_port;p.f.suspend(deadline=p.deadline)
    if change=='manifest':p.f.executor.manifest['extra']=True
    elif change=='preflight':p.f.executor.preflight['extra']=True
    elif change=='root':p.f.executor.root=Path('/foreign/root')
    elif change=='program':p.f.program+=b'changed'
    elif change=='authority':p.f.auth['old_epoch']='f'*32
    elif change=='original':p.f.expected['child']['pid']+=1
    else:p.f.bound_transport=(('/foreign/kubectl',),'foreign','foreign')
    with pytest.raises(m.AdmissionError):p.f.cleanup(deadline=time.monotonic()-1)
    assert p.events.count('resume')==0 and p.f.cleanup_started is True


observation = existing.fixture


def test_actual_observer_and_fixed_fault_prove_failure_peer_rules_and_replacement(observation,native_port,monkeypatch):
    import asyncio
    p=native_port;f=observation;f.http.mode='timeout'
    recover=p.f.recovered
    def replacement(*,deadline):p.model['replacement']=True;return recover(deadline=deadline)
    monkeypatch.setattr(p.f,'recovered',replacement)
    observed=f.make(m.CASES[2],fault=p.f);observed.value['operation']=p.auth['operation']
    asyncio.run(observed.run(deadline=p.deadline))
    value=observed.complete(restoration='restored')
    assert value['requests'][0]['decision']['action']=='block'
    assert value['requests'][0]['decision']['reason_codes']==['evaluation_timeout']
    assert value['requests'][2]['decision']['finding_rules']==[f.sub['team-b']['rule_id']]
    assert value['fault']['events'][-1]['value']=={'status':'gone','signalled':False}
    assert p.events.count('stop')==p.events.count('resume')==1 and len(f.http.calls)==3


@pytest.mark.parametrize('primary',[False,True])
def test_cleanup_failure_retains_prefix_and_original_error(observation,primary):
    import asyncio
    f=observation
    class Fault:
        def __init__(self):self.value={'status':'failed'}
        def cleanup(self,*,deadline):raise OSError('synthetic cleanup failure')
    fault=Fault();observed=f.make(m.CASES[2],fault=fault)
    async def failed(deadline):
        if primary:raise ValueError('synthetic observation failure')
    observed.saturation=failed
    with pytest.raises(ValueError if primary else OSError) as captured:
        asyncio.run(observed.run(deadline=time.monotonic()+5))
    assert f.records[-1]['cleanup']['fault']=='failed' and f.records[-1]['cleanup']['requests']=='joined'
    assert f.records[-1]['status']=='failed' and f.records[-1]['elapsed_ms']>=0
    if primary:assert captured.value.__notes__==['admission_fault_cleanup_failed']
