"""One original 20-denial group; unregistered until final proof integration.

The required fixture must verify the authentic invocation, final selected-model
qualification, prepared/cold/current credentials and current owned-node offline
boundary. No fixture is supplied here. Host/external/metadata results are
composed isolation evidence, not attribution exclusively to CNI policy.
"""
from mvp.tests.evolution.e2e.classifier_cni_acceptance_support import run_case


def test_classifier_original_cni_group(classifier_cni_provenance):
    run_case(verify_provenance=classifier_cni_provenance)
