"""Explicit factory refuses before source reads or deployment construction."""
import pytest

from mvp.tests.evolution.e2e import classifier_budget_acceptance_support as budget
from mvp.tests.evolution.e2e import classifier_cni_acceptance_support as cni
from mvp.tests.evolution.e2e import classifier_resource_sampling_support as resource
from mvp.tests.evolution.e2e import classifier_tls_acceptance_support as tls


@pytest.mark.parametrize("family", [tls, resource, cni, budget])
@pytest.mark.parametrize("factory", [None, False, "module:function"])
def test_missing_or_noncallable_constructor_refuses_before_source_access(monkeypatch, family, factory):
    monkeypatch.setattr(family.evidence, "gate_sources", lambda *args: pytest.fail("source read before factory validation"))
    def verifier(**kwargs):
        pytest.fail("proof before valid factory")
    with pytest.raises(ValueError):
        if family is tls:
            family.run_tls_case(factory, root=None, runtime="runtime-1", verify_provenance=verifier)
        elif family is cni:
            family.run_case(verify_provenance=verifier, cluster_factory=factory)
        elif family is resource:
            family.run_case("team-a", "input", verify_provenance=verifier, cluster_factory=factory)
        else:
            family.run_case("team-a", "input", "benign", verify_provenance=verifier, cluster_factory=factory)
