#!/usr/bin/env node
/*
 * loopback-mcp: npm entry point for Loopback.
 *
 *   npx loopback-mcp                 install (or update) Loopback on this machine
 *   npx loopback-mcp install [opts]  same, with installer options (see install.sh --help)
 *   npx loopback-mcp <command>       run the installed 'loopback' CLI (status, doctor, ...)
 *
 * The installer downloads the GitHub release matching this package version,
 * verifies its SHA-256 against the release's SHA256SUMS, extracts it with the
 * system 'tar' and runs install.sh (Linux/macOS) or install.ps1 (Windows).
 * LOOPBACK_SOURCE_DIR=<checkout> installs from a local checkout instead (tests).
 * No dependencies; Node >= 18.
 */
'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const pkg = require('../package.json');
const REPO = 'p5n-n3t/loopback';
const IS_WIN = process.platform === 'win32';
const TAG = `v${pkg.version}`;

function fail(message, code = 1) {
  process.stderr.write(`loopback-mcp: ${message}\n`);
  process.exit(code);
}

function configDir() {
  if (process.env.LOOPBACK_CONFIG_DIR) return process.env.LOOPBACK_CONFIG_DIR;
  if (IS_WIN) return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'Loopback');
  const xdg = process.env.XDG_CONFIG_HOME;
  return path.join(xdg || path.join(os.homedir(), '.config'), 'loopback');
}

/** Returns {cmd, args} for the installed CLI, or null when Loopback is not installed. */
function installedCli() {
  const cfg = configDir();
  if (IS_WIN) {
    const py = path.join(cfg, 'venv', 'Scripts', 'python.exe');
    const cli = path.join(cfg, 'cli.py');
    if (fs.existsSync(py) && fs.existsSync(cli)) return { cmd: py, args: [cli] };
    return null;
  }
  const binDir = process.env.LOOPBACK_BIN_DIR || path.join(os.homedir(), '.local', 'bin');
  const wrapper = path.join(binDir, 'loopback');
  if (fs.existsSync(wrapper) && fs.existsSync(path.join(cfg, 'cli.py'))) return { cmd: wrapper, args: [] };
  return null;
}

function run(cmd, args) {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { stdio: 'inherit', windowsHide: false });
    child.on('error', (err) => {
      process.stderr.write(`loopback-mcp: could not start ${cmd}: ${err.message}\n`);
      resolve(127);
    });
    child.on('exit', (code, signal) => resolve(code === null ? (signal ? 128 : 1) : code));
  });
}

function download(url, dest, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 10) return reject(new Error(`too many redirects for ${url}`));
    if (!url.startsWith('https://')) return reject(new Error(`refusing non-https URL ${url}`));
    const req = https.get(url, { headers: { 'User-Agent': `loopback-mcp/${pkg.version}` } }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
        res.resume();
        const next = new URL(res.headers.location, url).toString();
        return resolve(download(next, dest, redirects + 1));
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
      }
      const out = fs.createWriteStream(dest, { mode: 0o600 });
      res.pipe(out);
      out.on('finish', () => out.close(() => resolve(dest)));
      out.on('error', reject);
      res.on('error', reject);
    });
    req.setTimeout(60000, () => req.destroy(new Error(`timeout downloading ${url}`)));
    req.on('error', reject);
  });
}

function sha256File(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    fs.createReadStream(file).on('data', (d) => hash.update(d)).on('end', () => resolve(hash.digest('hex'))).on('error', reject);
  });
}

async function fetchRelease() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'loopback-'));
  const name = `loopback-${TAG}.tar.gz`;
  const base = `https://github.com/${REPO}/releases/download/${TAG}`;
  const archive = path.join(tmp, name);
  const sums = path.join(tmp, 'SHA256SUMS');
  process.stdout.write(`Downloading Loopback ${TAG}...\n`);
  await download(`${base}/${name}`, archive);
  await download(`${base}/SHA256SUMS`, sums);
  const line = fs.readFileSync(sums, 'utf8').split(/\r?\n/).find((l) => l.trim().endsWith(name));
  if (!line) throw new Error(`SHA256SUMS has no entry for ${name}`);
  const expected = line.trim().split(/\s+/)[0].toLowerCase();
  const actual = await sha256File(archive);
  if (expected !== actual) throw new Error(`checksum verification FAILED for ${name}`);
  process.stdout.write(`verified ${name} (sha256)\n`);
  const tar = spawnSync('tar', ['-xzf', archive, '-C', tmp], { stdio: 'inherit' });
  if (tar.error || tar.status !== 0) {
    throw new Error("could not extract the release with 'tar' (Windows needs 10 1803+; Linux/macOS: install tar)");
  }
  const root = path.join(tmp, `loopback-${TAG}`);
  if (!fs.existsSync(path.join(root, 'runtime', 'cli.py'))) throw new Error('release archive is missing runtime/cli.py');
  return { root, tmp };
}

function havePython() {
  const candidates = IS_WIN ? [['py', ['-3', '--version']], ['python', ['--version']]] : [['python3', ['--version']]];
  return candidates.some(([cmd, args]) => {
    const r = spawnSync(cmd, args, { stdio: 'ignore' });
    return !r.error && r.status === 0;
  });
}

async function install(args) {
  let root = process.env.LOOPBACK_SOURCE_DIR;
  let tmp = null;
  if (root) {
    root = path.resolve(root);
    if (!fs.existsSync(path.join(root, 'runtime', 'cli.py'))) fail(`LOOPBACK_SOURCE_DIR=${root} has no runtime/cli.py`, 2);
  } else {
    try {
      ({ root, tmp } = await fetchRelease());
    } catch (err) {
      fail(`${err.message}\n  You can also install from a checkout: git clone https://github.com/${REPO} && cd loopback && ./install.sh`);
    }
  }
  if (!havePython()) {
    process.stderr.write(
      IS_WIN
        ? 'loopback-mcp: Python 3.11+ was not found; install.ps1 will offer "winget install Python.Python.3.12".\n'
        : 'loopback-mcp: python3 was not found; install.sh will offer to install Python 3.11+ with your package manager\n' +
          '              (Debian/Ubuntu: sudo apt-get install python3 python3-venv, macOS: brew install python@3.12).\n',
    );
  }
  let code;
  if (IS_WIN) {
    const shell = spawnSync('pwsh', ['-NoProfile', '-Command', 'exit 0'], { stdio: 'ignore' }).status === 0 ? 'pwsh' : 'powershell';
    code = await run(shell, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(root, 'install.ps1'), ...args]);
  } else {
    code = await run('bash', [path.join(root, 'install.sh'), ...args]);
  }
  if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
  if (code !== 0 && !havePython()) {
    process.stderr.write('loopback-mcp: installation failed because Python 3.11+ is missing. Install it and rerun npx loopback-mcp.\n');
  }
  return code;
}

async function main() {
  const argv = process.argv.slice(2);
  if (argv[0] === '--version' || argv[0] === '-v') {
    process.stdout.write(`${pkg.version}\n`);
    return 0;
  }
  if (argv.length === 0 || argv[0] === 'install') {
    return install(argv.slice(1));
  }
  const cli = installedCli();
  if (!cli) {
    process.stderr.write('loopback-mcp: Loopback is not installed yet. Run: npx loopback-mcp install\n');
    return 1;
  }
  return run(cli.cmd, [...cli.args, ...argv]);
}

main().then((code) => process.exit(code), (err) => fail(err && err.stack ? err.stack : String(err)));
