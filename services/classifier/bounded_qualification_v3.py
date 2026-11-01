"""One approved initial run, one model child, all cumulative cases retained.

Only the reviewed parent launcher invokes run_plan inside its owned, observed
network namespace and process group. This module never enables production.
"""
from __future__ import annotations

import asyncio
import os
import resource
import sys
from datetime import UTC, datetime
from pathlib import Path

from evolution.classifier_provenance import corpus_projection
from guardrails.evolution.classifier_contract import canonical

from services.classifier import bounded_qualification_core as core
from services.classifier import bounded_qualification_evidence as evidence
from services.classifier import bounded_qualification_plan_v3 as binding
from services.classifier import bounded_qualification_process_v3 as process
from services.classifier import network_isolation
from services.classifier.wolf_full_qualification import (
    Output,
    read,
    sha,
    verify_environment,
)


def require(value,code):core.require(value,code)


def run_plan(plan,root,plan_sha256):
    """Actual execution; callers must authenticate source before importing."""
    original=os.sched_getaffinity(0)
    require(type(original) is set and len(original)>=2 and all(type(n) is int and n>=0 for n in original),'cpu_available')
    selected=sorted(original)[:2]
    try:
        os.sched_setaffinity(0,set(selected))
        require(os.sched_getaffinity(0)==set(selected),'cpu_affinity')
        return _run(plan,root,plan_sha256,selected)
    finally:
        primary=sys.exc_info()[0] is not None
        try:os.sched_setaffinity(0,original)
        except OSError:
            if not primary:raise


def _run(plan,root,plan_sha256,selected):
    expected,raws,documents,closure=binding.validate_plan(plan,root,plan_sha256)
    verify_environment(closure)
    network=network_isolation.snapshot(plan['host_namespace'])
    output=Output(plan['output'])
    try:
        start=evidence.started(expected,network,datetime.now(UTC).isoformat())
        output.write('started.json',start)
        configuration=process.ChildConfiguration(expected.specification,Path(root),Path(plan['model_directory']),
            output.path,expected.loaded_json)
        child=process.ProcessChild(configuration)
        cpu_before=resource.getrusage(resource.RUSAGE_CHILDREN)
        with output.open('cases.jsonl') as stream:
            def retain(raw):
                output.check();stream.write(raw);stream.flush();os.fsync(stream.fileno());output.check()
            def loaded(value):require(canonical(value)==expected.loaded_json,'loaded_changed')
            rows,startup,metadata,attempts=asyncio.run(core.score_once(documents,expected.specification,child,
                retain=retain,validate_loaded=loaded))
        output.check();os.fsync(output.fd)
        cpu_after=resource.getrusage(resource.RUSAGE_CHILDREN)
        again,after_raws,_,after_closure=binding.validate_plan(plan,root,plan_sha256)
        require(again==expected and after_raws==raws and after_closure==closure,'inputs_changed')
        verify_environment(closure)
        after_network=network_isolation.snapshot(plan['host_namespace'])
        require(after_network==network and os.sched_getaffinity(0)==set(selected),'boundary_changed')
        cases=read(output.path/'cases.jsonl',private=True,maximum=core.MAXIMUM_REPORT_BYTES)
        require(core.validate_rows(cases,documents,expected.specification)==rows,'retained_rows_changed')
        cpu={'child_user_seconds':cpu_after.ru_utime-cpu_before.ru_utime,
            'child_system_seconds':cpu_after.ru_stime-cpu_before.ru_stime,'child_max_rss_kib':cpu_after.ru_maxrss,
            'torch_threads':2,'torch_interop_threads':1,'affinity':selected}
        result={**start,**core.summarize(rows,expected.specification),'status':'completed',
            'completed_at':datetime.now(UTC).isoformat(),'cases_sha256':sha(cases),'startup_seconds':startup,
            'loaded':metadata,'cpu':cpu,'resources_pass':evidence.validate_cpu(cpu),
            'network_after':after_network,'cost':evidence.cost(rows,attempts)}
        evidence.validate_report(raws,canonical(start),canonical(result),cases,expected,
            verify_corpus=corpus_projection,require_quality=False)
        output.write('result.json',result)  # Complete quality/resource failures are retained.
        return 0 if result['quality_pass'] and result['resources_pass'] else 1
    except BaseException as error:
        try:output.write('failure.json',{'status':'failed','complete':False,'quality_verified':False,
            'exception_type':type(error).__name__,'production_enabled':False})
        except BaseException:  # noqa: BLE001,S110 - never expose recorder errors or replace primary refusal
            pass
        raise
    finally:output.close()
