"""Actual classifier TLS/auth and independently observed forbidden dispatch.

Parent must prepare a qualified, cold-proved classifier deployment before these
cases are registered and run. They never prepare, reconcile, retry or mutate it.
"""
import pytest
from cluster_support import ROOT
from evaluator_matrix_support import EvaluatorCluster

from mvp.tests.evolution.e2e.classifier_operational_support import run_tls_case


@pytest.mark.parametrize("runtime", ("runtime-1", "runtime-2"))
def test_classifier_deployed_tls_auth_has_zero_unauthorized_dispatch(runtime):
    run_tls_case(EvaluatorCluster, root=ROOT, runtime=runtime)
