"""Closed, versioned acceptance inventory. An unavailable phase can never pass."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


class EvidenceError(ValueError):
    """A static reason code safe to include in metadata-only evidence."""


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def read_json(path: Path):
    try:
        return json.loads(
            path.read_text(),
            object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                EvidenceError("invalid_json_number")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid_json_document") from exc


@dataclass(frozen=True)
class Case:
    case_id: str
    requirement_id: str
    profile: str
    mode: str
    family: str
    nodeid: str
    assertions: tuple[str, ...]
    timeout_s: int


def load_registry(path: Path, phase: int, profile: str = "local") -> tuple[Case, ...]:
    if type(phase) is not int or phase not in range(1, 7):
        raise EvidenceError("unknown_phase")
    if profile != "local":
        raise EvidenceError("optional_profile_cannot_satisfy_local_gate")
    document = read_json(path)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "phases"}
        or document["schema_version"] != "guardrails-evolution-cases-v1"
    ):
        raise EvidenceError("invalid_registry_schema")
    if not isinstance(document["phases"], dict) or set(document["phases"]) != {
        str(value) for value in range(1, 7)
    }:
        raise EvidenceError("phase_inventory_mismatch")
    seen_ids, seen_nodes = set(), set()
    chosen = ()
    for number, entry in document["phases"].items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"status", "required_families", "cases"}
            or not isinstance(entry["status"], str)
            or entry["status"] not in {"ready", "unavailable"}
            or not isinstance(entry["cases"], list)
        ):
            raise EvidenceError("invalid_phase_registry")
        families = entry["required_families"]
        if (
            not isinstance(families, list)
            or not families
            or any(not isinstance(value, str) or not value for value in families)
            or len(families) != len(set(families))
        ):
            raise EvidenceError("invalid_required_families")
        cases = []
        for value in entry["cases"]:
            if not isinstance(value, dict) or set(value) != set(
                Case.__dataclass_fields__
            ):
                raise EvidenceError("invalid_case_schema")
            if (
                not isinstance(value["case_id"], str)
                or len(value["case_id"]) > 128
                or not re.fullmatch(
                    r"(?:GOV|TEN|NET|INT|DET|UI|OPS|CMR|GW|BND|SEC)-[A-Z0-9-]+",
                    value["case_id"],
                )
            ):
                raise EvidenceError("invalid_case_id")
            if not isinstance(value["nodeid"], str):
                raise EvidenceError("invalid_test_nodeid")
            if value["case_id"] in seen_ids or value["nodeid"] in seen_nodes:
                raise EvidenceError("duplicate_case")
            seen_ids.add(value["case_id"])
            seen_nodes.add(value["nodeid"])
            file, separator, test = value["nodeid"].partition("::")
            if (
                not separator
                or not test
                or Path(file).is_absolute()
                or ".." in Path(file).parts
                or not file.startswith(
                    (
                        "mvp/tests/evolution/",
                        "tests/evolution-browser/",
                        "services/classifier/tests/",
                    )
                )
                or not file.endswith(".py")
            ):
                raise EvidenceError("invalid_test_nodeid")
            if (
                value["profile"] != "local"
                or not isinstance(value["mode"], str)
                or value["mode"]
                not in {
                    "remote",
                    "gateway",
                    "customer",
                }
            ):
                raise EvidenceError("invalid_case_profile")
            if (
                not isinstance(value["requirement_id"], str)
                or not value["requirement_id"]
                or len(value["requirement_id"]) > 128
                or not isinstance(value["family"], str)
                or value["family"] not in families
            ):
                raise EvidenceError("invalid_case_requirement")
            if (
                type(value["timeout_s"]) is not int
                or not 1 <= value["timeout_s"] <= 1200
            ):
                raise EvidenceError("invalid_case_timeout")
            assertions = value["assertions"]
            if (
                not isinstance(assertions, list)
                or not assertions
                or any(not isinstance(a, str) or not a.strip() for a in assertions)
            ):
                raise EvidenceError("missing_expected_assertions")
            cases.append(Case(**(value | {"assertions": tuple(assertions)})))
        if entry["status"] == "ready" and (
            not cases or {case.family for case in cases} != set(families)
        ):
            raise EvidenceError("required_family_missing")
        if int(number) == phase:
            if entry["status"] != "ready":
                raise EvidenceError("phase_not_implemented")
            chosen = tuple(cases)
    return chosen
