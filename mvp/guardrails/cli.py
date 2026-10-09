"""API-backed local-proof lifecycle. Secrets are accepted only through files."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4


class CLIError(Exception):
    def __init__(self, code='invalid_request', exit_code=2, status=422, *, committed=None):
        self.code, self.exit_code, self.status, self.committed = code, exit_code, status, committed


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIError(status=400)


def positive(value):
    result = int(value)
    if result < 1:
        raise ValueError()
    return result


def identifier(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
        raise ValueError()
    return value


def parser():
    common = Parser(add_help=False)
    for flag in ('management-url', 'runtime-url', 'token-file', 'ca-file'):
        common.add_argument('--' + flag, default=argparse.SUPPRESS)
    common.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
    common.add_argument('--no-wait', action='store_true', default=argparse.SUPPRESS)
    common.add_argument('--wait-timeout', type=float, default=argparse.SUPPRESS)
    root = Parser(prog='guardrails', description=__doc__, parents=[common])
    groups = root.add_subparsers(dest='group', required=True)
    specs = {
        'tenants': {'register': ('id', 'name')},
        'workloads': {'register': ('tenant', 'id', 'name', 'credential-out'), 'list': ('tenant',),
                      'get': ('tenant', 'id'), 'rotate-credential': ('tenant', 'id', 'revision', 'reason', 'credential-out')},
        'policies': {'validate': ('tenant', 'file'), 'publish': ('tenant', 'file'), 'list': ('tenant',), 'get': ('tenant', 'id', 'version')},
        'bindings': {'set': ('tenant', 'workload', 'policy', 'version', 'reason'), 'get': ('tenant', 'workload'),
                     'history': ('tenant', 'workload'), 'state': ('tenant', 'workload', 'state', 'revision', 'reason', 'fixture-run'),
                     'rollback': ('tenant', 'workload', 'to-revision', 'revision', 'reason'), 'remove': ('tenant', 'workload', 'revision', 'reason')},
        'floors': {'set': ('scope', 'scope-id', 'file'), 'get': ('scope', 'scope-id')},
        'operations': {'get': ('id',)}, 'audit': {'list': ()},
    }
    for group, commands in specs.items():
        parent = groups.add_parser(group, parents=[common])
        sub = parent.add_subparsers(dest='command', required=True)
        for command, required in commands.items():
            cmd = sub.add_parser(command, parents=[common])
            for flag in required:
                convert = positive if flag in ('revision', 'to-revision') else str
                if flag in ('tenant', 'workload', 'policy', 'version', 'scope-id') or (flag == 'id' and group != 'operations'):
                    convert = identifier
                if flag == 'fixture-run' or (flag == 'id' and group == 'operations'):
                    convert = UUID
                cmd.add_argument('--' + flag, required=True, type=convert)
            if (group, command) in (('bindings', 'set'), ('floors', 'set')):
                cmd.add_argument('--revision', type=positive)
            if (group, command) == ('bindings', 'set'):
                cmd.add_argument('--input-timeout-ms', type=positive, default=1500)
                cmd.add_argument('--output-timeout-ms', type=positive, default=1500)
            if command in ('list', 'history'):
                cmd.add_argument('--limit', type=positive, default=50)
                cmd.add_argument('--cursor')
            if group == 'audit':
                for flag in ('tenant', 'workload', 'correlation', 'source', 'category'):
                    cmd.add_argument('--' + flag)
                cmd.add_argument('--include-content', action='store_true')
                cmd.add_argument('--allow-partial', action='store_true')
    evaluate = groups.add_parser('evaluate', parents=[common])
    evaluate.add_argument('--file', required=True)
    groups.add_parser('capabilities', parents=[common])
    return root


def file_json(path):
    from .transport import strict_json_loads
    raw = Path(path).read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise CLIError(status=413)
    return strict_json_loads(raw)


def route(args):
    a = vars(args)
    group, command = args.group, a.get('command')
    envelope = {'schema_version': 'local-proof-v1'}
    headers, query, body = {}, {}, None
    method, path = 'GET', '/v1/' + group
    if a.get('revision') is not None:
        headers['If-Match'] = str(args.revision)
    elif (group, command) in (('bindings', 'set'), ('floors', 'set')):
        headers['If-None-Match'] = '*'
    if command in ('list', 'history'):
        query = {key: a[key] for key in ('limit', 'cursor') if a.get(key) is not None}
    q = lambda name: quote(str(a[name]), safe='')
    if group == 'tenants':
        method, body = 'POST', envelope | {'tenant_id': args.id, 'display_name': args.name}
    elif group in ('workloads', 'policies', 'bindings'):
        path = '/v1/tenants/' + q('tenant')
        if group == 'workloads':
            path += '/workloads'
            if command == 'register':
                method, body = 'POST', envelope | {'workload_id': args.id, 'display_name': args.name}
            elif command != 'list':
                path += '/' + q('id')
                if command == 'rotate-credential':
                    path += '/credentials/rotate'
                    method, body = 'POST', envelope | {'reason': args.reason}
        elif group == 'policies':
            path += '/policies'
            if command in ('publish', 'validate'):
                method, body = 'POST', file_json(args.file)
                if command == 'validate':
                    path += '/validate'
            elif command == 'get':
                path += '/' + q('id') + '/versions/' + q('version')
        else:
            path += '/workloads/' + q('workload') + '/binding'
            if command == 'set':
                method, body = 'PUT', envelope | {key: a[key] for key in ('input_timeout_ms', 'output_timeout_ms', 'reason')}
                body.update(policy_id=args.policy, policy_version=args.version)
            elif command in ('state', 'rollback'):
                path += '/' + command
                method, body = 'POST', envelope | {'reason': args.reason}
                if command == 'state':
                    body.update(enforcement_state=args.state, evidence_kind='synthetic_local_proof', fixture_run_id=str(args.fixture_run))
                else:
                    body['target_revision'] = args.to_revision
            elif command == 'remove':
                method, body = 'DELETE', envelope | {'reason': args.reason}
            elif command == 'history':
                path += '/history'
    elif group == 'floors':
        path = '/v1/governance/floors/' + q('scope') + '/' + q('scope_id')
        if command == 'set':
            method, body = 'PUT', file_json(args.file)
    elif group == 'operations':
        path += '/' + q('id')
    elif group == 'audit':
        for flag, field in (('tenant', 'tenant_id'), ('workload', 'workload_id'), ('correlation', 'correlation_id'), ('source', 'source'), ('category', 'category')):
            if a.get(flag) is not None:
                query[field] = a[flag]
        query.update(include_content=str(args.include_content).lower(), allow_partial=str(args.allow_partial).lower())
    elif group == 'evaluate':
        method, body = 'POST', file_json(args.file)
    return method, path, body, headers, query


def credential_file(path, token):
    """Atomic create without replacing a preexisting path, even in a race."""
    target = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.credential-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(token + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def problem(error):
    value = {'type': 'urn:guardrails:problem:' + error.code, 'title': 'Command could not complete',
             'status': error.status, 'code': error.code, 'correlation_id': str(uuid4()), 'errors': []}
    if error.committed is not None:
        value['committed_operation'] = error.committed
    return value


def main(argv=None):
    import httpx
    from .transport import ContractProblem, strict_json_loads
    committed = None
    try:
        args = parser().parse_args(argv)
        settings = vars(args)
        wait = settings.get('wait_timeout', 5.0)
        if not math.isfinite(wait) or wait <= 0:
            raise CLIError()
        method, path, body, headers, query = route(args)
        url = settings.get('runtime_url', 'https://localhost:8444') if args.group in ('evaluate', 'capabilities') else settings.get('management_url', 'https://localhost:8443')
        origin = urlsplit(url)
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.query or origin.fragment or origin.path not in ('', '/')):
            raise CLIError()
        if not settings.get('token_file') or not settings.get('ca_file'):
            raise CLIError()
        token_path = Path(args.token_file)
        if token_path.stat().st_mode & 0o077:
            raise CLIError('authentication_failed', 3, 401)
        token = token_path.read_text().strip()
        if not token or any(c.isspace() for c in token):
            raise CLIError('authentication_failed', 3, 401)
        target = settings.get('credential_out')
        if target and (Path(target).exists() or Path(target).is_symlink()):
            raise CLIError('invalid_request', 4, 409)
        tls = ssl.create_default_context(cafile=args.ca_file)
        headers['Authorization'] = 'Bearer ' + token
        with httpx.Client(base_url=url.rstrip('/'), verify=tls, trust_env=False, follow_redirects=False, timeout=5) as client:
            response = client.request(method, path, json=body, headers=headers, params=query)
            if response.status_code >= 300:
                status = response.status_code
                code = 'invalid_request'
                try:
                    from .local_contract import OPERATIONAL_CODES
                    candidate = strict_json_loads(response.content).get('code')
                    if candidate in OPERATIONAL_CODES:
                        code = candidate
                except Exception:
                    pass
                exit_code = 3 if status in (401, 403) else 4 if status in (404, 409, 428) else 2 if status in (400, 413, 422) else 5
                raise CLIError(code, exit_code, status)
            result = strict_json_loads(response.content)
            if not isinstance(result, dict):
                raise CLIError('evaluation_unavailable', 5, 503)
            committed = result.get('operation')
            secret = result.pop('token', None)
            if secret is not None:
                if not target or not isinstance(secret, str):
                    raise CLIError('evaluation_unavailable', 5, 503, committed=committed)
                credential_file(target, secret)
                result['credential_file'] = str(target)
            if committed and not settings.get('no_wait', False):
                deadline, delay = time.monotonic() + wait, .1
                while committed['status'] != 'applied':
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CLIError('management_unavailable', 6, 503, committed=committed)
                    try:
                        receipt = client.get('/v1/operations/' + str(UUID(committed['operation_id'])), headers=headers, timeout=min(.5, remaining))
                        if receipt.status_code == 200:
                            committed = strict_json_loads(receipt.content)
                            result['operation'] = committed
                    except httpx.TransportError:
                        pass
                    if committed['status'] != 'applied':
                        time.sleep(min(delay, max(0, deadline - time.monotonic())))
                        delay = min(.5, delay * 1.5)
            print(json.dumps(result, sort_keys=True))
        return 0
    except CLIError as error:
        print(json.dumps(problem(error)), file=sys.stderr)
        return error.exit_code
    except ContractProblem as error:
        print(json.dumps(problem(CLIError(error.code))), file=sys.stderr)
        return 2
    except (OSError, httpx.HTTPError, ValueError, TypeError, KeyError):
        print(json.dumps(problem(CLIError('evaluation_unavailable', 5, 503, committed=committed))), file=sys.stderr)
        return 5


if __name__ == '__main__':
    raise SystemExit(main())
