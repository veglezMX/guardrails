"""Policy bindings and workload identities — the control plane's output.

A binding associates one workload with one immutable policy version and records
where that workload sits on the enforcement ladder. Everything here is authored
by the control plane (CP-1, CP-3) and reaches the runtime by asynchronous
distribution; the runtime only ever reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ENFORCEMENT_STATES
from .errors import PolicyValidationError


@dataclass(frozen=True)
class Binding:
    tenant_id: str
    workload_id: str
    policy_name: str
    policy_version: int
    enforcement_state: str
    rollback_version: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.tenant_id, self.workload_id)


@dataclass(frozen=True)
class WorkloadIdentity:
    """Per-tenant workload identity (CON-011).

    MVP stand-in for SigV4/IAM: a bearer credential issued by CP-1 and bound to
    exactly one (tenant, workload). The runtime authenticates against this
    rather than trusting the identifiers in the request body (CON-012).
    """

    credential: str
    tenant_id: str
    workload_id: str


def parse_binding(doc: dict[str, Any]) -> Binding:
    for key in ("tenant_id", "workload_id", "policy_name", "policy_version",
                "enforcement_state"):
        if key not in doc:
            raise PolicyValidationError(f"binding: missing required key '{key}'")
    state = doc["enforcement_state"]
    if state not in ENFORCEMENT_STATES:
        raise PolicyValidationError(
            f"binding: enforcement_state '{state}' is outside the ladder "
            f"{list(ENFORCEMENT_STATES)}"
        )
    version = doc["policy_version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise PolicyValidationError("binding: 'policy_version' must be an integer")
    return Binding(
        tenant_id=doc["tenant_id"],
        workload_id=doc["workload_id"],
        policy_name=doc["policy_name"],
        policy_version=version,
        enforcement_state=state,
        rollback_version=doc.get("rollback_version"),
    )


def load_binding_file(path: Path) -> Binding:
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PolicyValidationError(f"{path}: not valid JSON — {exc}") from exc
    return parse_binding(doc)


def load_identities_file(path: Path) -> dict[str, WorkloadIdentity]:
    doc = json.loads(path.read_text())
    identities: dict[str, WorkloadIdentity] = {}
    for credential, entry in doc.items():
        identities[credential] = WorkloadIdentity(
            credential=credential,
            tenant_id=entry["tenant_id"],
            workload_id=entry["workload_id"],
        )
    return identities
