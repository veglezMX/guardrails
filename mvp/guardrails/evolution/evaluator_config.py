"""Explicit private operator endpoint snapshots; never discovers or dispatches.

The registry owns lifecycle and policy admission. Loading is a bounded local
operation, and configured support alone makes no health or capability claim.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import ssl
import stat
from datetime import UTC, datetime
from pathlib import Path

from .bedrock import AwsCredentials
from .classifier import ClassifierEndpoint, ClassifierEndpointConfig
from .egress import AuthorizedRoute, PinnedHttpsTransport
from .errors import EvolutionError
from .managed_endpoint import BedrockEndpoint
from .models import Destination, ProviderProfile, canonical_json_bytes

SCHEMA_VERSION = "evolution-evaluator-endpoints-v1"
MAX_CONFIG_BYTES = 1048576
MAX_CA_BYTES = 262144
MAX_SECRET_BYTES = 16384
_CLASSIFIER = {"tenant_id", "provider", "destination", "language_profile", "bearer_token_file", "ca_file"}
_BEDROCK = {"tenant_id", "provider", "destination", "region", "credentials_file", "ca_file", "enabled"}
_CREDENTIALS = {"credential_ref", "access_key_id", "secret_access_key", "session_token", "expires_at"}


def _require(value):
    if not value:
        raise EvolutionError("validation_failed")


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_nlink, info.st_mode,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(path, *, limit, private):
    """Read one canonical regular file, refusing substitution and blocking FIFOs."""
    _require(isinstance(path, (str, Path)))
    text = os.fspath(path)
    _require(type(text) is str and 1 <= len(text) <= 4096)
    candidate = Path(text)
    _require(candidate.is_absolute() and str(candidate) == text
             and candidate.resolve(strict=True) == candidate)
    before = candidate.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
             and before.st_nlink == 1 and 0 < before.st_size <= limit
             and stat.S_IMODE(before.st_mode) in ({0o600} if private else {0o600, 0o644}))
    fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        _require(_identity(os.fstat(fd)) == _identity(before))
        pieces, size = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            pieces.append(chunk)
            size += len(chunk)
            _require(size <= limit)
        _require(size == before.st_size
                 and _identity(os.fstat(fd)) == _identity(before)
                 and _identity(candidate.lstat()) == _identity(before)
                 and candidate.resolve(strict=True) == candidate)
        return b"".join(pieces), _identity(before)
    finally:
        os.close(fd)


class _Inputs:
    def __init__(self):
        self.files = {}

    def read(self, path, *, limit, private=True):
        raw, identity = _read(path, limit=limit, private=private)
        key = os.fspath(path)
        previous = self.files.get(key)
        if previous is not None:
            _require(previous[:2] == (raw, identity))
        self.files[key] = (raw, identity, limit, private)
        return raw

    def check(self):
        for path, (raw, identity, limit, private) in self.files.items():
            _require(_read(path, limit=limit, private=private) == (raw, identity))


def _document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def number(value):
        result = float(value)
        _require(math.isfinite(result))
        return result

    def constant(_value):
        raise EvolutionError("validation_failed")

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                        parse_float=number, parse_constant=constant)
    _require(type(result) is dict)
    return result


def _tenant(value):
    _require(type(value) is str and re.fullmatch(r"[a-z0-9-]{1,64}", value))
    return value


def _same(bindings, key, value):
    if key in bindings:
        _require(bindings[key] == value)
    bindings[key] = value


def _resources(row, tenants, resources):
    tenant = _tenant(row["tenant_id"])
    _require(tenants is None or tenant in tenants)
    _require(type(row["provider"]) is dict and type(row["destination"]) is dict)
    provider = ProviderProfile.model_validate_json(canonical_json_bytes(row["provider"]))
    destination = Destination.model_validate_json(canonical_json_bytes(row["destination"]))
    _require(provider.tenant_id in {tenant, "_platform"}
             and destination.tenant_id in {tenant, "_platform"}
             and provider.credential_ref is not None)
    _same(resources, ("provider", provider.provider_id), canonical_json_bytes(provider))
    _same(resources, ("destination", destination.destination_id), canonical_json_bytes(destination))
    return tenant, provider, destination


def _transport(row, inputs, destination, ca_refs, *, local=False):
    raw = inputs.read(row["ca_file"], limit=MAX_CA_BYTES, private=False)
    # Parse only the explicitly read CA bytes; never consult ambient trust/env.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=raw.decode("ascii"))
    _same(ca_refs, destination.tls_ca_ref, raw)
    services = {}
    if local:
        addresses = []
        for cidr in destination.cidrs:
            network = ipaddress.ip_network(cidr, strict=True)
            _require(network.prefixlen == network.max_prefixlen)
            addresses.append(str(network.network_address))
        _require(len(addresses) == len(set(addresses)))
        services[destination.destination_id] = (destination.host, tuple(addresses))
    return PinnedHttpsTransport(ca_pem={destination.tls_ca_ref: raw},
                               local_services=services)


def _credentials(raw, reference, now):
    document = _document(raw)
    _require(set(document) == _CREDENTIALS and document["credential_ref"] == reference)
    expires = document["expires_at"]
    if expires is not None:
        _require(type(expires) is str and re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", expires))
        expires = datetime.fromisoformat(expires)
    value = AwsCredentials(document["access_key_id"], document["secret_access_key"],
                           document["session_token"], expires)
    value.validate(now)
    return value


def load_evaluator_endpoints(path, *, runtime_zone, external_processing=False, tenant_ids=None):
    """Return registry kwargs from one explicit operator snapshot, with no I/O beyond files.

    Both arrays are required, with zero to four rows each. Empty arrays disable
    configuration. Managed rows additionally require an explicit ``enabled``
    boolean; enabling them requires the caller's external-processing permission.
    Credential JSON uses the existing explicit AWS snapshot fields and UTC Z
    expiry (null is allowed only where AwsCredentials permits it). Bearer files
    contain an ASCII token with at most one trailing LF. The registry still
    authenticates exact approved policy resources for every dispatch.
    """
    try:
        _require(type(runtime_zone) is str and 1 <= len(runtime_zone) <= 128
                 and type(external_processing) is bool)
        tenants = None
        if tenant_ids is not None:
            _require(type(tenant_ids) in (tuple, list) and len(tenant_ids) <= 4)
            tenants = tuple(_tenant(value) for value in tenant_ids)
            _require(len(tenants) == len(set(tenants)))
        inputs = _Inputs()
        document = _document(inputs.read(path, limit=MAX_CONFIG_BYTES))
        _require(set(document) == {"schema_version", "classifier", "bedrock"}
                 and document["schema_version"] == SCHEMA_VERSION)
        for name in ("classifier", "bedrock"):
            _require(type(document[name]) is list and len(document[name]) <= 4)
        classifiers, managed, resources, ca_refs, credentials = {}, {}, {}, {}, {}
        for row in document["classifier"]:
            _require(type(row) is dict and set(row) == _CLASSIFIER)
            tenant, provider, destination = _resources(row, tenants, resources)
            _require(tenant not in classifiers)
            token = inputs.read(row["bearer_token_file"], limit=4097).decode("ascii")
            token = token.removesuffix("\n")
            _same(credentials, provider.credential_ref, ("classifier", token))
            config = ClassifierEndpointConfig(provider=provider, destination=destination,
                runtime_zone=runtime_zone, language_profile=row["language_profile"], bearer_token=token)
            route = config.route()
            for suffix in ("ready", "evaluate"):
                PinnedHttpsTransport.validate_url(route,
                    f"https://{destination.host}" + (f":{destination.port}" if destination.port != 443 else "")
                    + "/v1/classifier/" + suffix)
            transport = _transport(row, inputs, destination, ca_refs, local=True)
            # Exercise the actual transport address policy locally, without DNS.
            transport._addresses(route, transport.local_services[destination.destination_id][1])
            classifiers[tenant] = ClassifierEndpoint(config, transport)
        for row in document["bedrock"]:
            _require(type(row) is dict and set(row) == _BEDROCK
                     and type(row["enabled"]) is bool
                     and (not row["enabled"] or external_processing))
            tenant, provider, destination = _resources(row, tenants, resources)
            _require(tenant not in managed)
            region = row["region"]
            _require(type(region) is str and re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", region))
            suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
            _require(destination.host == f"bedrock-runtime.{region}.{suffix}" and destination.port == 443)
            AuthorizedRoute(provider, destination, external_processing=True).validate()
            raw = inputs.read(row["credentials_file"], limit=MAX_SECRET_BYTES)
            value = _credentials(raw, provider.credential_ref, datetime.now(UTC))
            _same(credentials, provider.credential_ref, ("bedrock", value))
            managed[tenant] = BedrockEndpoint(provider=provider, destination=destination,
                runtime_zone=runtime_zone, region=region, credentials=value,
                transport=_transport(row, inputs, destination, ca_refs), enabled=row["enabled"])
        inputs.check()
        for endpoint in managed.values():
            endpoint.credentials.validate(datetime.now(UTC))
        return {"classifier_endpoints": classifiers, "bedrock_endpoints": managed}
    except EvolutionError:
        raise
    except (ValueError, TypeError, OSError, RuntimeError, RecursionError, OverflowError):
        # Pydantic, JSON, SSL, file and encoding exceptions may carry private input.
        raise EvolutionError("validation_failed") from None
