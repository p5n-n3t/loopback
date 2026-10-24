# loopback-mcp

Installer and launcher for [Loopback](https://github.com/p5n-n3t/loopback), a
self-hostable MCP control plane for computers and servers.

```bash
npx loopback-mcp                      # install or update Loopback on this machine
npx loopback-mcp install --non-interactive --ingress local
npx loopback-mcp status               # any other command runs the installed `loopback` CLI
npx loopback-mcp doctor --deep
```

What `install` does:

1. Downloads `loopback-v<version>.tar.gz` and `SHA256SUMS` from the GitHub
   release that matches this package version (HTTPS, redirects followed).
2. Verifies the archive's SHA-256 against `SHA256SUMS` and refuses to continue
   on a mismatch.
3. Extracts it with the system `tar` (bundled with Linux, macOS and Windows 10 1803+).
4. Runs `install.sh` (Linux/macOS) or `install.ps1` (Windows) with your options.
   The installer needs Python 3.11+ and offers to install it when missing.

Loopback installs into `~/.config/loopback` (Windows: `%LOCALAPPDATA%\Loopback`)
and runs as your normal account. The npm package has no dependencies and does
not run anything at `npm install` time.

Environment variables:

| Variable | Purpose |
|---|---|
| `LOOPBACK_SOURCE_DIR` | Install from a local checkout instead of downloading (used in tests) |
| `LOOPBACK_CONFIG_DIR` | Alternative configuration directory |

Requirements: Node.js 18+, Python 3.11+ (with `venv`).

License: MIT.
