"""Content-free accounting of gateway connection attempts to nonlocal peers."""
import ipaddress
import socket
import sys
from pathlib import Path

external_connect_attempts = 0
_installed = False


def install():
    global _installed
    if _installed:
        return
    # This namespace has only the isolated proof bridge. Read its directly
    # connected subnet without DNS: stopped peers may have no DNS record, and
    # their addresses may change on restart. Never allow a default/gateway route.
    permitted = []
    for line in Path('/proc/net/route').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[1] != '00000000' and fields[2] == '00000000':
            address = socket.inet_ntoa(bytes.fromhex(fields[1])[::-1])
            mask = socket.inet_ntoa(bytes.fromhex(fields[7])[::-1])
            permitted.append(ipaddress.ip_network(address + '/' + mask))
    def observe(event, args):
        global external_connect_attempts
        if event != 'socket.connect' or not isinstance(args[1], tuple):
            return
        address = args[1][0]
        try:
            peer = ipaddress.ip_address(address)
            local = peer.is_loopback or any(peer in subnet for subnet in permitted)
        except ValueError:
            local = False
        if not local:
            external_connect_attempts += 1
    sys.addaudithook(observe)
    _installed = True
