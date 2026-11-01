"""Bounded Linux namespace isolation proof; stdlib only, no network mutation.

The optional tunl0 exception is for the down, unconfigured IPIP fallback emitted
by ipip_init_net. Its name alone is never authority. Query current rtnetlink
link/address/route dumps, then require both TESTNET connects to be unreachable.
No model, corpus, subprocess, DNS, module loading, or interface changes occur.
"""

import errno
import os
import re
import socket
import struct
import time

SCHEMA = "classifier-offline-namespace-v1"
CODES = frozenset({"namespace_identity", "netlink_deadline", "netlink_bound",
    "netlink_framing", "netlink_peer", "netlink_interrupted", "netlink_error",
    "netlink_attribute", "link_shape", "address_shape", "route_shape",
    "network_interfaces", "network_loopback", "network_tunnel", "network_addresses",
    "network_routes", "network_route", "network_changed", "network_record"})


class IsolationError(ValueError):
    """Contains only one of the finite CODES, never kernel output or addresses."""


def require(condition, code):
    if not condition:
        raise IsolationError(code)


def _remaining(deadline):
    value = deadline - time.monotonic()
    require(value > 0, "netlink_deadline")
    return min(value, 1.0)


def _attrs(raw):
    result = {}
    offset = 0
    while offset < len(raw):
        require(len(result) < 128 and len(raw) - offset >= 4, "netlink_attribute")
        length, kind = struct.unpack_from("=HH", raw, offset)
        size = (length + 3) & ~3
        require(length >= 4 and offset + size <= len(raw), "netlink_attribute")
        kind &= 0x3FFF  # NLA_F_NESTED and NLA_F_NET_BYTEORDER are flag bits.
        require(kind > 0 and kind not in result, "netlink_attribute")
        result[kind] = raw[offset + 4:offset + length]
        offset += size
    return result


def _integer(raw, size):
    require(type(raw) is bytes and len(raw) == size, "netlink_attribute")
    return int.from_bytes(raw, byteorder="little" if struct.pack("=I", 1)[0] else "big")


def _name(raw):
    require(type(raw) is bytes and 2 <= len(raw) <= 33 and raw[-1:] == b"\0"
            and re.fullmatch(rb"[A-Za-z0-9_.:-]+", raw[:-1]) is not None, "link_shape")
    return raw[:-1].decode("ascii")


def _dump(request, response, body, deadline):
    """Only RTM_GETLINK/GETADDR/GETROUTE; kernel peer, full finite dump, no retry."""
    require((request, response, len(body)) in {(18, 16, 16), (22, 20, 8), (26, 24, 12)},
            "netlink_framing")
    result = []
    sequence = 1
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as stream:
        stream.settimeout(_remaining(deadline))
        stream.bind((0, 0))
        port = stream.getsockname()[0]
        packet = struct.pack("=IHHII", 16 + len(body), request, 0x301, sequence, port) + body
        require(stream.sendto(packet, (0, 0)) == len(packet), "netlink_framing")
        total = 0
        for _ in range(16):
            stream.settimeout(_remaining(deadline))
            raw, ancillary, flags, peer = stream.recvmsg(65536)
            total += len(raw)
            require(peer == (0, 0) and not ancillary, "netlink_peer")
            require(raw and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                    and total <= 262144, "netlink_bound")
            offset = 0
            done = False
            while offset < len(raw):
                require(len(raw) - offset >= 16, "netlink_framing")
                length, kind, message_flags, seq, pid = struct.unpack_from("=IHHII", raw, offset)
                aligned = (length + 3) & ~3
                require(length >= 16 and offset + aligned <= len(raw)
                        and seq == sequence and pid == port and not done, "netlink_framing")
                require(not message_flags & 0x10, "netlink_interrupted")  # NLM_F_DUMP_INTR
                payload = raw[offset + 16:offset + length]
                if kind == 3:  # NLMSG_DONE, no extack accepted on this closed query.
                    require(payload in (b"", b"\0\0\0\0"), "netlink_error")
                    done = True
                else:
                    require(kind == response and message_flags & 2, "netlink_error")
                    require(len(result) < 64, "netlink_bound")
                    result.append(payload)
                offset += aligned
            if done:
                return result
    raise IsolationError("netlink_bound")


