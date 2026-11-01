"""Closed deployed classifier attachments with explicit parent verification.

The parent supplies each verifier from its original launch/completion context.
This module never discovers a latest receipt or derives a trusted pin from an
attachment. Component observations alone cannot complete an operational case.
"""
from __future__ import annotations

import hashlib
import os
import stat

from guardrails.evolution.classifier_contract import canonical

from . import (
    classifier_budget_measurements as budget,
)
from . import (
    classifier_cni_measurements as cni,
)
from . import (
    classifier_resource_measurements as resource,
)
from . import (
    classifier_tls_measurements as tls,
)
from .classifier_source_projection import check_deadline, project_sources
from .registry import EvidenceError

FAMILIES = {
    **dict.fromkeys(budget.CASES, budget),
    cni.CASE: cni,
    **dict.fromkeys(resource.CASES, resource),
    **dict.fromkeys(tls.CASES, tls),
}
FILES = {case: family.FILENAME for case, family in FAMILIES.items()}


def require(value):
    if not value:
        raise EvidenceError("measurement_evidence_invalid")


def names_for(family):
    return ({family.FILENAME} if family is resource else
            {family.FILENAME, family.INTENT, family.AUTHORIZATION})


def attachment_names(directory, family):
    allowed = names_for(family)
    names = set()
    with os.scandir(directory) as entries:
        for ordinal, entry in enumerate(entries):
            require(ordinal < len(allowed) and entry.name in allowed and entry.name not in names)
            info = entry.stat(follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and 0 < info.st_size <= family.MAX_BYTES)
            names.add(entry.name)
    return names


def auxiliaries(family, value, case_id, artifacts, parsed, *, root, sources):
    """Reconstruct the exact exclusive producer intent and authorization bytes."""
    if family is tls:
        family.validate_auxiliaries(value, case_id, artifacts, root=root, sources=sources)
        return
    require(artifacts[family.FILENAME] == canonical(value) + b"\n")
    if family is resource:
        require(set(artifacts) == {family.FILENAME})
        return
    required = {family.INTENT, family.FILENAME}
    if value["authorization_sha256"] is not None:
        required.add(family.AUTHORIZATION)
    require(set(artifacts) == required)
    intended = (family.intent(value["operation"], sources) if family is cni else
                family.intent(value["invocation"], case_id, sources))
    require(artifacts[family.INTENT] == canonical(intended) + b"\n")
    require(hashlib.sha256(artifacts[family.INTENT]).hexdigest() == value["intent_sha256"])
    if family.AUTHORIZATION not in artifacts:
        return
    if family is cni:
        auth = parsed[family.AUTHORIZATION]
        require(type(auth) is dict and set(auth) == {
            "schema_version", "case_id", "operation", "intent_sha256",
            "provenance", "binding", "image_binding",
        })
        expected = family.authorization(value["operation"], value["intent_sha256"],
            value["provenance"], auth["binding"], auth["image_binding"])
        if value["group"] is not None and value["group"]["readiness_binding"] is not None:
            require(auth["binding"] == value["group"]["readiness_binding"]
                and auth["image_binding"] == value["group"]["image_binding"])
    else:
        expected = family.authorization(value["invocation"], value["operation"], case_id,
            value["intent_sha256"], value["provenance"], value["binding"])
    require(artifacts[family.AUTHORIZATION] == canonical(expected) + b"\n"
        and hashlib.sha256(artifacts[family.AUTHORIZATION]).hexdigest() == value["authorization_sha256"])


def _collect(directory, *, case_id, root, sources, complete, provenance, read_value, deadline):
    """Read and recheck every closed attachment around the mandatory verifier."""
    require(case_id in FAMILIES and type(complete) is bool)
    before = directory.lstat()
    require(directory.is_absolute() and directory.resolve() == directory
        and stat.S_ISDIR(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o700
        and before.st_uid == os.getuid())
    family = FAMILIES[case_id]
    names = attachment_names(directory, family)
    if not names and not complete:
        return {}
    require(family.FILENAME in names)
    original_sources = dict(sources)
    sources = project_sources(root, original_sources, deadline=deadline)
    family.validate_sources(root=root, sources=sources)
    values = {name: read_value(directory / name, family.MAX_BYTES) for name in sorted(names)}
    artifacts = {name: pair[0] for name, pair in values.items()}
    parsed = {name: pair[1] for name, pair in values.items()}
    value = parsed[family.FILENAME]
    callback = None
    if provenance is not None:
        require(type(provenance) is dict)
        callback = provenance.get(case_id)
    if complete:
        require(callable(callback))
    family.validate(value, case_id, root=root, sources=sources, complete=False)
    auxiliaries(family, value, case_id, artifacts, parsed, root=root, sources=sources)
    if complete:
        family.validate(value, case_id, root=root, sources=sources,
                        complete=True, verify_provenance=callback)
    require(project_sources(root, original_sources, deadline=deadline) == sources)
    family.validate_sources(root=root, sources=sources)
    require(attachment_names(directory, family) == names)
    for name, original in artifacts.items():
        require(read_value(directory / name, family.MAX_BYTES)[0] == original)
    after = directory.lstat()
    require(directory.resolve() == directory
        and (before.st_dev, before.st_ino, before.st_mode, before.st_uid)
        == (after.st_dev, after.st_ino, after.st_mode, after.st_uid))
    check_deadline(deadline)
    return {name: {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                   "source_files": dict(sources)}
            for name, raw in artifacts.items()}


def collect(directory, *, case_id, root, sources, complete, provenance, read_value, deadline):
    try:
        return _collect(directory, case_id=case_id, root=root, sources=sources,
                        complete=complete, provenance=provenance, read_value=read_value, deadline=deadline)
    except EvidenceError:
        raise
    except Exception:  # noqa: BLE001 - malformed or unverifiable evidence must fail the owning case
        raise EvidenceError("measurement_evidence_invalid") from None
