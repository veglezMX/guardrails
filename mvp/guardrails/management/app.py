"""Management HTTP boundary: strict bytes, safe errors and explicit commit status."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic.json_schema import models_json_schema
from starlette.concurrency import run_in_threadpool

from .. import local_contract as d
from ..storage.control import ControlRepository, audit, document, read_document
from ..transport import (
    ContractProblem, MAX_JSON_BYTES, PolicyWriteModel, PolicyValidationModel,
    ProblemModel, install_exception_handlers, parse_model, problem_document, strict_json_loads,
)
from . import models as m
from .bindings import BindingService
from .identity import IdentityService
from .policies import PolicyService
from .snapshots import SnapshotPublisher


def identifier(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
        raise ContractProblem(status=422)
    return value


def precondition(headers):
    match, absent = headers.get('if-match'), headers.get('if-none-match')
    if match is not None and absent is not None:
        raise ContractProblem(status=422)
    if absent is not None:
        if absent != '*':
            raise ContractProblem(status=422)
        return '*'
    if match is None:
        return None
    if re.fullmatch(r'"[1-9][0-9]*"', match):
        match = match[1:-1]
    if not re.fullmatch(r'[1-9][0-9]{0,15}', match):
        raise ContractProblem(status=422)
    return int(match)


def query_options(request, allowed):
    pairs = list(request.query_params.multi_items())
    if len({key for key, _ in pairs}) != len(pairs) or any(key not in allowed for key, _ in pairs):
        raise ContractProblem(status=422)
    query = dict(pairs)
    if 'limit' in query and not re.fullmatch(r'[1-9][0-9]{0,2}', query['limit']):
        raise ContractProblem(status=422)
    limit = int(query.get('limit', '50'))
    if limit > 200:
        raise ContractProblem(status=422)
    for key in ('allow_partial', 'include_content'):
        if key in query and query[key] not in ('true', 'false'):
            raise ContractProblem(status=422)
    for key in ('tenant_id', 'workload_id'):
        if key in query:
            identifier(query[key])
    if 'correlation_id' in query:
        try:
            query['correlation_id'] = str(UUID(query['correlation_id']))
        except ValueError:
            raise ContractProblem(status=422) from None
    if query.get('source') not in (None, 'management', 'runtime', 'connector') or query.get('category') not in (None, 'GR-003'):
        raise ContractProblem(status=422)
    return query, limit


def paginate(items, key, request, query, limit):
    """Scope-bound opaque keyset cursor; no caller-provided SQL or offsets."""
    scope = hashlib.sha256(document([request.url.path, {k: v for k, v in query.items() if k not in ('cursor', 'limit')}]).encode()).hexdigest()
    after = None
    if 'cursor' in query:
        try:
            raw = base64.b64decode(query['cursor'], altchars=b'-_', validate=True)
            decoded = strict_json_loads(raw, max_bytes=2048)
            if set(decoded) != {'scope', 'after'} or decoded['scope'] != scope or not isinstance(decoded['after'], list) or not all(isinstance(item, str) for item in decoded['after']):
                raise ValueError()
            after = tuple(decoded['after'])
        except (ValueError, TypeError, ContractProblem):
            raise ContractProblem(status=422) from None
    ordered = sorted(items, key=key)
    if after is not None:
        ordered = [item for item in ordered if key(item) > after]
    page = ordered[:limit]
    cursor = None
    if len(ordered) > limit:
        cursor = base64.urlsafe_b64encode(document({'scope': scope, 'after': key(page[-1])}).encode()).decode()
    return dict(items=page, next_cursor=cursor)


class ManagementBoundary:
    def __init__(self, repository):
        self.repository = repository
        self.identity = IdentityService(repository)
        self.policies = PolicyService(repository)
        self.bindings = BindingService(repository)
        self.snapshots = SnapshotPublisher(repository)

    def dispatch(self, name, request, body, actor, correlation_id):
        args = request.path_params
        tenant_id, workload_id = args.get('t'), args.get('w')
        for key, value in args.items():
            if key == 'operation_id':
                try:
                    UUID(value)
                except ValueError:
                    raise ContractProblem(status=422) from None
            else:
                identifier(value)
        if name == 'tenants_create':
            return self.identity.register_tenant(actor, body, correlation_id), 201
        if name == 'workloads_create':
            return self.identity.register_workload(actor, tenant_id, body, correlation_id), 201
        if name == 'rotate':
            return self.identity.rotate(actor, tenant_id, workload_id, precondition(request.headers), correlation_id), 201
        if name == 'policies_validate':
            return self.policies.validate(actor, tenant_id, body), 200
        if name == 'policies_publish':
            result, created = self.policies.publish(actor, tenant_id, body, correlation_id)
            return result, 201 if created else 200
        if name in ('binding_set', 'binding_state', 'binding_rollback', 'binding_remove'):
            return self.bindings.change(actor, tenant_id, workload_id, body, precondition(request.headers), name.removeprefix('binding_'), correlation_id), 202
        if name == 'binding_read':
            return self.bindings.read(actor, tenant_id, workload_id), 200
        if name == 'floor_set':
            return self.bindings.set_floor(actor, args['scope'], args['scope_id'], body, precondition(request.headers), correlation_id), 202
        if name == 'operation':
            return self.snapshots.operation(actor, args['operation_id']), 200
        if name == 'snapshot':
            snapshot = self.snapshots.snapshot(actor)
            etag = '"' + snapshot['digest'] + '"'
            if request.headers.get('if-none-match') == etag:
                return Response(status_code=304, headers={'ETag': etag, 'Cache-Control': 'no-store'}), 304
            return JSONResponse(snapshot, headers={'ETag': etag, 'Cache-Control': 'no-store'}), 200
        if name == 'applied':
            return self.snapshots.acknowledge(actor, body, correlation_id), 200
        if name == 'audit':
            return self.audit_query(actor, request, correlation_id)
        self.identity.authorize(actor, tenant_id)
        if name == 'ready':
            with self.repository.transaction() as connection:
                connection.execute('SELECT version FROM schema_migrations').fetchone()
            return dict(ready=True, degraded=False), 200
        if name == 'schema':
            return request.app.openapi(), 200
        if name == 'runtime_schema':
            raise ContractProblem('evaluation_unavailable', 503)
        with self.repository.transaction() as connection:
            if name == 'workload':
                return read_document(connection.execute('SELECT document FROM workloads WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id)).fetchone()), 200
            if name == 'policy':
                return read_document(connection.execute('SELECT document FROM policies WHERE tenant_id=? AND policy_id=? AND version=?', (tenant_id, args['p'], args['v'])).fetchone()), 200
            if name == 'floor':
                scope, scope_id = args['scope'], args['scope_id']
                if scope not in ('organization', 'tenant') or (scope == 'organization' and scope_id != 'org'):
                    raise ContractProblem(status=404)
                if scope == 'tenant':
                    self.identity.authorize(actor, scope_id)
                return read_document(connection.execute('SELECT document FROM governance_floors WHERE scope=? AND scope_id=?', (scope, scope_id)).fetchone()), 200
            query, limit = query_options(request, {'limit', 'cursor'})
            read_document(connection.execute('SELECT document FROM tenants WHERE tenant_id=?', (tenant_id,)).fetchone())
            if name == 'workloads':
                items = [read_document(row) for row in connection.execute('SELECT document FROM workloads WHERE tenant_id=?', (tenant_id,))]
                key = lambda item: (item['created_at'], item['workload_id'])
            elif name == 'policies':
                items = [m.PolicySummary.model_validate_json(document({k: v for k, v in read_document(row).items() if k not in ('checks', 'failure_behavior')})).model_dump(mode='json')
                         for row in connection.execute('SELECT document FROM policies WHERE tenant_id=?', (tenant_id,))]
                key = lambda item: (item['published_at'], item['policy_id'], item['version'])
            elif name == 'history':
                read_document(connection.execute('SELECT document FROM workloads WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id)).fetchone())
                items = [read_document(row) for row in connection.execute('SELECT document FROM binding_history WHERE tenant_id=? AND workload_id=?', (tenant_id, workload_id))]
                key = lambda item: (item['updated_at'], str(item['revision']).zfill(20))
            else:
                raise ContractProblem(status=404)
            return paginate(items, key, request, query, limit), 200

    def audit_query(self, actor, request, correlation_id):
        self.identity.authorize(actor)
        query, limit = query_options(request, {'tenant_id', 'workload_id', 'correlation_id', 'source', 'category', 'allow_partial', 'include_content', 'limit', 'cursor'})
        tenant_id = query.get('tenant_id')
        if actor.role == 'tenant_operator' and tenant_id is None:
            raise ContractProblem('identity_mismatch', 403)
        self.identity.authorize(actor, tenant_id)
        if query.get('include_content') == 'true':
            self.identity.authorize(actor, admin=True)
            with self.repository.transaction(write=True) as connection:
                audit(connection, actor_id=str(actor.credential_id), tenant_id=tenant_id,
                      event_type='audit_content_read', correlation_id=correlation_id, metadata={})
        source = query.get('source')
        status = {}
        items = []
        if source in (None, 'management'):
            status['management'] = 'available'
            try:
                with self.repository.transaction() as connection:
                    clauses, params = [], []
                    for key in ('tenant_id', 'workload_id', 'correlation_id'):
                        if key in query:
                            clauses.append(key + '=?')
                            params.append(query[key])
                    sql = 'SELECT document FROM management_audit' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                    items = [read_document(row) for row in connection.execute(sql, params)]
                    if 'category' in query:
                        items = [item for item in items if item['metadata'].get('guardrail_category') == query['category']]
            except ContractProblem as error:
                if error.status != 503:
                    raise
                status['management'] = 'unavailable'
        if source in (None, 'runtime', 'connector'):
            # Phase 3 installs the authenticated runtime audit adapter. Until then
            # this source is explicitly unavailable, never an empty successful query.
            status['runtime'] = 'unavailable'
        complete = all(value == 'available' for value in status.values())
        if not complete and query.get('allow_partial') != 'true':
            result = problem_document(ContractProblem('evaluation_unavailable', 503), UUID(correlation_id))
            result.update(source_status=status, complete=False)
            return JSONResponse(result, status_code=503, media_type='application/problem+json'), 503
        page = paginate(items, lambda item: (item['occurred_at'], item['event_id']), request, query, limit)
        return dict(**page, source_status=status, complete=complete), 200


# Models listed here produce both endpoint validation and the published OpenAPI.
ROUTES = (
    ('POST', '/v1/tenants', 'tenants_create', m.TenantWrite, m.TenantModel, 201),
    ('GET', '/v1/tenants/{t}/workloads', 'workloads', None, m.Page[m.WorkloadModel], 200),
    ('POST', '/v1/tenants/{t}/workloads', 'workloads_create', m.WorkloadWrite, m.WorkloadIssued, 201),
    ('GET', '/v1/tenants/{t}/workloads/{w}', 'workload', None, m.WorkloadModel, 200),
    ('POST', '/v1/tenants/{t}/workloads/{w}/credentials/rotate', 'rotate', m.RotationWrite, m.CredentialIssued, 201),
    ('POST', '/v1/tenants/{t}/policies/validate', 'policies_validate', PolicyWriteModel, PolicyValidationModel, 200),
    ('POST', '/v1/tenants/{t}/policies', 'policies_publish', PolicyWriteModel, m.PolicyPublished, 201),
    ('GET', '/v1/tenants/{t}/policies', 'policies', None, m.Page[m.PolicySummary], 200),
    ('GET', '/v1/tenants/{t}/policies/{p}/versions/{v}', 'policy', None, m.PolicyModel, 200),
    ('PUT', '/v1/tenants/{t}/workloads/{w}/binding', 'binding_set', m.BindingWrite, m.Mutation[m.PolicyBindingModel], 202),
    ('GET', '/v1/tenants/{t}/workloads/{w}/binding', 'binding_read', None, m.BindingRead, 200),
    ('GET', '/v1/tenants/{t}/workloads/{w}/binding/history', 'history', None, m.Page[m.PolicyBindingModel], 200),
    ('POST', '/v1/tenants/{t}/workloads/{w}/binding/state', 'binding_state', m.StateWrite, m.Mutation[m.PolicyBindingModel], 202),
    ('POST', '/v1/tenants/{t}/workloads/{w}/binding/rollback', 'binding_rollback', m.RollbackWrite, m.Mutation[m.PolicyBindingModel], 202),
    ('DELETE', '/v1/tenants/{t}/workloads/{w}/binding', 'binding_remove', m.RemovalWrite, m.OperationModel, 202),
    ('PUT', '/v1/governance/floors/{scope}/{scope_id}', 'floor_set', m.FloorWrite, m.Mutation[m.GovernanceFloorModel], 202),
    ('GET', '/v1/governance/floors/{scope}/{scope_id}', 'floor', None, m.GovernanceFloorModel, 200),
    ('GET', '/v1/operations/{operation_id}', 'operation', None, m.OperationModel, 200),
    ('GET', '/v1/audit', 'audit', None, m.AuditPage, 200),
    ('GET', '/internal/v1/snapshot', 'snapshot', None, m.SnapshotModel, 200),
    ('POST', '/internal/v1/applied', 'applied', m.AppliedWrite, m.AppliedResponse, 200),
    ('GET', '/health/ready', 'ready', None, m.ReadyModel, 200),
    ('GET', '/openapi.json', 'schema', None, None, 200),
    ('GET', '/runtime-openapi.json', 'runtime_schema', None, None, 200),
)


def create_app(repository=None, *, credentials=(), schema_only=False):
    if repository is None and not schema_only:
        config = strict_json_loads(Path(os.environ['GUARDRAILS_MANAGEMENT_CONFIG']).read_bytes())
        credentials = tuple(parse_model(document(value).encode(), m.CredentialModel).to_domain() for value in config['credentials'])
        repository = ControlRepository(os.environ['GUARDRAILS_CONTROL_DB'])
    boundary = ManagementBoundary(repository)
    if credentials:
        boundary.identity.bootstrap(credentials)
    app = FastAPI(title='Guardrails local proof management', version=d.SCHEMA_VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None)
    install_exception_handlers(app)
    app.state.management = boundary

    @app.get('/health/live')
    def live():
        return {'live': True}

    def endpoint(name, model, response_model):
        async def handle(request: Request):
            correlation_id = str(uuid4())
            try:
                supplied = request.headers.get('x-correlation-id')
                if supplied is not None:
                    try:
                        correlation_id = str(UUID(supplied))
                    except ValueError:
                        raise ContractProblem(status=422) from None
                actor = await run_in_threadpool(boundary.identity.authenticate, request.headers.get('authorization'))
                if name not in ('workloads', 'policies', 'history', 'audit'):
                    query_options(request, set())
                body = None
                if model is not None:
                    raw = bytearray()
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > MAX_JSON_BYTES:
                            raise ContractProblem(status=413)
                        raw.extend(chunk)
                    body = parse_model(bytes(raw), model)
                result, status = await run_in_threadpool(boundary.dispatch, name, request, body, actor, correlation_id)
                if isinstance(result, Response):
                    return result
                if response_model:
                    try:
                        result = parse_model(document(result).encode(), response_model).model_dump(mode='json')
                    except ContractProblem:
                        raise ContractProblem('management_unavailable', 503) from None
                return JSONResponse(result, status_code=status, headers={'Cache-Control': 'no-store', 'X-Correlation-ID': correlation_id})
            except ContractProblem as error:
                return JSONResponse(problem_document(error, UUID(correlation_id)), status_code=error.status, media_type='application/problem+json')
            except d.DomainValidationError as error:
                return JSONResponse(problem_document(ContractProblem(error.code, 422), UUID(correlation_id)), status_code=422, media_type='application/problem+json')
            except Exception:
                # Uvicorn must not print request values or arbitrary exception text.
                print('management_unavailable', file=sys.stderr)
                return JSONResponse(problem_document(ContractProblem('management_unavailable', 503), UUID(correlation_id)), status_code=503, media_type='application/problem+json')
        handle.__name__ = name
        return handle

    for method, path, name, model, response_model, status in ROUTES:
        extra = {'requestBody': {'required': True, 'content': {'application/json': {'schema': {'$ref': f'#/components/schemas/{model.__name__}'}}}}} if model else {}
        parameters = []
        for parameter in re.findall(r'\{([^}]+)\}', path):
            schema = {'type': 'string', 'format': 'uuid'} if parameter == 'operation_id' else {'type': 'string', 'pattern': r'^[a-z0-9][a-z0-9_-]{0,63}$'}
            parameters.append({'name': parameter, 'in': 'path', 'required': True, 'schema': schema})
        if name in ('workloads', 'policies', 'history', 'audit'):
            parameters.extend([{'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'default': 50, 'minimum': 1, 'maximum': 200}},
                               {'name': 'cursor', 'in': 'query', 'schema': {'type': 'string'}}])
        if name == 'audit':
            for parameter in ('tenant_id', 'workload_id', 'correlation_id', 'source', 'category'):
                parameters.append({'name': parameter, 'in': 'query', 'schema': {'type': 'string'}})
            for parameter in ('allow_partial', 'include_content'):
                parameters.append({'name': parameter, 'in': 'query', 'schema': {'type': 'boolean', 'default': False}})
        if model:
            parameters.append({'name': 'X-Correlation-ID', 'in': 'header', 'schema': {'type': 'string', 'format': 'uuid'}})
        if name in ('rotate', 'binding_set', 'binding_state', 'binding_rollback', 'binding_remove', 'floor_set'):
            parameters.append({'name': 'If-Match', 'in': 'header', 'schema': {'type': 'string'}, 'description': 'Current revision required for an existing resource (428 if absent; 409 if stale).'})
        if name in ('binding_set', 'floor_set', 'snapshot'):
            parameters.append({'name': 'If-None-Match', 'in': 'header', 'schema': {'type': 'string'}, 'description': 'Use * for creation; snapshot reads use the returned ETag.'})
        extra['parameters'] = parameters
        responses = {code: {'model': ProblemModel} for code in (400, 401, 403, 404, 409, 413, 422, 428, 503)}
        if name == 'policies_publish':
            responses[200] = {'model': m.PolicyPublished, 'description': 'Identical immutable publication readback'}
        if name == 'snapshot':
            responses[304] = {'description': 'Unchanged authenticated snapshot'}
        if name == 'audit':
            responses[503] = {'model': m.AuditUnavailable, 'description': 'Explicit unavailable audit sources'}
        app.add_api_route(path, endpoint(name, model, response_model), methods=[method],
                          response_model=response_model, status_code=status,
                          responses=responses,
                          openapi_extra=extra)
    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        requests = {(model, 'validation') for _, _, _, model, _, _ in ROUTES if model}
        _, definitions = models_json_schema(sorted(requests, key=lambda item: item[0].__name__), ref_template='#/components/schemas/{model}')
        schema['components']['schemas'].update(definitions['$defs'])
        schema['components']['securitySchemes'] = {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}
        for method, path, _, _, _, _ in ROUTES:
            schema['paths'][path][method.lower()]['security'] = [{'bearerAuth': []}]
        return schema

    app.openapi = openapi
    return app
