"""Actual generic child protocol with fake pipes; no models, sockets or processes."""
from __future__ import annotations

import asyncio
import builtins
import dataclasses
import json
import socket
import time
from types import SimpleNamespace

import pytest
from guardrails.evolution.classifier_contract import ClassifierError, canonical

from services.classifier import bounded_qualification_core as q
from services.classifier import bounded_qualification_process_v3 as p


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.split('.')[0] in {'torch','transformers','tokenizers','sentencepiece'}:pytest.fail('real ML import')
        return original(name,*args,**kwargs)
    def forbidden(*args,**kwargs):pytest.fail('unapproved external boundary')
    monkeypatch.setattr(builtins,'__import__',guarded)
    monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(asyncio,'create_subprocess_exec',forbidden)


@pytest.fixture
def config(tmp_path):
    root=tmp_path/'root';root.mkdir();model=tmp_path/'model';model.mkdir();output=tmp_path/'out';output.mkdir()
    pins={}
    for _,name,_ in p.module_order(3):
        if name is None:continue
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'# authenticated synthetic source\n')
        path.chmod(0o600);pins[name]=p.sha(path.read_bytes())
    identity={'repository':'synthetic/candidate','revision':'c'*40,'weights_sha256':'d'*64,'bundle_sha256':'e'*64,
        'candidate_manifest_sha256':'a'*64,'selection_index':3,'qualification_only':True,'production_enabled':False}
    names=('original','heldout_1','heldout_2','confirmation_1','confirmation_2','confirmation_3')
    spec=q.Specification(3,'a'*64,'b'*64,canonical(identity),canonical(pins),
        tuple(q.CorpusPin(name,str(n)*64) for n,name in enumerate(names)))
    loaded={'qualification_only':True,'quality_verified':False,'scoring_requests':0,
        **{k:identity[k] for k in ('repository','revision','weights_sha256')}}
    return p.ChildConfiguration(spec,root,model,output,canonical(loaded))


class Process:
    def __init__(self,config):
        self.returncode=None;self.kills=0;self.waits=0;self.writes=[];self.delay=False;self.drain_error=False
        self.rows=[canonical({'ready':True,'identity':config.specification.identity,'loaded':json.loads(config.loaded_metadata_json)})+b'\n']
        self.stdin=SimpleNamespace(write=self.write,drain=self.drain);self.stdout=SimpleNamespace(readline=self.readline)
    def write(self,raw):self.writes.append(raw)
    async def drain(self):
        if self.drain_error:raise BrokenPipeError
    async def readline(self):
        if self.delay:await asyncio.sleep(1)
        return self.rows.pop(0)
    def kill(self):self.kills+=1;self.returncode=-9
    async def communicate(self):self.waits+=1;return b'',b''


def spawn(monkeypatch,config):
    process=Process(config);calls=[]
    async def fake(*args,**kwargs):calls.append((args,kwargs));return process
    monkeypatch.setattr(asyncio,'create_subprocess_exec',fake)
    return process,calls


def reply(config,**changes):
    value={'identity':config.specification.identity,'sequence':0,'complete':True,'score':.9,'windows':1};value.update(changes)
    return canonical(value)+b'\n'


def test_one_captured_child_sends_only_text_and_protocol_identity(monkeypatch,config):
    process,calls=spawn(monkeypatch,config);process.rows.append(reply(config))
    async def scenario():
        child=p.ProcessChild(config);await child.start()
        value=await child.evaluate(0,'synthetic content',time.monotonic()+1)
        assert value=={'complete':True,'score':.9,'windows':1};await child.close()
        assert child.attempts==1 and child.unavailable
        with pytest.raises(ClassifierError):await child.evaluate(1,'later',time.monotonic()+1)
        with pytest.raises(ValueError):await child.start()
    asyncio.run(scenario())
    assert len(calls)==1 and process.kills==process.waits==1 and len(process.writes)==1
    request=json.loads(process.writes[0]);assert set(request)=={'identity','sequence','text','deadline'}
    assert request['text']=='synthetic content'
    argv,kwargs=calls[0];assert argv[1:4]==('-I','-S','-B') and kwargs['stderr']==asyncio.subprocess.DEVNULL
    assert 'PYTHONPATH' not in kwargs['env'] and kwargs['env']['HF_HUB_OFFLINE']=='1'


