"""Real HTTP/tenant-repository ports for bounded Phase3 admission scenarios.

The caller supplies an authenticated read-only cluster, actual governed policy
setup/restoration, parent authorization, and an exclusive private recorder.
There are no default clients, baseline changes, profile switches, fault signals,
or provenance fallbacks. The independent worker transition is a separate port.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import re
import ssl
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from guardrails.evolution import models
from guardrails.evolution.storage import current_deadline
from mvp.tests.evolution.e2e.classifier_network_support import private_bytes

from mvp.evolution import classifier_admission_measurements as evidence
from mvp.tests.evolution.e2e.classifier_budget_support import response_headers

LEASE_FIELDS = ('tenant_id', 'principal_id', 'workload_id', 'request_id', 'policy_digest',
                'stage', 'lease_id', 'state', 'expires_epoch')


def check(deadline):
    evidence.number(deadline)
    evidence.require(time.monotonic() < deadline, 'admission_deadline')


def projection(decision):
    value = decision.model_dump(mode='json')
    return {**{key: value[key] for key in evidence.DECISION_FIELDS - {'finding_rules'}},
            'finding_rules': sorted({row['rule_id'] for row in value['findings']})}


class RuntimePort:
    """One direct loopback HTTPS request; original profile budget, no retry."""
    def __init__(self, cluster, *, deadline):
        check(deadline)
        parts = urlsplit(cluster.access['runtime_url'])
        evidence.require(parts.scheme == 'https' and parts.hostname == 'localhost' and parts.port
            and 1024 <= parts.port <= 65535 and cluster.access['runtime_url'] == f'https://localhost:{parts.port}'
            and parts.username is None and parts.password is None and not parts.path and not parts.query and not parts.fragment
            and cluster.profile == cluster.access['profile'] == 'classifier')
        self.url = f'https://127.0.0.1:{parts.port}/v2/evaluations'
        self.host = f'localhost:{parts.port}'
        ca_path = Path(cluster.access['ca_file'])
        evidence.require(ca_path.is_absolute() and ca_path == cluster.state / 'credentials/ca.crt')
        ca = private_bytes(ca_path, deadline=deadline, maximum=65536)
        self.ca = ca.decode('ascii')
        self.headers, self.subjects, self.pins = {}, {}, {'ca_sha256': hashlib.sha256(ca).hexdigest()}
        for tenant in evidence.TENANTS:
            item = cluster.inventory['tenants'][tenant]
            role = item['roles']['workload']; path = Path(role['token_file'])
            evidence.require(str(path) == cluster.access['tenants'][tenant]['workload_token_file']
                and path.is_absolute() and path.is_relative_to(cluster.state / 'assembly'))
            raw = private_bytes(path, deadline=deadline, maximum=4097)
            token = raw.decode('ascii').removesuffix('\n')
            evidence.require(re.fullmatch(r'[!-~]{32,4096}', token) is not None)
            self.headers[tenant] = {'Authorization': 'Bearer ' + token}
            self.subjects[tenant] = {'tenant_id': tenant, 'principal_id': role['principal_id'],
                'workload_id': item['workload']['workload_id']}
            self.pins[tenant] = hashlib.sha256(raw).hexdigest()
        check(deadline)

    async def __call__(self, subject, sent, *, label, deadline):
        started = time.monotonic()
        evidence.require(all(subject[key] == value for key, value in self.subjects[subject['tenant_id']].items()))
        models.EvaluationRequest.model_validate_json(models.canonical_json_bytes(sent))
        evidence.require(sent['tenant_id'] == subject['tenant_id'] and sent['workload_id'] == subject['workload_id']
            and sent['provider_id'] == subject['provider_id'] and sent['deadline_ms'] == subject['budget_ms'])
        client_seconds = 4 if subject['budget_ms'] == 3000 else 1.5
        absolute = min(deadline, started + client_seconds)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=self.ca)
        body = models.canonical_json_bytes(sent)
        headers = {**self.headers[subject['tenant_id']], 'Host': self.host, 'Content-Type': 'application/json',
                   'Content-Length': str(len(body)), 'Accept-Encoding': 'identity', 'X-Request-ID': sent['correlation_id']}
        check(absolute)
        loop = asyncio.get_running_loop()
        async with asyncio.timeout_at(loop.time() + absolute - time.monotonic()):
            async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False,  # noqa: SIM117 -- timeout owns close too
                    timeout=httpx.Timeout(client_seconds, connect=0.25),
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                async with client.stream('POST', self.url, headers=headers, content=body) as response:
                    evidence.require(response.status_code in {200, 429})
                    length = response_headers(response, sent['correlation_id'])
                    stream = response.extensions.get('network_stream')
                    tls = None if stream is None else stream.get_extra_info('ssl_object')
                    evidence.require(tls is not None and tls.version() in {'TLSv1.2', 'TLSv1.3'})
                    raw = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        evidence.require(len(raw) + len(chunk) <= 2 * 1024 * 1024)
                        raw.extend(chunk)
                    evidence.require(length is None or length == len(raw))
                    value = models.strict_json_loads(bytes(raw)); raw.clear()
                    if response.status_code == 200:
                        decision = projection(models.Decision.model_validate_json(models.canonical_json_bytes(value)))
                        code = retry = None
                    else:
                        evidence.require(type(value) is dict and set(value) == {'error'})
                        error = models.Error.model_validate_json(models.canonical_json_bytes(value['error']))
                        evidence.require(error.code == 'quota_exceeded' and error.correlation_id == sent['correlation_id'])
                        retry_text = response.headers.get('retry-after', '')
                        evidence.require(retry_text.isascii() and retry_text.isdecimal() and 1 <= int(retry_text) <= 60)
                        decision, code, retry = None, 'quota_exceeded', int(retry_text)
                    status = response.status_code
        check(absolute)
        row = {'label': label, 'tenant_id': subject['tenant_id'], 'request_id': sent['request_id'],
            'correlation_id': sent['correlation_id'], 'stage': sent['stage'], 'context_id': sent.get('context_id'),
            'body_sha256': hashlib.sha256(body).hexdigest(), 'status_code': status, 'decision': decision,
            'error_code': code, 'retry_after': retry, 'started': started, 'finished': time.monotonic(),
            'budget_ms': subject['budget_ms']}
        evidence.request(row, subject)
        return row


class ScopeReader:
    """Only committed tenant-scoped metadata through the existing non-owner repo."""
    def __init__(self, cluster):
        self.cluster = cluster

    @contextmanager
    def tx(self, tenant, deadline):
        check(deadline)
        token = current_deadline.set(min(deadline, time.monotonic() + 0.5))
        try:
            with self.cluster.repo.transaction(tenant) as tx:
                yield tx
            check(deadline)
        finally:
            current_deadline.reset(token)

    @staticmethod
    def key(subject, request_id):
        return f"{subject['principal_id']}:{subject['workload_id']}:{request_id}"

    def active(self, subject, request_id, *, deadline):
        with self.tx(subject['tenant_id'], deadline) as tx:
            lease = tx.get('quota_leases', self.key(subject, request_id))
            now = tx.execute('SELECT extract(epoch FROM clock_timestamp())').scalar_one()
        check(deadline)
        if lease is None: return None
        selected = {k: lease[k] for k in LEASE_FIELDS}
        evidence.require(selected['tenant_id'] == subject['tenant_id'] and selected['principal_id'] == subject['principal_id']
            and selected['workload_id'] == subject['workload_id'] and selected['policy_digest'] == subject['policy']['digest'])
        if selected['state'] != 'active' or selected['expires_epoch'] <= float(now): return None
        return {'observed': time.monotonic(), 'lease': selected, 'database_epoch': float(now)}

    def committed(self, subject, row, *, deadline):
        d = row['decision']; tenant = subject['tenant_id']
        with self.tx(tenant, deadline) as tx:
            context = tx.get('evaluation_contexts', d['context_id'], required=True)
            lease = tx.get('quota_leases', self.key(subject, row['request_id']), required=True)
            event = tx.get('audit', d['decision_id'], required=True)
        other = 'team-b' if tenant == 'team-a' else 'team-a'
        with self.tx(other, deadline) as tx:
            foreign = {'context': tx.get('evaluation_contexts', d['context_id']) is not None,
                'lease': tx.get('quota_leases', self.key(subject, row['request_id'])) is not None,
                'audit': tx.get('audit', d['decision_id']) is not None}
        inner = context['context']
        evidence.require(inner['context_id'] == d['context_id'] and event['event_type'] == 'evaluation'
                         and event['decision_id'] == d['decision_id'])
        return {'tenant_id': tenant, 'observed': time.monotonic(),
            'context': {**{k: inner[k] for k in ('context_id', 'tenant_id', 'workload_id', 'policy')},
                        **{k: context[k] for k in ('principal_id', 'request_id', 'state')}},
            'lease': {k: lease[k] for k in LEASE_FIELDS}, 'foreign': foreign,
            'audit': {**{k: event[k] for k in ('event_id', 'tenant_id', 'actor_id', 'workload_id', 'correlation_id',
                'policy_digest', 'binding_revision', 'action', 'event_hash', 'raw_content_retained')}, 'stage': event['metadata']['stage']}}

    def absent(self, subject, row, *, deadline):
        with self.tx(subject['tenant_id'], deadline) as tx:
            lease = tx.get('quota_leases', self.key(subject, row['request_id']))
            contexts = list(tx.execute("SELECT key FROM evolution.records WHERE tenant_id=:tenant AND kind='evaluation_contexts' "
                "AND data->>'request_id'=:request LIMIT 2", {'tenant': subject['tenant_id'], 'request': row['request_id']}))
            # The denied request has a unique correlation, unlike the explicit collision case.
            audits = list(tx.execute("SELECT key FROM evolution.records WHERE tenant_id=:tenant AND kind='audit' "
                "AND data->>'event_type'='evaluation' AND data->>'correlation_id'=:correlation LIMIT 2",
                {'tenant': subject['tenant_id'], 'correlation': row['correlation_id']}))
        return {'context_count': len(contexts), 'lease': lease is not None, 'audit_count': len(audits)}


def sent(subject, *, request_id, correlation_id, stage='input', context_id=None):
    # Synthetic content is not a benchmark/corpus. B deliberately exercises Rules.
    text = 'A clear blue sky.' if subject['tenant_id'] == 'team-a' else 'Synthetic identifier AKIAABCDEFGHIJKLMNOP.'
    value = {'schema_version': 'guardrails-v2', 'request_id': request_id, 'correlation_id': correlation_id,
        'tenant_id': subject['tenant_id'], 'workload_id': subject['workload_id'], 'provider_id': subject['provider_id'], 'stage': stage,
        'parts': [{'part_id': 'content', 'kind': 'text', 'role': 'user' if stage == 'input' else 'assistant', 'text': text}],
        'deadline_ms': subject['budget_ms']}
    if context_id is not None: value['context_id'] = context_id
    models.EvaluationRequest.model_validate_json(models.canonical_json_bytes(value))
    return value


async def join(tasks):
    for task in tasks:
        if not task.done(): task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class AdmissionObservation:
    """Recoverable progress. Recorder is called after each finite prefix and finally.

    Caller owns its original durable writer and retention deadline. Never infer a
    replacement root or replay an interrupted observation. HTTP and SQL ports may
    be replaced only in isolated synthetic tests; production ports are above.
    """
    def __init__(self, *, case_id, operation, sources, subjects, authorization_sha256, http, reader, record, verify, fault=None):
        evidence.require(case_id in evidence.CASES and callable(http) and callable(record) and callable(verify))
        evidence.subjects(subjects); evidence.uuid(operation); evidence.require(evidence.sha(authorization_sha256))
        self.http, self.reader, self.record, self.verify, self.fault = http, reader, record, verify, fault
        self.value = {'schema_version': evidence.SCHEMA, 'case_id': case_id, 'operation': operation, 'status': 'failed',
            'phase': 'intent', 'sources': copy.deepcopy(sources), 'authorization_sha256': authorization_sha256,
            'subjects': copy.deepcopy(subjects), 'requests': [], 'observations': {}, 'fault': None,
            'cleanup': {'requests': 'pending', 'governance': 'pending', 'fault': 'clear'}, 'elapsed_ms': 0}
        self.sequence = 0

    def save(self):
        self.sequence += 1
        evidence.require(self.sequence <= 32)
        self.record(f'admission-{self.sequence:02d}.json', copy.deepcopy(self.value))

    async def request(self, tenant, label, payload, deadline):
        row = await self.http(self.value['subjects'][tenant], payload, label=label, deadline=deadline)
        self.value['requests'].append(copy.deepcopy(row)); self.save()
        return row

    async def committed(self, row, deadline):
        subject = self.value['subjects'][row['tenant_id']]
        # Original cleanup is asynchronous. This reads only, never releases a lease.
        until = min(deadline, time.monotonic() + 1)
        while True:
            value = await asyncio.to_thread(self.reader.committed, subject, row, deadline=until)
            if value['lease']['state'] != 'active': break
            check(until); await asyncio.sleep(0.005)
        self.value['observations'][row['label']] = copy.deepcopy(value); self.save()
        evidence.isolation(value, row, subject, state='blocked' if row['decision']['action'] == 'block'
                           else 'open' if row['stage'] == 'input' else 'completed')

    async def same_identifiers(self, deadline):
        correlation = str(uuid4()); contexts = {}
        for stage in ('input', 'output'):
            request_id = str(uuid4())
            tasks = [asyncio.create_task(self.request(tenant, tenant + '-' + stage,
                sent(self.value['subjects'][tenant], request_id=request_id, correlation_id=correlation,
                     stage=stage, context_id=contexts.get(tenant)), deadline)) for tenant in evidence.TENANTS]
            try:
                rows = await asyncio.gather(*tasks)
            finally:
                await join(tasks)
            for row in rows:
                evidence.successful(row, self.value['subjects'][row['tenant_id']])
                contexts[row['tenant_id']] = row['decision']['context_id']
                await self.committed(row, deadline)

    async def saturation(self, deadline):
        subjects = self.value['subjects']; evidence.require(subjects['team-a']['max_inflight'] == 1)
        use_fault = self.value['case_id'] == evidence.CASES[2]
        if use_fault:
            evidence.require(self.fault is not None)
            self.value['fault'] = await asyncio.to_thread(self.fault.suspend, deadline=deadline)
            self.save()
        first_id = str(uuid4())
        first_started = time.monotonic()
        tasks = [asyncio.create_task(self.request('team-a', 'a-original',
            sent(subjects['team-a'], request_id=first_id, correlation_id=str(uuid4())), deadline))]
        try:
            while True:
                check(deadline)
                evidence.require(not tasks[0].done(), 'admission_no_active_lease')
                active = await asyncio.to_thread(self.reader.active, subjects['team-a'], first_id, deadline=deadline)
                if active is not None: break
                await asyncio.sleep(0.002)
            self.value['observations']['active'] = copy.deepcopy(active); self.save()
            tasks += [asyncio.create_task(self.request(tenant, label,
                sent(subjects[tenant], request_id=str(uuid4()), correlation_id=str(uuid4())), deadline))
                for tenant, label in (('team-a', 'a-overflow'), ('team-b', 'b-peer'))]
            overflow, peer = await asyncio.gather(*tasks[1:])
            after_peer = await asyncio.to_thread(self.reader.active, subjects['team-a'], first_id, deadline=deadline)
            evidence.require(after_peer is not None, 'admission_peer_not_during_active_lease')
            self.value['observations']['active_after_peer'] = copy.deepcopy(after_peer); self.save()
            if use_fault: await asyncio.to_thread(self.fault.stopped, deadline=min(deadline, first_started + 3))
            first = await tasks[0]
            self.value['requests'] = [first, overflow, peer]
            self.value['observations']['overflow_absent'] = await asyncio.to_thread(
                self.reader.absent, subjects['team-a'], overflow, deadline=deadline)
            for row in (first, peer): await self.committed(row, deadline)
            if use_fault:
                self.value['fault'] = await asyncio.to_thread(self.fault.recovered, deadline=deadline)
        finally:
            await join(tasks)

    async def run(self, *, deadline):
        """Observation only; caller restores governed bindings before validate()."""
        started = time.monotonic(); deadline = min(deadline, started + (180 if self.fault else 30))
        primary = None
        self.save()  # durable intent before the first HTTP or signal
        try:
            self.value['phase'] = 'observe'
            if self.value['case_id'] == evidence.CASES[0]: await self.same_identifiers(deadline)
            else: await self.saturation(deadline)
            check(deadline)
        except BaseException as error:
            primary = error
            raise
        finally:
            self.value['cleanup']['requests'] = 'joined'
            cleanup_error = None
            if self.fault is not None:
                try:
                    self.value['cleanup']['fault'] = await asyncio.to_thread(self.fault.cleanup, deadline=deadline)
                    self.value['fault'] = copy.deepcopy(self.fault.value)
                except BaseException as error:  # noqa: BLE001 - retain cleanup failure without replacing primary cancellation
                    self.value['cleanup']['fault'] = 'failed'
                    cleanup_error = error
                    if primary is not None: primary.add_note('admission_fault_cleanup_failed')
            self.value['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
            try: self.save()
            except BaseException:
                if primary is None and cleanup_error is None: raise
                (primary or cleanup_error).add_note('admission_retention_failed')
            if primary is None and cleanup_error is not None: raise cleanup_error
        return copy.deepcopy(self.value)

    def complete(self, *, restoration):
        evidence.require(restoration == 'restored')
        self.value['cleanup']['governance'] = restoration
        self.value['status'], self.value['phase'] = 'observed', 'complete'
        try:
            value = evidence.validate(self.value, verify_provenance=self.verify)
            self.save()
            return value
        except BaseException as error:
            self.value['status'] = 'failed'
            try: self.save()
            except BaseException: error.add_note('admission_retention_failed')  # noqa: BLE001 - preserve primary refusal
            raise
