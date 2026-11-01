"""Synthetic rtnetlink/probe bytes only: no actual socket or namespace access."""

import copy
import errno
import importlib.util
import socket
import struct
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("isolation_subject", Path(__file__).parents[1] / "network_isolation.py")
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("real network access forbidden")
    monkeypatch.setattr(s.socket, "socket", refuse)
    monkeypatch.setattr(s.os, "readlink", lambda name: "net:[4026533000]")


def attr(kind, value):
    raw = struct.pack("=HH", len(value) + 4, kind) + value
    return raw + b"\0" * (-len(raw) % 4)


def integer(kind, value, size=4):
    return attr(kind, value.to_bytes(size, "little"))


def link(name="tunl0", *, flags=128, hardware=768, kind=b"ipip\0", extra=b"", tunnel=None):
    if tunnel is None:
        tunnel = {1: (0, 4), 2: (0, 4), 3: (0, 4), 4: (0, 1), 5: (0, 1), 9: (0, 1),
                  10: (0, 1), 15: (0, 2), 16: (0, 2), 17: (0, 2), 18: (0, 2), 20: (0, 4)}
    body = struct.pack("=BBHiII", 0, 0, hardware, 1 if name == "lo" else 2, flags, 0)
    body += attr(3, name.encode() + b"\0") + integer(16, 2, 1) + integer(33, 0, 1)
    if kind is not None:
        body += attr(18, attr(1, kind) + attr(2, b"".join(integer(key, *value) for key, value in tunnel.items())))
    return body + extra


def loopback():
    return link("lo", flags=8, hardware=772, kind=None)


def address(index=2):
    return struct.pack("=BBBBI", socket.AF_INET, 32, 0, 0, index) + attr(1, b"\x0a\0\0\x01")


def route(index=1, extra=b""):
    return struct.pack("=BBBBBBBBI", socket.AF_INET, 32, 0, 0, 255, 2, 254, 2, 0) + integer(4, index) + extra


def observe_fixture(monkeypatch, *, links=None, addresses=None, routes=None):
    answers = {18: [loopback(), link()] if links is None else links,
               22: [] if addresses is None else addresses,
               26: [] if routes is None else routes}
    calls = []
    def dump(request, response, body, deadline):
        calls.append((request, response))
        return answers[request]
    monkeypatch.setattr(s, "_dump", dump)
    return s.observe("net:[4026531833]"), calls


@pytest.mark.parametrize("optional", [False, True])
def test_only_actual_down_unconfigured_fallback_is_eligible(monkeypatch, optional):
    value, calls = observe_fixture(monkeypatch, links=[loopback()] + ([link()] if optional else []))
    s.validate(value)
    assert calls == [(18, 16), (22, 20), (26, 24)]
    assert value["interfaces"] == (["lo", "tunl0"] if optional else ["lo"])
    assert value["external_routes"] == 0
    assert "ipv4_connect_errno" not in value  # Metadata projection is not proof.


@pytest.mark.parametrize("flags", [129, 192, 65664, 0, 144, 384])
def test_up_running_lowerup_promiscuous_or_nonfallback_flags_refuse(monkeypatch, flags):
    value, _ = observe_fixture(monkeypatch, links=[loopback(), link(flags=flags)])
    with pytest.raises(s.IsolationError, match="network_tunnel"):
        s.validate(value)


@pytest.mark.parametrize("field,kwargs", [("name", {"name": "other"}), ("type", {"hardware": 1}),
    ("kind", {"kind": b"gre\0"}), ("underlying", {"extra": integer(5, 3)}),
    ("master", {"extra": integer(10, 3)}), ("foreign", {"extra": integer(37, 0)})])
def test_similar_names_or_foreign_device_relationships_refuse(monkeypatch, field, kwargs):
    value, _ = observe_fixture(monkeypatch, links=[loopback(), link(**kwargs)])
    with pytest.raises(s.IsolationError):
        s.validate(value)


@pytest.mark.parametrize("key", [1, 2, 3, 4, 5, 9, 10, 15, 16, 17, 18, 20])
def test_any_configured_tunnel_field_refuses(monkeypatch, key):
    data = {1: (0, 4), 2: (0, 4), 3: (0, 4), 4: (0, 1), 5: (0, 1), 9: (0, 1),
            10: (0, 1), 15: (0, 2), 16: (0, 2), 17: (0, 2), 18: (0, 2), 20: (0, 4)}
    data[key] = (1, data[key][1])
    value, _ = observe_fixture(monkeypatch, links=[loopback(), link(tunnel=data)])
    with pytest.raises(s.IsolationError, match="network_tunnel"):
        s.validate(value)


