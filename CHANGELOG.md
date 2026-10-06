# Changelog

## 3.0.0 — 2026-10-06

Loopback grows from a portable remote MCP bridge into a self-hostable AI machine
control plane.

### Added

- bounded one-shot shell execution
- background jobs with incremental output
- persistent PTY terminal sessions
- structured process listing and signaling
- server-side filesystem allow/deny roots
- per-tool allow/deny policy
- standard, trusted, read-only, and locked profiles
- bounded approvals for sensitive command patterns
- hard-deny command patterns
- MCP read-only/destructive/idempotent annotations
- local SQLite MCP audit history
- authenticated operations dashboard
- direct multi-node gateway and node routing tools
- ergonomic node read/execute/diagnostics wrappers
- DOCX text read/replace tools
- XLSX range read/write tools
- PDF text/merge/page-extraction tools
- optional structured browser automation adapter
- optional Linux/X11 native desktop adapter with MCP screenshots
- portable Agent Plugin package builder
- Python 3.11/3.13 CI and runtime/integration tests

### Changed

- OAuth now uses authorization code + mandatory PKCE S256.
- OAuth clients receive short-lived signed access and refresh tokens instead of
  the permanent machine bearer token.
- Static bearer authentication remains supported for compatible MCP clients.
- Installer deploys the modular runtime rather than a single server file.
- CLI now exposes dashboard, policy, and node management commands.

### Security

- common credential directories are denied by the default filesystem policy
- remote-node credentials are separated from node metadata
- dashboard uses an HttpOnly, SameSite=Strict session cookie
- audit storage excludes bearer credentials and MCP request bodies
- browser and native-desktop automation are disabled by default
- destructive capabilities remain subject to both client approval policy and
  Loopback's own machine-side policy
