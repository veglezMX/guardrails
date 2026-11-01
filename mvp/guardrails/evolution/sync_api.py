"""Runtime-only synchronization routes; mount on the TLS management service."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .errors import EvolutionError
from .governance import principal
from .models import canonical_json_bytes, parse_time, strict_json_loads
from .snapshots import stamp


def mount_sync_routes(app, service, distributor):
    async def handle(request: Request):
        correlation = str(uuid4())
        headers = {"Cache-Control": "no-store", "X-Request-ID": correlation}
        try:
            if request.url.scheme != "https":
                raise EvolutionError("forbidden")
            authorization = request.headers.get("authorization", "")
            if not authorization.startswith("Bearer "):
                raise EvolutionError("authentication_failed")
            identity = service.identity.authenticate(authorization[7:])
            actor = principal(identity)
            if "runtime" not in actor["roles"] or not actor["tenant_id"]:
                raise EvolutionError("forbidden")
            body, idempotency = None, None
            if request.method == "POST":
                if "idempotency-key" not in request.headers:
                    raise EvolutionError("precondition_required")
                idempotency = str(UUID(request.headers["idempotency-key"]))
                if (
                    request.headers.get("content-type", "").split(";")[0]
                    != "application/json"
                ):
                    raise EvolutionError("invalid_json")
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise EvolutionError("payload_too_large")
                try:
                    body = strict_json_loads(bytes(raw))
                except ValueError:
                    raise EvolutionError("invalid_json") from None
            with service.repository.transaction(actor["tenant_id"]) as tx:
                # Revoked installs and suspended tenants cannot replay old success.
                distributor.runtime(tx, identity)
                if request.method == "GET":
                    snapshot = distributor.issue(tx, identity)
                    headers["ETag"] = '"' + snapshot["digest"] + '"'
                    if request.headers.get("if-none-match") == headers["ETag"]:
                        return Response(status_code=304, headers=headers)
                    return JSONResponse(snapshot, headers=headers)
                fingerprint = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
                key = f"{actor['principal_id']}:POST:/v2/runtime/receipts:{idempotency}"
                tx.lock("idempotency:" + key)
                previous = tx.get("sync_idempotency", key)
                if (
                    previous
                    and parse_time(previous["expires_at"]) > distributor.clock()
                ):
                    if previous["fingerprint"] != fingerprint:
                        raise EvolutionError("idempotency_conflict")
                    return JSONResponse(previous["response"], headers=headers)
                receipt = distributor.accept_receipt(tx, identity, body)
                value = {
                    "fingerprint": fingerprint,
                    "response": receipt,
                    "expires_at": stamp(distributor.clock() + timedelta(hours=24)),
                }
                if previous:
                    tx.put(
                        "sync_idempotency",
                        key,
                        value,
                        expected_revision=previous["revision"],
                    )
                else:
                    tx.insert("sync_idempotency", key, value)
                return JSONResponse(receipt, headers=headers)
        except EvolutionError as exc:
            return JSONResponse(
                exc.envelope(correlation), status_code=exc.status, headers=headers
            )
        except ValidationError, ValueError, TypeError, KeyError:
            error = EvolutionError("validation_failed")
            return JSONResponse(
                error.envelope(correlation), status_code=422, headers=headers
            )

    app.add_api_route(
        "/v2/runtime/snapshot", handle, methods=["GET"], name="runtime_snapshot"
    )
    app.add_api_route(
        "/v2/runtime/receipts", handle, methods=["POST"], name="runtime_receipt"
    )
    return app