@pytest.mark.parametrize("where", ["address", "route"])
def test_inert_looking_tunnel_with_address_or_route_refuses(monkeypatch, where):
    value, _ = observe_fixture(monkeypatch, addresses=[address()] if where == "address" else [],
                               routes=[route(2)] if where == "route" else [])
    with pytest.raises(s.IsolationError, match="network_addresses|network_routes"):
        s.validate(value)


def test_loopback_routes_and_addresses_do_not_become_external(monkeypatch):
    value, _ = observe_fixture(monkeypatch, addresses=[address(1)], routes=[route()])
    s.validate(value)
    assert value["links"][0]["address_count"] == value["links"][0]["route_count"] == 1
    assert b"\x0a" not in repr(value).encode()


@pytest.mark.parametrize("extra", [integer(5, 0), integer(9, 0), integer(18, 0),
                                  integer(21, 0), integer(22, 0), integer(30, 0)])
def test_nonlocal_route_shapes_refuse_without_address_capture(monkeypatch, extra):
    with pytest.raises(s.IsolationError, match="network_routes"):
        observe_fixture(monkeypatch, routes=[route(extra=extra)])


@pytest.mark.parametrize("raw", [b"x", b"\x03\0\x01\0", attr(1, b"x") * 2,
                               b"\x09\0\x01\0xxx", attr(0, b""), attr(1, b"a") * 129])
def test_malformed_and_duplicate_attributes_refuse(raw):
    with pytest.raises(s.IsolationError, match="netlink_attribute"):
        s._attrs(raw)


def message(kind, body, *, sequence=1, pid=123, flags=2):
    raw = struct.pack("=IHHII", len(body) + 16, kind, flags, sequence, pid) + body
    return raw + b"\0" * (-len(raw) % 4)


class Netlink:
    def __init__(self, packets):
        self.packets = iter(packets)
        self.sent = []
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def bind(self, value): assert value == (0, 0)
    def getsockname(self): return (123, 0)
    def settimeout(self, value): assert 0 < value <= 1
    def sendto(self, raw, peer):
        assert peer == (0, 0)
        self.sent.append(raw)
        return len(raw)
    def recvmsg(self, size):
        assert size == 65536
        return next(self.packets)


def packet(raw, *, peer=(0, 0), flags=0):
    return (raw, [], flags, peer)


def test_actual_netlink_wire_queries_are_read_only_and_closed(monkeypatch):
    stream = Netlink([packet(message(16, loopback()) + message(16, link())),
                      packet(message(3, b"\0" * 4))])
    monkeypatch.setattr(s.socket, "socket", lambda *args: stream)
    assert s._dump(18, 16, b"\0" * 16, s.time.monotonic() + 1) == [loopback(), link()]
    assert struct.unpack("=IHHII", stream.sent[0][:16]) == (32, 18, 0x301, 1, 123)
    assert stream.closed


@pytest.mark.parametrize("bad", [packet(message(16, loopback()), peer=(22, 0)),
    packet(message(16, loopback()), flags=socket.MSG_TRUNC),
    packet(message(16, loopback(), sequence=2)), packet(message(16, loopback(), pid=0)),
    packet(message(16, loopback(), flags=18)), packet(message(2, b"\xff" * 4)),
    packet(message(3, b"\xff" * 4)), packet(b"short"),
    packet(message(3, b"") + message(16, loopback()))])
def test_untrusted_truncated_interrupted_or_error_dump_refuses(monkeypatch, bad):
    stream = Netlink([bad])
    monkeypatch.setattr(s.socket, "socket", lambda *args: stream)
    with pytest.raises(s.IsolationError):
        s._dump(18, 16, b"\0" * 16, s.time.monotonic() + 1)
    assert stream.closed


def test_finite_message_bound(monkeypatch):
    stream = Netlink([packet(message(16, loopback()) * 65)])
    monkeypatch.setattr(s.socket, "socket", lambda *args: stream)
    with pytest.raises(s.IsolationError, match="netlink_bound"):
        s._dump(18, 16, b"\0" * 16, s.time.monotonic() + 1)


