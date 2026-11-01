"""Private execution limits selected from an already eligible compiled policy.

This module does not authorize a policy. Runtime admission must revalidate the
exact selection before charging quota or creating a context. Request deadlines
can only shorten the selected profile, including time spent resolving it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .config import ServiceConfig
from .errors import EvolutionError
from .models import PolicyVersion


@dataclass(frozen=True)
class ExecutionProfile:
    name: Literal["text", "classifier"]
    server_ms: int
    client_ms: int
    connect_ms: int = 250


TEXT = ExecutionProfile("text", 500, 1500)
CLASSIFIER = ExecutionProfile("classifier", 3000, 4000)
PROFILES = MappingProxyType({"text": TEXT, "classifier": CLASSIFIER})


def policy_profile(policy: PolicyVersion, stage: str, state: str,
                   config: ServiceConfig) -> ExecutionProfile:
    """Select a ceiling; eligibility, installed artifacts and health are separate."""
    if (not isinstance(policy, PolicyVersion) or not isinstance(config, ServiceConfig)
            or stage not in {"input", "output"}
            or state not in {"disabled", "observe", "warn", "redact", "block"}):
        raise EvolutionError("validation_failed")
    selected = TEXT
    if state != "disabled":
        for rule in policy.rules:
            if stage not in rule.stages:
                continue
            if rule.kind == "image_text":
                # Image execution belongs to its separately implemented profile.
                raise EvolutionError("capability_missing")
            if rule.kind == "injection":
                selected = CLASSIFIER
    configured = config.classifier_budget_ms if selected is CLASSIFIER else config.text_budget_ms
    limit = min(configured, selected.server_ms)
    if config.audit_reserve_ms >= limit:
        raise EvolutionError("validation_failed")
    return ExecutionProfile(selected.name, limit, selected.client_ms)


def evaluation_deadlines(profile: ExecutionProfile, *, started: float,
                         request_ms: int, outer_deadline: float,
                         audit_reserve_ms: int, now: float) -> tuple[float, float]:
    """Keep arrival-based elapsed time and reserve audit before detector work."""
    if (not isinstance(profile, ExecutionProfile) or type(profile.name) is not str
            or profile.name not in PROFILES
            or type(profile.server_ms) is not int
            or not 0 < profile.server_ms <= PROFILES[profile.name].server_ms
            or type(profile.client_ms) is not int or profile.client_ms != PROFILES[profile.name].client_ms
            or type(profile.connect_ms) is not int or profile.connect_ms != 250
            or type(request_ms) is not int or not 1 <= request_ms <= 180000
            or type(audit_reserve_ms) is not int or not 0 < audit_reserve_ms < profile.server_ms
            or any(type(value) not in {int, float} or not math.isfinite(value)
                   for value in (started, outer_deadline, now))
            or now < started):
        raise EvolutionError("validation_failed")
    deadline = min(outer_deadline, started + min(profile.server_ms, request_ms) / 1000)
    detector_deadline = deadline - audit_reserve_ms / 1000
    if detector_deadline <= now:
        raise EvolutionError("evaluation_timeout")
    return deadline, detector_deadline
