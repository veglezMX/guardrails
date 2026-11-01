"""Fixed stdlib program for an explicitly authorized original worker transition.

Executed only by the parent-owned exact Pod transport. The only signals are
SIGSTOP/SIGCONT through a held pidfd after matching start time, command, UID,
cgroup and PID namespace. Numeric PID signaling has no fallback. No model,
network, shell, process creation, pod deletion or service restart is performed.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import stat
import sys
import time

MAX = 32768
PROCESS_FIELDS = {'pid', 'start_ticks', 'uid', 'command_sha256', 'cgroup_sha256', 'pid_namespace', 'state'}


class TransitionRefusal(ValueError):
    pass


def require(value, code='transition_refused'):
    if not value: raise TransitionRefusal(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode))
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
        after = os.fstat(fd)
        require(len(data) <= maximum and (before.st_dev, before.st_ino, before.st_uid) ==
                (after.st_dev, after.st_ino, after.st_uid))
        return bytes(data)
    finally:
        os.close(fd)


def process(pid):
    require(type(pid) is int and 1 <= pid <= 2**31 - 1)
    base = '/proc/' + str(pid)
    def stamp():
        raw = read(base + '/stat', 4096).decode('ascii')
        left, sep, right = raw.rpartition(') ')
        require(sep and left.startswith(str(pid) + ' ('))
        fields = right.split(); require(len(fields) >= 20 and fields[19].isdigit())
        return int(fields[19]), fields[0]
    start, state = stamp()
    status = read(base + '/status', 16384).decode('ascii').splitlines()
    uids = [row.split()[1:] for row in status if row.startswith('Uid:')]
    require(len(uids) == 1 and len(uids[0]) == 4 and len(set(uids[0])) == 1)
    namespace = os.readlink(base + '/ns/pid')
    require(namespace.startswith('pid:[') and namespace.endswith(']') and namespace[5:-1].isdigit())
    command = read(base + '/cmdline', 8192); cgroup = read(base + '/cgroup', 4096)
    require(command and command.endswith(b'\0') and cgroup and stamp()[0] == start and start > 0)
    return {'pid': pid, 'start_ticks': start, 'uid': int(uids[0][0]), 'command_sha256': sha(command),
            'cgroup_sha256': sha(cgroup), 'pid_namespace': namespace, 'state': state}


def fixed(value): return {k: value[k] for k in PROCESS_FIELDS - {'state'}}


def same(actual, expected):
    require(type(actual) is dict and set(actual) == PROCESS_FIELDS and type(expected) is dict and set(expected) == PROCESS_FIELDS)
    require(fixed(actual) == fixed(expected), 'transition_identity')


def capture(spec):
    supervisor = process(1)
    require(supervisor['uid'] == 10001 and supervisor['command_sha256'] == spec['supervisor_command_sha256'])
    children = read('/proc/1/task/1/children', 4096).split()
    require(len(children) == 1 and children[0].isdigit())
    child = process(int(children[0]))
    require(child['pid'] != 1 and child['uid'] == 10001 and child['command_sha256'] == spec['worker_command_sha256']
            and child['cgroup_sha256'] == supervisor['cgroup_sha256'] and child['pid_namespace'] == supervisor['pid_namespace'])
    return {'supervisor': supervisor, 'child': child}


def sources(spec):
    pins = spec['worker_files']
    require(type(pins) is dict and 1 <= len(pins) <= 32)
    for name, digest in pins.items():
        require(type(name) is str and name.startswith('/app/') and name.endswith('.py')
                and all(p not in {'', '.', '..'} for p in name[1:].split('/'))
                and type(digest) is str and len(digest) == 64)
        require(sha(read(name, 2 * 1024 * 1024)) == digest, 'transition_source')


def signal_owned(expected, operation, *, proc=process, open_pidfd=os.pidfd_open,
                 send=signal.pidfd_send_signal, closed=os.close, poller=select.poll):
    """Open before checking identity; a stale pidfd never targets a replacement."""
    require(operation in {'stop', 'resume'})
    try: fd = open_pidfd(expected['pid'], 0)
    except ProcessLookupError: return {'status': 'gone', 'signalled': False}
    try:
        wait = poller(); wait.register(fd, select.POLLIN)
        if wait.poll(0): return {'status': 'gone', 'signalled': False}
        try: current = proc(expected['pid'])
        except FileNotFoundError: return {'status': 'gone', 'signalled': False}
        if current['start_ticks'] != expected['start_ticks']:
            require(operation == 'resume', 'transition_identity')
            return {'status': 'pid_reused_not_signalled', 'signalled': False}
        same(current, expected)
        if wait.poll(0): return {'status': 'gone', 'signalled': False}
        if operation == 'resume' and current['state'] != 'T':
            require(current['state'] in {'R', 'S', 'D'}, 'transition_state')
            return {'status': 'already_running', 'signalled': False}
        require(current['state'] in ({'R', 'S'} if operation == 'stop' else {'T'}), 'transition_state')
        try: send(fd, signal.SIGSTOP if operation == 'stop' else signal.SIGCONT, None, 0)
        except ProcessLookupError: return {'status': 'gone', 'signalled': False}
        return {'status': 'signal_sent', 'signalled': True}
    finally:
        closed(fd)


def operate(value):
    require(type(value) is dict and set(value) == {'operation', 'spec', 'expected'}
            and value['operation'] in {'capture', 'stop', 'status', 'resume'})
    spec = value['spec']
    require(type(spec) is dict and set(spec) == {'supervisor_command_sha256', 'worker_command_sha256', 'worker_files'})
    sources(spec)
    op, expected = value['operation'], value['expected']
    if op == 'capture':
        require(expected is None); return {'status': 'captured', **capture(spec)}
    require(type(expected) is dict and set(expected) == {'supervisor', 'child'})
    same(process(1), expected['supervisor'])
    require(expected['supervisor']['command_sha256'] == spec['supervisor_command_sha256']
            and expected['child']['command_sha256'] == spec['worker_command_sha256'])
    if op == 'resume':
        result = signal_owned(expected['child'], 'resume'); sources(spec)
        return result
    if op == 'stop':
        now = capture(spec)
        same(now['supervisor'], expected['supervisor']); same(now['child'], expected['child'])
        result = signal_owned(expected['child'], 'stop')
        require(result == {'status': 'signal_sent', 'signalled': True}, 'transition_gone_before_stop')
        until = time.monotonic() + 0.5
        while True:
            actual = process(expected['child']['pid']); same(actual, expected['child'])
            if actual['state'] == 'T': break
            require(time.monotonic() < until, 'transition_stop_timeout'); time.sleep(0.005)
        sources(spec); same(process(1), expected['supervisor'])
        return {'status': 'stopped', 'supervisor': expected['supervisor'], 'child': actual}
    try:
        old = process(expected['child']['pid'])
        old_gone = old['start_ticks'] != expected['child']['start_ticks'] or old['state'] in {'Z', 'X'}
        if not old_gone: same(old, expected['child'])
    except FileNotFoundError:
        old, old_gone = None, True
    current = capture(spec)
    same(current['supervisor'], expected['supervisor']); sources(spec)
    return {'status': 'observed', 'old_terminated': old_gone, 'old': old, **current}


def main():
    try:
        require(len(sys.argv) == 2)
        raw = sys.argv[1].encode('ascii'); require(0 < len(raw) <= MAX)
        value = json.loads(raw)
        result = operate(value)
        print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))
    except BaseException:  # noqa: BLE001 - finite private process boundary never exposes arbitrary error text
        # Only finite status; no command, source bytes, arguments, locals or error text.
        print('{"status":"refused"}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