def _link(raw):
    require(len(raw) >= 16, "link_shape")
    family, padding, hardware, index, flags, change = struct.unpack_from("=BBHiII", raw)
    require(family == padding == 0 and index > 0, "link_shape")
    attrs = _attrs(raw[16:])
    name = _name(attrs.get(3))
    info = _attrs(attrs[18]) if 18 in attrs else {}
    kind = _name(info[1]) if 1 in info else None
    tunnel = None
    if name == "tunl0":
        data = _attrs(info[2]) if 2 in info else {}
        # Exact fields ipip_fill_info emits for a non-collect-metadata device.
        expected = {1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 9: 1, 10: 1,
                    15: 2, 16: 2, 17: 2, 18: 2, 20: 4}
        require(set(data) == set(expected), "network_tunnel")
        values = {key: _integer(data[key], size) for key, size in expected.items()}
        tunnel = {"link_index": values[1], "local_zero": values[2] == 0,
            "remote_zero": values[3] == 0, "ttl": values[4], "tos": values[5],
            "protocol": values[9], "pmtudisc": values[10], "encapsulation_type": values[15],
            "encapsulation_flags": values[16], "source_port_zero": values[17] == 0,
            "destination_port_zero": values[18] == 0, "fwmark": values[20],
            "collect_metadata": False}
    return {"name": name, "index": index, "hardware_type": hardware, "flags": flags,
        "kind": kind, "operstate": _integer(attrs[16], 1) if 16 in attrs else None,
        "carrier": _integer(attrs[33], 1) if 33 in attrs else None,
        "link_index": _integer(attrs[5], 4) if 5 in attrs else 0,
        "master_index": _integer(attrs[10], 4) if 10 in attrs else 0,
        "foreign_namespace_link": 37 in attrs or 19 in attrs or 28 in attrs,
        "tunnel": tunnel}


def _address(raw):
    require(len(raw) >= 8, "address_shape")
    family, prefix, flags, scope, index = struct.unpack_from("=BBBBI", raw)
    require(family in (socket.AF_INET, socket.AF_INET6) and index > 0
            and prefix <= (32 if family == socket.AF_INET else 128), "address_shape")
    _attrs(raw[8:])  # Validate framing; never retain address bytes.
    return index


def _route(raw):
    require(len(raw) >= 12, "route_shape")
    family, dst, src, tos, table, protocol, scope, kind, flags = struct.unpack_from("=BBBBBBBBI", raw)
    require(family in (socket.AF_INET, socket.AF_INET6), "route_shape")
    attrs = _attrs(raw[12:])
    # Multipath, gateway, cross-family gateway, encapsulation, and nexthop IDs
    # are not evidence of a namespace with only local loopback routes.
    require(not set(attrs) & {5, 9, 18, 21, 22, 30}, "network_routes")
    require(4 in attrs, "network_routes")
    return _integer(attrs[4], 4)


def observe(host_namespace, *, deadline=None):
    """Finite metadata only; this projection is not a successful isolation proof."""
    if deadline is None:
        deadline = time.monotonic() + 4
    namespace = os.readlink("/proc/self/ns/net")
    require(type(host_namespace) is str and len(host_namespace) <= 80
            and re.fullmatch(r"net:\[\d+\]", host_namespace)
            and re.fullmatch(r"net:\[\d+\]", namespace)
            and namespace != host_namespace, "namespace_identity")
    links = [_link(raw) for raw in _dump(18, 16, struct.pack("=BBHiII", 0, 0, 0, 0, 0, 0), deadline)]
    require(len(links) <= 8 and len({row["name"] for row in links}) == len(links)
            and len({row["index"] for row in links}) == len(links), "link_shape")
    addresses = [_address(raw) for raw in _dump(22, 20, b"\0" * 8, deadline)]
    routes = [_route(raw) for raw in _dump(26, 24, b"\0" * 12, deadline)]
    known = {row["index"] for row in links}
    require(set(addresses) <= known and set(routes) <= known, "network_changed")
    for row in links:
        row["address_count"] = addresses.count(row["index"])
        row["route_count"] = routes.count(row["index"])
    require(os.readlink("/proc/self/ns/net") == namespace, "network_changed")
    return {"schema_version": SCHEMA, "namespace": namespace, "host_namespace": host_namespace,
        "interfaces": sorted(row["name"] for row in links),
        "links": sorted(links, key=lambda row: row["name"]),
        "external_routes": sum(row["route_count"] for row in links if row["name"] != "lo")}


# A newly allocated software device may retain carrier_ok while administratively
# down (kernel networking/operstates.rst). IFF_UP/RUNNING/LOWER_UP and OPERSTATE,
# plus the exact zero tunnel/address/route checks, decide eligibility.
def _unreachable(value, family, code):
    # ipv6_dev_get_saddr returns EADDRNOTAVAIL when no source candidate exists.
    # This exception applies only to a completely addressless/routeless blank
    # namespace. It is not a deployed CNI connectivity/timeout observation.
    return type(code) is int and (code == errno.ENETUNREACH or
        family == socket.AF_INET6 and code == errno.EADDRNOTAVAIL and
        all(row["address_count"] == row["route_count"] == 0 for row in value["links"]))


