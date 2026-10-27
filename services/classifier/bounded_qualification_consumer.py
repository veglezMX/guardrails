"""Initial qualification evidence, anchored by an external parent completion pin.

This consumer never supplies deployed acceptance or selected-worker proof. The
parent creates its capsule only after the original reviewed invocation exits.
The caller supplies that capsule's independently established raw SHA; arbitrary
self-consistent reports, initial failures and stale source cannot pass.
"""
from __future__ import annotations

import os
from pathlib import Path

from evolution.classifier_provenance import corpus_projection
from guardrails.evolution.classifier_contract import canonical, parse

from services.classifier import bounded_qualification_core as core
from services.classifier import bounded_qualification_evidence as evidence
from services.classifier import bounded_qualification_plan as binding
from services.classifier.wolf_full_qualification import reference, sha

LAUNCHER_SHA256='1c409eba5fc3ed6bdcf2d277f0fe10d2e56922e6c4d6b5eef41d9bfb8da6b70d'  # Filled only with the final independently reviewed launcher.
SUPPORT_SHA256='b432692ca1e2ff9c4a4917c40075846f5df8667929edd5dde8321539e7d43da3'
START_FIELDS={'schema_version','status','started_at','plan_sha256','launcher_sha256','support_sha256',
    'output','maximum_seconds','automatic_retry','warmup_requests','sources'}
RESULT_FIELDS=START_FIELDS|{'completed_at','exit_code','elapsed_seconds','cleanup_sha256','quality_report_pins',
    'enabled_model_changed','final_selected_runtime_rerun_required'}


def require(value,code):core.require(value,code)


def verify(capsule_ref, *, root):
    """The caller, not this file or the capsule itself, authenticates capsule_ref."""
    require(type(LAUNCHER_SHA256) is str,'launcher_unreleased')
    raw=reference(capsule_ref);capsule=parse(raw,limit=65536)
    require(type(capsule) is dict and set(capsule)=={'schema_version','scope','plan','launcher','support','invocation','quality'}
        and capsule['schema_version']=='bounded-initial-parent-completion-v1'
        and capsule['scope']==evidence.SCOPE,'parent_capsule')
    require(capsule['launcher']['sha256']==LAUNCHER_SHA256 and capsule['support']['sha256']==SUPPORT_SHA256,'reviewed_launcher')
    launcher=reference(capsule['launcher']);support=reference(capsule['support'])
    plan_raw=reference(capsule['plan']);plan=parse(plan_raw,limit=1024*1024)
    expected,corpora,_,_=binding.validate_plan(plan,root,sha(plan_raw))
    invocation,quality=capsule['invocation'],capsule['quality']
    require(type(invocation) is dict and set(invocation)=={'started.json','result.json','cleanup.json'}
        and type(quality) is dict and set(quality)=={'started.json','result.json','cases.jsonl'},'report_files')
    output=Path(plan['output']);directory=output.parent/'invocation'
    require(all(Path(row['path'])==directory/name for name,row in invocation.items())
        and all(Path(row['path'])==output/name for name,row in quality.items())
        and not os.path.lexists(directory/'failure.json') and not os.path.lexists(output/'failure.json'),'report_directories')
    inv={name:reference(row) for name,row in invocation.items()};reports={name:reference(row) for name,row in quality.items()}
    start,result,cleanup=(parse(inv[name],limit=65536) for name in ('started.json','result.json','cleanup.json'))
    require(set(start)==START_FIELDS and set(result)==RESULT_FIELDS,'invocation_schema')
    require(start['schema_version']==result['schema_version']=='bounded-initial-invocation-v1'
        and start['status']=='running' and result['status']=='completed'
        and canonical({name:result[name] for name in START_FIELDS-{'status'}})==canonical({name:start[name] for name in START_FIELDS-{'status'}})
        and result['plan_sha256']==sha(plan_raw) and result['launcher_sha256']==LAUNCHER_SHA256
        and result['support_sha256']==SUPPORT_SHA256 and result['output']==str(output)
        and type(result['exit_code']) is int and result['exit_code']==0
        and type(result['maximum_seconds']) is int and result['maximum_seconds']==3600
        and result['automatic_retry'] is False and type(result['warmup_requests']) is int and result['warmup_requests']==0
        and result['enabled_model_changed'] is False and result['final_selected_runtime_rerun_required'] is True
        and canonical(result['sources'])==expected.specification.sources_json,'invocation_identity')
    require(evidence.timestamp(result['completed_at'])>=evidence.timestamp(start['started_at'])
        and evidence.numeric(result['elapsed_seconds']) and 0<result['elapsed_seconds']<=3600,'invocation_time')
    require(result['cleanup_sha256']==sha(inv['cleanup.json']) and result['quality_report_pins']=={name:sha(value) for name,value in reports.items()},'invocation_hashes')
    require(set(cleanup)=={'schema_version','group_signal_requested','group_already_absent','leader_reaped','leader_exit_observed','errors'}
        and cleanup['schema_version']=='wolf-full-initial-cleanup-v1' and cleanup['leader_reaped'] is True
        and cleanup['leader_exit_observed'] is True and cleanup['errors']==[]
        and type(cleanup['group_signal_requested']) is bool and type(cleanup['group_already_absent']) is bool
        and cleanup['group_signal_requested'] is not cleanup['group_already_absent'],'invocation_cleanup')
    value=evidence.validate_report(corpora,reports['started.json'],reports['result.json'],reports['cases.jsonl'],expected,
        verify_corpus=corpus_projection,require_quality=True)
    require(evidence.timestamp(parse(reports['started.json'])['created_at'])>=evidence.timestamp(start['started_at'])
        and evidence.timestamp(parse(reports['result.json'])['completed_at'])<=evidence.timestamp(result['completed_at']),'report_times')
    again,after_corpora,_,_=binding.validate_plan(plan,root,sha(plan_raw))
    require(again==expected and after_corpora==corpora and reference(capsule['plan'])==plan_raw
        and reference(capsule['launcher'])==launcher and reference(capsule['support'])==support
        and all(reference(invocation[name])==value for name,value in inv.items())
        and all(reference(quality[name])==value for name,value in reports.items())
        and reference(capsule_ref)==raw,'consume_drift')
    return {**value,'parent_capsule_sha256':sha(raw),'invocation_sha256':sha(inv['result.json']),
        'launcher_sha256':LAUNCHER_SHA256,'plan_sha256':sha(plan_raw)}