@pytest.mark.parametrize('change',['changed','missing','added_namespace','symlink'])
def test_source_drift_refuses_before_any_process_creation(monkeypatch,config,change):
    path=config.root/'services/classifier/worker.py'
    if change=='changed':path.write_bytes(b'changed')
    elif change=='missing':path.unlink()
    elif change=='added_namespace':(config.root/'services/__init__.py').write_bytes(b'foreign')
    else:path.rename(path.with_suffix('.old'));path.symlink_to(path.with_suffix('.old'))
    with pytest.raises((ValueError,OSError)):asyncio.run(p.ProcessChild(config).start())


@pytest.mark.parametrize('change',['identity','loaded','extra','false_ready','dead'])
def test_handshake_refusal_closes_exact_child(monkeypatch,config,change):
    process,calls=spawn(monkeypatch,config);value=json.loads(process.rows[0])
    if change=='identity':value['identity']['revision']='f'*40
    elif change=='loaded':value['loaded']['quality_verified']=True
    elif change=='extra':value['extra']=True
    elif change=='false_ready':value['ready']=False
    else:process.returncode=1
    process.rows[0]=canonical(value)+b'\n'
    with pytest.raises(ValueError):asyncio.run(p.ProcessChild(config).start())
    assert len(calls)==1 and process.waits==1 and process.kills==(change!='dead')


@pytest.mark.parametrize('changes',[{'sequence':True},{'sequence':1},{'score':True},{'score':1.1},{'complete':False},
    {'windows':0},{'windows':17},{'windows':True},{'extra':'unexpected'}])
def test_malformed_response_retires_worker_without_partial_success(monkeypatch,config,changes):
    process,calls=spawn(monkeypatch,config);process.rows.append(reply(config,**changes))
    async def scenario():
        child=p.ProcessChild(config);await child.start()
        with pytest.raises(ClassifierError) as error:await child.evaluate(0,'synthetic',time.monotonic()+1)
        assert error.value.code=='evaluator_unavailable' and child.unavailable and child.attempts==1
    asyncio.run(scenario());assert process.kills==process.waits==len(calls)==1


def test_actual_absolute_timeout_closes_without_reload(monkeypatch,config):
    process,calls=spawn(monkeypatch,config)
    async def scenario():
        child=p.ProcessChild(config);await child.start();process.delay=True
        with pytest.raises(ClassifierError) as error:await child.evaluate(0,'synthetic',time.monotonic()+.005)
        assert error.value.code=='evaluation_timeout' and child.unavailable
        with pytest.raises(ClassifierError):await child.evaluate(1,'later',time.monotonic()+1)
    asyncio.run(scenario());assert process.kills==process.waits==len(calls)==len(process.writes)==1


def test_explicit_finite_inspection_refusal_is_not_retried(monkeypatch,config):
    process,calls=spawn(monkeypatch,config)
    process.rows.append(canonical({'identity':config.specification.identity,'sequence':0,'error':'inspection_incomplete'})+b'\n')
    async def scenario():
        child=p.ProcessChild(config);await child.start()
        with pytest.raises(ClassifierError) as error:await child.evaluate(0,'synthetic',time.monotonic()+1)
        assert error.value.code=='inspection_incomplete' and child.attempts==1
        await child.close()
    asyncio.run(scenario());assert len(process.writes)==len(calls)==1


def test_cancellation_closes_owned_child_before_propagating(monkeypatch,config):
    process,_=spawn(monkeypatch,config)
    async def scenario():
        child=p.ProcessChild(config);await child.start();process.delay=True
        task=asyncio.create_task(child.evaluate(0,'synthetic',time.monotonic()+5));await asyncio.sleep(.001);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert child.unavailable
    asyncio.run(scenario());assert process.kills==process.waits==1


@pytest.mark.parametrize('value',[float('nan'),True,'future'])
def test_invalid_deadline_never_writes(monkeypatch,config,value):
    process,_=spawn(monkeypatch,config)
    async def scenario():
        child=p.ProcessChild(config);await child.start()
        with pytest.raises(ClassifierError):await child.evaluate(0,'synthetic',value)
        await child.close()
    asyncio.run(scenario());assert process.writes==[]


def test_generic_configuration_does_not_admit_disqualified_candidate01(config):
    with pytest.raises(ValueError):p.module_order(1)
    with pytest.raises(ValueError):p.module_order(2)
    bad=json.loads(config.loaded_metadata_json);bad['quality_verified']=True
    with pytest.raises(ValueError):dataclasses.replace(config,loaded_metadata_json=canonical(bad))
