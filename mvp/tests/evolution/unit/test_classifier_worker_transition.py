"""Owned pidfd transition program; every process/read/signal boundary is fake."""
from __future__ import annotations

import copy
import signal

import pytest

from mvp.tests.evolution.e2e import classifier_worker_transition as s
from mvp.tests.evolution.unit import test_classifier_admission as existing

no_actual_operations = existing.no_actual_operations


def process(pid=9, state='S'):
    return {'pid': pid, 'start_ticks': 100+pid, 'uid': 10001, 'command_sha256': 'a'*64,
        'cgroup_sha256': 'b'*64, 'pid_namespace': 'pid:[1234]', 'state': state}


def signal_port(actual, *, poll=(False, False), open_error=None, send_error=None):
    events=[]; outcomes=iter(poll)
    def opened(pid, flags):
        events.append(('open', pid, flags))
        if open_error: raise open_error
        return 77
    class Poll:
        def register(self, fd, flag): assert fd == 77; events.append('register')
        def poll(self, timeout): assert timeout == 0; return [(77, 1)] if next(outcomes) else []
    def proc(pid):
        events.append(('identity', pid))
        if isinstance(actual, Exception): raise actual
        return copy.deepcopy(actual)
    def send(fd, sig, info, flags):
        events.append(('signal', fd, sig, info, flags))
        if send_error: raise send_error
    return events, {'proc': proc, 'open_pidfd': opened, 'send': send,
        'closed': lambda fd: events.append(('close', fd)), 'poller': Poll}


@pytest.mark.parametrize('operation,state,sig', [('stop','R',signal.SIGSTOP),('stop','S',signal.SIGSTOP),('resume','T',signal.SIGCONT)])
def test_held_pidfd_identity_is_checked_before_signalling(operation, state, sig):
    expected=process();events,ports=signal_port(process(state=state))
    assert s.signal_owned(expected,operation,**ports)=={'status':'signal_sent','signalled':True}
    assert events[0]==('open',9,0) and events[2]==('identity',9)
    assert events[-2:]==[('signal',77,sig,None,0),('close',77)]


@pytest.mark.parametrize('field,value', [('uid',0),('command_sha256','c'*64),('cgroup_sha256','d'*64),
    ('pid_namespace','pid:[9876]'),('pid',10),('extra','x')])
@pytest.mark.parametrize('operation', ['stop','resume'])
def test_foreign_owned_process_never_receives_signal(field,value,operation):
    actual=process(state='T' if operation=='resume' else 'S');actual[field]=value
    events,ports=signal_port(actual)
    with pytest.raises(s.TransitionRefusal):s.signal_owned(process(),operation,**ports)
    assert not any(row[0]=='signal' for row in events if isinstance(row,tuple))
    assert events[-1]==('close',77)


def test_cleanup_pid_reuse_is_no_signal_but_initial_stop_refuses():
    actual=process();actual['start_ticks']+=1
    events,ports=signal_port(actual)
    assert s.signal_owned(process(),'resume',**ports)=={'status':'pid_reused_not_signalled','signalled':False}
    assert events[-1]==('close',77)
    events,ports=signal_port(actual)
    with pytest.raises(s.TransitionRefusal):s.signal_owned(process(),'stop',**ports)
    assert not any(isinstance(row,tuple) and row[0]=='signal' for row in events)


@pytest.mark.parametrize('failure', ['open_gone','already_dead','dies_before_signal','read_gone','send_gone'])
def test_termination_races_never_signal_reused_numeric_pid(failure):
    events,ports=signal_port(FileNotFoundError() if failure=='read_gone' else process(state='T'),
        poll=(True,) if failure=='already_dead' else (False,True) if failure=='dies_before_signal' else (False,False),
        open_error=ProcessLookupError() if failure=='open_gone' else None,
        send_error=ProcessLookupError() if failure=='send_gone' else None)
    assert s.signal_owned(process(),'resume',**ports)=={'status':'gone','signalled':False}
    attempts=[row for row in events if isinstance(row,tuple) and row[0]=='signal']
    assert attempts==([('signal',77,signal.SIGCONT,None,0)] if failure=='send_gone' else [])
    if failure!='open_gone':assert events[-1]==('close',77)


