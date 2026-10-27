"""Closed classifier CNI matrix; pure validation, never a transport observation.

The caller must obtain the bindings and all controls from original owned
processes. This component cannot replace the parent invocation/provenance
closure, actual positive-control implementation or complete evidence family.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re

from guardrails.evolution.classifier_contract import canonical

from evolution.classifier_dispatch import delta

ORIGINS = {
    "classifier": ("gr-platform", "classifier", "classifier"),
    "runtime-1": ("gr-platform", "runtime-1", "runtime"),
    "runtime-2": ("gr-platform", "runtime-2", "runtime"),
    "allowed-team-a": ("gr-team-a", "allowed-probe", "probe"),
    "allowed-team-b": ("gr-team-b", "allowed-probe", "probe"),
    "sink": ("gr-platform", "tls-probe", "probe"),
}
ADDRESS_NAMES = {"classifier_service", "sink_service", "mock_service", "postgres_service", "api_service", "dns_service"}


class CniObservationError(ValueError):
    """Finite diagnostic only."""


def require(condition):
    if not condition:
        raise CniObservationError("classifier_cni_observation_invalid")


def digest(value, length=64):
    require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None)
    return value


def integer(value, minimum=0, maximum=2**63 - 1):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def private_ip(value):
    require(type(value) is str)
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        raise CniObservationError("classifier_cni_observation_invalid") from None
    require(str(address) == value and address.is_private and not
            (address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local))
    return value


def bindings(value):
    """Validate a prebound current IPv4 deployment; never discover new routes."""
    try:
        require(type(value) is dict and set(value) == {"owner_id", "node_id", "readiness_binding_sha256", "peers", "addresses", "host_sink"})
        digest(value["owner_id"], 32)
        digest(value["node_id"])
        digest(value["readiness_binding_sha256"])
        peers = value["peers"]
        require(type(peers) is dict and set(peers) == set(ORIGINS))
        uids, containers, ips = set(), set(), set()
        for name, (namespace, deployment, container) in ORIGINS.items():
            row = peers[name]
            require(type(row) is dict and set(row) == {"namespace", "pod_name", "pod_uid", "container", "container_id", "pod_ip", "process_start_ticks", "netns_inode"}
                    and row["namespace"] == namespace and row["container"] == container
                    and type(row["pod_name"]) is str and len(row["pod_name"]) <= 253
                    and re.fullmatch(re.escape(deployment) + "-[a-z0-9]+-[a-z0-9]+", row["pod_name"]) is not None
                    and type(row["pod_uid"]) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", row["pod_uid"]) is not None)
            digest(row["container_id"])
            private_ip(row["pod_ip"])
            integer(row["process_start_ticks"], 1)
            integer(row["netns_inode"], 1)
            require(row["pod_uid"] not in uids and row["container_id"] not in containers and row["pod_ip"] not in ips)
            uids.add(row["pod_uid"])
            containers.add(row["container_id"])
            ips.add(row["pod_ip"])
        addresses = value["addresses"]
        require(type(addresses) is dict and set(addresses) == ADDRESS_NAMES)
        require(len({private_ip(address) for address in addresses.values()}) == len(ADDRESS_NAMES))
        require(not set(addresses.values()) & ips)
        host = value["host_sink"]
        require(type(host) is dict and set(host) == {"address", "port", "identity_sha256"} and host["address"] == "192.168.51.1")
        integer(host["port"], 1024, 65535)
        digest(host["identity_sha256"])
        return value
    except (AttributeError, KeyError, TypeError, ValueError):
        raise CniObservationError("classifier_cni_observation_invalid") from None


def jobs(bound):
    bound = bindings(bound)
    peers, addresses = bound["peers"], bound["addresses"]
    result = []
    def add(name, origin, destination, port, transport="tcp"):
        result.append({"job_id": name, "origin": origin, "settings": {"source_ip": peers[origin]["pod_ip"],
            "destination_ip": destination, "port": port, "transport": transport}})
    for name, address, port in (
        ("sink_service", addresses["sink_service"], 8443), ("sink_pod", peers["sink"]["pod_ip"], 8443),
        ("mock", addresses["mock_service"], 8443), ("postgres", addresses["postgres_service"], 5432),
        ("api", addresses["api_service"], 443), ("host", bound["host_sink"]["address"], bound["host_sink"]["port"]),
        ("external", "1.1.1.1", 443), ("metadata", "169.254.169.254", 80)):
        add("egress_" + name, "classifier", address, port)
    for transport in ("tcp", "udp"):
        add("dns_" + transport, "classifier", addresses["dns_service"], 53, transport)
    for origin in ("allowed-team-a", "allowed-team-b"):
        for kind, address in (("service", addresses["classifier_service"]), ("pod", peers["classifier"]["pod_ip"])):
            for port in (8443, 9443):
                add("ingress_" + origin + "_" + kind + "_" + str(port), origin, address, port)
    for origin in ("runtime-1", "runtime-2"):
        add("ingress_" + origin + "_alternate", origin, peers["classifier"]["pod_ip"], 9443)
    require(len(result) == 20)
    return result


def result(value, job):
    require(type(value) is dict and set(value) == {"outcome", "source_ip", "destination_ip", "port", "transport", "elapsed_ms"}
            and value["outcome"] == "timeout" and all(value[key] == expected for key, expected in job["settings"].items())
            and type(value["elapsed_ms"]) in (int, float) and math.isfinite(value["elapsed_ms"])
            and 1800 <= value["elapsed_ms"] <= 3500)
    return value


def zero_sink_delivery(value):
    """Controls surround denials: each positive must advance the exact sink once."""
    require(type(value) is dict and set(value) == {"identity_sha256", "before_positive", "after_positive", "after_denials", "after_final_positive"})
    digest(value["identity_sha256"])
    for name in ("before_positive", "after_positive", "after_denials", "after_final_positive"):
        integer(value[name])
    require(value["after_positive"] == value["before_positive"] + 1
            and value["after_denials"] == value["after_positive"]
            and value["after_final_positive"] == value["after_denials"] + 1)


def validate_group(bound, rows, *, ready_before, ready_after, owned_sink, host_sink, dns_controls):
    """A completed matrix still requires the parent's original launch closure.

    Only authenticated actual readiness headers may supply ready counters. Sink
    counts are local to each positively controlled sink, never inference counts.
    DNS controls must be actual successful original UDP/TCP queries. No callback
    or result supplied by a fake is operational proof.
    """
    expected = jobs(bound)
    require(type(rows) is list and len(rows) == len(expected))
    for row, job in zip(rows, expected, strict=True):
        require(type(row) is dict and set(row) == {"job_id", "result"} and row["job_id"] == job["job_id"])
        result(row["result"], job)
    for ready in (ready_before, ready_after):
        require(type(ready) is dict and set(ready) == {"binding_sha256", "counter", "tls", "ready_elapsed_ms"}
                and ready["binding_sha256"] == bound["readiness_binding_sha256"] and ready["tls"] in {"TLSv1.2", "TLSv1.3"}
                and type(ready["ready_elapsed_ms"]) in (float, int) and math.isfinite(ready["ready_elapsed_ms"])
                and 0 <= ready["ready_elapsed_ms"] < 8000)
    require(delta(ready_before["counter"], ready_after["counter"]) == 0)
    zero_sink_delivery(owned_sink)
    zero_sink_delivery(host_sink)
    require(owned_sink["identity_sha256"] == hashlib.sha256(canonical(bound["peers"]["sink"])).hexdigest()
            and host_sink["identity_sha256"] == bound["host_sink"]["identity_sha256"])
    require(type(dns_controls) is list and len(dns_controls) == 4)
    for row, (position, transport) in zip(dns_controls, (("before", "udp"), ("before", "tcp"), ("after", "udp"), ("after", "tcp")), strict=True):
        require(type(row) is dict and set(row) == {"position", "transport", "source_ip", "server_ip", "resolved_ip"}
                and row == {"position": position, "transport": transport, "source_ip": bound["peers"]["allowed-team-a"]["pod_ip"],
                    "server_ip": bound["addresses"]["dns_service"], "resolved_ip": bound["addresses"]["api_service"]})
    return {"binding_sha256": hashlib.sha256(canonical(bound)).hexdigest(), "denial_count": 20,
        "classifier_dispatch_delta": 0, "owned_sink_delivery_delta": 0, "host_sink_delivery_delta": 0}
