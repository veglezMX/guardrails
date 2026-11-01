"""Unregistered until authentic final qualification and provenance integration."""
import pytest

from mvp.tests.evolution.e2e.classifier_budget_acceptance_support import run_case


@pytest.mark.parametrize("tenant", ("team-a", "team-b"))
@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("scenario", ("benign", "malicious", "slow_body"))
def test_original_governed_classifier_budget(tenant, stage, scenario, classifier_provenance_context):
    run_case(tenant, stage, scenario, verify_provenance=classifier_provenance_context.verify, cluster_factory=classifier_provenance_context.cluster_factory)
