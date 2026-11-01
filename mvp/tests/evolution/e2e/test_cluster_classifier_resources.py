"""Four real resource samples; intentionally unregistered pending final proof schema.

Integration must supply the required ``classifier_provenance_context`` fixture:
a reviewed callable verifying the actual original invocation, final selected
runtime qualification, prepared/cold/current credential proof chain. No default
fixture is provided and initial historical qualification is insufficient.
"""
import pytest
from mvp.tests.evolution.e2e.classifier_resource_sampling_support import run_case


@pytest.mark.parametrize("tenant", ("team-a", "team-b"))
@pytest.mark.parametrize("stage", ("input", "output"))
def test_classifier_admitted_request_resources(tenant, stage, classifier_provenance_context):
    run_case(tenant, stage, verify_provenance=classifier_provenance_context.verify, cluster_factory=classifier_provenance_context.cluster_factory)