@pytest.mark.parametrize("host", ["net:[4026533000]", "net:4026531833", "net:[secret]", "x" * 81])
def test_same_or_invalid_namespace_refuses_before_any_dump(host):
    with pytest.raises(s.IsolationError, match="namespace_identity"):
        s.observe(host)


class Probe:
    def __init__(self, outcome): self.outcome = outcome
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, timeout): assert 0 < timeout <= 0.25
    def connect(self, target):
        assert target in (("198.51.100.1", 443), ("2001:db8::1", 443, 0, 0))
        if self.outcome is not None:
            raise OSError(self.outcome, "untrusted socket detail")


@pytest.mark.parametrize("outcome", [errno.ENETUNREACH, errno.ECONNREFUSED, errno.EACCES, errno.ETIMEDOUT, None])
def test_both_real_probe_errno_contracts_remain_required(monkeypatch, outcome):
    value, _ = observe_fixture(monkeypatch)
    calls = []
    monkeypatch.setattr(s, "observe", lambda *args, **kwargs: copy.deepcopy(value))
    def probe(family, mode):
        calls.append(family)
        assert mode == socket.SOCK_STREAM
        return Probe(outcome)
    monkeypatch.setattr(s.socket, "socket", probe)
    if outcome == errno.ENETUNREACH:
        result = s.snapshot("net:[4026531833]")
        assert calls == [socket.AF_INET, socket.AF_INET6]
        assert result["ipv4_connect_errno"] == result["ipv6_connect_errno"] == errno.ENETUNREACH
    else:
        with pytest.raises(s.IsolationError, match="network_route"):
            s.snapshot("net:[4026531833]")


