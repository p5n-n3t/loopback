"""SSH-controlled worker. No listening control port and no persistent control daemon.

All mutations require the SSH account (or the local dashboard admin). Each binding
owns its processes and OAuth store. Existing Tailscale listeners are never adopted.
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
ROOT = Path(__file__).resolve().parent


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value) + '\n')
    tmp.chmod(0o600)
    tmp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def run(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    if p.returncode:
        # CLI output can contain credentials. Keep it in the private worker log.
        raise ValueError(f'{args[0]} failed (exit {p.returncode}); inspect the worker log on this target')
    return p.stdout


def pid_identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def alive(proc):
    if not proc or not proc.get('pid'):
        return False
    return bool(proc.get('start') and pid_identity(proc['pid']) == proc['start'])


def launch(args, directory, label, env=None):
    with (directory / (label + '.log')).open('ab') as log:
        p = subprocess.Popen(args, cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, env=env)
    return {'pid': p.pid, 'start': pid_identity(p.pid)}


def terminate(proc):
    if alive(proc):
        os.killpg(proc['pid'], signal.SIGTERM)
        for _ in range(20):
            if not alive(proc):
                break
            time.sleep(.05)
        if alive(proc):
            os.killpg(proc['pid'], signal.SIGKILL)


def serve_config():
    return json.loads(run(['tailscale', 'serve', 'status', '--json']) or '{}')


def listeners(data, port):
    """Include background and foreground configs; a port belongs to one owner."""
    found = []
    for cfg in [data, *(data.get('Foreground') or {}).values()]:
        if not isinstance(cfg, dict):
            continue
        for host, web in (cfg.get('Web') or {}).items():
            if host.endswith(':' + str(port)):
                found.append((host, web))
        if str(port) in (cfg.get('TCP') or {}) and not found:
            found.append((str(port), {}))
    return found


def validate(b):
    if not isinstance(b, dict) or not NAME.fullmatch(str(b.get('name', ''))):
        raise ValueError('Invalid binding name')
    if b.get('transport') not in ('http', 'stdio'):
        raise ValueError('Transport must be http or stdio')
    if b.get('exposure') not in ('private', 'tailscale-serve', 'tailscale-funnel', 'cloudflare'):
        raise ValueError('Unknown exposure')
    port = b.get('port')
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError('Worker port must be 1024–65535')
    if b['transport'] == 'http':
        u = urlsplit(str(b.get('upstream', '')))
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Upstream must be an http(s) URL without credentials, query or fragment')
        if u.port == port and u.hostname in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Upstream cannot point to its own worker port')
    else:
        cmd = b.get('command')
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x and '\x00' not in x for x in cmd):
            raise ValueError('Command must be a nonempty JSON array of arguments')
    exposure = b['exposure']
    if exposure in ('cloudflare', 'tailscale-funnel') and b.get('confirm_public') is not True:
        raise ValueError('Confirm public exposure before starting this binding')
    if exposure.startswith('tailscale-'):
        hp = b.get('https_port', 8443)
        if isinstance(hp, bool) or not isinstance(hp, int) or not 1 <= hp <= 65535:
            raise ValueError('HTTPS port must be 1–65535')
        if exposure == 'tailscale-funnel' and hp not in (443, 8443, 10000):
            raise ValueError('Tailscale Funnel supports HTTPS ports 443, 8443 and 10000')
    if exposure == 'cloudflare':
        if not b.get('hostname') or not b.get('token_file'):
            raise ValueError('Cloudflare requires a hostname and a target-local token file; configure its origin to this worker port in Cloudflare')
    host = b.get('hostname')
    if host and (not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]+', host) or '.' not in host):
        raise ValueError('Hostname must be a DNS name')
    for key in ('token_file', 'upstream_token_file', 'cwd'):
        if b.get(key) and (not isinstance(b[key], str) or '\x00' in b[key] or '\n' in b[key]):
            raise ValueError(f'Invalid {key}')
    return b


def stop_binding(directory, state):
    b = state.get('binding', {})
    if b.get('exposure', '').startswith('tailscale-') and state.get('tailscale_owned'):
        current = listeners(serve_config(), b['https_port'])
        expected = f"http://127.0.0.1:{b['port']}"
        # Never turn off a port after someone else has changed any of its handlers.
        if current and not all(w.get('Handlers') == {'/': {'Proxy': expected}} for _, w in current):
            raise ValueError('Tailscale listener changed outside Loopback; refusing to stop it')
        if current:
            mode = 'funnel' if b['exposure'] == 'tailscale-funnel' else 'serve'
            run(['tailscale', mode, '--https=' + str(b['https_port']), 'off'])
    terminate(state.get('tunnel'))
    terminate(state.get('bridge'))
    state.update(bridge=None, tunnel=None, tailscale_owned=False, stopped_at=time.time())
    write_json(directory / 'state.json', state)
    return {'ok': True, 'name': b.get('name'), 'running': False}


def start_binding(root, b):
    validate(b)
    if b['exposure'].startswith('tailscale-'):
        b.setdefault('https_port', 8443)
    directory = root / 'bindings' / b['name']
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    old = read_json(directory / 'state.json', {})
    if alive(old.get('bridge')):
        raise ValueError('Binding is already running; stop it before changing it')
    # A crashed bridge may still own a tunnel. Clean it up using the same ownership checks.
    if old.get('tailscale_owned') or alive(old.get('tunnel')):
        stop_binding(directory, old)
    for f in (root / 'bindings').glob('*/state.json'):
        other = read_json(f, {})
        if f.parent != directory and alive(other.get('bridge')) and other.get('binding', {}).get('port') == b['port']:
            raise ValueError('Worker port is already used by another binding')
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', b['port']))
        except OSError:
            raise ValueError('Worker port is already occupied') from None
    if b['exposure'].startswith('tailscale-'):
        if listeners(serve_config(), b['https_port']):
            raise ValueError('Tailscale port already has a listener; choose another port or another machine. Existing routes are protected')
        status = json.loads(run(['tailscale', 'status', '--json']))
        b['hostname'] = str(status['Self']['DNSName']).rstrip('.')
    if b['exposure'] == 'cloudflare' and not Path(b['token_file']).expanduser().is_file():
        raise ValueError('Cloudflare token file does not exist on target')
    config = {'port': b['port'], 'bind': '127.0.0.1',
              'public_host': (b.get('hostname') if b['exposure'] != 'private' else None) or 'localhost'}
    if b['exposure'].startswith('tailscale-') and b['https_port'] != 443:
        config['public_host'] += ':' + str(b['https_port'])
    write_json(directory / 'config.json', config)
    write_json(directory / 'binding.json', b)
    python = root / 'venv' / 'bin' / 'python'
    python_exe = str(python) if python.exists() else sys.executable
    env = dict(os.environ, LOOPBACK_CONFIG_DIR=str(directory))
    bridge = launch([python_exe, str(root / 'worker_bridge.py'), str(directory)], directory, 'bridge', env)
    state = {'binding': b, 'bridge': bridge, 'started_at': time.time()}
    write_json(directory / 'state.json', state)
    try:
        for _ in range(60):
            if not alive(bridge):
                raise ValueError('Worker bridge exited; inspect bridge.log on this target')
            try:
                with socket.create_connection(('127.0.0.1', b['port']), timeout=.2):
                    break
            except OSError:
                time.sleep(.2)
        else:
            raise ValueError('Worker bridge did not become ready')
        if b['exposure'].startswith('tailscale-'):
            mode = 'funnel' if b['exposure'] == 'tailscale-funnel' else 'serve'
            run(['tailscale', mode, '--bg', '--https=' + str(b['https_port']), f"http://127.0.0.1:{b['port']}"])
            state['tailscale_owned'] = True
        elif b['exposure'] == 'cloudflare':
            state['tunnel'] = launch(['cloudflared', 'tunnel', '--no-autoupdate', 'run', '--token-file',
                                      str(Path(b['token_file']).expanduser())], directory, 'tunnel')
            time.sleep(.3)
            if not alive(state['tunnel']):
                raise ValueError('Cloudflare connector exited; inspect tunnel.log on this target')
        write_json(directory / 'state.json', state)
    except Exception:
        terminate(state.get('tunnel'))
        terminate(bridge)
        state['bridge'] = None
        write_json(directory / 'state.json', state)
        raise
    return {'ok': True, 'name': b['name'], 'running': True, 'url': endpoint(b), 'auth': 'OAuth 2.1; enroll using the dashboard credential action'}


def endpoint(b):
    host = b.get('hostname') if b.get('exposure') != 'private' else None
    if host:
        port = b.get('https_port', 443) if b.get('exposure', '').startswith('tailscale-') else 443
        return f"https://{host}" + (f':{port}' if port != 443 else '') + '/mcp'
    return f"http://127.0.0.1:{b['port']}/mcp"


def process_sample(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'ppid': int(stat[1]), 'cpu': (int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK'),
                'rss': int(stat[21]) * os.sysconf('SC_PAGE_SIZE')}
    except (OSError, ValueError, IndexError):
        return None


# Stacked or virtual adapters carry traffic that a physical adapter already counts. Counting both
# doubles the host figure, so they are listed separately and excluded from the host totals.
VIRTUAL_IFACE = re.compile(r'^(lo|tailscale|ts|docker|br-|virbr|veth|tun|tap|wg|cni|flannel|lxc|lxd|vnet|utun|zt|nebula|podman)')


def host_network(text):
    """Parse /proc/net/dev into (rx_total, tx_total, counted_names, excluded_names)."""
    counted, excluded, rx, tx = [], [], 0, 0
    for line in text.splitlines()[2:]:
        if ':' not in line:
            continue
        name, rest = line.split(':', 1)
        name = name.strip()
        fields = rest.split()
        if len(fields) < 9:
            continue
        if VIRTUAL_IFACE.match(name):
            excluded.append(name)
            continue
        counted.append(name)
        rx += int(fields[0])
        tx += int(fields[8])
    return rx, tx, counted, excluded


def metrics(root, controller_pid=None):
    host = {'cpu_count': os.cpu_count() or 1, 'cpu_seconds': None, 'cpu_idle_seconds': None,
            'memory_total': None, 'memory_used': None, 'rx_bytes': None, 'tx_bytes': None,
            'net_interfaces': [], 'net_excluded': [], 'load1': None}
    procs = {}
    try:
        cpu = [int(x) / os.sysconf('SC_CLK_TCK') for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        host.update(cpu_seconds=sum(cpu), cpu_idle_seconds=cpu[3] + cpu[4])
        mem = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()}
        host.update(memory_total=mem['MemTotal'], memory_used=mem['MemTotal'] - mem['MemAvailable'])
        rx, tx, counted, excluded = host_network(Path('/proc/net/dev').read_text())
        host.update(rx_bytes=rx, tx_bytes=tx, net_interfaces=counted, net_excluded=excluded)
        try:
            host['load1'] = round(os.getloadavg()[0], 2)
        except (OSError, AttributeError):
            pass
        for f in Path('/proc').iterdir():
            if f.name.isdigit():
                p = process_sample(int(f.name))
                if p:
                    procs[int(f.name)] = p
    except (OSError, KeyError, ValueError):
        pass
    states = [read_json(f, {}) for f in (root / 'bindings').glob('*/state.json')]
    owned = {p['pid'] for s in states for p in (s.get('bridge'), s.get('tunnel')) if alive(p)}
    if controller_pid:
        owned.add(controller_pid)
    for _ in range(len(procs)):
        children = {pid for pid, p in procs.items() if p['ppid'] in owned}
        if children <= owned:
            break
        owned |= children
    chosen = [p for pid, p in procs.items() if pid in owned]
    traffic = [read_json(f, {}) for f in (root / 'bindings').glob('*/traffic.json')]
    return {'sampled_at': time.time(), 'host': host, 'worker': {
        'cpu_seconds': sum(p['cpu'] for p in chosen), 'rss_bytes': sum(p['rss'] for p in chosen),
        'process_count': len(chosen), 'rx_bytes': sum(t.get('rx_bytes', 0) for t in traffic),
        'tx_bytes': sum(t.get('tx_bytes', 0) for t in traffic)},
        'bindings': [{'name': s.get('binding', {}).get('name'), 'running': alive(s.get('bridge')),
                      'url': endpoint(s['binding']), 'started_at': s.get('started_at')} for s in states if s.get('binding')]}


# --------------------------------------------------------------------------- read-only discovery
SECRET_ARG = re.compile(r'token=\S*', re.I)
SECRET_FLAGS = ('--token', '--token-file', '--credentials-file', '--credentials-contents', '--origincert')


def _safe_cloudflared_args(args):
    """Redact credential values in cloudflared argv without hiding config paths."""
    safe = []
    redact_next = False
    for arg in args:
        if redact_next:
            safe.append('<redacted>')
            redact_next = False
            continue
        flag = next((flag for flag in SECRET_FLAGS if arg.lower() == flag), None)
        if flag:
            safe.append(flag)
            redact_next = True
            continue
        inline_flag = next((flag for flag in SECRET_FLAGS if arg.lower().startswith(flag + '=')), None)
        if inline_flag:
            safe.append(inline_flag + '=<redacted>')
            continue
        safe.append(SECRET_ARG.sub('token=<redacted>', arg))
    return safe


def _hex_addr(value):
    host, port = value.split(':')
    port = int(port, 16)
    raw = bytes.fromhex(host)
    if len(raw) == 4:
        return socket.inet_ntoa(raw[::-1]), port
    words = [raw[i:i + 4][::-1] for i in range(0, 16, 4)]
    return socket.inet_ntop(socket.AF_INET6, b''.join(words)), port


def tcp_listeners():
    """LISTEN sockets from /proc/net/tcp{,6} with best-effort owner (own processes only)."""
    inodes = {}
    for table in ('tcp', 'tcp6'):
        try:
            lines = Path(f'/proc/net/{table}').read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 10 or f[3] != '0A':
                continue
            try:
                addr, port = _hex_addr(f[1])
            except (ValueError, OSError):
                continue
            inodes[f[9]] = {'address': addr, 'port': port, 'pid': None, 'process': None}
    if inodes:
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                for fd in (proc / 'fd').iterdir():
                    target = os.readlink(fd)
                    if target.startswith('socket:[') and target[8:-1] in inodes:
                        ino = inodes[target[8:-1]]
                        ino['pid'] = int(proc.name)
                        ino['process'] = (proc / 'comm').read_text().strip()[:40]
            except OSError:
                continue  # another user's process: owner stays unknown
    out = sorted(inodes.values(), key=lambda x: (x['port'], x['address']))
    seen, unique = set(), []
    for item in out:
        key = (item['address'], item['port'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def tailscale_routes(data):
    """Every Serve/Funnel handler (background and foreground) as flat, read-only route records."""
    routes = []
    configs = [(data, False)] + [(fg, True) for fg in (data.get('Foreground') or {}).values() if isinstance(fg, dict)] if isinstance(data, dict) else []
    for cfg, foreground in configs:
        allow = cfg.get('AllowFunnel') or {}
        for host_port, web in (cfg.get('Web') or {}).items():
            handlers = (web or {}).get('Handlers') or {}
            for path, handler in handlers.items():
                h = handler or {}
                routes.append({'provider': 'tailscale', 'mode': 'funnel' if allow.get(host_port) else 'serve', 'host_port': host_port,
                               'path': path, 'target': h.get('Proxy') or h.get('Path') or h.get('Text') and 'text' or None,
                               'foreground': foreground})
        for host_port, tcp in (cfg.get('TCP') or {}).items():
            routes.append({'provider': 'tailscale', 'mode': 'funnel' if allow.get(host_port) else 'serve', 'host_port': str(host_port),
                           'path': None, 'target': 'tcp:' + str((tcp or {}).get('TCPForward') or ''), 'foreground': foreground, 'tcp': True})
    return routes


def cloudflared_processes():
    procs = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc / 'comm').read_text().strip() != 'cloudflared':
                continue
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            args = [a.decode('utf-8', 'replace') for a in args if a]
        except OSError:
            continue
        safe = _safe_cloudflared_args(args[1:])
        config = None
        for i, a in enumerate(safe):
            if a == '--config' and i + 1 < len(safe):
                config = safe[i + 1]
            elif a.startswith('--config='):
                config = a.split('=', 1)[1]
        entry = {'pid': int(proc.name), 'args': ' '.join(safe)[:300], 'config_file': config, 'ingress': []}
        if config:
            try:
                for line in Path(os.path.expanduser(config)).read_text().splitlines()[:200]:
                    key, _, value = line.strip().partition(':')
                    key = key.strip('- ').strip()
                    if key in ('hostname', 'service', 'tunnel') and value.strip():
                        entry['ingress'].append({key: value.strip()[:200]})
            except OSError:
                entry['config_readable'] = False
        procs.append(entry)
    return procs


def discover(root):
    """Read-only host inventory: nothing here starts, stops or rewrites anything."""
    out = {'observed_at': time.time(), 'hostname': socket.gethostname(), 'tailscale': {'status': 'ok', 'routes': [], 'dns_name': None},
           'cloudflared': {'status': 'ok', 'processes': []}, 'listeners': {'status': 'ok', 'items': []}, 'bindings': []}
    try:
        out['tailscale']['routes'] = tailscale_routes(serve_config())
        status = json.loads(run(['tailscale', 'status', '--json']))
        out['tailscale']['dns_name'] = str((status.get('Self') or {}).get('DNSName') or '').rstrip('.') or None
    except (FileNotFoundError, ValueError, OSError) as exc:
        out['tailscale'] = {'status': 'unavailable', 'routes': [], 'dns_name': None, 'error': 'tailscale not available: ' + str(exc)[:120]}
    try:
        out['cloudflared']['processes'] = cloudflared_processes()
    except OSError as exc:
        out['cloudflared'] = {'status': 'unavailable', 'processes': [], 'error': str(exc)[:120]}
    try:
        out['listeners']['items'] = tcp_listeners()
    except OSError as exc:
        out['listeners'] = {'status': 'unavailable', 'items': [], 'error': str(exc)[:120]}
    for f in (root / 'bindings').glob('*/state.json'):
        state = read_json(f, {})
        b = state.get('binding') or {}
        if b:
            out['bindings'].append({'name': b.get('name'), 'port': b.get('port'), 'exposure': b.get('exposure'), 'https_port': b.get('https_port'),
                                    'hostname': b.get('hostname'), 'running': alive(state.get('bridge')),
                                    'tunnel_pid': (state.get('tunnel') or {}).get('pid') if alive(state.get('tunnel')) else None,
                                    'tailscale_owned': bool(state.get('tailscale_owned'))})
    return out


def dispatch(root, payload):
    root = Path(root)
    action = payload.get('action')
    if action == 'metrics':
        return metrics(root, payload.get('controller_pid'))
    if action == 'discover':
        return discover(root)
    b = payload.get('binding', {})
    validate(b)
    directory = root / 'bindings' / b['name']
    if action == 'start':
        return start_binding(root, dict(b))
    state = read_json(directory / 'state.json', {})
    if action == 'stop':
        return stop_binding(directory, state)
    if action == 'credential':
        if not state.get('binding') or not alive(state.get('bridge')):
            raise ValueError('Start this binding before enrolling')
        # Use the same auth implementation as the gateway; never return the machine token.
        from auth import AuthStore
        auth = AuthStore(directory, resource_uris=[endpoint(state['binding'])], base_urls=[endpoint(state['binding']).removesuffix('/mcp')])
        code = auth.create_enrollment()
        return {'url': endpoint(state['binding']).removesuffix('/mcp') + '/admin/enroll#code=' + code, 'code': code}
    raise ValueError('Unknown worker action')


if __name__ == '__main__':
    try:
        result = dispatch(ROOT, json.load(sys.stdin))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