def validate(value):
    """Validate a closed observation or saved snapshot; no IO and no inference."""
    fields = {"schema_version", "namespace", "host_namespace", "interfaces", "links", "external_routes"}
    extension = {"ipv4_connect_errno", "ipv6_connect_errno"}
    require(type(value) is dict and set(value) in (fields, fields | extension), "network_record")
    require(value["schema_version"] == SCHEMA, "network_record")
    for name in ("namespace", "host_namespace"):
        require(type(value[name]) is str and len(value[name]) <= 80
                and re.fullmatch(r"net:\[\d+\]", value[name]), "namespace_identity")
    require(value["namespace"] != value["host_namespace"], "namespace_identity")
    require(type(value["interfaces"]) is list and type(value["links"]) is list
            and len(value["links"]) == len(value["interfaces"]), "network_record")
    require(value["interfaces"] in (["lo"], ["lo", "tunl0"]), "network_interfaces")
    link_fields = {"name", "index", "hardware_type", "flags", "kind", "operstate", "carrier",
        "link_index", "master_index", "foreign_namespace_link", "tunnel", "address_count", "route_count"}
    integer_fields = {"index", "hardware_type", "flags", "link_index", "master_index", "address_count", "route_count"}
    names, indexes = [], []
    for row in value["links"]:
        require(type(row) is dict and set(row) == link_fields, "network_record")
        require(all(type(row[name]) is int and 0 <= row[name] < 2 ** 32 for name in integer_fields)
                and row["index"] > 0 and row["address_count"] <= 64 and row["route_count"] <= 64,
                "network_record")
        require(type(row["name"]) is str and row["kind"] in (None, "ipip")
                and (row["operstate"] is None or type(row["operstate"]) is int and 0 <= row["operstate"] <= 6)
                and (row["carrier"] is None or type(row["carrier"]) is int and row["carrier"] in (0, 1))
                and type(row["foreign_namespace_link"]) is bool, "network_record")
        names.append(row["name"])
        indexes.append(row["index"])
        if row["name"] == "lo":
            require(row["hardware_type"] == 772 and row["flags"] == 8 and row["kind"] is None
                    and row["tunnel"] is None and not row["foreign_namespace_link"]
                    and row["master_index"] == 0 and row["link_index"] in (0, row["index"]),
                    "network_loopback")
        else:
            require(row["name"] == "tunl0" and row["hardware_type"] == 768
                    and row["kind"] == "ipip" and row["flags"] == 128
                    and row["operstate"] == 2 and row["carrier"] in (0, 1)
                    and row["link_index"] == row["master_index"] == 0
                    and not row["foreign_namespace_link"], "network_tunnel")
            tunnel = row["tunnel"]
            integers = {"link_index", "ttl", "tos", "protocol", "pmtudisc", "encapsulation_type",
                        "encapsulation_flags", "fwmark"}
            booleans = {"local_zero", "remote_zero", "source_port_zero", "destination_port_zero", "collect_metadata"}
            require(type(tunnel) is dict and set(tunnel) == integers | booleans
                    and all(type(tunnel[name]) is int and 0 <= tunnel[name] < 2 ** 32 for name in integers)
                    and all(type(tunnel[name]) is bool for name in booleans), "network_record")
            require(tunnel["protocol"] in (0, 4)
                    and all(tunnel[name] == 0 for name in ("link_index", "ttl", "tos", "pmtudisc",
                        "encapsulation_type", "encapsulation_flags", "fwmark"))
                    and all(tunnel[name] is True for name in ("local_zero", "remote_zero",
                        "source_port_zero", "destination_port_zero"))
                    and tunnel["collect_metadata"] is False, "network_tunnel")
            require(row["address_count"] == 0, "network_addresses")
            require(row["route_count"] == 0, "network_routes")
    require(names == value["interfaces"] and len(set(indexes)) == len(indexes), "network_record")
    require(type(value["external_routes"]) is int and value["external_routes"] == 0
            and value["external_routes"] == sum(row["route_count"] for row in value["links"] if row["name"] != "lo"),
            "network_routes")
    if extension <= set(value):
        require(_unreachable(value, socket.AF_INET, value["ipv4_connect_errno"])
                and _unreachable(value, socket.AF_INET6, value["ipv6_connect_errno"]), "network_route")


def snapshot(host_namespace):
    """Observe/prove/reobserve within eight seconds; only an exact inert exception."""
    deadline = time.monotonic() + 8
    before = observe(host_namespace, deadline=deadline)
    validate(before)
    results = []
    for family, destination in ((socket.AF_INET, ("198.51.100.1", 443)),
                                (socket.AF_INET6, ("2001:db8::1", 443, 0, 0))):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(min(0.25, _remaining(deadline)))
            try:
                probe.connect(destination)
            except OSError as error:
                require(_unreachable(before, family, error.errno), "network_route")
                results.append(error.errno)
            else:
                raise IsolationError("network_route")
    after = observe(host_namespace, deadline=deadline)
    validate(after)
    require(before == after, "network_changed")
    return {**after, "ipv4_connect_errno": results[0], "ipv6_connect_errno": results[1]}
