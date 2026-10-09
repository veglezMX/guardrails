"""Verified TLS delegation of audit reads; no evaluation bodies reach management."""
import ssl
from pathlib import Path
from types import SimpleNamespace

import httpx

from ..transport import ContractProblem, parse_model, strict_json_loads
from .models import AuditPage


class RuntimeAuditClient:
    def __init__(self, url, ca_file, token_file):
        if httpx.URL(url).scheme != 'https' or httpx.URL(url).userinfo:
            raise ValueError('tls_required')
        self.client = httpx.Client(base_url=url, verify=ssl.create_default_context(cafile=ca_file),
                                   headers={'Authorization': 'Bearer ' + Path(token_file).read_text().strip()},
                                   timeout=0.5, trust_env=False,
                                   limits=httpx.Limits(max_keepalive_connections=0))

    def get(self, path, **kwargs):
        try:
            with self.client.stream('GET', path, **kwargs) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > 8_388_608:
                        raise ValueError()
                    raw.extend(chunk)
                return bytes(raw)
        except (httpx.HTTPError, ValueError):
            raise ContractProblem('evaluation_unavailable', 503) from None

    def audit(self, query, limit, *, after, actor_id, role):
        from .app import cursor_for
        params = {k: v for k, v in query.items() if k in ('tenant_id', 'workload_id', 'correlation_id', 'source', 'category', 'include_content')}
        params['limit'] = str(limit)
        if after:
            params['cursor'] = cursor_for(SimpleNamespace(url=SimpleNamespace(path='/internal/v1/audit')), params, after)
        raw = self.get('/internal/v1/audit', params=params,
                       headers={'X-Audit-Actor': actor_id, 'X-Audit-Role': role})
        try:
            result = parse_model(raw, AuditPage).model_dump(mode='json')
            if not result['complete'] or result['source_status'] != {'runtime': 'available'}:
                raise ValueError()
            for event in result['items']:
                if event['tenant_id'] != params['tenant_id'] or event['source'] not in ('runtime', 'connector'):
                    raise ValueError()
                if params.get('include_content') != 'true':
                    event['content'] = None
            return result
        except (ContractProblem, ValueError):
            raise ContractProblem('evaluation_unavailable', 503) from None

    def schema(self):
        try:
            result = strict_json_loads(self.get('/openapi.json'))
            if not isinstance(result, dict) or 'openapi' not in result or 'paths' not in result:
                raise ValueError()
            return result
        except (ContractProblem, ValueError):
            raise ContractProblem('evaluation_unavailable', 503) from None

    def close(self):
        self.client.close()