@pytest.mark.parametrize('state',['R','S','D'])
def test_cleanup_leaves_live_running_original_unchanged(state):
    events,ports=signal_port(process(state=state))
    assert s.signal_owned(process(),'resume',**ports)=={'status':'already_running','signalled':False}
    assert not any(isinstance(row,tuple) and row[0]=='signal' for row in events)


@pytest.mark.parametrize('state',['D','T','Z','X'])
def test_initial_stop_refuses_unexpected_process_state(state):
    events,ports=signal_port(process(state=state))
    with pytest.raises(s.TransitionRefusal):s.signal_owned(process(),'stop',**ports)
    assert events[-1]==('close',77)


@pytest.fixture
def program(monkeypatch):
    original={'supervisor':process(1),'child':process()}
    spec={'supervisor_command_sha256':'a'*64,'worker_command_sha256':'a'*64,'worker_files':{'/app/worker.py':'f'*64}}
    events=[]; child=[copy.deepcopy(original['child'])]
    def proc(pid):
        if pid==1:return copy.deepcopy(original['supervisor'])
        if child[0] is None:raise FileNotFoundError
        return copy.deepcopy(child[0])
    monkeypatch.setattr(s,'process',proc)
    def capture(value):assert value==spec;return {'supervisor':proc(1),'child':proc(9)}
    monkeypatch.setattr(s,'capture',capture)
    monkeypatch.setattr(s,'sources',lambda value:events.append('sources'))
    def signalling(expected,operation):
        assert expected==original['child'];events.append(operation)
        child[0]['state']='T' if operation=='stop' else 'S'
        return {'status':'signal_sent','signalled':True}
    monkeypatch.setattr(s,'signal_owned',signalling)
    return original,spec,events,child


def test_original_stop_waits_for_stopped_identity_and_rechecks_sources(program):
    original,spec,events,_=program
    value=s.operate({'operation':'stop','spec':spec,'expected':original})
    assert value['status']=='stopped' and value['child']['state']=='T'
    assert events==['sources','stop','sources']


def test_readonly_status_and_capture_have_no_signal(program):
    original,spec,events,_=program
    value=s.operate({'operation':'capture','spec':spec,'expected':None})
    assert value=={'status':'captured',**original}
    value=s.operate({'operation':'status','spec':spec,'expected':original})
    assert value['old_terminated'] is False and set(events)=={'sources'}


def test_native_sources_are_verified_without_importing_worker(monkeypatch):
    raw=b'# synthetic worker source\n';spec={'worker_files':{'/app/worker.py':s.sha(raw)}};paths=[]
    def read(path,maximum):paths.append((path,maximum));return raw
    monkeypatch.setattr(s,'read',read)
    s.sources(spec);assert paths==[('/app/worker.py',2*1024*1024)]
    spec['worker_files']['/app/worker.py']='0'*64
    with pytest.raises(s.TransitionRefusal):s.sources(spec)


@pytest.mark.parametrize('path',['/tmp/source.py','/app/../source.py','/app/child//source.py','/app/script.sh'])
def test_source_paths_cannot_expand_fault_authority(monkeypatch,path):
    monkeypatch.setattr(s,'read',lambda *a:pytest.fail('read foreign source'))
    with pytest.raises(s.TransitionRefusal):s.sources({'worker_files':{path:'a'*64}})


def test_main_failure_never_exposes_arguments_or_exception(capsys,monkeypatch):
    monkeypatch.setattr(s.sys,'argv',['program','{"private":"credential"}'])
    assert s.main()==1 and capsys.readouterr().out=='{"status":"refused"}\n'
