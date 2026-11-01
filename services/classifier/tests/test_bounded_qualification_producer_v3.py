"""Actual output/score/report composition with synthetic corpora and child port."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from services.classifier import bounded_qualification_v3 as p
from services.classifier.tests.test_bounded_qualification_core import (
    Child,
    Clock,
    no_ml_or_network,
    verify,
)
from services.classifier.tests.test_bounded_qualification_evidence import (
    network,
    report,
)

__all__=['no_ml_or_network']


@pytest.fixture
def setup(tmp_path,monkeypatch):
    expected,raws,documents,_,_,_=report(3);output=tmp_path/'quality';clock=Clock();children=[];checks=[]
    plan={'output':str(output),'model_directory':str(tmp_path),'host_namespace':'net:[1]'}
    def validate(*args):checks.append('plan');return expected,copy.deepcopy(raws),copy.deepcopy(documents),{'synthetic':'closure'}
    def environment(value):assert value=={'synthetic':'closure'};checks.append('environment')
    class FakeChild(Child):
        def __init__(self,config):
            super().__init__(clock);self.loaded=json.loads(expected.loaded_json);children.append(self)
    monkeypatch.setattr(p.binding,'validate_plan',validate);monkeypatch.setattr(p,'verify_environment',environment)
    monkeypatch.setattr(p.network_isolation,'snapshot',lambda host:network())
    monkeypatch.setattr(p.process,'ProcessChild',FakeChild);monkeypatch.setattr(p,'corpus_projection',verify)
    affinity={0,1,2};monkeypatch.setattr(p.os,'sched_getaffinity',lambda _:set(affinity))
    monkeypatch.setattr(p.os,'sched_setaffinity',lambda _,value:(affinity.clear(),affinity.update(value)))
    cpu=iter([SimpleNamespace(ru_utime=0,ru_stime=0,ru_maxrss=0),SimpleNamespace(ru_utime=2,ru_stime=.1,ru_maxrss=1024)])
    monkeypatch.setattr(p.resource,'getrusage',lambda _:next(cpu))
    return plan,tmp_path,expected,output,children,checks,affinity


def test_full_original_files_retained_and_inputs_rechecked_before_result(setup):
    plan,root,_expected,output,children,checks,affinity=setup
    assert p.run_plan(plan,root,'9'*64)==0
    assert len(children)==1 and children[0].starts==children[0].closes==1 and children[0].attempts==3600
    assert checks==['plan','environment','plan','environment'] and affinity=={0,1,2}
    result=json.loads((output/'result.json').read_bytes())
    assert result['quality_pass'] and len((output/'cases.jsonl').read_bytes().splitlines())==3600
    assert result['cases_sha256']==p.sha((output/'cases.jsonl').read_bytes())
    assert not (output/'failure.json').exists()


def test_quality_failure_keeps_complete_original_rows_and_returns_one(setup,monkeypatch):
    plan,root,_,output,children,_,_=setup
    original=p.process.ProcessChild
    def failing(config):
        child=original(config);child.fail_at=20;return child
    monkeypatch.setattr(p.process,'ProcessChild',failing)
    assert p.run_plan(plan,root,'9'*64)==1
    result=json.loads((output/'result.json').read_bytes())
    assert not result['quality_pass'] and result['cost']['failed_cases']==3580
    assert len((output/'cases.jsonl').read_bytes().splitlines())==3600 and len(children)==1


def test_late_source_drift_keeps_rows_but_never_publishes_success(setup,monkeypatch):
    plan,root,_,output,_,_,affinity=setup;original=p.binding.validate_plan;calls=[]
    def changed(*args):
        calls.append(True)
        if len(calls)>1:raise ValueError('changed_source')
        return original(*args)
    monkeypatch.setattr(p.binding,'validate_plan',changed)
    with pytest.raises(ValueError,match='changed_source'):p.run_plan(plan,root,'9'*64)
    assert len((output/'cases.jsonl').read_bytes().splitlines())==3600
    assert (output/'failure.json').exists() and not (output/'result.json').exists() and affinity=={0,1,2}


def test_replaced_output_never_receives_result_or_failure(setup,monkeypatch):
    plan,root,_,output,_,_,affinity=setup;original=p.process.ProcessChild
    def swapping(config):
        child=original(config);evaluate=child.evaluate
        async def wrapped(*args):
            if args[0]==0:
                output.rename(root/'retained');output.mkdir(mode=0o700)
            return await evaluate(*args)
        child.evaluate=wrapped;return child
    monkeypatch.setattr(p.process,'ProcessChild',swapping)
    with pytest.raises(ValueError,match='output_changed'):p.run_plan(plan,root,'9'*64)
    assert not list(output.iterdir()) and (root/'retained/started.json').exists() and affinity=={0,1,2}


def test_preflight_refusal_never_creates_quality_or_child(setup,monkeypatch):
    plan,root,_,output,children,_,_=setup
    def refuse(*args):raise ValueError('actual_load_or_corpus_refused')
    monkeypatch.setattr(p.binding,'validate_plan',refuse)
    with pytest.raises(ValueError):p.run_plan(plan,root,'9'*64)
    assert not output.exists() and children==[]
