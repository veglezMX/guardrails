"""One original CNI group under the parent-held deployment lock.

Requires an already bound readiness observer and reviewed native image preflight.
This group does not prepare/reconcile the deployment or register acceptance. The
parent must retain ``value`` even after failure, bind the authentic invocation,
qualified artifacts/current credentials/cold proof, and integrate its collector.
"""
from __future__ import annotations

import hashlib
import time

from evolution import classifier_cni_evidence as evidence
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.e2e.classifier_cni_bindings import CniBindingReader
from mvp.tests.evolution.e2e.classifier_cni_controls import CniControls, HostSink
from mvp.tests.evolution.e2e.classifier_cni_support import CniSocketReader
from mvp.tests.evolution.e2e.classifier_network_support import require


class CniObservation:
    def __init__(self, observer, image_binding):
        require(observer.bound is not None)
        self.observer, self.image_binding = observer, image_binding
        self._ran = False
        self.value = {"schema_version": "evolution-classifier-cni-group-v1", "status": "failed", "stage": "setup", "elapsed_ms": 0,
            "binding": None, "closure": None, "readiness_binding": observer.bound, "image_binding": image_binding,
            "host_descriptor": None, "rows": [], "controls": {}, "proofs": None, "cleanup": "pending"}

    def run_locked(self):
        require(not self._ran and self.value["stage"] == "setup" and not self.value["rows"])
        self._ran = True
        started = time.monotonic()
        deadline = started + 120
        host, primary = HostSink(), None
        controls = self.value["controls"]
        try:
            host.open_locked(deadline=deadline)
            self.value["host_descriptor"] = host.descriptor
            self.value["stage"] = "binding"
            capture = CniBindingReader(self.observer, self.image_binding, host)
            retained = capture.bind_locked(deadline=deadline)
            self.value.update(retained)
            bound = retained["binding"]
            probe = CniControls(capture)
            self.value["stage"] = "ready_before"
            controls["ready_before"] = {}
            for runtime in ("runtime-1", "runtime-2"):
                controls["ready_before"][runtime] = probe.ready(runtime, deadline=deadline)
            self.value["stage"] = "dns_before"
            controls["dns"] = []
            for transport in ("udp", "tcp"):
                controls["dns"].append(probe.dns(transport, "before", deadline=deadline))
            self.value["stage"] = "positive_before"
            controls["sink_before"] = probe.sink(positive=False, deadline=deadline)
            controls["host_before"] = host.count()
            controls["sink_positive"] = probe.sink(positive=True, deadline=deadline)
            controls["host_positive"] = host.positive()
            require(controls["sink_positive"]["counts"] == {**controls["sink_before"]["counts"],
                "8443": controls["sink_before"]["counts"]["8443"] + 1}
                and controls["host_positive"] == controls["host_before"] + 1)
            self.value["stage"] = "denials"
            for job in evidence.jobs(bound):
                def current(*, deadline, origin=job["origin"]):
                    return capture.recheck_locked(deadline=deadline, origins=(origin,))
                row = CniSocketReader(self.observer, current).probe_locked(bound, job["job_id"], deadline=deadline)
                self.value["rows"].append(row)
            self.value["stage"] = "positive_after"
            controls["sink_after"] = probe.sink(positive=False, deadline=deadline)
            controls["host_after"] = host.count()
            require(controls["sink_after"]["counts"] == controls["sink_positive"]["counts"] and controls["host_after"] == controls["host_positive"])
            controls["sink_final"] = probe.sink(positive=True, deadline=deadline)
            controls["host_final"] = host.positive()
            require(controls["sink_final"]["counts"] == {**controls["sink_after"]["counts"], "8443": controls["sink_after"]["counts"]["8443"] + 1}
                    and controls["host_final"] == controls["host_after"] + 1)
            self.value["stage"] = "dns_after"
            for transport in ("udp", "tcp"):
                controls["dns"].append(probe.dns(transport, "after", deadline=deadline))
            self.value["stage"] = "ready_after"
            controls["ready_after"] = {}
            for runtime in ("runtime-1", "runtime-2"):
                controls["ready_after"][runtime] = probe.ready(runtime, deadline=deadline)
            self.value["stage"] = "closure"
            require(capture(deadline=deadline) == bound)
            sink_identity = hashlib.sha256(canonical(bound["peers"]["sink"])).hexdigest()
            owned_sink = {"identity_sha256": sink_identity, **{target: controls[source]["counts"]["8443"] for target, source in (
                ("before_positive", "sink_before"), ("after_positive", "sink_positive"), ("after_denials", "sink_after"), ("after_final_positive", "sink_final"))}}
            host_sink = {"identity_sha256": bound["host_sink"]["identity_sha256"], **{target: controls[source] for target, source in (
                ("before_positive", "host_before"), ("after_positive", "host_positive"), ("after_denials", "host_after"), ("after_final_positive", "host_final"))}}
            self.value["proofs"] = {runtime: evidence.validate_group(bound, self.value["rows"],
                ready_before=controls["ready_before"][runtime], ready_after=controls["ready_after"][runtime],
                owned_sink=owned_sink, host_sink=host_sink, dns_controls=controls["dns"]) for runtime in ("runtime-1", "runtime-2")}
            self.value["status"], self.value["stage"] = "observed", "complete"
        except BaseException as error:
            primary = error
            raise
        finally:
            failure = None
            try:
                host.close()
                self.value["cleanup"] = "closed"
            except BaseException as error:  # noqa: BLE001 - original failure remains primary; cleanup is retained
                failure = error
                self.value["cleanup"] = "failed"
            self.value["elapsed_ms"] = round((time.monotonic() - started) * 1000, 6)
            if self.value["elapsed_ms"] > 120000:
                failure = failure or RuntimeError("classifier_cni_group_deadline")
            if primary is not None or failure is not None:
                self.value["status"] = "failed"
            if failure is not None:
                if primary is None:
                    self.value["stage"] = "cleanup"
                    raise failure
                primary.add_note("CNI cleanup also failed: " + type(failure).__name__)
        return self.value
