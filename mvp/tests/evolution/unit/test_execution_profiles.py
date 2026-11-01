import pytest
from guardrails.evolution.config import ServiceConfig
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.execution_profiles import (
    CLASSIFIER,
    TEXT,
    ExecutionProfile,
    evaluation_deadlines,
    policy_profile,
)
from test_compiler import policy, rule, uid


def config(**changes):
    return ServiceConfig(mode="remote", database_url_file="/private/db",
                         tls_cert_file="/private/cert", tls_key_file="/private/key",
                         tls_ca_file="/private/ca", runtime_id=uid(900), **changes)


def injection(**changes):
    return rule("injection", kind="injection", config={}, evaluator_revision="deberta-v2", **changes)


@pytest.mark.parametrize("state", ["observe", "warn", "redact", "block"])
def test_trusted_stage_rules_choose_classifier_without_extending_other_stage(state):
    candidate = policy(rules=[rule(), injection(stages=["input"])])
    assert policy_profile(candidate, "input", state, config()) == CLASSIFIER
    assert policy_profile(candidate, "output", state, config()) == TEXT
    assert policy_profile(candidate, "input", "disabled", config()) == TEXT


def test_config_cannot_enlarge_documented_profiles_and_bedrock_has_no_larger_grant():
    settings = config(text_budget_ms=9000, classifier_budget_ms=9000)
    assert policy_profile(policy(), "input", "block", settings) == TEXT
    assert policy_profile(policy(rules=[injection()]), "input", "block", settings) == CLASSIFIER
    managed = rule("managed", kind="bedrock", config={"provider_id":uid(910), "guardrail_id":"fixture", "guardrail_version":"1"}, evaluator_revision="bedrock-v1")
    assert policy_profile(policy(rules=[managed]), "output", "observe", settings) == TEXT
    shorter = policy_profile(policy(rules=[injection()]), "input", "block", config(classifier_budget_ms=900))
    assert shorter == ExecutionProfile("classifier", 900, 4000)


@pytest.mark.parametrize("profile", [TEXT, CLASSIFIER])
def test_caller_can_only_shorten_and_resolution_time_is_already_spent(profile):
    full, detector = evaluation_deadlines(profile, started=10., now=10.3,
        request_ms=180000, outer_deadline=100., audit_reserve_ms=100)
    assert full == 10 + profile.server_ms / 1000
    assert detector == full - .1
    short, detector = evaluation_deadlines(profile, started=10., now=10.1,
        request_ms=400, outer_deadline=100., audit_reserve_ms=100)
    assert short == 10.4 and detector == 10.3
    outer, detector = evaluation_deadlines(profile, started=10., now=10.1,
        request_ms=180000, outer_deadline=10.35, audit_reserve_ms=100)
    assert outer == 10.35 and detector == 10.25


@pytest.mark.parametrize("profile,now,request_ms", [(TEXT,10.4,180000),(CLASSIFIER,12.9,180000),(CLASSIFIER,10.31,400)])
def test_no_detector_starts_after_resolution_consumes_reserved_audit(profile,now,request_ms):
    with pytest.raises(EvolutionError) as caught:
        evaluation_deadlines(profile,started=10.,now=now,request_ms=request_ms,outer_deadline=100.,audit_reserve_ms=100)
    assert caught.value.code == "evaluation_timeout"


@pytest.mark.parametrize("changes", [
    {"profile":ExecutionProfile("text",3000,1500)},
    {"profile":ExecutionProfile("classifier",3001,4000)},
    {"profile":ExecutionProfile("classifier",3000,4001)},
    {"profile":ExecutionProfile("text",500,1500,251)},
    {"request_ms":True}, {"request_ms":0}, {"request_ms":180001},
    {"audit_reserve_ms":500}, {"audit_reserve_ms":0},
    {"now":float("nan")}, {"outer_deadline":float("inf")},
    {"started":True}, {"now":9.9},
])
def test_invalid_or_forged_profile_and_time_values_refuse(changes):
    values={"profile":TEXT,"started":10.,"now":10.1,"request_ms":500,"outer_deadline":11.,"audit_reserve_ms":100}
    with pytest.raises(EvolutionError) as caught:
        evaluation_deadlines(**(values | changes))
    assert caught.value.code == "validation_failed"


@pytest.mark.parametrize("stage,state", [("tool_result","block"),("input","bypass")])
def test_unknown_surface_or_state_cannot_choose_budget(stage,state):
    with pytest.raises(EvolutionError):
        policy_profile(policy(),stage,state,config())


def test_image_rule_does_not_silently_use_classifier_profile():
    image = rule("image",kind="image_text",config={"languages":["eng"]},evaluator_revision="ocr-v1")
    with pytest.raises(EvolutionError) as caught:
        policy_profile(policy(rules=[image]),"input","block",config())
    assert caught.value.code == "capability_missing"
