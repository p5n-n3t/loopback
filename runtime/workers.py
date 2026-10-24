"""Central registry and SSH transport for independently hosted MCP bindings."""
from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

import node_agent
from ssh_targets import atomic_write_json, read_json

RUNTIME = Path(__file__).resolve().parent
WORKER_FILES = ['node_agent.py', 'worker_bridge.py', 'auth.py', 'auth_http.py', 'context.py', 'bootstrap.py',
                'config.py', 'identity.py', 'version.py', 'policy.py', 'sandbox.py', 'audit.py']
WORKER_PACKAGES = ['mcp>=2.3,<3', 'uvicorn>=0.52,<1', 'starlette>=1.3.1,<2', 'PyJWT>=2.13,<3',
                   'python-multipart>=0.0.20,<1', 'cryptography>=45,<51', 'httpx>=0.28,<1']
REMOTE_ROOT = '.local/share/loopback-worker'

# stdin is data, not shell source. File names and root are constant.
INSTALL = '''import json,pathlib,subprocess,sys,os
p=json.load(sys.stdin)
r=pathlib.Path.home()/'.local/share/loopback-worker'
r.mkdir(parents=True,exist_ok=True,mode=0o700)
for name,body in p['files'].items():
 f=r/name;f.write_text(body);f.chmod(0o600)
v=r/'venv'
if not (v/'bin/python').exists(): subprocess.run([sys.executable,'-m','venv',str(v)],check=True)
(r/'setup-result.json').unlink(missing_ok=True)
code="import subprocess,json,pathlib,sys; r=pathlib.Path(sys.argv[1]); p=subprocess.run([sys.executable,'-m','pip','install',*json.loads(sys.argv[2])]); (r/'setup-result.json').write_text(json.dumps({'ok':p.returncode==0})); sys.exit(p.returncode)"
with (r/'setup.log').open('ab') as log:
 proc=subprocess.Popen([str(v/'bin/python'),'-c',code,str(r),json.dumps(p['packages'])],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
(r/'setup.pid').write_text(str(proc.pid))
print(json.dumps({'ok':True,'setup':'installing','pid':proc.pid}))
'''

REMOTE_CALL = '''import pathlib,subprocess,sys,json
r=pathlib.Path.home()/'.local/share/loopback-worker'
if not (r/'node_agent.py').is_file():
 print(json.dumps({'error':'Worker is not installed; install it from the dashboard'}));sys.exit(1)
p=r/'venv/bin/python'
subprocess.run([str(p) if p.exists() else sys.executable,str(r/'node_agent.py')],stdin=sys.stdin,check=True)
'''


class WorkerManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.path = ctx.cfg_dir / 'bindings.json'
        self.workers_path = ctx.cfg_dir / 'workers.json'
        self.local_root = ctx.cfg_dir / 'worker'
        self.lock = threading.RLock()
        self.samples = {}
        self.cache = None
        self.cache_at = 0.0
        self.sample_lock = threading.Lock()

    def bindings(self):
        return read_json(self.path, [])

    def targets(self):
        return read_json(self.workers_path, ['local'])

    def _ssh(self, target):
        if target == 'local':
            return None
        return self.ctx.ssh._ssh_args(self.ctx.ssh.get(target))

    def call(self, target, payload, timeout=25):
        if target == 'local':
            if payload.get('action') == 'start':
                self.install('local')
            return node_agent.dispatch(self.local_root, payload)
        p = subprocess.run([*self._ssh(target), 'python3 -c ' + shlex.quote(REMOTE_CALL)],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
        try:
            result = json.loads(p.stdout)
        except ValueError:
            raise ValueError('Worker did not respond. Check SSH, Python 3.11+ and worker setup on ' + target) from None
        if result.get('error'):
            raise ValueError(result['error'])
        if p.returncode:
            raise ValueError('Worker command failed on ' + target)
        return result

    def install(self, target):
        self._ssh(target)  # validate the registered SSH target before changing anything
        if target == 'local':
            self.local_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for name in WORKER_FILES:
                dest = self.local_root / name
                dest.write_text((RUNTIME / name).read_text())
                dest.chmod(0o600)
            result = {'ok': True, 'setup': 'ready'}
        else:
            payload = {'files': {name: (RUNTIME / name).read_text() for name in WORKER_FILES}, 'packages': WORKER_PACKAGES}
            p = subprocess.run([*self._ssh(target), 'python3 -c ' + shlex.quote(INSTALL)], input=json.dumps(payload),
                               capture_output=True, text=True, timeout=30)
            if p.returncode:
                raise ValueError('Worker setup failed; Python 3.11+ with venv is required on ' + target)
            try:
                result = json.loads(p.stdout)
            except ValueError:
                raise ValueError('Worker setup returned an invalid response') from None
        with self.lock:
            atomic_write_json(self.workers_path, sorted(set(self.targets()) | {target}))
            self.cache_at = 0
        return result

    def save(self, value):
        b = copy.deepcopy(value)
        node_agent.validate(b)
        target = b.get('target', 'local')
        self._ssh(target)
        allowed = ('name', 'target', 'transport', 'upstream', 'upstream_token_file', 'command', 'cwd', 'port',
                   'exposure', 'https_port', 'hostname', 'token_file', 'confirm_public')
        b = {k: v for k, v in b.items() if k in allowed}
        b['target'] = target
        with self.lock:
            rows = self.bindings()
            old = next((x for x in rows if x['name'] == b['name']), None)
            if old and old.get('desired') == 'running':
                raise ValueError('Stop this binding before editing or moving it')
            for x in rows:
                if x['name'] != b['name'] and x['target'] == target and x['port'] == b['port']:
                    raise ValueError('This worker port is already assigned on this target')
                if x['name'] != b['name'] and x['target'] == target and b['exposure'].startswith('tailscale-') \
                        and x['exposure'].startswith('tailscale-') and x.get('https_port', 8443) == b.get('https_port', 8443):
                    raise ValueError('This Tailscale port is already assigned on this target')
            b['desired'] = 'stopped'
            atomic_write_json(self.path, [x for x in rows if x['name'] != b['name']] + [b])
        return b

    def action(self, name, action):
        if action not in ('start', 'stop', 'credential', 'delete'):
            raise ValueError('Unknown binding action')
        with self.lock:
            rows = self.bindings()
            b = next((x for x in rows if x['name'] == name), None)
            if b is None:
                raise KeyError('Binding not found')
            if action == 'delete' and b.get('desired') == 'running':
                raise ValueError('Stop the binding before deleting it')
            if action == 'delete':
                result = {'ok': True}
                rows.remove(b)
            else:
                result = self.call(b['target'], {'action': action, 'binding': b})
                if action in ('start', 'stop'):
                    b['desired'] = 'running' if action == 'start' else 'stopped'
                    b['url'] = result.get('url', b.get('url'))
            atomic_write_json(self.path, rows)
            self.cache_at = 0
            return result

    def _sample(self, target):
        try:
            payload = {'action': 'metrics'}
            if target == 'local':
                payload['controller_pid'] = os.getpid()
            sample = self.call(target, payload, timeout=12)
            prior = self.samples.get(target)
            self.samples[target] = copy.deepcopy(sample)
            host, worker = sample['host'], sample['worker']
            for group in (host, worker):
                group.update(cpu_percent=None, rx_per_second=None, tx_per_second=None)
            if prior:
                elapsed = sample['sampled_at'] - prior['sampled_at']
                if elapsed > 0:
                    for key in ('host', 'worker'):
                        cur, prev = sample[key], prior[key]
                        for name in ('rx', 'tx'):
                            a, b = cur.get(name + '_bytes'), prev.get(name + '_bytes')
                            if a is not None and b is not None and a >= b:
                                cur[name + '_per_second'] = round((a - b) / elapsed, 1)
                    if host['cpu_seconds'] is not None and prior['host']['cpu_seconds'] is not None:
                        total = host['cpu_seconds'] - prior['host']['cpu_seconds']
                        idle = host['cpu_idle_seconds'] - prior['host']['cpu_idle_seconds']
                        if total > 0:
                            host['cpu_percent'] = round(max(0, min(100, 100 * (1 - idle / total))), 1)
                    delta = worker['cpu_seconds'] - prior['worker']['cpu_seconds']
                    if delta >= 0:
                        worker['cpu_percent'] = round(min(100, delta / elapsed / host['cpu_count'] * 100), 1)
            if host['memory_total']:
                host['memory_percent'] = round(host['memory_used'] / host['memory_total'] * 100, 1)
                worker['memory_percent'] = round(worker['rss_bytes'] / host['memory_total'] * 100, 1)
            sample.update(target=target, status='healthy', heartbeat_at=sample['sampled_at'])
            return sample
        except Exception:
            # Never present stale measurements as live, and never echo SSH output or credentials.
            return {'target': target, 'status': 'unreachable', 'error': 'Worker unavailable; check SSH and worker setup',
                    'heartbeat_at': self.samples.get(target, {}).get('sampled_at')}

    def snapshot(self):
        with self.sample_lock:
            if self.cache and time.monotonic() - self.cache_at < 2:
                return copy.deepcopy(self.cache)
            targets = sorted(set(self.targets()) | {'local'} | {b['target'] for b in self.bindings()})
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
                workers = list(pool.map(self._sample, targets))
            self.cache = {'workers': workers, 'bindings': self.bindings(), 'sampled_at': time.time()}
            self.cache_at = time.monotonic()
            return copy.deepcopy(self.cache)