def test_postprobe_interface_change_refuses(monkeypatch):
    value, _ = observe_fixture(monkeypatch)
    changed = copy.deepcopy(value)
    changed["links"][0]["index"] += 10
    answers = iter([value, changed])
    monkeypatch.setattr(s, "observe", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(s.socket, "socket", lambda *args: Probe(errno.ENETUNREACH))
    with pytest.raises(s.IsolationError, match="network_changed"):
        s.snapshot("net:[4026531833]")


@pytest.mark.parametrize("fault", ["empty_links", "missing_link", "duplicate_names", "duplicate_index",
    "extra_field", "extra_link_field", "extra_tunnel_field", "same_namespace", "foreign_namespace",
    "bool_count", "bool_flags", "bool_external_routes", "bool_errno", "one_errno", "wrong_errno",
    "missing_tunnel_field", "int_zero_bool", "reordered_links", "wrong_schema"])
def test_saved_record_is_closed_and_cannot_fabricate_isolation(monkeypatch, fault):
    value, _ = observe_fixture(monkeypatch)
    value.update(ipv4_connect_errno=errno.ENETUNREACH, ipv6_connect_errno=errno.ENETUNREACH)
    if fault == "empty_links": value["links"] = []
    elif fault == "missing_link": value["links"].pop()
    elif fault == "duplicate_names": value["links"][1] = copy.deepcopy(value["links"][0])
    elif fault == "duplicate_index": value["links"][1]["index"] = value["links"][0]["index"]
    elif fault == "extra_field": value["trusted"] = True
    elif fault == "extra_link_field": value["links"][0]["trusted"] = True
    elif fault == "extra_tunnel_field": value["links"][1]["tunnel"]["trusted"] = True
    elif fault == "same_namespace": value["namespace"] = value["host_namespace"]
    elif fault == "foreign_namespace": value["host_namespace"] = "net:other"
    elif fault == "bool_count": value["links"][1]["address_count"] = False
    elif fault == "bool_flags": value["links"][1]["flags"] = True
    elif fault == "bool_external_routes": value["external_routes"] = False
    elif fault == "bool_errno": value["ipv4_connect_errno"] = True
    elif fault == "one_errno": del value["ipv6_connect_errno"]
    elif fault == "wrong_errno": value["ipv6_connect_errno"] = errno.ECONNREFUSED
    elif fault == "missing_tunnel_field": del value["links"][1]["tunnel"]["protocol"]
    elif fault == "int_zero_bool": value["links"][1]["tunnel"]["collect_metadata"] = 0
    elif fault == "reordered_links": value["links"].reverse()
    elif fault == "wrong_schema": value["schema_version"] = "other"
    with pytest.raises(s.IsolationError):
        s.validate(value)


def test_saved_snapshot_exact_extension_is_valid(monkeypatch):
    value, _ = observe_fixture(monkeypatch)
    value.update(ipv4_connect_errno=errno.ENETUNREACH, ipv6_connect_errno=errno.ENETUNREACH)
    s.validate(value)


@pytest.mark.parametrize("carrier", [0, 1])
def test_administratively_down_software_carrier_is_not_admin_state(monkeypatch, carrier):
    value, _ = observe_fixture(monkeypatch)
    for row in value["links"]:
        row["carrier"] = carrier
    s.validate(value)
    assert value["links"][1]["flags"] == 128 and value["links"][1]["operstate"] == 2


@pytest.mark.parametrize("field,bad", [("carrier", True), ("carrier", 2), ("carrier", None),
    ("flags", 129), ("flags", 192), ("flags", 65664), ("operstate", 0), ("operstate", 6),
    ("address_count", 1), ("route_count", 1)])
def test_carrier_one_never_excuses_active_or_configured_interface(monkeypatch, field, bad):
    value, _ = observe_fixture(monkeypatch)
    value["links"][1]["carrier"] = 1
    value["links"][1][field] = bad
    with pytest.raises(s.IsolationError):
        s.validate(value)


def test_actual_blank_namespace_ipv6_missing_source_retains_exact_numeric_proof(monkeypatch):
    value, _ = observe_fixture(monkeypatch)
    for row in value["links"]:
        row["carrier"] = 1
    monkeypatch.setattr(s, "observe", lambda *args, **kwargs: copy.deepcopy(value))
    def probe(family, mode):
        return Probe(errno.ENETUNREACH if family == socket.AF_INET else errno.EADDRNOTAVAIL)
    monkeypatch.setattr(s.socket, "socket", probe)
    result = s.snapshot("net:[4026531833]")
    assert result["ipv4_connect_errno"] == errno.ENETUNREACH
    assert result["ipv6_connect_errno"] == errno.EADDRNOTAVAIL
    s.validate(result)


@pytest.mark.parametrize("fault", ["ipv4_missing_source", "loopback_address", "loopback_route", "tunnel_address",
    "tunnel_route", "ipv6_refused", "ipv6_timeout", "ipv6_denied", "ipv6_no_errno"])
def test_missing_source_exception_never_covers_configured_or_other_failure(monkeypatch, fault):
    value, _ = observe_fixture(monkeypatch)
    value.update(ipv4_connect_errno=errno.ENETUNREACH, ipv6_connect_errno=errno.EADDRNOTAVAIL)
    if fault == "ipv4_missing_source": value["ipv4_connect_errno"] = errno.EADDRNOTAVAIL
    elif fault == "loopback_address": value["links"][0]["address_count"] = 1
    elif fault == "loopback_route": value["links"][0]["route_count"] = 1
    elif fault == "tunnel_address": value["links"][1]["address_count"] = 1
    elif fault == "tunnel_route": value["links"][1]["route_count"] = 1
    elif fault == "ipv6_refused": value["ipv6_connect_errno"] = errno.ECONNREFUSED
    elif fault == "ipv6_timeout": value["ipv6_connect_errno"] = errno.ETIMEDOUT
    elif fault == "ipv6_denied": value["ipv6_connect_errno"] = errno.EACCES
    elif fault == "ipv6_no_errno": value["ipv6_connect_errno"] = None
    with pytest.raises(s.IsolationError):
        s.validate(value)


@pytest.mark.parametrize("field", ["address_count", "route_count"])
def test_live_snapshot_refuses_ipv6_99_if_even_loopback_is_configured(monkeypatch, field):
    value, _ = observe_fixture(monkeypatch)
    value["links"][0][field] = 1
    monkeypatch.setattr(s, "observe", lambda *args, **kwargs: copy.deepcopy(value))
    monkeypatch.setattr(s.socket, "socket", lambda family, mode: Probe(
        errno.ENETUNREACH if family == socket.AF_INET else errno.EADDRNOTAVAIL))
    with pytest.raises(s.IsolationError, match="network_route"):
        s.snapshot("net:[4026531833]")
