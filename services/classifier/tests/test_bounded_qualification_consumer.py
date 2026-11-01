"""Closed parent invocation binding plus complete synthetic row revalidation."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from guardrails.evolution.classifier_contract import canonical, parse

from services.classifier import bounded_qualification_consumer as c
from services.classifier.tests.test_bounded_qualification_core import (
    no_ml_or_network,
    verify,
)
from services.classifier.tests.test_bounded_qualification_evidence import report
from services.classifier.tests.test_bounded_qualification_plan import put

__all__=['no_ml_or_network']


@pytest.fixture
def capsule(tmp_path,monkeypatch):
    expected,corpora,documents,start,result,cases=report()
    plan={'output':str(tmp_path/'run/quality')};plan_ref=put(tmp_path/'plan.json',plan)
    import dataclasses
    expected=dataclasses.replace(expected,plan_sha256=plan_ref['sha256'])
    start['plan_sha256']=result['plan_sha256']=plan_ref['sha256']
    launcher=put(tmp_path/'launcher.py',b'# reviewed synthetic launcher\n')
    support=put(tmp_path/'support.py',b'# reviewed synthetic support\n')
    monkeypatch.setattr(c,'LAUNCHER_SHA256',launcher['sha256']);monkeypatch.setattr(c,'SUPPORT_SHA256',support['sha256'])
    monkeypatch.setattr(c.binding,'validate_plan',lambda *args:(expected,copy.deepcopy(corpora),documents,{}))
    monkeypatch.setattr(c,'corpus_projection',verify)
    quality={name:put(tmp_path/'run/quality'/name,value) for name,value in
        [('started.json',start),('result.json',result),('cases.jsonl',cases)]}
    inv_start={'schema_version':'bounded-initial-invocation-v1','status':'running','started_at':'2026-10-12T15:59:00+00:00',
        'plan_sha256':plan_ref['sha256'],'launcher_sha256':launcher['sha256'],'support_sha256':support['sha256'],
        'output':plan['output'],'maximum_seconds':3600,'automatic_retry':False,'warmup_requests':0,'sources':expected.specification.sources}
    cleanup={'schema_version':'wolf-full-initial-cleanup-v1','group_signal_requested':True,'group_already_absent':False,
        'leader_reaped':True,'leader_exit_observed':True,'errors':[]}
    invocation={name:put(tmp_path/'run/invocation'/name,value) for name,value in [('started.json',inv_start),('cleanup.json',cleanup)]}
    inv_result={**inv_start,'status':'completed','completed_at':'2026-10-12T16:02:00+00:00','exit_code':0,'elapsed_seconds':180.,
        'cleanup_sha256':invocation['cleanup.json']['sha256'],'quality_report_pins':{name:ref['sha256'] for name,ref in quality.items()},
        'enabled_model_changed':False,'final_selected_runtime_rerun_required':True}
    invocation['result.json']=put(tmp_path/'run/invocation/result.json',inv_result)
    value={'schema_version':'bounded-initial-parent-completion-v1','scope':c.evidence.SCOPE,'plan':plan_ref,'launcher':launcher,
        'support':support,'invocation':invocation,'quality':quality}
    return tmp_path,value,put(tmp_path/'completion.json',value)


def test_actual_private_reader_and_original_all_row_consumer(capsule):
    root,_,ref=capsule;result=c.verify(ref,root=root)
    assert result['status']=='qualified_initial_only' and result['cases']==3000
    assert result['parent_capsule_sha256']==ref['sha256'] and not result['production_enabled']


@pytest.mark.parametrize('field,value',[('exit_code',1),('exit_code',True),('automatic_retry',True),('warmup_requests',1),
    ('maximum_seconds',3601),('elapsed_seconds',3601),('enabled_model_changed',True),('final_selected_runtime_rerun_required',False),
    ('launcher_sha256','0'*64),('support_sha256','0'*64),('plan_sha256','0'*64),('sources',{}),('quality_report_pins',{}),
    ('completed_at','2026-10-12T15:58:00+00:00'),('extra',True)])
def test_parent_pinned_capsule_cannot_launder_failed_or_inconsistent_invocation(capsule,field,value):
    root,capsule_value,_=capsule;reference=capsule_value['invocation']['result.json'];result=parse(Path(reference['path']).read_bytes())
    result[field]=value;capsule_value['invocation']['result.json']=put(Path(reference['path']),result)
    ref=put(root/'completion.json',capsule_value)
    with pytest.raises(ValueError):c.verify(ref,root=root)


@pytest.mark.parametrize('file',['plan','launcher','support','parent','quality','invocation','lost_quality','failure'])
def test_original_file_loss_or_drift_refuses(capsule,file):
    root,value,ref=capsule
    if file in {'plan','launcher','support'}:put(Path(value[file]['path']),b'{}')
    elif file=='parent':put(Path(ref['path']),b'{}')
    elif file=='failure':put(root/'run/invocation/failure.json',b'{}')
    elif file=='lost_quality':Path(value['quality']['cases.jsonl']['path']).unlink()
    else:put(Path(value[file]['result.json']['path']),b'{}')
    with pytest.raises((ValueError,OSError)):c.verify(ref,root=root)


def test_refuse_until_reviewed_launcher_pin_is_released(capsule,monkeypatch):
    root,_,ref=capsule;monkeypatch.setattr(c,'LAUNCHER_SHA256',None)
    with pytest.raises(ValueError,match='launcher_unreleased'):c.verify(ref,root=root)


def test_consumer_reopens_files_after_complete_row_validation(capsule,monkeypatch):
    root,value,ref=capsule;original=c.evidence.validate_report
    def drift(*args,**kwargs):
        result=original(*args,**kwargs);put(Path(value['quality']['cases.jsonl']['path']),b'{}');return result
    monkeypatch.setattr(c.evidence,'validate_report',drift)
    with pytest.raises(ValueError):c.verify(ref,root=root)


def test_failed_english_corpus_stays_failed_even_with_original_parent_exit_zero(capsule):
    root,value,_=capsule
    rows=[parse(line) for line in Path(value['quality']['cases.jsonl']['path']).read_bytes().splitlines()]
    n=0
    for row in rows:
        if row['corpus']=='confirmation_2' and row['language']=='en' and not row['expected'] and n<11:
            row.update(score=.95,predicted=True);n+=1
    raw=b''.join(canonical(row)+b'\n' for row in rows)
    value['quality']['cases.jsonl']=put(Path(value['quality']['cases.jsonl']['path']),raw)
    # Even updating the parent's report pin cannot excuse stale/false matrices.
    inv=parse(Path(value['invocation']['result.json']['path']).read_bytes());inv['quality_report_pins']['cases.jsonl']=c.sha(raw)
    value['invocation']['result.json']=put(Path(value['invocation']['result.json']['path']),inv)
    with pytest.raises(ValueError):c.verify(put(root/'completion.json',value),root=root)
