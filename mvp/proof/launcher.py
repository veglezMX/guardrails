"""Non-destructive lifecycle for the workstation's selected local proof stack."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .gateway_test_support import GatewayStack, PREPARED, ROOT

POINTER = PREPARED / 'live.json'


def save(value):
    temporary = POINTER.with_suffix('.pending')
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    os.replace(temporary, POINTER)


def main():
    action = sys.argv[1]
    value = json.loads(POINTER.read_text()) if POINTER.exists() else None
    if action == 'serve':
        stack = GatewayStack(live=True, resume=value if value and value['status'] == 'stopped' else None)
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            save({'pid':os.getpid(),'project':stack.project,'root':str(stack.root),'status':'starting'})
            stack.start()
            save({'pid':os.getpid(),'project':stack.project,'root':str(stack.root),'status':'ready','ports':stack.ports})
            while not stopping:
                time.sleep(.2)
        except Exception:
            save({'pid':os.getpid(),'project':stack.project,'root':str(stack.root),'status':'failed'})
            print('proof_start_failed', file=sys.stderr)
            return 1
        finally:
            stack.stop()
        save({'project':stack.project,'root':str(stack.root),'status':'stopped'})
        return 0
    if action == 'up':
        if value and value['status'] == 'ready':
            pid = value.get('pid')
            try:
                alive = pid and b'proof.launcher\x00serve' in Path(f'/proc/{pid}/cmdline').read_bytes()
            except FileNotFoundError:
                alive = False
            if alive:
                import ssl
                import httpx
                root = Path(value['root'])
                try:
                    with httpx.Client(verify=ssl.create_default_context(cafile=str(PREPARED/'pki/ca.crt')), trust_env=False, timeout=1) as client:
                        for service, token in (('management','management-admin'),('runtime','management-service'),('gateway-a','gateway-a.client'),('gateway-b','gateway-b.client')):
                            response = client.get('https://localhost:' + str(value['ports'][service]) + '/health/ready',
                                headers={'Authorization':'Bearer '+(root/'secrets'/f'{token}.token').read_text().strip()})
                            if response.status_code != 200:
                                print('proof_stack_not_ready',file=sys.stderr)
                                return 2
                except (httpx.HTTPError, OSError):
                    print('proof_stack_not_ready',file=sys.stderr)
                    return 2
                print(json.dumps(value))
                return 0
            value = value | {'status':'stopped','pid':None}
            save(value)
        if value and value['status'] == 'starting':
            print('proof_start_already_running', file=sys.stderr)
            return 2
        log = PREPARED / 'state/launcher.log'
        with log.open('ab') as output:
            process = subprocess.Popen([sys.executable,'-m','proof.launcher','serve'], cwd=ROOT,
                env=os.environ | {'PYTHONPATH':str(ROOT/'mvp')}, stdout=output, stderr=output, start_new_session=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            value = json.loads(POINTER.read_text()) if POINTER.exists() else {}
            if value.get('pid') == process.pid and value.get('status') == 'ready':
                print(json.dumps(value))
                return 0
            if process.poll() is not None:
                return 2
            time.sleep(.3)
        print('proof_start_timeout; inspect .proof/live.json and launcher.log',file=sys.stderr)
        return 2
    if action in ('down','reset'):
        if not value:
            return 0
        pid = value.get('pid')
        if pid:
            # Refuse to signal a reused unrelated PID.
            try:
                command = Path(f'/proc/{pid}/cmdline').read_bytes()
                if b'proof.launcher\x00serve' in command:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 40
                    while time.monotonic() < deadline:
                        value = json.loads(POINTER.read_text())
                        if value.get('status') in ('stopped','failed'):
                            break
                        time.sleep(.2)
                    else:
                        return 2
            except ProcessLookupError:
                pass
            except FileNotFoundError:
                pass
        root = Path(value['root']).resolve()
        if not root.is_relative_to((PREPARED / 'state/live-stacks').resolve()):
            raise ValueError('invalid_proof_state')
        if (root/'compose.json').exists():
            subprocess.run(['docker','compose','-p',value['project'],'-f',str(root/'compose.json'),'down','--remove-orphans'],check=True)
        if action == 'reset':
            # Retire the selected run. The next start creates fresh DBs/secrets;
            # previous stores, logs and reports remain inspectable.
            POINTER.unlink()
            print('Selected disposable run reset; retained prior run evidence.')
        else:
            save(value | {'status':'stopped','pid':None})
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
