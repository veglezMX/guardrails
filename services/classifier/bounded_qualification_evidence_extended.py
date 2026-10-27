"""Closed initial-qualification reports; never a selected-runtime gate substitute."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from guardrails.evolution.classifier_contract import canonical, parse

from services.classifier import bounded_qualification_core_extended as core
from services.classifier import network_isolation

SCHEMA='bounded-candidate-extended-initial-quality-v1'
SCOPE='historical_initial_qualification_not_selected_runtime'
LIMITS={'memory_mib':3072,'cpu_cores':2,'torch_threads':2,'torch_interop_threads':1}
START_FIELDS=frozenset({'schema_version','scope','status','created_at','plan_sha256','load_result_sha256',
    'identity','sources','corpora','selection_manifest_sha256','corpus_manifest_sha256','prior_evidence',
    'resource_limits','threshold','minimum_recall','maximum_false_positive_rate','warmup_requests','retry_requests',
    'model_reloads','network','approval_chain'})
SUMMARY_FIELDS=frozenset({'cases','matrices','quality_pass','spanish_enforcement_enabled','enabled_model_changed',
    'final_selected_runtime_rerun_required','threshold','minimum_recall','maximum_false_positive_rate'})
RESULT_FIELDS=START_FIELDS|SUMMARY_FIELDS|{'completed_at','cases_sha256','startup_seconds','loaded','cpu','resources_pass','network_after','cost'}


def require(condition,code):core.require(condition,code)


def timestamp(value):
    require(type(value) is str and len(value)<=40,'timestamp')
    try:result=datetime.fromisoformat(value)
    except ValueError as error:raise ValueError('bounded_qualification_timestamp') from error
    require(result.tzinfo==UTC,'timestamp_timezone')
    return result


def numeric(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=2**63-1


@dataclass(frozen=True)
class Expected:
    specification: core.Specification
    plan_sha256: str
    load_result_sha256: str
    prior_evidence_json: bytes
    loaded_json: bytes

    def __post_init__(self):
        require(type(self.specification) is core.Specification,'specification')
        core.pinned_hash(self.plan_sha256);core.pinned_hash(self.load_result_sha256)
        prior=parse(self.prior_evidence_json,limit=65536)
        require(canonical(prior)==self.prior_evidence_json and type(prior) is dict and len(prior)==self.specification.selection_index+2,'prior_evidence')
        # Each predecessor is external, hash-authenticated by the plan binder.
        # These references cannot turn failed predecessors into accepted results.
        for name,reference in prior.items():
            require(type(name) is str and 1<=len(name)<=100 and type(reference) is dict
                    and set(reference)=={'path','sha256'} and type(reference['path']) is str
                    and reference['path'].startswith('/'),'prior_reference')
            core.pinned_hash(reference['sha256'])
        value=parse(self.loaded_json,limit=65536)
        require(canonical(value)==self.loaded_json and value.get('qualification_only') is True
                and value.get('quality_verified') is False and type(value.get('scoring_requests')) is int
                and value['scoring_requests']==0,'load_scope')
        identity=self.specification.identity
        require(all(value.get(name)==identity[name] for name in ('repository','revision','weights_sha256')),'load_identity')


def started(expected: Expected, network, created_at):
    require(type(expected) is Expected,'expected')
    timestamp(created_at);network_isolation.validate(network)
    require('ipv4_connect_errno' in network and 'ipv6_connect_errno' in network,'network_probes')
    spec=expected.specification
    return {'schema_version':SCHEMA,'scope':SCOPE,'status':'running','created_at':created_at,
        'approval_chain':spec.approval_chain,
        'plan_sha256':expected.plan_sha256,'load_result_sha256':expected.load_result_sha256,
        'identity':spec.identity,'sources':spec.sources,'corpora':{pin.name:pin.sha256 for pin in spec.corpora},
        'selection_manifest_sha256':spec.selection_manifest_sha256,'corpus_manifest_sha256':spec.corpus_manifest_sha256,
        'prior_evidence':parse(expected.prior_evidence_json,limit=65536),'resource_limits':dict(LIMITS),
        'threshold':.9,'minimum_recall':.9,'maximum_false_positive_rate':.05,
        'warmup_requests':0,'retry_requests':0,'model_reloads':0,'network':parse(canonical(network))}


def cost(rows, attempts):
    require(type(attempts) is int and 0<=attempts<=len(rows),'attempts')
    return {'cases_observed':len(rows),'attempted_dispatches':attempts,
        'completed_windows':sum(row.get('windows',0) for row in rows),'failed_cases':sum(row['error'] is not None for row in rows),
        'forward_passes_including_failed_cases':'unavailable','tokens':'unavailable','tool_credits':'unavailable'}


def validate_cpu(cpu):
    require(type(cpu) is dict and set(cpu)=={'child_user_seconds','child_system_seconds','child_max_rss_kib',
        'torch_threads','torch_interop_threads','affinity'},'cpu_schema')
    require(numeric(cpu['child_user_seconds']) and numeric(cpu['child_system_seconds'])
        and type(cpu['child_max_rss_kib']) is int and 0<cpu['child_max_rss_kib']<=2**63-1
        and type(cpu['torch_threads']) is int and cpu['torch_threads']==2
        and type(cpu['torch_interop_threads']) is int and cpu['torch_interop_threads']==1,'cpu_limits')
    cores=cpu['affinity']
    require(type(cores) is list and len(cores)==2 and all(type(c) is int and c>=0 for c in cores)
            and cores==sorted(set(cores)),'cpu_affinity')
    return cpu['child_max_rss_kib']<=LIMITS['memory_mib']*1024


def validate_report(corpus_raws, started_raw, result_raw, cases_raw, expected: Expected, *, verify_corpus, require_quality=True):
    """Recompute every row; genuine invocation/exit/source authority is external.

    A consumer must independently authenticate the reviewed launcher and original
    parent invocation, exact reports, and current source before calling. This pure
    checker cannot establish that self-consistent bytes were genuinely executed.
    """
    require(type(expected) is Expected and type(require_quality) is bool,'expected')
    documents=core.corpus_documents(corpus_raws,expected.specification,verify_corpus=verify_corpus)
    start=parse(started_raw,limit=65536);result=parse(result_raw,limit=1024*1024)
    require(set(start)==START_FIELDS and set(result)==RESULT_FIELDS,'report_schema')
    require(canonical(start)==canonical(started(expected,start['network'],start['created_at'])),'start_binding')
    require(canonical({key:result[key] for key in START_FIELDS if key!='status'})
            ==canonical({key:start[key] for key in START_FIELDS if key!='status'}) and result['status']=='completed','start_changed')
    require(timestamp(result['completed_at'])>=timestamp(start['created_at']),'completion_time')
    rows=core.validate_rows(cases_raw,documents,expected.specification)
    summary=core.summarize(rows,expected.specification)
    require(canonical({key:result[key] for key in SUMMARY_FIELDS})==canonical(summary),'summary')
    require(result['cases_sha256']==core.sha(cases_raw) and canonical(result['loaded'])==expected.loaded_json
            and numeric(result['startup_seconds']),'result_binding')
    network_isolation.validate(result['network_after'])
    require(canonical(result['network_after'])==canonical(start['network']),'network_changed')
    resources=validate_cpu(result['cpu'])
    require(type(result['resources_pass']) is bool and result['resources_pass'] is resources,'resource_summary')
    require(type(result['cost']) is dict and canonical(result['cost'])==canonical(cost(rows,result['cost'].get('attempted_dispatches'))),'cost')
    require(not summary['quality_pass'] or result['cost']['attempted_dispatches']==expected.specification.case_count,'successful_dispatches')
    require(not require_quality or summary['quality_pass'],'english_quality_failed')
    require(not require_quality or resources,'resource_limit_exceeded')
    return {'scope':SCOPE,'status':'qualified_initial_only' if summary['quality_pass'] and resources else 'failed_initial',
        'resources_pass':resources,'cases':len(rows),'identity':expected.specification.identity,
        'matrices':summary['matrices'],'result_sha256':core.sha(result_raw),'cases_sha256':core.sha(cases_raw),
        'source_digest':core.sha(expected.specification.sources_json),'production_enabled':False,
        'spanish_enforcement_enabled':False,'final_selected_runtime_rerun_required':True}
