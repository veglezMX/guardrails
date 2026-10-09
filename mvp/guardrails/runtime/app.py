"""Authenticated live runtime, with an explicit separate smoke fixture profile."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic.json_schema import models_json_schema

from .. import local_contract as d
from ..engine import BatchDecisionEngine
from ..management.app import cursor_key, paginate, query_options
from ..management.models import AuditPage, CredentialModel
from ..storage.audit import AuditRepository
from ..transport import (ClientConfigModel, ContractProblem, DecisionModel, EvaluationRequestModel,
                         ProblemModel, install_exception_handlers, parse_model, problem_response, strict_json_loads)
from . import models as m
from .contexts import ContextStore
from .executor import DeadlineExecutor, ExecutionFailure
from .snapshots import SnapshotCache, SnapshotPoller


class EvaluationRuntime:
    def __init__(self, cache, audit, *, credentials=(), control_hash=None):
        self.cache, self.audit, self.credentials = cache, audit, credentials
        self.config = cache.config
        self.contexts = ContextStore(self.config)
        self.executor = DeadlineExecutor(self.config)
        self.engine = BatchDecisionEngine()
        self.audit_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='runtime-audit')
        self.control_hash, self.fault = control_hash, None

    def service(self, request, capability):
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        if not 32 <= len(token) <= 256:
            raise ContractProblem('authentication_failed', 401)
        digest = hashlib.sha256(token.encode()).hexdigest()
        for credential in self.credentials:
            if credential.revoked_at is None and hmac.compare_digest(digest, credential.token_sha256):
                if credential.role != 'management_service' or capability not in credential.capabilities:
                    raise ContractProblem('identity_mismatch', 403)
                return credential
        # A known workload is authenticated but not authorized for service APIs.
        if self.cache.current:
            for credential in self.cache.current.credential_hashes:
                if hmac.compare_digest(digest, credential.token_sha256):
                    raise ContractProblem('identity_mismatch', 403)
        raise ContractProblem('authentication_failed', 401)

    def workload(self, request):
        authorization = request.headers.get('authorization', '')
        if authorization.startswith('Bearer '):
            digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
            if any(hmac.compare_digest(digest, c.token_sha256) for c in self.credentials):
                raise ContractProblem('identity_mismatch', 403)
        return self.cache.authenticate(authorization)

    def proof_authorize(self, request):
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not self.control_hash or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.control_hash):
            raise ContractProblem('authentication_failed', 401)

    async def evaluate(self, evaluation, ticket, started):
        context = None
        if evaluation.stage == 'output':
            context = self.contexts.consume(evaluation)
            binding, policy, client = context.binding, context.policy, context.client_config
        else:
            binding, policy, client = self.cache.resolve((evaluation.tenant_id, evaluation.workload_id))
            if policy:
                context = self.contexts.create(evaluation, binding, policy, client)
        try:
            failure, result = None, None
            if policy is None:
                failure = 'policy_unresolved'
            elif self.cache.stale:
                failure = 'management_unavailable'
            elif binding.enforcement_state != 'disabled':
                checks = tuple(c for c in policy.checks if evaluation.stage in c.stages)
                try:
                    result = await self.executor.execute(evaluation.content, checks,
                        cutoff=started + (self.config.runtime_deadline_ms - self.config.audit_reserve_ms) / 1000,
                        ticket=ticket, fault=self.fault)
                except ExecutionFailure as error:
                    failure = error.code
            decision = self.engine.decide(evaluation, binding, policy, client,
                context_id=context.context_id if context else None, result=result, failure=failure,
                elapsed_ms=(time.monotonic() - started) * 1000)
            deadline = started + self.config.runtime_deadline_ms / 1000
            try:
                loop = asyncio.get_running_loop()
                write = partial(self.audit.write_decision, decision, evaluation, policy,
                                deadline=deadline, cache_age=self.cache.age)
                # One bounded writer; each task independently checks the same
                # absolute deadline on entry and inside the SQLite transaction.
                async with asyncio.timeout_at(deadline):
                    audit_id = await loop.run_in_executor(self.audit_pool, write)
                decision = replace(decision, audit_id=audit_id, audit_status='persisted')
            except (ContractProblem, TimeoutError, OSError):
                decision = self.engine.audit_failed(decision, client.failure_behavior)
                print('audit_write_failed', file=sys.stderr)
            if decision.action == 'block' and context:
                self.contexts.release(context.context_id)
            return replace(decision, evaluation_ms=(time.monotonic() - started) * 1000)
        except BaseException:
            if context:
                self.contexts.release(context.context_id)
            raise

    async def close(self):
        await self.executor.close()
        self.contexts.contexts.clear()
        self.audit_pool.shutdown(wait=True, cancel_futures=True)


async def body_bytes(request, limit):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise ContractProblem(status=413)
        raw.extend(chunk)
    return bytes(raw)


def response(value, model):
    # Validate JSON projections in JSON mode: strict Python mode intentionally
    # rejects wire UUID/timestamp strings and array-to-tuple conversions.
    return JSONResponse(parse_model(d.canonical_bytes(value), model).model_dump(mode='json'),
                        headers={'Cache-Control': 'no-store'})


def create_app(runtime=None, *, schema_only=False, poller=None):
    if runtime is None and not schema_only and os.environ.get('GUARDRAILS_PROOF_PROFILE') == 'smoke':
        from .smoke_app import create_app as create_smoke
        return create_smoke()
    management_client = None
    if runtime is None and not schema_only:
        config = strict_json_loads(Path(os.environ['GUARDRAILS_RUNTIME_CONFIG']).read_bytes())
        settings = d.ProofConfig(**config.get('proof_config', {}))
        credentials = tuple(parse_model(d.canonical_bytes(c), CredentialModel).to_domain() for c in config['credentials'])
        if any(c.role != 'management_service' for c in credentials):
            raise ValueError('invalid_runtime_credentials')
        cache = SnapshotCache(settings)
        runtime = EvaluationRuntime(cache, AuditRepository(os.environ['GUARDRAILS_AUDIT_DB'], busy_timeout_ms=settings.sqlite_busy_timeout_ms),
                                    credentials=credentials, control_hash=config.get('proof_control_sha256') if os.environ.get('GUARDRAILS_PROOF_PROFILE') == 'runtime' else None)
        url = httpx.URL(config['management_url'])
        if url.scheme != 'https' or url.userinfo:
            raise ValueError('tls_required')
        management_client = httpx.AsyncClient(base_url=url, verify=ssl.create_default_context(cafile=config['ca_file']),
            headers={'Authorization': 'Bearer ' + Path(config['token_file']).read_text().strip()}, timeout=0.5, trust_env=False)
        poller = SnapshotPoller(cache, management_client)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        try:
            await runtime.executor.start()
            if poller:
                task = asyncio.create_task(poller.run())
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if management_client:
                await management_client.aclose()
            if runtime:
                await runtime.close()

    app = FastAPI(title='Guardrails local proof runtime', version=d.SCHEMA_VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    install_exception_handlers(app)

    @app.middleware('http')
    async def safe_boundary(request, call_next):
        try:
            return await call_next(request)
        except Exception:
            print('evaluation_unavailable', file=sys.stderr)
            return problem_response(ContractProblem('evaluation_unavailable', 503), uuid4())

    @app.get('/health/live')
    async def live():
        return {'live': True}

    @app.get('/health/ready', response_model=m.RuntimeReady)
    async def ready(request: Request):
        runtime.service(request, 'readiness')
        query_options(request, set())
        health = runtime.cache.health()
        saturated = runtime.executor.health()['saturated'] or runtime.executor.closed
        health['ready'] = health['ready'] and not saturated
        health['degraded'] = health['degraded'] or saturated
        return JSONResponse(health, status_code=200 if health['ready'] else 503)

    @app.get('/v1/client-config', response_model=ClientConfigModel)
    async def client_config(request: Request):
        actor = runtime.workload(request)
        query_options(request, set())
        return response(runtime.cache.resolve((actor.tenant_id, actor.workload_id))[2].to_wire(), ClientConfigModel)

    @app.get('/v1/capabilities', response_model=m.Capabilities)
    async def capabilities(request: Request):
        try:
            runtime.service(request, 'schema')
        except ContractProblem:
            runtime.workload(request)
        query_options(request, set())
        return response(dict(schema_version=d.SCHEMA_VERSION, supported_stages=['input', 'output'],
                    supported_actions=list(BatchDecisionEngine.actions),
                    evaluators=[dict(id='local_pii', format='pii-ascii-v1', category='GR-003')]), m.Capabilities)

    @app.post('/v1/evaluate', response_model=DecisionModel)
    async def evaluate(request: Request):
        actor = runtime.workload(request)
        query_options(request, set())
        # Admission precedes reading/retaining any body bytes.
        ticket = runtime.executor.admit()
        started = time.monotonic()
        try:
            async with asyncio.timeout(runtime.config.runtime_deadline_ms / 1000):
                raw = await body_bytes(request, runtime.config.max_http_bytes)
            value = strict_json_loads(raw)
            if isinstance(value, dict) and isinstance(value.get('content'), dict):
                segments = value['content'].get('segments')
                if isinstance(segments, list) and (len(segments) > runtime.config.max_segments or sum(
                        len(s['text'].encode()) for s in segments if isinstance(s, dict) and isinstance(s.get('text'), str)) > runtime.config.max_content_bytes):
                    raise ContractProblem(status=413)
            model = parse_model(raw, EvaluationRequestModel)
            if (model.tenant_id, model.workload_id) != (actor.tenant_id, actor.workload_id):
                raise ContractProblem('identity_mismatch', 403)
            evaluation = model.to_domain()
            if (len(evaluation.content.segments) > runtime.config.max_segments
                    or sum(len(s.text.encode()) for s in evaluation.content.segments) > runtime.config.max_content_bytes):
                raise ContractProblem(status=413)
            decision = await runtime.evaluate(evaluation, ticket, started)
            return response(decision.to_wire(), DecisionModel)
        except TimeoutError:
            raise ContractProblem('evaluation_timeout', 503) from None
        finally:
            ticket.release()

    @app.post('/v1/client-events', response_model=m.ClientEventsResponse)
    async def client_events(request: Request):
        actor = runtime.workload(request)
        query_options(request, set())
        ticket = runtime.executor.admit()
        try:
            events = strict_json_loads(await body_bytes(request, runtime.config.max_http_bytes))
            if not isinstance(events, list) or not 1 <= len(events) <= 100:
                raise ContractProblem(status=422)
            result = await asyncio.get_running_loop().run_in_executor(runtime.audit_pool, partial(
                runtime.audit.ingest, events, (actor.tenant_id, actor.workload_id), deadline=time.monotonic() + 0.5))
            return response({'accepted_event_ids': result}, m.ClientEventsResponse)
        finally:
            ticket.release()

    @app.get('/internal/v1/audit', response_model=AuditPage)
    async def audit(request: Request):
        runtime.service(request, 'audit')
        query, limit = query_options(request, {'tenant_id', 'workload_id', 'correlation_id', 'source', 'category', 'include_content', 'limit', 'cursor'})
        if not query.get('tenant_id') or query.get('source') == 'management':
            raise ContractProblem('identity_mismatch', 403)
        include = query.get('include_content') == 'true'
        if include:
            try:
                UUID(request.headers.get('x-audit-actor', ''))
                if request.headers.get('x-audit-role') != 'security_admin':
                    raise ValueError()
            except ValueError:
                raise ContractProblem('identity_mismatch', 403) from None
        items = await asyncio.to_thread(runtime.audit.query, query, after=cursor_key(request, query), limit=limit, include_content=include)
        page = paginate(items, lambda e: (e['occurred_at'], e['event_id']), request, query, limit)
        return response(dict(**page, complete=True, source_status={'runtime': 'available'}), AuditPage)

    @app.get('/openapi.json')
    async def schema(request: Request):
        runtime.service(request, 'schema')
        query_options(request, set())
        return app.openapi()

    if runtime is not None and runtime.control_hash:
        @app.put('/proof/fault', include_in_schema=False)
        async def fault(request: Request):
            runtime.proof_authorize(request)
            body = parse_model(await body_bytes(request, 1024), m.FaultControl)
            runtime.fault = body.worker
            return {'worker': runtime.fault}

        @app.get('/proof/resources', include_in_schema=False)
        async def resources(request: Request):
            runtime.proof_authorize(request)
            runtime.contexts.expire()
            return dict(**runtime.executor.health(), contexts=len(runtime.contexts.contexts),
                        acknowledged_generation=runtime.cache.acknowledged_generation,
                        worker_pids=sorted(p.pid for p in runtime.executor.processes))

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        _, definitions = models_json_schema([(EvaluationRequestModel, 'validation'), (m.RuntimeAuditEvent, 'validation')],
                                             ref_template='#/components/schemas/{model}')
        schema['components']['schemas'].update(definitions['$defs'])
        schema['components']['securitySchemes'] = {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}
        for path, methods in schema['paths'].items():
            if path != '/health/live':
                for operation in methods.values():
                    operation['security'] = [{'bearerAuth': []}]
                    for code in (400, 401, 403, 413, 422, 429, 503):
                        operation['responses'][str(code)] = {'description': 'Sanitized Problem', 'content': {
                            'application/problem+json': {'schema': {'$ref': '#/components/schemas/ProblemModel'}}}}
        schema['components']['schemas']['ProblemModel'] = ProblemModel.model_json_schema(ref_template='#/components/schemas/{model}')
        # Include the nested field error definition in the same component space.
        schema['components']['schemas'].update(schema['components']['schemas']['ProblemModel'].pop('$defs', {}))
        schema['paths']['/health/ready']['get']['responses']['503'] = {'description': 'Not ready', 'content': {
            'application/json': {'schema': {'$ref': '#/components/schemas/RuntimeReady'}}}}
        params = []
        for name in ('tenant_id', 'workload_id', 'correlation_id', 'source', 'category', 'cursor'):
            params.append({'name': name, 'in': 'query', 'required': name == 'tenant_id', 'schema': {'type': 'string'}})
        params.extend([{'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50}},
                       {'name': 'include_content', 'in': 'query', 'schema': {'type': 'boolean', 'default': False}},
                       {'name': 'X-Audit-Actor', 'in': 'header', 'schema': {'type': 'string', 'format': 'uuid'}},
                       {'name': 'X-Audit-Role', 'in': 'header', 'schema': {'type': 'string', 'enum': ['security_admin', 'tenant_operator']}}])
        schema['paths']['/internal/v1/audit']['get']['parameters'] = params
        for path, model in (('/v1/evaluate', EvaluationRequestModel), ('/v1/client-events', m.RuntimeAuditEvent)):
            body = {'$ref': '#/components/schemas/' + model.__name__}
            if path.endswith('client-events'):
                body = dict(type='array', minItems=1, maxItems=100, items=body)
            schema['paths'][path]['post']['requestBody'] = dict(required=True, content={'application/json': {'schema': body}})
        return schema

    app.openapi = openapi
    return app
